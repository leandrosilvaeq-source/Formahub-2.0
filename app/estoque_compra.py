"""Registrar Compra: leitura do formulário da janela, validação, cálculos e gravação.

Toda entrada no estoque é uma compra: data, local e uma ou mais linhas (filamento, acessório
ou embalagem). Cada linha vira um lote ligado ao item da compra que o originou.
O servidor valida tudo de novo; nada do navegador é confiado. Valores em Decimal, sem
arredondar etapas intermediárias (a tela só arredonda o que mostra).

Campos do formulário: csrf, chave_envio, data_compra, local, local_nome, frete e, para cada linha
na posição i da tela, item_{i}_categoria, item_{i}_<campo> e a foto opcional item_{i}_foto.
Os erros voltam com essas mesmas chaves, para a tela marcar o campo certo.
"""

import logging
import re
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal

from app.estoque import (
    LIMITE_TEXTO,
    TIPOS_FILAMENTO,
    formatar_numero,
    ler_data,
    validar_texto,
    validar_tipo,
)
from app.estoque_repositorio import (
    CompraParaGravar,
    FalhaNoEstoque,
    ItemCompraListado,
    ItemCompraParaGravar,
    RepositorioEstoque,
    custo_por_kg,
    custo_por_unidade,
)
from app.pedidos import Imagem, parse_moeda, problema_da_imagem

logger = logging.getLogger("formahub.estoque")

# Local de compra: código do banco -> texto do botão (na ordem dos botões).
LOCAIS = {
    "mercado_livre": "Mercado Livre",
    "shopee": "Shopee",
    "aliexpress": "AliExpress",
    "loja_fisica": "Loja Física",
    "outro": "Outro",
}
LOCAIS_COM_NOME = {"outro", "loja_fisica"}  # pedem o nome do local
# Logos das plataformas, servidos de app/static/img/locais (Loja Física e Outro usam ícones).
LOGOS_LOCAIS = {
    "mercado_livre": "mercado-livre.webp",
    "shopee": "shopee.png",
    "aliexpress": "aliexpress.ico",
}


def local_da_compra(local_tipo: str, local_nome: str | None) -> str:
    """'loja_fisica' + 'Papelaria' -> 'Loja Física · Papelaria'; plataformas: só o nome."""
    rotulo = LOCAIS.get(local_tipo, local_tipo)
    return f"{rotulo} · {local_nome}" if local_nome else rotulo


def descrever_item(item: ItemCompraListado) -> str:
    """'3 rolos · PLA Silk Preto · Marca X · 1.000 g' ou '40 un · Argola'."""
    if item.categoria != "filamento":
        return f"{item.quantidade} un · {item.nome}"
    rolos = f"{item.quantidade} {'rolo' if item.quantidade == 1 else 'rolos'}"
    tipo = TIPOS_FILAMENTO.get(item.tipo, item.tipo)
    partes = [rolos, f"{item.material} {tipo} {item.cor}", item.marca]
    if item.peso_rolo_g is not None:
        partes.append(f"{formatar_numero(item.peso_rolo_g)} g")
    return " · ".join(partes)


CATEGORIAS = {"filamento": "Filamento", "acessorio": "Acessório", "embalagem": "Embalagem"}
# Cabeçalho de colunas de cada seção da janela (a ordem dos campos da linha).
COLUNAS_COMPRA = {
    "filamento": (
        "Quantidade",
        "Material",
        "Tipo",
        "Peso (g)",
        "Cor",
        "Marca",
        "Valor unitário",
    ),
    "acessorio": ("Quantidade", "Nome", "Valor total"),
    "embalagem": ("Quantidade", "Nome", "Valor total"),
}
# Seções da janela, na ordem: (categoria, título, texto quando ainda não há linhas).
SECOES_COMPRA = (
    ("filamento", "Filamentos", "Nenhum filamento adicionado."),
    ("acessorio", "Acessórios", "Nenhum acessório adicionado."),
    ("embalagem", "Embalagens", "Nenhuma embalagem adicionada."),
)

# Os mesmos limites dos CHECK do banco.
LIMITE_ITENS = 50
LIMITE_ROLOS = 5  # rolos por linha de filamento (para mais, adicione outra linha)
LIMITE_QUANTIDADE = 999999
# Listas da janela para o filamento: o servidor só aceita estes valores.
MATERIAIS = ("PLA", "PETG", "TPU")
PESOS_ROLO_G = (Decimal("250"), Decimal("500"), Decimal("1000"))
LIMITE_VALOR = Decimal("9999999.99")  # cabe no numeric(12, 2)

