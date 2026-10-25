"""Tela de novo pedido: leitura do formulário, validação, cálculos e gravação.

O servidor valida tudo de novo e recalcula subtotais e total; nada do navegador é confiado.
"""

import logging
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from app.pedidos_repositorio import (
    AlteracaoRecusada,
    FalhaAoAlterar,
    FalhaAoGravar,
    ItemParaGravar,
    PedidoConsultado,
    PedidoParaGravar,
    RepositorioPedidos,
)

logger = logging.getLogger("formahub.pedidos")

FORMAS_PAGAMENTO = ["PIX", "Dinheiro", "Cartão"]
STATUS_PAGAMENTO = ["Pendente", "Pago"]
FORMAS_ENTREGA = ["Entrega em mãos", "Retirada"]

# Grupos de escolha obrigatórios -> mensagem quando nada foi escolhido
# (a mesma no servidor e na marcação imediata feita no navegador).
ESCOLHA_OBRIGATORIA = {
    "pagamento": "Escolha a forma de pagamento.",
    "status_pagamento": "Escolha o status do pagamento.",
    "entrega": "Escolha a forma de entrega.",
}

# Opção da tela -> valor gravado no banco.
CODIGOS_PAGAMENTO = {"PIX": "pix", "Dinheiro": "dinheiro", "Cartão": "cartao"}
CODIGOS_STATUS = {"Pendente": "pendente", "Pago": "pago"}
CODIGOS_ENTREGA = {"Entrega em mãos": "entrega", "Retirada": "retirada"}
# Valor do banco -> texto das telas de consulta.
ROTULOS_PAGAMENTO = {codigo: rotulo for rotulo, codigo in CODIGOS_PAGAMENTO.items()}
ROTULOS_STATUS = {codigo: rotulo for rotulo, codigo in CODIGOS_STATUS.items()}
ROTULOS_ENTREGA = {codigo: rotulo for rotulo, codigo in CODIGOS_ENTREGA.items()}

# Produção: etapas na ordem do processo (código do banco, título da coluna).
ETAPAS_PRODUCAO = [
    ("fila_producao", "Na Fila de Produção"),
    ("em_producao", "Em Produção"),
    ("aguardando_entrega", "Ag. Entrega"),
    ("entregue", "Entregue"),
]
ROTULOS_ETAPA = dict(ETAPAS_PRODUCAO)

# Filtro de status da listagem de pedidos: as mesmas etapas da Produção, com nomes curtos.
FILTROS_STATUS = [
    ("fila_producao", "Na Fila"),
    ("em_producao", "Em Produção"),
    ("aguardando_entrega", "Ag. Entrega"),
    ("entregue", "Finalizado"),
    ("cancelado", "Cancelado"),
]


@dataclass(frozen=True)
class Movimento:
    """Um botão do card: leva o pedido para `destino`."""

    destino: str
    texto: str
    retorno: bool = False  # volta uma etapa (botão secundário)


# Movimentos permitidos a partir de cada etapa, na ordem dos botões do card (os mesmos da
# função mover_etapa_producao do banco). Nada sai de "entregue".
MOVIMENTOS_ETAPA = {
    "fila_producao": [Movimento("em_producao", "Iniciar produção")],
    "em_producao": [
        Movimento("fila_producao", "Voltar para fila", retorno=True),
        Movimento("aguardando_entrega", "Finalizar produção"),
    ],
    "aguardando_entrega": [Movimento("entregue", "Marcar como entregue")],
}


# Na lista de pedidos, o pedido também pode ser finalizado (entregue) de qualquer etapa; o
# quadro da Produção continua só com os botões de MOVIMENTOS_ETAPA.
MOVIMENTOS_PEDIDOS = {
    etapa: [*movimentos, Movimento("entregue", "Finalizar")]
    if all(m.destino != "entregue" for m in movimentos)
    else list(movimentos)
    for etapa, movimentos in MOVIMENTOS_ETAPA.items()
}


def movimento_permitido(atual: str, nova: str) -> bool:
    return any(m.destino == nova for m in MOVIMENTOS_PEDIDOS.get(atual, []))


# Status do pagamento na Produção: alterna entre os dois valores (os mesmos da função
# alterar_status_pagamento do banco). Status atual -> (novo status, ação do botão).
ALTERNANCIA_PAGAMENTO = {
    "pendente": ("pago", "Marcar pagamento como pago"),
    "pago": ("pendente", "Marcar pagamento como pendente"),
}

# Comentários da produção: o mesmo limite na tela, no servidor e no banco.
LIMITE_COMENTARIO = 2000


