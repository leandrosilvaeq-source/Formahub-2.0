"""Tela Estoque: validação dos lotes, busca, filtros, ordenação e totais.

Cada lote de compra tem saldo, data e custo próprios; o mesmo insumo pode aparecer em várias
compras. O servidor valida tudo de novo; nada do navegador é confiado. Peso e custos usam
Decimal (numeric no banco), nunca float.
"""

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app.estoque_repositorio import Filamento, ItemEstoque
from app.pedidos import formatar_brl, parse_moeda

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

LIMITE_TEXTO = 60  # cor, material, marca e nome (o mesmo CHECK do banco)
LIMITE_VALOR = Decimal("9999999.99")  # peso (g) e custos: cabe no numeric(12, 2)
LIMITE_QUANTIDADE = 999999
ANO_MINIMO, ANO_MAXIMO = 2000, 2099

# Peso: só números, com vírgula ou ponto decimal (até 2 casas), sem separador de milhar.
FORMATO_PESO = re.compile(r"\d{1,7}(?:[.,]\d{1,2})?")
# Data de compra: aaaa-mm-dd (campo de data do navegador) ou dd/mm/aaaa (digitada).
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


def _valor_no_campo(valor: Decimal) -> str:
    """Como o número volta ao campo do formulário na edição: '1000,5' (sem milhar)."""
    return formatar_numero(valor).replace(".", "")


# ---------- Formulários ----------


@dataclass
class FormFilamento:
    """O que foi digitado (texto) e, depois da validação, os erros por campo."""

    cor: str = ""
    material: str = ""
    tipo: str = ""
    marca: str = ""
    peso: str = ""
    data_compra: str = ""
    custo_kg: str = ""
    erros: dict[str, str] = field(default_factory=dict)


@dataclass
class FormItem:
    nome: str = ""
    quantidade: str = ""
    data_compra: str = ""
    custo_unitario: str = ""
    erros: dict[str, str] = field(default_factory=dict)


def _campo(dados: dict[str, str], nome: str) -> str:
    return " ".join(dados.get(nome, "").split())  # sem espaços nas pontas nem repetidos


def ler_filamento(dados: dict[str, str]) -> FormFilamento:
    return FormFilamento(
        cor=_campo(dados, "cor"),
        material=_campo(dados, "material"),
        tipo=_campo(dados, "tipo"),
        marca=_campo(dados, "marca"),
        peso=_campo(dados, "peso"),
        data_compra=_campo(dados, "data_compra"),
        custo_kg=_campo(dados, "custo_kg"),
    )


def ler_item(dados: dict[str, str]) -> FormItem:
    return FormItem(
        nome=_campo(dados, "nome"),
        quantidade=_campo(dados, "quantidade"),
        data_compra=_campo(dados, "data_compra"),
        custo_unitario=_campo(dados, "custo_unitario"),
    )


def form_de_filamento(f: Filamento) -> FormFilamento:
    return FormFilamento(
        cor=f.cor,
        material=f.material,
        tipo=f.tipo,
        marca=f.marca,
        peso=_valor_no_campo(f.peso_g),
        data_compra=f.data_compra.isoformat(),
        custo_kg=formatar_brl(f.custo_kg),
    )


def form_de_item(i: ItemEstoque) -> FormItem:
    return FormItem(
        nome=i.nome,
        quantidade=str(i.quantidade),
        data_compra=i.data_compra.isoformat(),
        custo_unitario=formatar_brl(i.custo_unitario),
    )


def _texto(valor: str, erros: dict, campo: str, rotulo: str) -> None:
    if not valor:
        erros[campo] = f"Informe {rotulo}."
    elif len(valor) > LIMITE_TEXTO:
        erros[campo] = f"Use no máximo {LIMITE_TEXTO} caracteres."


def ler_data(texto: str) -> tuple[date | None, str | None]:
    """'2026-10-01' ou '01/10/2026' -> (date, None); com problema -> (None, mensagem)."""
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


def ler_custo(texto: str, rotulo: str) -> tuple[Decimal | None, str | None]:
    if not texto:
        return None, f"Informe o {rotulo}."
    valor = parse_moeda(texto)
    if valor is None:
        return None, "Valor inválido."
    if valor < 0:
        return None, "O custo não pode ser negativo."
    if valor > LIMITE_VALOR:
        return None, "Valor alto demais."
    return valor.quantize(Decimal("0.01")), None


def ler_peso(texto: str) -> tuple[Decimal | None, str | None]:
    if not texto:
        return None, "Informe o peso disponível."
    if texto.startswith("-"):
        return None, "O peso não pode ser negativo."
    if not FORMATO_PESO.fullmatch(texto):
        return None, "Use só números, com até 2 casas decimais (ex.: 750,5)."
    return Decimal(texto.replace(",", ".")), None


def ler_quantidade(texto: str) -> tuple[int | None, str | None]:
    if not texto:
        return None, "Informe a quantidade disponível."
    if texto.startswith("-"):
        return None, "A quantidade não pode ser negativa."
    if not texto.isdigit() or not texto.isascii():
        return None, "Use um número inteiro."
    if int(texto) > LIMITE_QUANTIDADE:
        return None, "Quantidade alta demais."
    return int(texto), None


def validar_filamento(form: FormFilamento, lote_id: int | None) -> Filamento | None:
    """Preenche form.erros; devolve o lote pronto para gravar, ou None se houver erro."""
    e = form.erros
    _texto(form.cor, e, "cor", "a cor")
    _texto(form.material, e, "material", "o material")
    _texto(form.marca, e, "marca", "a marca")
    # Só valores da lista: qualquer outro texto enviado pelo navegador é recusado.
    if form.tipo not in TIPOS_FILAMENTO:
        e["tipo"] = "Escolha o tipo."

    peso, problema = ler_peso(form.peso)
    if problema:
        e["peso"] = problema
    else:
        form.peso = _valor_no_campo(peso)
    data_compra, problema = ler_data(form.data_compra)
    if problema:
        e["data_compra"] = problema
    else:
        form.data_compra = data_compra.isoformat()
    custo, problema = ler_custo(form.custo_kg, "custo por kg")
    if problema:
        e["custo_kg"] = problema
    else:
        form.custo_kg = formatar_brl(custo)

    if e:
        return None
    return Filamento(
        id=lote_id,
        cor=form.cor,
        material=form.material,
        tipo=form.tipo,
        marca=form.marca,
        peso_g=peso,
        data_compra=data_compra,
        custo_kg=custo,
    )


def validar_item(form: FormItem, area: Area, lote_id: int | None) -> ItemEstoque | None:
    """Preenche form.erros; devolve o lote pronto para gravar, ou None se houver erro."""
    e = form.erros
    _texto(form.nome, e, "nome", "o nome")

    quantidade, problema = ler_quantidade(form.quantidade)
    if problema:
        e["quantidade"] = problema
    else:
        form.quantidade = str(quantidade)
    data_compra, problema = ler_data(form.data_compra)
    if problema:
        e["data_compra"] = problema
    else:
        form.data_compra = data_compra.isoformat()
    custo, problema = ler_custo(form.custo_unitario, "custo por unidade")
    if problema:
        e["custo_unitario"] = problema
    else:
        form.custo_unitario = formatar_brl(custo)

    if e:
        return None
    return ItemEstoque(
        id=lote_id,
        categoria=area.categoria,
        nome=form.nome,
        quantidade=quantidade,
        data_compra=data_compra,
        custo_unitario=custo,
    )


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