# Campo de uma linha: item_{posição}_{campo}.
CAMPO_ITEM = re.compile(r"item_(\d{1,3})_([a-z_]+)")
CHAVE_ENVIO = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")

MENSAGEM_SEM_ITENS = "Adicione pelo menos um item."


def _chave_cor(cor: str) -> str:
    """Cores iguais, salvo espaços e maiúsculas/minúsculas, têm a mesma chave."""
    return " ".join(cor.split()).casefold()


def cores_conhecidas(cores: list[str]) -> list[str]:
    """Cores já salvas, sem repetição por espaços ou caixa.

    Vence a grafia mais usada; em empate, a que não é toda maiúscula nem minúscula.
    """
    grafias: dict[str, Counter] = {}
    for cor in cores:
        limpa = " ".join(cor.split())
        if limpa:
            grafias.setdefault(_chave_cor(limpa), Counter())[limpa] += 1
    escolhidas = [
        min(c, key=lambda g: (-c[g], g.isupper(), g.islower(), g)) for c in grafias.values()
    ]
    return sorted(escolhidas, key=lambda g: (g.casefold(), g))


@dataclass
class Compra:
    """Compra validada: pronta para gravar se `erros` estiver vazio."""

    chave_envio: uuid.UUID | None = None
    data_compra: date | None = None
    local_tipo: str = ""
    local_nome: str | None = None
    frete: Decimal = Decimal("0")
    # Linhas na ordem da tela, cada uma com a foto escolhida (ou None).
    itens: list[tuple[ItemCompraParaGravar, Imagem | None]] = field(default_factory=list)
    erros: dict[str, str] = field(default_factory=dict)


# ---------- Cálculos (os mesmos que a janela mostra) ----------


def peso_recebido(item: ItemCompraParaGravar) -> Decimal:
    """Filamento: quantidade de rolos x peso por rolo (g)."""
    return item.quantidade * item.peso_rolo_g


def valor_total(item: ItemCompraParaGravar) -> Decimal:
    """Filamento: quantidade x valor unitário; acessório e embalagem: o valor digitado."""
    if item.categoria == "filamento":
        return item.quantidade * item.valor_unitario
    return item.valor_total


def custo(item: ItemCompraParaGravar) -> Decimal:
    """Filamento: custo/kg; acessório e embalagem: custo unitário (sem arredondar)."""
    if item.categoria == "filamento":
        return custo_por_kg(item.valor_unitario, item.peso_rolo_g)
    return custo_por_unidade(item.valor_total, item.quantidade)


def subtotal_itens(itens: list[ItemCompraParaGravar]) -> Decimal:
    """Soma dos valores totais das linhas (sem frete)."""
    return sum((valor_total(item) for item in itens), Decimal("0"))


def total_da_compra(subtotal: Decimal, frete: Decimal) -> Decimal:
    """Total da compra = subtotal dos itens + frete. O frete não entra no custo dos lotes."""
    return subtotal + frete


# ---------- Leitura e validação ----------


def chave_da_submissao(texto: str) -> uuid.UUID | None:
    """UUID v4 gerado pela janela ao abrir; None se ausente ou fora do formato."""
    return uuid.UUID(texto) if CHAVE_ENVIO.fullmatch(texto) else None


def _limpo(texto: str) -> str:
    return " ".join(texto.split())


def _quantidade(texto: str, limite: int, rotulo: str) -> tuple[int | None, str | None]:
    if not texto:
        return None, f"Informe a {rotulo}."
    if not texto.isascii() or not texto.isdigit():
        return None, "Use um número inteiro."
    if int(texto) < 1:
        return None, "A quantidade deve ser maior que zero."
    if int(texto) > limite:
        return None, "Quantidade alta demais."
    return int(texto), None


def _valor(texto: str, rotulo: str) -> tuple[Decimal | None, str | None]:
    if not texto:
        return None, f"Informe o {rotulo}."
    valor = parse_moeda(texto)
    if valor is None:
        return None, "Valor inválido."
    if valor < 0:
        return None, "O valor não pode ser negativo."
    if valor > LIMITE_VALOR:
        return None, "Valor alto demais."
    if valor != valor.quantize(Decimal("0.01")):
        return None, "Use no máximo 2 casas decimais."
    return valor, None