def normalizar_comentario(texto: str) -> str | None:
    """Quebras de linha como \\n, sem espaços nas pontas; vazio vira None."""
    return texto.replace("\r\n", "\n").replace("\r", "\n").strip() or None


# Imagem de referência do item: opcional, uma por item, PNG/JPEG/WebP até 10 MB.
LIMITE_IMAGEM_MB = 10
LIMITE_IMAGEM_BYTES = LIMITE_IMAGEM_MB * 1024 * 1024

DIGITOS_CONTATO = 11
SO_MASCARA_CONTATO = re.compile(r"^[\d()\s-]*$")
# Prazo de entrega: dd/mm/aa (as barras são inseridas pela tela; sem JavaScript, só os seis
# números também valem). O ano é 20aa.
FORMATO_PRAZO = re.compile(r"(\d{2})/?(\d{2})/?(\d{2})")
# O calendário da tela envia aaaa-mm-dd.
FORMATO_PRAZO_ISO = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


def somente_digitos(texto: str) -> str:
    return re.sub(r"\D", "", texto)


def ler_prazo(texto: str) -> tuple[date | None, str | None]:
    """'05/10/26' -> (date(2026, 10, 5), None); com problema -> (None, mensagem de erro)."""
    if not texto:
        return None, "Informe o prazo de entrega."
    if iso := FORMATO_PRAZO_ISO.fullmatch(texto):
        ano, mes, dia = (int(parte) for parte in iso.groups())
    elif encontrado := FORMATO_PRAZO.fullmatch(texto):
        dia, mes, ano = (int(parte) for parte in encontrado.groups())
        ano += 2000
    else:
        return None, "Use o formato dd/mm/aa."
    try:
        return date(ano, mes, dia), None
    except ValueError:
        return None, "Data inválida."


def formatar_data_curta(dia: date) -> str:
    """date(2026, 10, 5) -> '05/10/26'."""
    return dia.strftime("%d/%m/%y")


def formatar_contato(texto: str) -> str:
    """'11987654321' -> '(11) 98765-4321' (formata o que houver, como a máscara da tela)."""
    d = somente_digitos(texto)[:DIGITOS_CONTATO]
    if len(d) <= 2:
        return f"({d}" if d else ""
    if len(d) <= 7:
        return f"({d[:2]}) {d[2:]}"
    return f"({d[:2]}) {d[2:7]}-{d[7:]}"


def parse_moeda(texto: str) -> Decimal | None:
    """Converte 'R$ 1.234,56', '1234,56' ou '1234.56' em Decimal."""
    texto = texto.replace("R$", "").replace("\xa0", "").replace(" ", "").strip()
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    try:
        valor = Decimal(texto)
    except InvalidOperation:
        return None
    return valor if valor.is_finite() else None


def formatar_brl(valor: Decimal) -> str:
    """Decimal('1234.5') -> 'R$ 1.234,50'."""
    sinal = "-" if valor < 0 else ""
    inteiro, centavos = f"{abs(valor):.2f}".split(".")
    inteiro = f"{int(inteiro):,}".replace(",", ".")
    return f"{sinal}R$ {inteiro},{centavos}"


def tipo_da_imagem(conteudo: bytes) -> tuple[str, str] | None:
    """(tipo MIME, extensão) pelo conteúdo do arquivo, não pelo nome; None se não for aceito."""
    if conteudo.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", "png"
    if conteudo.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", "jpg"
    if conteudo[:4] == b"RIFF" and conteudo[8:12] == b"WEBP":
        return "image/webp", "webp"
    return None


@dataclass
class Imagem:
    conteudo: bytes
    tipo: str = ""
    extensao: str = ""


@dataclass
class Item:
    produto: str = ""
    quantidade: str = "1"
    valor_unitario: str = ""
    subtotal: Decimal = Decimal("0")
    erros: dict[str, str] = field(default_factory=dict)
    indice: int = 0  # posição da linha no formulário enviado (liga a imagem ao item)
    imagem: Imagem | None = None
    # Só na edição: caminho da imagem que o item já tem no Storage e se o usuário pediu para
    # removê-la. Uma imagem nova (`imagem`) substitui a atual.
    imagem_atual: str = ""
    remover_imagem: bool = False


