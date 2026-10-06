"""Tela Estoque: edição da descrição dos lotes, busca, filtros, ordenação e totais.

Todo lote nasce de um item de compra (app/estoque_compra.py), com saldo próprio; a data e o
custo vêm da compra. O mesmo insumo pode aparecer em várias compras. O servidor valida tudo
de novo; nada do navegador é confiado. Peso e custos usam Decimal (numeric no banco), nunca
float.
"""

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app.estoque_repositorio import Filamento, ItemEstoque

# Tipos de filamento: código do banco -> texto da tela (na ordem das opções).
TIPOS_FILAMENTO = {
    "solido": "Sólido",
    "velvet": "Velvet",
    "silk": "Silk",
    "bicolor": "Bicolor",
    "tricolor": "Tricolor",
}


@dataclass(frozen=True)
class Area:
    """Acessórios ou embalagens: mesma estrutura, categorias separadas no banco."""

    slug: str  # no endereço: /estoque/acessorios
    categoria: str  # no banco
    singular: str
    plural: str


AREAS_ITENS = {
    "acessorios": Area("acessorios", "acessorio", "Acessório", "Acessórios"),
    "embalagens": Area("embalagens", "embalagem", "Embalagem", "Embalagens"),
}

LIMITE_TEXTO = 60  # cor, material, marca, nome e local (o mesmo CHECK do banco)
ANO_MINIMO, ANO_MAXIMO = 2000, 2099

# Data de compra: dd/mm/aaaa (digitada) ou aaaa-mm-dd.
DATA_ISO = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
DATA_BR = re.compile(r"(\d{2})/(\d{2})/(\d{4})")


# ---------- Formatação ----------


def formatar_numero(valor: Decimal, casas: int = 2) -> str:
    """Decimal('1234.5') -> '1.234,5' (sem zeros à direita depois da vírgula)."""
    inteiro, _, fracao = f"{valor:.{casas}f}".partition(".")
    inteiro = f"{int(inteiro):,}".replace(",", ".")
    fracao = fracao.rstrip("0")
    return f"{inteiro},{fracao}" if fracao else inteiro


def formatar_peso(valor: Decimal) -> str:
    """Decimal('1234.5') -> '1.234,5 g'."""
    return f"{formatar_numero(valor)} g"


def formatar_unidades(quantidade: int) -> str:
    """1234 -> '1.234 un'."""
    return f"{quantidade:,} un".replace(",", ".")


def formatar_data(dia: date) -> str:
    return dia.strftime("%d/%m/%Y")


# ---------- Formulários ----------


@dataclass
class FormFilamento:
    """Edição do lote: só a descrição (o saldo, a data e o custo vêm da compra)."""

    cor: str = ""
    material: str = ""
    tipo: str = ""
    marca: str = ""
    erros: dict[str, str] = field(default_factory=dict)


@dataclass
class FormItem:
    nome: str = ""
    erros: dict[str, str] = field(default_factory=dict)


def limpar_campo(dados: dict[str, str], nome: str) -> str:
    return " ".join(dados.get(nome, "").split())  # sem espaços nas pontas nem repetidos


def ler_filamento(dados: dict[str, str]) -> FormFilamento:
    return FormFilamento(
        cor=limpar_campo(dados, "cor"),
        material=limpar_campo(dados, "material"),
        tipo=limpar_campo(dados, "tipo"),
        marca=limpar_campo(dados, "marca"),
    )


def ler_item(dados: dict[str, str]) -> FormItem:
    return FormItem(nome=limpar_campo(dados, "nome"))


def form_de_filamento(f: Filamento) -> FormFilamento:
    return FormFilamento(cor=f.cor, material=f.material, tipo=f.tipo, marca=f.marca)


def form_de_item(i: ItemEstoque) -> FormItem:
    return FormItem(nome=i.nome)


def validar_texto(valor: str, erros: dict, campo: str, rotulo: str) -> None:
    if not valor:
        erros[campo] = f"Informe {rotulo}."
    elif len(valor) > LIMITE_TEXTO:
        erros[campo] = f"Use no máximo {LIMITE_TEXTO} caracteres."


def validar_tipo(tipo: str, erros: dict, campo: str) -> None:
    # Só valores da lista: qualquer outro texto enviado pelo navegador é recusado.
    if tipo not in TIPOS_FILAMENTO:
        erros[campo] = "Escolha o tipo."


def ler_data(texto: str) -> tuple[date | None, str | None]:
    """'01/10/2026' ou '2026-10-01' -> (date, None); com problema -> (None, mensagem)."""
    if not texto:
        return None, "Informe a data de compra."
    if encontrado := DATA_ISO.fullmatch(texto):
        ano, mes, dia = (int(p) for p in encontrado.groups())
    elif encontrado := DATA_BR.fullmatch(texto):
        dia, mes, ano = (int(p) for p in encontrado.groups())
    else:
        return None, "Use o formato dd/mm/aaaa."
    if not ANO_MINIMO <= ano <= ANO_MAXIMO:
        return None, "Data inválida."
    try:
        return date(ano, mes, dia), None
    except ValueError:
        return None, "Data inválida."


def validar_filamento(form: FormFilamento) -> bool:
    """Preenche form.erros; True se a descrição pode ser gravada."""
    e = form.erros
    validar_texto(form.cor, e, "cor", "a cor")
    validar_texto(form.material, e, "material", "o material")
    validar_texto(form.marca, e, "marca", "a marca")
    validar_tipo(form.tipo, e, "tipo")
    return not e