def _frete(texto: str) -> tuple[Decimal, str | None]:
    """Frete opcional: vazio vale zero; não pode ser negativo."""
    if not texto:
        return Decimal("0"), None
    valor, problema = _valor(texto, "frete")
    return (valor if valor is not None else Decimal("0")), problema


def _peso_rolo(texto: str) -> tuple[Decimal | None, str | None]:
    """Peso de cada rolo: só 250, 500 ou 1000 g (as opções da lista)."""
    if not texto:
        return None, "Escolha o peso."
    for peso in PESOS_ROLO_G:
        if texto == str(peso):
            return peso, None
    return None, "Escolha um dos pesos da lista."


def _linhas(textos: dict[str, str]) -> dict[int, dict[str, str]]:
    """Posição da linha -> seus campos (só as linhas com categoria)."""
    linhas: dict[int, dict[str, str]] = {}
    for chave, valor in textos.items():
        if encontrado := CAMPO_ITEM.fullmatch(chave):
            linhas.setdefault(int(encontrado.group(1)), {})[encontrado.group(2)] = valor
    return {i: campos for i, campos in sorted(linhas.items()) if "categoria" in campos}


def _validar_linha(
    indice: int, ordem: int, campos: dict[str, str], erros: dict[str, str]
) -> ItemCompraParaGravar | None:
    """Valida uma linha; os erros entram em `erros` com a chave do campo na tela."""
    e: dict[str, str] = {}
    categoria = campos.get("categoria", "")

    def campo(nome: str) -> str:
        return _limpo(campos.get(nome, ""))

    item = None
    if categoria == "filamento":
        quantidade, problema = _quantidade(campo("quantidade"), LIMITE_ROLOS, "quantidade")
        if problema == "Quantidade alta demais.":
            problema = f"Escolha de 1 a {LIMITE_ROLOS} rolos; para mais, adicione outra linha."
        if problema:
            e["quantidade"] = problema
        cor, material, marca, tipo = campo("cor"), campo("material"), campo("marca"), campo("tipo")
        validar_texto(cor, e, "cor", "a cor")
        if material not in MATERIAIS:
            e["material"] = "Escolha o material."
        validar_tipo(tipo, e, "tipo")
        validar_texto(marca, e, "marca", "a marca")
        peso, problema = _peso_rolo(campo("peso_rolo"))
        if problema:
            e["peso_rolo"] = problema
        valor, problema = _valor(campo("valor_unitario"), "valor unitário por rolo")
        if problema:
            e["valor_unitario"] = problema
        if not e:
            item = ItemCompraParaGravar(
                ordem=ordem,
                categoria=categoria,
                quantidade=quantidade,
                cor=cor,
                material=material,
                tipo=tipo,
                marca=marca,
                peso_rolo_g=peso,
                valor_unitario=valor,
            )
    elif categoria in CATEGORIAS:
        rotulo = CATEGORIAS[categoria].lower()
        nome = campo("nome")
        artigo = "do" if categoria == "acessorio" else "da"
        validar_texto(nome, e, "nome", f"o nome {artigo} {rotulo}")
        quantidade, problema = _quantidade(campo("quantidade"), LIMITE_QUANTIDADE, "quantidade")
        if problema:
            e["quantidade"] = problema
        valor, problema = _valor(campo("valor_total"), "valor total")
        if problema:
            e["valor_total"] = problema
        if not e:
            item = ItemCompraParaGravar(
                ordem=ordem,
                categoria=categoria,
                quantidade=quantidade,
                nome=nome,
                valor_total=valor,
            )
    else:
        e["categoria"] = "Item de tipo desconhecido. Remova a linha e adicione de novo."

    erros.update({f"item_{indice}_{nome}": mensagem for nome, mensagem in e.items()})
    return item