@dataclass
class Pedido:
    cliente: str = ""
    contato: str = ""
    pagamento: str = ""
    status_pagamento: str = ""
    entrega: str = ""
    prazo_entrega: str = ""  # como enviado (aaaa-mm-dd do calendário)
    prazo: date | None = None  # preenchido pela validação
    observacoes: str = ""
    itens: list[Item] = field(default_factory=list)
    quantidade_total: int = 0
    total: Decimal = Decimal("0")
    erros: dict[str, str] = field(default_factory=dict)

    @property
    def valido(self) -> bool:
        return not self.erros and not any(item.erros for item in self.itens)

    @property
    def prazo_calendario(self) -> str:
        """Prazo digitado no formato aaaa-mm-dd do campo de calendário; vazio se inválido."""
        dia, _ = ler_prazo(self.prazo_entrega.strip())
        return dia.isoformat() if dia else ""


def pedido_vazio() -> Pedido:
    return Pedido(itens=[Item()])


def ler_formulario(dados: dict[str, list[str]], imagens: dict[int, bytes] | None = None) -> Pedido:
    """Monta o pedido a partir do formulário (valores como listas, estilo parse_qs).

    `imagens` liga a posição da linha no formulário ao conteúdo da imagem daquele item.
    """
    imagens = imagens or {}

    def campo(nome: str) -> str:
        return dados.get(nome, [""])[0].strip()

    produtos = dados.get("item_produto", [])
    quantidades = dados.get("item_quantidade", [])
    valores = dados.get("item_valor", [])
    atuais = dados.get("item_imagem_atual", [])
    acoes = dados.get("item_imagem_acao", [])
    linhas = [
        Item(
            produto=produtos[i].strip() if i < len(produtos) else "",
            quantidade=quantidades[i].strip() if i < len(quantidades) else "",
            valor_unitario=valores[i].strip() if i < len(valores) else "",
            indice=i,
            imagem=Imagem(imagens[i]) if i in imagens else None,
            imagem_atual=atuais[i].strip() if i < len(atuais) else "",
            remover_imagem=i < len(acoes) and acoes[i] == "remover",
        )
        for i in range(max(len(produtos), len(quantidades), len(valores)))
    ]
    # Linhas totalmente em branco (e sem imagem) são ignoradas; se todas estiverem em branco,
    # a primeira é mantida para que os erros apareçam nos próprios campos.
    itens = [
        item
        for item in linhas
        if item.produto
        or item.valor_unitario
        or item.quantidade not in ("", "1")
        or item.imagem is not None
        or item.imagem_atual
    ] or linhas[:1]

    return Pedido(
        cliente=campo("cliente"),
        contato=campo("contato"),
        pagamento=campo("pagamento"),
        status_pagamento=campo("status_pagamento"),
        entrega=campo("entrega"),
        prazo_entrega=campo("prazo_entrega"),
        observacoes=campo("observacoes"),
        itens=itens,
    )


def validar(pedido: Pedido) -> Pedido:
    """Preenche erros, subtotais, quantidade total e total do pedido."""
    erros = pedido.erros

    if not pedido.cliente:
        erros["cliente"] = "Informe o nome do cliente."

    digitos = somente_digitos(pedido.contato)
    if not pedido.contato:
        erros["contato"] = "Informe o contato."
    elif not SO_MASCARA_CONTATO.match(pedido.contato):
        erros["contato"] = "Use somente números."
    elif len(digitos) != DIGITOS_CONTATO:
        erros["contato"] = "Informe DDD e número: (00) 00000-0000."
    pedido.contato = formatar_contato(pedido.contato) if "contato" not in erros else pedido.contato

    # Só valores da lista: qualquer outro texto enviado pelo navegador é recusado.
    if pedido.pagamento not in FORMAS_PAGAMENTO:
        erros["pagamento"] = ESCOLHA_OBRIGATORIA["pagamento"]
    if pedido.status_pagamento not in STATUS_PAGAMENTO:
        erros["status_pagamento"] = ESCOLHA_OBRIGATORIA["status_pagamento"]
    if pedido.entrega not in FORMAS_ENTREGA:
        erros["entrega"] = ESCOLHA_OBRIGATORIA["entrega"]

    pedido.prazo, problema = ler_prazo(pedido.prazo_entrega)
    if problema:
        erros["prazo_entrega"] = problema
    else:
        pedido.prazo_entrega = formatar_data_curta(pedido.prazo)

    if not pedido.itens:
        erros["itens"] = "Adicione pelo menos um item."

    for item in pedido.itens:
        if not item.produto:
            item.erros["produto"] = "Informe o produto."

        quantidade = None
        if not item.quantidade:
            item.erros["quantidade"] = "Informe a quantidade."
        elif not item.quantidade.isdigit():
            item.erros["quantidade"] = "Use um número inteiro."
        elif int(item.quantidade) < 1:
            item.erros["quantidade"] = "Quantidade mínima é 1."
        else:
            quantidade = int(item.quantidade)

        valor = None
        if not item.valor_unitario:
            item.erros["valor_unitario"] = "Informe o valor unitário."
        else:
            valor = parse_moeda(item.valor_unitario)
            if valor is None:
                item.erros["valor_unitario"] = "Valor inválido."
            elif valor < 0:
                item.erros["valor_unitario"] = "Valor não pode ser negativo."
                valor = None
            else:
                valor = valor.quantize(Decimal("0.01"))
                item.valor_unitario = formatar_brl(valor)

        if item.imagem is not None:
            problema = problema_da_imagem(item.imagem)
            if problema:
                item.erros["imagem"] = problema

        if quantidade is not None:
            pedido.quantidade_total += quantidade
        if quantidade is not None and valor is not None:
            item.subtotal = quantidade * valor
            pedido.total += item.subtotal

    return pedido


