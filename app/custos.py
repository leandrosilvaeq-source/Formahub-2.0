"""Custos da empresa: leitura e validação dos campos, cálculos e formatação.

Tudo em Decimal (nunca float) e sem arredondar nos cálculos: o arredondamento existe só na
exibição. O servidor valida de novo o que o navegador envia; o banco também confere.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, localcontext

from app.pedidos import parse_moeda

CASAS_MAXIMAS = 4  # casas decimais aceitas nos valores digitados (recusa, não arredonda)
LIMITE_VALOR = Decimal("1000000000")  # exclusivo; o mesmo limite do banco
LIMITE_DESCRICAO = 200
LIMITE_VIDA_UTIL_ANOS = Decimal(1000)

# Chaves de custos_parametros.
TARIFA = "energia_tarifa_kwh"
CONSUMO = "energia_consumo_w"  # legado (W): a tela usa o consumo mensal em kWh
CONSUMO_KWH = "energia_consumo_kwh_mes"
HORAS_MES = "horas_impressas_mes"  # referência única de energia e depreciação
ENERGIA_MANUAL = "energia_hora_manual"  # sem valor (None) = usar o calculado
PERDA = "perda_percentual"
MDO = "mdo_hora"
MANUTENCAO = "manutencao_hora"  # ajuste manual; sem valor (None) = calculado pelos gastos reais
AQUISICAO = "depreciacao_valor_aquisicao"
VIDA_UTIL = "depreciacao_vida_util_anos"
RESIDUAL = "depreciacao_valor_residual"
DEPRECIACAO_MANUAL = "depreciacao_hora_manual"
HORAS_DIA = "depreciacao_horas_dia"  # legado: substituído por HORAS_MES
DIAS_MES = "depreciacao_dias_mes"  # legado: substituído por HORAS_MES
TAXA_FIXA = "taxa_venda_fixa"
TAXA_PERCENTUAL = "taxa_venda_percentual"
MEI = "mei_mensal"
RETIRADA = "retirada_desejada_mensal"

DIAS_REVISAO = 90  # a referência de horas mensais deve ser revista a cada trimestre
MINUTOS_MDO = (0, 10, 20, 30)  # referências de tempo ativo mostradas para a MDO
LIMITE_HORAS_MES = Decimal(744)  # 31 dias x 24 h
LIMITE_HORAS_PERIODO = Decimal(999_999)  # exclusivo no banco: horas < 1.000.000


# Grupos da média ponderada do custo por kg dos filamentos comprados:
# (chave do ajuste, título, material, tipos).
GRUPOS_CUSTO_KG = (
    ("filamento:petg", "PETG", "PETG", None),  # None = todos os tipos
    ("filamento:pla", "PLA (Sólido, Velvet, Silk)", "PLA", ("solido", "velvet", "silk")),
    (
        "filamento:pla-multicolor",
        "PLA Multicolor (Bicolor, Tricolor)",
        "PLA",
        ("bicolor", "tricolor"),
    ),
)


# Grupos de filamento da tela Custos: o card PLA reúne Sólido, Velvet e Silk (a média do Estoque).
CHAVE_PLA = "filamento:pla"
GRUPOS_FILAMENTO = (
    ("filamento:petg", "PETG", "PETG", None),
    (CHAVE_PLA, "PLA", "PLA", ("solido", "velvet", "silk")),
    ("filamento:pla-multicolor", "PLA Multicolor", "PLA", ("bicolor", "tricolor")),
)
# O custo de cada tipo do card PLA, mostrado em "Ver detalhes".
TIPOS_DO_PLA = (
    ("filamento:pla-solido", "PLA Sólido", "PLA", ("solido",)),
    ("filamento:pla-velvet", "PLA Velvet", "PLA", ("velvet",)),
    ("filamento:pla-silk", "PLA Silk", "PLA", ("silk",)),
)


@dataclass(frozen=True)
class CustoMedioKg:
    chave: str
    titulo: str
    custo_kg: Decimal | None  # None = nenhuma compra do grupo
    kg_comprados: Decimal  # peso líquido comprado (rolos x peso do rolo); não diminui com o uso
    compras: int  # lotes (linhas de compra) considerados
    saldo_kg: Decimal = Decimal(0)  # o que ainda está disponível no estoque (outra coisa)


def custo_medio_kg_lotes(lotes) -> Decimal | None:
    """Média ponderada pelo peso comprado: total pago nos rolos / total de kg comprados.

    O frete não entra (a compra o guarda à parte, sem distribuí-lo nos lotes). Sem lotes: None.
    """
    peso_g = sum((f.peso_comprado_g for f in lotes), Decimal(0))
    pago = sum((f.valor_unitario * f.peso_comprado_g / f.peso_rolo_g for f in lotes), Decimal(0))
    return pago * 1000 / peso_g if peso_g else None


def custo_medio_unidade_lotes(lotes) -> Decimal | None:
    """Média ponderada pela quantidade comprada: total pago / total de unidades. Sem lotes: None."""
    quantidade = sum(i.quantidade_comprada for i in lotes)
    pago = sum((i.valor_total for i in lotes), Decimal(0))
    return pago / quantidade if quantidade else None


def custo_medio_por_kg(filamentos, grupos=GRUPOS_CUSTO_KG) -> list[CustoMedioKg]:
    """Custo médio por kg de cada grupo (por padrão PETG, PLA, PLA Multicolor), pela regra acima."""
    resultado = []
    for chave, titulo, material, tipos in grupos:
        lotes = [
            f for f in filamentos if f.material == material and (tipos is None or f.tipo in tipos)
        ]
        peso_g = sum((f.peso_comprado_g for f in lotes), Decimal(0))
        saldo_g = sum((f.peso_g for f in lotes), Decimal(0))
        resultado.append(
            CustoMedioKg(
                chave,
                titulo,
                custo_medio_kg_lotes(lotes),
                peso_g / 1000,
                len(lotes),
                saldo_g / 1000,
            )
        )
    return resultado


def comparar_manual(manual: Decimal | None, calculado: Decimal | None) -> str | None:
    """Manual frente ao calculado ('menor', 'igual' ou 'maior'), em Decimal e sem arredondar.

    Sem valor manual ou sem base calculada não há comparação (None).
    """
    if manual is None or calculado is None:
        return None
    if manual == calculado:
        return "igual"
    return "maior" if manual > calculado else "menor"


@dataclass(frozen=True)
class Insumo:
    """Um insumo (filamento, acessório ou embalagem) com o custo das compras e o ajuste manual."""

    chave: str  # 'filamento:petg', 'acessorio:<nome>' ou 'embalagem:<nome>'
    titulo: str
    categoria: str  # filamento, acessorio ou embalagem
    unidade: str  # 'kg' ou 'un'
    calculado: Decimal | None  # média ponderada das compras (None = nenhuma compra)
    manual: Decimal | None  # ajuste manual; as compras originais não são alteradas
    comprado: Decimal  # kg ou unidades compradas
    saldo: Decimal  # kg ou unidades ainda disponíveis (não confundir com o comprado)
    compras: int
    imagem_caminho: str | None = None  # foto da compra (bucket privado)
    # Compras do item, da mais recente para a mais antiga: (data, quantidade, valor pago).
    historico: tuple = ()
    # Custo por kg de cada tipo reunido no card: (título, custo ou None se não houve compra).
    por_tipo: tuple = ()

    @property
    def usado(self) -> Decimal | None:
        return self.manual if self.manual is not None else self.calculado

    @property
    def comparacao(self) -> str | None:
        return comparar_manual(self.manual, self.calculado)

    @property
    def origem(self) -> str | None:
        if self.manual is not None:
            return "Manual"
        return "Calculado" if self.calculado is not None else None


def grupos_de_filamento(filamentos) -> list[tuple]:
    """Os três grupos fixos mais um por material adicional cadastrado (TPU, por exemplo)."""
    fixos = {material for _chave, _titulo, material, _tipos in GRUPOS_FILAMENTO}
    extras = sorted({f.material for f in filamentos} - fixos, key=str.casefold)
    return [*GRUPOS_FILAMENTO, *((f"filamento:material:{m}", m, m, None) for m in extras)]


def insumos_filamento(filamentos, ajustes: dict[str, Decimal]) -> list[Insumo]:
    """Os grupos de filamento, cada um com o ajuste manual (se houver)."""
    por_tipo = tuple((g.titulo, g.custo_kg) for g in custo_medio_por_kg(filamentos, TIPOS_DO_PLA))
    return [
        Insumo(
            g.chave, g.titulo, "filamento", "kg", g.custo_kg, ajustes.get(g.chave),
            g.kg_comprados, g.saldo_kg, g.compras,
            por_tipo=por_tipo if g.chave == CHAVE_PLA else (),
        )
        for g in custo_medio_por_kg(filamentos, grupos_de_filamento(filamentos))
    ]  # fmt: skip


def card_pla(filamentos, ajustes: dict[str, Decimal]) -> tuple[Insumo, int]:
    """(card PLA, posição que identifica o card na tela Custos, como em id="insumo-1")."""
    insumos = insumos_filamento(filamentos, ajustes)
    posicao = next(n for n, i in enumerate(insumos) if i.chave == CHAVE_PLA)
    return insumos[posicao], posicao


def ordem_de_exibicao(filamentos: list[Insumo]) -> list[Insumo]:
    """Ordem dos cards: PLA, PLA Multicolor, PETG e depois os outros materiais.

    Só a exibição muda: a posição (e o id do card) segue a ordem de GRUPOS_FILAMENTO.
    """
    chaves = [g[0] for g in GRUPOS_FILAMENTO]
    pla = [i for i in filamentos if i.chave.startswith(CHAVE_PLA)]
    petg = [i for i in filamentos if i.chave == "filamento:petg"]
    outros = [i for i in filamentos if i.chave not in chaves]
    return [*pla, *petg, *outros]


def chave_item(categoria: str, nome: str) -> str:
    return f"{categoria}:{' '.join(nome.split())}"


def insumos_itens(itens, ajustes: dict[str, Decimal]) -> dict[str, list[Insumo]]:
    """Acessórios e embalagens do estoque, agrupados por nome (média ponderada pela quantidade).

    Não há cadastro de custo à parte: o custo unitário vem das compras do estoque.
    """
    grupos: dict[tuple[str, str], list] = {}
    for item in itens:
        nome = " ".join(item.nome.split())
        grupos.setdefault((item.categoria, nome.casefold()), []).append(item)
    resultado: dict[str, list[Insumo]] = {"acessorio": [], "embalagem": []}
    for (categoria, _), lotes in grupos.items():
        if categoria not in resultado:
            continue
        nome = " ".join(lotes[0].nome.split())
        chave = chave_item(categoria, nome)
        resultado[categoria].append(
            Insumo(
                chave, nome, categoria, "un", custo_medio_unidade_lotes(lotes), ajustes.get(chave),
                Decimal(sum(i.quantidade_comprada for i in lotes)),
                Decimal(sum(i.quantidade for i in lotes)), len(lotes),
                next((i.imagem_caminho for i in lotes if i.imagem_caminho), None),
                tuple(
                    (i.data_compra, i.quantidade_comprada, i.valor_total)
                    for i in sorted(lotes, key=lambda i: (i.data_compra, i.id), reverse=True)
                ),
            )
        )  # fmt: skip
    for lista in resultado.values():
        lista.sort(key=lambda i: i.titulo.casefold())
    return resultado


def aplicar_perda(custo: Decimal, perda_percentual: Decimal) -> Decimal:
    """Custo com perda = custo x (1 + perda/100). Vale só para o filamento, e uma única vez."""
    with localcontext() as contexto:
        contexto.prec = 50
        return custo * (1 + perda_percentual / 100)


def ler_decimal(texto: str, *, casas: int = CASAS_MAXIMAS, maximo: Decimal | None = None):
    """(valor, erro): 'R$ 1.234,5678', '0,12' ou '0.12' -> Decimal; recusa em vez de arredondar."""
    texto = texto.strip()
    if not texto:
        return None, "Informe um valor."
    valor = parse_moeda(texto)
    if valor is None:
        return None, "Informe um número válido, por exemplo 12,50."
    if valor < 0:
        return None, "O valor não pode ser negativo."
    if valor == 0:
        valor = Decimal(0)  # "-0" vira 0
    if valor.as_tuple().exponent < -casas:
        return None, f"Use no máximo {casas} casas decimais."
    if (valor > maximo) if maximo is not None else (valor >= LIMITE_VALOR):
        return None, "Valor acima do limite permitido."
    return valor, ""


def ler_vida_util(texto: str):
    """Anos de vida útil: maior que zero, até duas casas."""
    valor, erro = ler_decimal(texto, casas=2, maximo=LIMITE_VIDA_UTIL_ANOS)
    if not erro and valor <= 0:
        return None, "A vida útil deve ser maior que zero."
    return valor, erro


def ler_horas_mes(texto: str):
    """Horas impressas por mês: maior que zero e até 744 (31 dias x 24 h), até duas casas."""
    valor, erro = ler_decimal(texto, casas=2, maximo=LIMITE_HORAS_MES)
    if erro == "Valor acima do limite permitido.":
        return None, "As horas por mês não podem passar de 744."
    if not erro and valor <= 0:
        return None, "As horas por mês devem ser maiores que zero."
    return valor, erro


def ler_horas_periodo(texto: str):
    """Horas impressas no período de referência da manutenção: maior que zero, até duas casas."""
    valor, erro = ler_decimal(texto, casas=2, maximo=LIMITE_HORAS_PERIODO)
    if erro == "Valor acima do limite permitido.":
        return None, "As horas do período estão acima do limite permitido."
    if not erro and valor <= 0:
        return None, "As horas do período devem ser maiores que zero."
    return valor, erro


def ler_percentual(texto: str):
    """Percentual de 0 a 100, até duas casas."""
    valor, erro = ler_decimal(texto, casas=2, maximo=Decimal(100))
    if erro == "Valor acima do limite permitido.":
        return None, "O percentual não pode passar de 100%."
    return valor, erro


def ler_data(texto: str):
    """(data, erro): aaaa-mm-dd (campo de data do navegador)."""
    try:
        return date.fromisoformat(texto.strip()), ""
    except ValueError:
        return None, "Informe uma data válida."


def ler_descricao(texto: str):
    """(descrição, erro): sem espaços nas pontas, obrigatória, até 200 caracteres."""
    descricao = " ".join(texto.split())
    if not descricao:
        return "", "Informe a descrição."
    if len(descricao) > LIMITE_DESCRICAO:
        return descricao, f"A descrição pode ter no máximo {LIMITE_DESCRICAO} caracteres."
    return descricao, ""


def ler_valor_gasto(texto: str):
    """Gasto real: maior que zero, até duas casas (centavos)."""
    valor, erro = ler_decimal(texto, casas=2)
    if not erro and valor <= 0:
        return None, "O valor deve ser maior que zero."
    return valor, erro


def ler_perda(texto: str):
    """Perdas em %: de 0 a 100, até duas casas."""
    valor, erro = ler_decimal(texto, casas=2, maximo=Decimal(100))
    if erro == "Valor acima do limite permitido.":
        return None, "As perdas não podem passar de 100%."
    return valor, erro


class Campo:
    """Um valor único editável (uma chave de custos_parametros)."""

    def __init__(
        self,
        nome: str,
        chave: str,
        rotulo: str,
        unidade: str,
        formato: str,
        regra: Callable | None = None,
        opcional: bool = False,
    ):
        self.nome = nome  # nome do campo no formulário
        self.chave = chave
        self.rotulo = rotulo
        self.unidade = unidade  # R$/kWh, W, %, R$/h, anos...
        self.formato = formato  # "moeda" (2+ casas) ou "numero"
        self.regra = regra or ler_decimal  # texto -> (valor, erro)
        self.opcional = opcional  # em branco = sem valor ("A definir")


# Cada formulário da tela altera um grupo de campos; os grupos viram rotas e seções.
GRUPOS = {
    "perdas": [Campo("perda_percentual", PERDA, "Perda de material", "%", "numero", ler_perda)],
    "taxas": [
        Campo("taxa_fixa", TAXA_FIXA, "Valor fixo por venda", "R$", "moeda"),
        Campo(
            "taxa_percentual",
            TAXA_PERCENTUAL,
            "Percentual sobre a venda",
            "%",
            "numero",
            ler_percentual,
        ),
    ],
    "energia": [
        Campo("consumo_kwh", CONSUMO_KWH, "Consumo mensal", "kWh/mês", "numero"),
        Campo("tarifa_kwh", TARIFA, "Tarifa de energia", "R$/kWh", "moeda"),
    ],
    "horas": [
        Campo("horas_mes", HORAS_MES, "Horas impressas por mês", "h/mês", "numero", ler_horas_mes)
    ],
    "depreciacao": [
        Campo("valor_aquisicao", AQUISICAO, "Valor de aquisição", "R$", "moeda"),
        Campo("valor_residual", RESIDUAL, "Valor residual", "R$", "moeda"),
        Campo("vida_util_anos", VIDA_UTIL, "Vida útil", "anos", "numero", ler_vida_util),
    ],
    "mdo": [Campo("mdo_hora", MDO, "Mão de obra direta (MDO)", "R$/h", "moeda")],
    "mei": [Campo("mei_mensal", MEI, "MEI", "R$/mês", "moeda")],
    "retirada": [Campo("retirada", RETIRADA, "Retirada desejada", "R$/mês", "moeda")],
    # Ajustes manuais dos custos derivados (R$/h). "Voltar ao calculado" apaga o valor.
    "energia-ajuste": [Campo("valor", ENERGIA_MANUAL, "Valor utilizado", "R$/h", "moeda")],
    "depreciacao-ajuste": [Campo("valor", DEPRECIACAO_MANUAL, "Valor utilizado", "R$/h", "moeda")],
    "manutencao-ajuste": [Campo("valor", MANUTENCAO, "Valor utilizado", "R$/h", "moeda")],
}
VOLTAR_AO_CALCULADO = {"energia-ajuste", "depreciacao-ajuste", "manutencao-ajuste"}


def ler_campo(campo: Campo, texto: str):
    """(valor, erro) de um campo; o opcional em branco vale "sem valor" (None), sem erro."""
    if campo.opcional and not texto.strip():
        return None, ""
    return campo.regra(texto)


def conferir_depreciacao(valores: dict[str, Decimal]) -> dict[str, str]:
    """Regras entre campos da depreciação: campo -> erro (o residual não passa da aquisição)."""
    aquisicao, residual = valores.get(AQUISICAO), valores.get(RESIDUAL)
    if aquisicao is not None and residual is not None and residual > aquisicao:
        return {"valor_residual": "O valor residual não pode ser maior que o valor de aquisição."}
    return {}


# ---------- Cálculos (exatos; sem arredondar) ----------


@dataclass(frozen=True)
class CustoHora:
    """Um custo por hora derivado: o calculado, o ajuste manual e o valor utilizado."""

    calculado: Decimal | None  # None = sem dados para calcular ("A definir")
    manual: Decimal | None = None
    provisorio: bool = False  # ajuste manual que entrou pela migration e ainda não foi revisto
    completo: bool = True  # False: o calculado é só um subtotal conhecido

    @property
    def usado(self) -> Decimal | None:
        return self.manual if self.manual is not None else self.calculado

    @property
    def comparacao(self) -> str | None:
        """Comparação do ajuste manual; sem base completa (subtotal conhecido), nenhuma (None)."""
        return comparar_manual(self.manual, self.calculado) if self.completo else None

    @property
    def origem(self) -> str | None:
        if self.manual is not None:
            return "Manual"
        return "Calculado" if self.calculado is not None else None


def energia_por_hora(consumo_kwh_mes: Decimal, tarifa_kwh: Decimal, horas_mes: Decimal) -> Decimal:
    """R$/h = consumo mensal (kWh) x tarifa (R$/kWh) / horas impressas no mês."""
    with localcontext() as contexto:
        contexto.prec = 50
        return consumo_kwh_mes * tarifa_kwh / horas_mes


@dataclass(frozen=True)
class Depreciacao:
    mensal: Decimal  # (aquisição - residual) / (vida útil em anos x 12)
    por_hora: Decimal  # (aquisição - residual) / (vida útil x 12 x horas mensais)


def depreciacao(
    aquisicao: Decimal, residual: Decimal, vida_util_anos: Decimal, horas_mes: Decimal
) -> Depreciacao:
    """Depreciação gerencial linear, sem arredondar em nenhuma etapa.

    O valor por hora é uma única divisão da base depreciável pelas horas de uso na vida útil.
    """
    with localcontext() as contexto:
        contexto.prec = 50
        base = aquisicao - residual
        meses = vida_util_anos * 12
        return Depreciacao(mensal=base / meses, por_hora=base / (meses * horas_mes))


def manutencao_por_hora(gastos, periodo) -> Decimal | None:
    """R$/h = gastos do período / horas impressas do mesmo período.

    Sem período, sem horas ou sem nenhum gasto dentro do período: None ("A definir"). Ausência de
    informação não é zero. Gastos fora do período não entram.
    """
    if periodo is None or periodo.horas <= 0:
        return None
    no_periodo = [g.valor for g in gastos if periodo.inicio <= g.data <= periodo.fim]
    if not no_periodo:
        return None
    with localcontext() as contexto:
        contexto.prec = 50
        return sum(no_periodo, Decimal(0)) / periodo.horas


@dataclass(frozen=True)
class BaseManutencao:
    """Tudo o que forma o custo de manutenção por hora, o mesmo em todas as telas."""

    periodo: object | None
    calculado: Decimal | None
    total: Decimal  # gastos com custo definido dentro do período
    quantidade: int
    sem_custo: int  # manutenções do período com custo "A definir"

    @property
    def situacao(self) -> str:
        """'sem_periodo', 'incompleto' (há custo "A definir"), 'sem_gastos' ou 'completo'."""
        if self.periodo is None or self.periodo.horas <= 0:
            return "sem_periodo"
        if self.sem_custo:
            return "incompleto"
        return "completo" if self.calculado is not None else "sem_gastos"


def custo_de_manutencao(custos) -> tuple[BaseManutencao, CustoHora]:
    """(base do cálculo, custo por hora com o ajuste manual) a partir dos custos salvos."""
    periodo = custos.periodo
    no_periodo = [
        g.valor for g in custos.gastos if periodo and periodo.inicio <= g.data <= periodo.fim
    ]
    base = BaseManutencao(
        periodo=periodo,
        calculado=manutencao_por_hora(custos.gastos, periodo),
        total=sum(no_periodo, Decimal(0)),
        quantidade=len(no_periodo),
        sem_custo=sum(
            1
            for d in custos.manutencoes_sem_custo
            if periodo and periodo.inicio <= d <= periodo.fim
        ),
    )
    manual = custos.parametros.get(MANUTENCAO)
    return base, CustoHora(base.calculado, manual, completo=base.situacao == "completo")


def mdo_referencias(mdo_hora: Decimal) -> list[tuple[int, Decimal]]:
    """Custo de MDO para 0, 10, 20 e 30 minutos de tempo ativo (R$/h x minutos / 60)."""
    with localcontext() as contexto:
        contexto.prec = 50
        return [(minutos, mdo_hora * minutos / 60) for minutos in MINUTOS_MDO]


def taxa_de_venda(venda: Decimal, fixa: Decimal, percentual: Decimal) -> Decimal:
    """Taxa = valor fixo + percentual sobre o valor da venda."""
    with localcontext() as contexto:
        contexto.prec = 50
        return fixa + venda * percentual / 100


def subtotal_maquina(
    energia: Decimal | None, depreciacao_hora: Decimal | None, manutencao: Decimal | None
) -> tuple[Decimal | None, bool]:
    """(R$/h, completo): energia + depreciação + manutenção, cada uma somada em separado.

    A MDO fica de fora (é tempo ativo de pessoas, não hora de máquina) e a perda também (é do
    filamento). Sem energia ou depreciação: (None, False). Com a manutenção ainda "A definir", o
    resultado é só um subtotal (completo=False), nunca o custo por hora completo.
    """
    if energia is None or depreciacao_hora is None:
        return None, False
    subtotal = energia + depreciacao_hora
    if manutencao is None:
        return subtotal, False
    return subtotal + manutencao, True


def revisao_das_horas(data_base: date | None, hoje: date) -> tuple[date | None, bool]:
    """(próxima revisão, vencida): a referência de horas mensais é revista a cada trimestre."""
    if data_base is None:
        return None, False
    proxima = data_base + timedelta(days=DIAS_REVISAO)
    return proxima, hoje > proxima


# ---------- Formatação (só para exibir) ----------


def _texto_decimal(valor: Decimal, minimo: int, maximo: int) -> str:
    """'1234.5' -> '1.234,50' (minimo=2) ou '1.234,5' (minimo=1): corta zeros sobrando."""
    arredondado = valor.quantize(Decimal(1).scaleb(-maximo), rounding=ROUND_HALF_UP)
    inteiro, _, fracao = f"{arredondado:f}".partition(".")
    fracao = fracao.rstrip("0").ljust(minimo, "0")
    inteiro = f"{int(inteiro):,}".replace(",", ".")
    return f"{inteiro},{fracao}" if fracao else inteiro


def formatar_custo(valor: Decimal) -> str:
    """'R$ 0,12', 'R$ 100,00', 'R$ 3,3333' (duas casas ou até quatro, se houver)."""
    return "R$ " + _texto_decimal(valor, 2, 4)


def formatar_custo_aprox(valor: Decimal) -> str:
    """Como formatar_custo, com '≈' quando a exibição (4 casas) não é o valor exato."""
    exato = valor.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP) == valor
    return ("" if exato else "≈ ") + formatar_custo(valor)


def formatar_custo_curto(valor: Decimal) -> str:
    """Duas casas, arredondado: 'R$ 0,90' (apoio ao valor com quatro casas)."""
    return "R$ " + _texto_decimal(valor, 2, 2)


def formatar_data(valor: date) -> str:
    """'01/11/2026'."""
    return valor.strftime("%d/%m/%Y")


def formatar_numero(valor: Decimal) -> str:
    """'120', '5', '2,5' (sem zeros sobrando)."""
    return _texto_decimal(valor, 0, 4)


def valor_do_campo(valor: Decimal | None, formato: str) -> str:
    """Texto para preencher um <input>: '100,00', '0,12', '120'; sem valor, em branco."""
    if valor is None:
        return ""
    return _texto_decimal(valor, 2, 4) if formato == "moeda" else _texto_decimal(valor, 0, 4)


def para_banco(valor: Decimal | None) -> str | None:
    """Decimal -> texto com ponto, sem notação científica; sem valor (None) segue como None."""
    return None if valor is None else f"{valor:f}"