def validar_compra(
    textos: dict[str, str],
    imagens: dict[int, bytes] | None = None,
    cores: list[str] | None = None,
) -> Compra:
    """Monta e valida a compra a partir dos campos de texto e das fotos (posição -> bytes).

    `cores`: as cores já salvas; uma cor igual a uma delas (salvo espaços e caixa) usa a grafia
    já salva, para não duplicar sugestões. Cores novas só são gravadas junto com a compra."""
    imagens = imagens or {}
    compra = Compra()
    e = compra.erros

    compra.chave_envio = chave_da_submissao(textos.get("chave_envio", ""))

    compra.data_compra, problema = ler_data(_limpo(textos.get("data_compra", "")))
    if problema:
        e["data_compra"] = problema

    compra.local_tipo = textos.get("local", "")
    if compra.local_tipo not in LOCAIS:
        e["local"] = "Escolha o local de compra."
    elif compra.local_tipo in LOCAIS_COM_NOME:
        # Outro ou Loja Física: o nome do local é obrigatório.
        nome = _limpo(textos.get("local_nome", ""))
        if not nome:
            e["local_nome"] = "Informe o nome do local."
        elif len(nome) > LIMITE_TEXTO:
            e["local_nome"] = f"Use no máximo {LIMITE_TEXTO} caracteres."
        else:
            compra.local_nome = nome
    # Plataforma: qualquer nome de local enviado é desconsiderado (local_nome fica None).

    compra.frete, problema = _frete(_limpo(textos.get("frete", "")))
    if problema:
        e["frete"] = problema

    canonicas = {_chave_cor(c): c for c in cores or []}
    linhas = _linhas(textos)
    if not linhas:
        e["itens"] = MENSAGEM_SEM_ITENS
    elif len(linhas) > LIMITE_ITENS:
        e["itens"] = f"Registre no máximo {LIMITE_ITENS} itens por compra."
        return compra

    for ordem, (indice, campos) in enumerate(linhas.items(), 1):
        item = _validar_linha(indice, ordem, campos, e)
        imagem = Imagem(imagens[indice]) if indice in imagens else None
        if imagem is not None and (problema := problema_da_imagem(imagem)):
            e[f"item_{indice}_foto"] = problema
        if item is not None:
            if item.cor is not None and (conhecida := canonicas.get(_chave_cor(item.cor))):
                item = replace(item, cor=conhecida)
            compra.itens.append((item, imagem))
    return compra


# ---------- Gravação ----------


class CompraNaoSalva(Exception):
    """A gravação falhou; nada da compra ficou gravado (nem pela metade)."""


def gravar_compra(
    repo: RepositorioEstoque,
    compra: Compra,
    usuario_id: int,
    novo_uuid: Callable[[], uuid.UUID] = uuid.uuid4,
) -> bool:
    """Grava uma compra já validada; devolve True se ela já estava gravada (reenvio).

    1. reserva o id; 2. envia as fotos (o caminho usa o id); 3. grava compra, itens e lotes
    numa única transação. Se algo falhar, remove só as fotos enviadas nesta tentativa e
    levanta CompraNaoSalva. Num reenvio da mesma submissão, nada novo é gravado e as fotos
    desta tentativa também são removidas.
    """
    enviadas: list[str] = []
    gravando = False
    try:
        compra_id = repo.reservar_id_compra()
        itens = []
        for item, imagem in compra.itens:
            if imagem is not None:
                caminho = f"compras/{compra_id}/itens/{item.ordem}/{novo_uuid()}.{imagem.extensao}"
                enviadas.append(caminho)  # antes do envio: se a resposta se perder, remove igual
                repo.enviar_imagem(caminho, imagem.conteudo, imagem.tipo)
                item = replace(item, imagem_caminho=caminho)
            itens.append(item)
        gravando = True
        duplicada = repo.registrar_compra(
            CompraParaGravar(
                id=compra_id,
                chave_envio=compra.chave_envio,
                data_compra=compra.data_compra,
                local_tipo=compra.local_tipo,
                local_nome=compra.local_nome,
                usuario_id=usuario_id,
                itens=tuple(itens),
                frete=compra.frete,
            )
        )
    except Exception as erro:
        if gravando and isinstance(erro, FalhaNoEstoque) and erro.incerta:
            # A resposta do banco não chegou: a compra pode ter sido gravada. Remover as fotos
            # deixaria itens com caminhos quebrados, então elas ficam. Reenviar a mesma
            # submissão não duplica a compra (chave_envio).
            logger.error("Gravação da compra sem resposta; %d foto(s) mantida(s).", len(enviadas))
        elif enviadas:
            _remover_imagens(repo, enviadas)
        if not isinstance(erro, FalhaNoEstoque):
            logger.error("Erro inesperado ao gravar compra: %s", type(erro).__name__)
        raise CompraNaoSalva from None
    if duplicada and enviadas:
        _remover_imagens(repo, enviadas)  # a compra gravada antes tem as próprias fotos
    return duplicada


def _remover_imagens(repo: RepositorioEstoque, caminhos: list[str]) -> None:
    try:
        repo.remover_imagens(caminhos)
    except Exception:
        logger.error("Não foi possível remover %d foto(s) de uma compra não salva.", len(caminhos))