def problema_da_imagem(imagem: Imagem) -> str | None:
    """Mesmas regras da tela; preenche o tipo e a extensão da imagem aceita."""
    if not imagem.conteudo:
        return "Arquivo vazio."
    if len(imagem.conteudo) > LIMITE_IMAGEM_BYTES:
        return f"Maior que {LIMITE_IMAGEM_MB} MB."
    tipo = tipo_da_imagem(imagem.conteudo)
    if tipo is None:
        return "Formato não aceito."
    imagem.tipo, imagem.extensao = tipo
    return None


def pedido_do_banco(consultado: PedidoConsultado) -> Pedido:
    """Pedido gravado -> o mesmo formato do formulário, para preencher a tela de edição."""
    itens = [
        Item(
            produto=item.produto,
            quantidade=str(item.quantidade),
            valor_unitario=formatar_brl(item.valor_unitario),
            subtotal=item.subtotal,
            indice=posicao,
            imagem_atual=item.imagem_caminho or "",
        )
        for posicao, item in enumerate(consultado.itens)
    ]
    return Pedido(
        cliente=consultado.cliente_nome,
        contato=formatar_contato(consultado.contato or ""),
        pagamento=ROTULOS_PAGAMENTO[consultado.forma_pagamento],
        status_pagamento=ROTULOS_STATUS[consultado.status_pagamento],
        entrega=ROTULOS_ENTREGA[consultado.tipo_entrega],
        prazo_entrega=consultado.prazo_entrega.isoformat(),
        prazo=consultado.prazo_entrega,
        observacoes=consultado.observacoes or "",
        itens=itens,
        quantidade_total=sum(item.quantidade for item in consultado.itens),
        total=consultado.valor_total,
    )


# ---------- Gravação ----------


class PedidoNaoSalvo(Exception):
    """A gravação falhou; o pedido não ficou gravado (nem pela metade)."""


def gravar_pedido(
    repo: RepositorioPedidos,
    pedido: Pedido,
    usuario_id: int,
    novo_uuid: Callable[[], uuid.UUID] = uuid.uuid4,
) -> int:
    """Grava um pedido já validado e devolve o id.

    1. reserva o id; 2. envia as imagens (o caminho usa o id); 3. grava pedido e itens numa
    única transação no banco. Se algo falhar, remove as imagens enviadas e levanta
    PedidoNaoSalvo.
    """
    enviadas: list[str] = []
    gravando = False
    try:
        pedido_id = repo.reservar_id()
        itens = []
        for ordem, item in enumerate(pedido.itens, 1):
            caminho = None
            if item.imagem is not None:
                caminho = f"pedidos/{pedido_id}/itens/{ordem}/{novo_uuid()}.{item.imagem.extensao}"
                enviadas.append(caminho)  # antes do envio: se a resposta se perder, remove igual
                repo.enviar_imagem(caminho, item.imagem.conteudo, item.imagem.tipo)
            itens.append(
                ItemParaGravar(
                    ordem=ordem,
                    produto=item.produto,
                    quantidade=int(item.quantidade),
                    valor_unitario=parse_moeda(item.valor_unitario),
                    subtotal=item.subtotal,
                    imagem_caminho=caminho,
                )
            )
        gravando = True
        repo.criar_pedido(
            PedidoParaGravar(
                id=pedido_id,
                criado_por=usuario_id,
                cliente_nome=pedido.cliente,
                contato=pedido.contato or None,
                forma_pagamento=CODIGOS_PAGAMENTO[pedido.pagamento],
                status_pagamento=CODIGOS_STATUS[pedido.status_pagamento],
                tipo_entrega=CODIGOS_ENTREGA[pedido.entrega],
                prazo_entrega=pedido.prazo,
                observacoes=pedido.observacoes or None,
                valor_total=pedido.total,
                itens=tuple(itens),
            )
        )
    except Exception as erro:
        if gravando and isinstance(erro, FalhaAoGravar) and erro.incerta:
            # A resposta do banco não chegou: o pedido pode ter sido gravado. Remover as imagens
            # deixaria um pedido com caminhos quebrados, então elas ficam.
            logger.error(
                "Gravação do pedido sem resposta; %d imagem(ns) mantida(s).", len(enviadas)
            )
        elif enviadas:
            _remover_imagens(repo, enviadas)
        if not isinstance(erro, FalhaAoGravar):
            logger.error("Erro inesperado ao gravar pedido: %s", type(erro).__name__)
        raise PedidoNaoSalvo from None
    return pedido_id


