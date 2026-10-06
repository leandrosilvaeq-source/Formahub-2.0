"""Registrar Compra: leitura do formulário da janela, validação, cálculos e gravação.

Toda entrada no estoque é uma compra: data, local e uma ou mais linhas (filamento, acessório
ou embalagem). Cada linha vira um lote ligado ao item da compra que o originou.
O servidor valida tudo de novo; nada do navegador é confiado. Valores em Decimal, sem
arredondar etapas intermediárias (a tela só arredonda o que mostra).

Campos do formulário: csrf, chave_envio, data_compra, local, local_nome e, para cada linha
na posição i da tela, item_{i}_categoria, item_{i}_<campo> e a foto opcional item_{i}_foto.
Os erros voltam com essas mesmas chaves, para a tela marcar o campo certo.
"""

import logging
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal

from app.estoque import LIMITE_TEXTO, ler_data, validar_texto, validar_tipo
from app.estoque_repositorio import (
    CompraParaGravar,
    FalhaNoEstoque,
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
    "outro": "Outro",
    "loja_fisica": "Loja Física",
}
LOCAIS_COM_NOME = {"outro", "loja_fisica"}  # pedem o nome do local

CATEGORIAS = {"filamento": "Filamento", "acessorio": "Acessório", "embalagem": "Embalagem"}

# Os mesmos limites dos CHECK do banco.
LIMITE_ITENS = 50
LIMITE_ROLOS = 9999
LIMITE_QUANTIDADE = 999999
LIMITE_PESO_ROLO = Decimal("99999.99")
LIMITE_VALOR = Decimal("9999999.99")  # cabe no numeric(12, 2)

# Campo de uma linha: item_{posição}_{campo}.
CAMPO_ITEM = re.compile(r"item_(\d{1,3})_([a-z_]+)")
# Peso por rolo: só números, com vírgula ou ponto decimal (até 2 casas), sem milhar.
FORMATO_PESO = re.compile(r"\d{1,5}(?:[.,]\d{1,2})?")
CHAVE_ENVIO = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")

MENSAGEM_SEM_ITENS = "Adicione pelo menos um item."


@dataclass
class Compra:
    """Compra validada: pronta para gravar se `erros` estiver vazio."""

    chave_envio: uuid.UUID | None = None
    data_compra: date | None = None
    local_tipo: str = ""
    local_nome: str | None = None
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


def _peso_rolo(texto: str) -> tuple[Decimal | None, str | None]:
    if not texto:
        return None, "Informe o peso por rolo."
    if texto.startswith("-"):
        return None, "O peso deve ser maior que zero."
    if not FORMATO_PESO.fullmatch(texto):
        return None, "Use só números, com até 2 casas decimais (ex.: 1000 ou 750,5)."
    peso = Decimal(texto.replace(",", "."))
    if peso <= 0:
        return None, "O peso deve ser maior que zero."
    if peso > LIMITE_PESO_ROLO:
        return None, "Peso alto demais."
    return peso, None


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
        quantidade, problema = _quantidade(campo("quantidade"), LIMITE_ROLOS, "quantidade de rolos")
        if problema:
            e["quantidade"] = problema
        cor, material, marca, tipo = campo("cor"), campo("material"), campo("marca"), campo("tipo")
        validar_texto(cor, e, "cor", "a cor")
        validar_texto(material, e, "material", "o material")
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


def validar_compra(textos: dict[str, str], imagens: dict[int, bytes] | None = None) -> Compra:
    """Monta e valida a compra a partir dos campos de texto e das fotos (posição -> bytes)."""
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