def validar_item(form: FormItem) -> bool:
    validar_texto(form.nome, form.erros, "nome", "o nome")
    return not form.erros


# ---------- Busca, filtros e ordenação ----------


def _sem_acento(texto: str) -> str:
    decomposto = unicodedata.normalize("NFKD", texto.casefold())
    return "".join(c for c in decomposto if not unicodedata.combining(c))


def _contem(busca: str, *textos: str) -> bool:
    busca = _sem_acento(" ".join(busca.split()))
    return not busca or any(busca in _sem_acento(t) for t in textos)


def _recentes(lote):
    return (lote.data_compra, lote.id or 0)


# Opções de ordenação: código -> (texto, chave, decrescente). A primeira é o padrão.
ORDENS_FILAMENTO = {
    "recentes": ("Compra mais recente", _recentes, True),
    "antigas": ("Compra mais antiga", _recentes, False),
    "cor": ("Cor (A–Z)", lambda f: (_sem_acento(f.cor), f.id or 0), False),
    "material": ("Material (A–Z)", lambda f: (_sem_acento(f.material), f.id or 0), False),
    "marca": ("Marca (A–Z)", lambda f: (_sem_acento(f.marca), f.id or 0), False),
    "maior_peso": ("Maior peso", lambda f: (f.peso_g, f.id or 0), True),
    "menor_peso": ("Menor peso", lambda f: (f.peso_g, f.id or 0), False),
    "menor_custo": ("Menor custo/kg", lambda f: (f.custo_kg, f.id or 0), False),
    "maior_custo": ("Maior custo/kg", lambda f: (f.custo_kg, f.id or 0), True),
}
ORDENS_ITEM = {
    "recentes": ("Compra mais recente", _recentes, True),
    "antigas": ("Compra mais antiga", _recentes, False),
    "nome": ("Nome (A–Z)", lambda i: (_sem_acento(i.nome), i.id or 0), False),
    "maior_quantidade": ("Maior quantidade", lambda i: (i.quantidade, i.id or 0), True),
    "menor_quantidade": ("Menor quantidade", lambda i: (i.quantidade, i.id or 0), False),
    "menor_custo": ("Menor custo/un", lambda i: (i.custo_unitario, i.id or 0), False),
    "maior_custo": ("Maior custo/un", lambda i: (i.custo_unitario, i.id or 0), True),
}


def _ordenar(lotes: list, ordens: dict, ordem: str) -> list:
    _, chave, decrescente = ordens.get(ordem) or next(iter(ordens.values()))
    return sorted(lotes, key=chave, reverse=decrescente)


@dataclass
class Consulta:
    """Busca, filtros e ordem de uma área, como vieram na URL (já saneados)."""

    busca: str = ""
    ordem: str = "recentes"
    material: str = ""  # só Filamentos
    tipo: str = ""  # só Filamentos

    @property
    def filtrando(self) -> bool:
        return bool(self.busca or self.material or self.tipo)


def _limpar(texto: str) -> str:
    return " ".join(texto.split())[:LIMITE_TEXTO]


def consulta_filamentos(parametros) -> Consulta:
    tipo = parametros.get("f_tipo", "")
    ordem = parametros.get("f_ordem", "")
    return Consulta(
        busca=_limpar(parametros.get("f_busca", "")),
        material=_limpar(parametros.get("f_material", "")),
        tipo=tipo if tipo in TIPOS_FILAMENTO else "",
        ordem=ordem if ordem in ORDENS_FILAMENTO else "recentes",
    )


def consulta_itens(parametros, prefixo: str) -> Consulta:
    ordem = parametros.get(f"{prefixo}_ordem", "")
    return Consulta(
        busca=_limpar(parametros.get(f"{prefixo}_busca", "")),
        ordem=ordem if ordem in ORDENS_ITEM else "recentes",
    )


def filtrar_filamentos(lotes: list[Filamento], c: Consulta) -> list[Filamento]:
    material = _sem_acento(c.material)
    escolhidos = [
        f
        for f in lotes
        if (not material or _sem_acento(f.material) == material)
        and (not c.tipo or f.tipo == c.tipo)
        and _contem(c.busca, f.cor, f.material, f.marca, TIPOS_FILAMENTO.get(f.tipo, f.tipo))
    ]
    return _ordenar(escolhidos, ORDENS_FILAMENTO, c.ordem)


def filtrar_itens(lotes: list[ItemEstoque], c: Consulta) -> list[ItemEstoque]:
    escolhidos = [i for i in lotes if _contem(c.busca, i.nome)]
    return _ordenar(escolhidos, ORDENS_ITEM, c.ordem)


def materiais(lotes: list[Filamento]) -> list[str]:
    """Materiais cadastrados, sem repetição (ignorando maiúsculas e acentos), em ordem."""
    vistos: dict[str, str] = {}
    for f in lotes:
        vistos.setdefault(_sem_acento(f.material), f.material)
    return sorted(vistos.values(), key=_sem_acento)


def peso_total(lotes: list[Filamento]) -> Decimal:
    return sum((f.peso_g for f in lotes), Decimal("0"))


def unidades_total(lotes: list[ItemEstoque]) -> int:
    return sum(i.quantidade for i in lotes)
