"""Tela de novo pedido: leitura do formulário, validação, cálculos e gravação.

O servidor valida tudo de novo e recalcula subtotais e total; nada do navegador é confiado.
"""

import logging
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from app.pedidos_repositorio import (
    FalhaAoGravar,
    ItemParaGravar,
    PedidoParaGravar,
    RepositorioPedidos,
)

logger = logging.getLogger("formahub.pedidos")

FORMAS_PAGAMENTO = ["PIX", "Dinheiro", "Cartão"]
FORMAS_ENTREGA = ["Entrega em mãos", "Retirada"]

# Opção da tela -> valor gravado no banco.
CODIGOS_PAGAMENTO = {"PIX": "pix", "Dinheiro": "dinheiro", "Cartão": "cartao"}
CODIGOS_ENTREGA = {"Entrega em mãos": "entrega", "Retirada": "retirada"}

# Imagem de referência do item: opcional, uma por item, PNG/JPEG/WebP até 10 MB.
LIMITE_IMAGEM_MB = 10
LIMITE_IMAGEM_BYTES = LIMITE_IMAGEM_MB * 1024 * 1024

DIGITOS_CONTATO = 11
SO_MASCARA_CONTATO = re.compile(r"^[\d()\s-]*$")


def somente_digitos(texto: str) -> str:
    return re.sub(r"\D", "", texto)


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


@dataclass
class Pedido:
    cliente: str = ""
    contato: str = ""
    pagamento: str = ""
    entrega: str = ""
    observacoes: str = ""
    itens: list[Item] = field(default_factory=list)
    quantidade_total: int = 0
    total: Decimal = Decimal("0")
    erros: dict[str, str] = field(default_factory=dict)

    @property
    def valido(self) -> bool:
        return not self.erros and not any(item.erros for item in self.itens)


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
    linhas = [
        Item(
            produto=produtos[i].strip() if i < len(produtos) else "",
            quantidade=quantidades[i].strip() if i < len(quantidades) else "",
            valor_unitario=valores[i].strip() if i < len(valores) else "",
            indice=i,
            imagem=Imagem(imagens[i]) if i in imagens else None,
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
    ] or linhas[:1]

    return Pedido(
        cliente=campo("cliente"),
        contato=campo("contato"),
        pagamento=campo("pagamento"),
        entrega=campo("entrega"),
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

    if pedido.pagamento not in FORMAS_PAGAMENTO:
        erros["pagamento"] = "Escolha a forma de pagamento."
    if pedido.entrega not in FORMAS_ENTREGA:
        erros["entrega"] = "Escolha a forma de entrega."

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
            problema = _problema_da_imagem(item.imagem)
            if problema:
                item.erros["imagem"] = problema

        if quantidade is not None:
            pedido.quantidade_total += quantidade
        if quantidade is not None and valor is not None:
            item.subtotal = quantidade * valor
            pedido.total += item.subtotal

    return pedido


def _problema_da_imagem(imagem: Imagem) -> str | None:
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
                tipo_entrega=CODIGOS_ENTREGA[pedido.entrega],
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