def _remover_imagens(repo: RepositorioPedidos, caminhos: list[str]) -> None:
    try:
        repo.remover_imagens(caminhos)
    except Exception:
        logger.error(
            "Não foi possível remover %d imagem(ns) de um pedido não salvo.", len(caminhos)
        )


class PedidoNaoEditavel(Exception):
    """O pedido foi entregue, cancelado ou não existe mais: não pode ser editado."""


def editar_pedido_gravado(
    repo: RepositorioPedidos,
    pedido_id: int,
    pedido: Pedido,
    usuario_id: int,
    caminhos_atuais: set[str],
    novo_uuid: Callable[[], uuid.UUID] = uuid.uuid4,
) -> None:
    """Grava a edição de um pedido já validado.

    `caminhos_atuais`: imagens que o pedido tem hoje; só elas podem ser mantidas (o caminho
    vindo do formulário nunca é confiado). 1. envia as imagens novas; 2. regrava dados e
    itens numa única transação; 3. remove do Storage as imagens que deixaram de ser usadas.
    Levanta PedidoNaoEditavel (banco recusou) ou PedidoNaoSalvo (falhou; nada mudou, ou a
    resposta se perdeu: nesse caso as imagens novas ficam, pois o pedido pode ter mudado).
    """
    enviadas: list[str] = []
    gravando = False
    mantidas: set[str] = set()
    try:
        itens = []
        for ordem, item in enumerate(pedido.itens, 1):
            caminho = None
            if item.imagem is not None:
                caminho = f"pedidos/{pedido_id}/itens/{ordem}/{novo_uuid()}.{item.imagem.extensao}"
                enviadas.append(caminho)  # antes do envio: se a resposta se perder, remove igual
                repo.enviar_imagem(caminho, item.imagem.conteudo, item.imagem.tipo)
            elif (
                item.imagem_atual in caminhos_atuais
                and not item.remover_imagem
                and item.imagem_atual not in mantidas
            ):
                caminho = item.imagem_atual
                mantidas.add(caminho)
            itens.append(
                ItemParaGravar(
                    ordem=ordem,
                    produto=item.produto,
                    quantidade=int(item.quantidade),
                    valor_unitario=parse_moeda(item.valor_unitario),
                    subtotal=item.subtotal,
                    imagem_caminho=caminho,
                )
            )
        gravando = True
        removidas = repo.editar_pedido(
            PedidoParaGravar(
                id=pedido_id,
                criado_por=usuario_id,  # na edição: quem está editando
                cliente_nome=pedido.cliente,
                contato=pedido.contato or None,
                forma_pagamento=CODIGOS_PAGAMENTO[pedido.pagamento],
                status_pagamento=CODIGOS_STATUS[pedido.status_pagamento],
                tipo_entrega=CODIGOS_ENTREGA[pedido.entrega],
                prazo_entrega=pedido.prazo,
                observacoes=pedido.observacoes or None,
                valor_total=pedido.total,
                itens=tuple(itens),
            )
        )
    except AlteracaoRecusada:
        # Recusa definitiva: nada foi gravado, então as imagens novas não servem.
        if enviadas:
            _remover_imagens(repo, enviadas)
        raise PedidoNaoEditavel from None
    except Exception as erro:
        if gravando and isinstance(erro, FalhaAoAlterar):
            logger.error("Edição do pedido sem resposta; %d imagem(ns) mantida(s).", len(enviadas))
        elif enviadas:
            _remover_imagens(repo, enviadas)
        if not isinstance(erro, FalhaAoGravar | FalhaAoAlterar):
            logger.error("Erro inesperado ao editar pedido: %s", type(erro).__name__)
        raise PedidoNaoSalvo from None

    if removidas:
        try:
            repo.remover_imagens(list(removidas))
        except Exception:
            logger.error("Não foi possível remover %d imagem(ns) antiga(s).", len(removidas))
