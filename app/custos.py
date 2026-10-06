"""Custos da empresa: leitura e validação dos campos, cálculos e formatação.

Tudo em Decimal (nunca float) e sem arredondar nos cálculos: o arredondamento existe só na
exibição. O servidor valida de novo o que o navegador envia; o banco também confere.
"""

from collections.abc import Callable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, localcontext

from app.pedidos import parse_moeda

CASAS_MAXIMAS = 4  # casas decimais aceitas nos valores digitados (recusa, não arredonda)
LIMITE_VALOR = Decimal("1000000000")  # exclusivo; o mesmo limite do banco
LIMITE_NOME = 120
LIMITE_QUANTIDADE = 1_000_000_000  # menor que o maior integer do banco (2.147.483.647)
LIMITE_VIDA_UTIL_ANOS = Decimal(1000)

# Chaves de custos_parametros.
TARIFA = "energia_tarifa_kwh"
CONSUMO = "energia_consumo_w"
PERDA = "perda_percentual"
MDO = "mdo_hora"
MANUTENCAO = "manutencao_hora"  # sem valor (None) = "A definir"; zero é um valor digitado
AQUISICAO = "depreciacao_valor_aquisicao"
VIDA_UTIL = "depreciacao_vida_util_anos"
RESIDUAL = "depreciacao_valor_residual"
HORAS_DIA = "depreciacao_horas_dia"
DIAS_MES = "depreciacao_dias_mes"


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


def ler_horas_dia(texto: str):
    """Horas de impressão por dia: maior que zero e até 24, até duas casas."""
    valor, erro = ler_decimal(texto, casas=2, maximo=Decimal(24))
    if erro == "Valor acima do limite permitido.":
        return None, "As horas por dia não podem passar de 24."
    if not erro and valor <= 0:
        return None, "As horas por dia devem ser maiores que zero."
    return valor, erro


def ler_dias_mes(texto: str):
    """Dias de impressão por mês: inteiro de 1 a 31."""
    texto = texto.strip()
    if not texto:
        return None, "Informe um valor."
    if not texto.isascii() or not texto.isdigit():
        return None, "Os dias por mês devem ser um número inteiro, de 1 a 31."
    dias = int(texto)
    if not 1 <= dias <= 31:
        return None, "Os dias por mês devem ficar entre 1 e 31."
    return Decimal(dias), ""


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
    "energia": [
        Campo("tarifa_kwh", TARIFA, "Tarifa de energia", "R$/kWh", "moeda"),
        Campo("consumo_w", CONSUMO, "Consumo médio da impressora", "W", "numero"),
    ],
    "perdas": [Campo("perda_percentual", PERDA, "Perdas", "%", "numero", ler_perda)],
    "mdo": [Campo("mdo_hora", MDO, "Mão de Obra (MDO)", "R$/h", "moeda")],
    "manutencao": [
        Campo("manutencao_hora", MANUTENCAO, "Manutenção", "R$/h", "moeda", opcional=True)
    ],
    "depreciacao": [
        Campo("valor_aquisicao", AQUISICAO, "Valor de aquisição", "R$", "moeda"),
        Campo("vida_util_anos", VIDA_UTIL, "Vida útil", "anos", "numero", ler_vida_util),
        Campo("valor_residual", RESIDUAL, "Valor residual", "R$", "moeda"),
        Campo("horas_dia", HORAS_DIA, "Impressão diária", "horas/dia", "numero", ler_horas_dia),
        Campo("dias_mes", DIAS_MES, "Dias por mês", "dias/mês", "numero", ler_dias_mes),
    ],
}

TIPOS_ITEM = {"acessorios": "acessorio", "embalagens": "embalagem"}  # rota -> código do banco
TITULOS_ITEM = {"acessorio": "Acessórios", "embalagem": "Embalagens"}


def ler_quantidade(texto: str):
    """(quantidade, erro): inteiro maior que zero."""
    texto = texto.strip()
    if not texto:
        return None, "Informe a quantidade."
    if not texto.isascii() or not texto.isdigit():
        return None, "A quantidade deve ser um número inteiro, sem vírgula."
    quantidade = int(texto)
    if quantidade <= 0:
        return None, "A quantidade deve ser maior que zero."
    if quantidade > LIMITE_QUANTIDADE:
        return None, "Quantidade acima do limite permitido."
    return quantidade, ""


def ler_nome(texto: str):
    """(nome, erro): sem espaços nas pontas, obrigatório, até 120 caracteres."""
    nome = " ".join(texto.split())
    if not nome:
        return "", "Informe o nome."
    if len(nome) > LIMITE_NOME:
        return nome, f"O nome pode ter no máximo {LIMITE_NOME} caracteres."
    return nome, ""


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


def energia_por_hora(tarifa_kwh: Decimal, consumo_w: Decimal) -> Decimal:
    """R$/h = tarifa (R$/kWh) x consumo (W) / 1000."""
    with localcontext() as contexto:
        contexto.prec = 50
        return tarifa_kwh * consumo_w / Decimal(1000)


@dataclass(frozen=True)
class Depreciacao:
    horas_mensais: Decimal  # dias por mês x horas por dia
    mensal: Decimal  # (aquisição - residual) / (vida útil em anos x 12)
    por_hora: Decimal  # (aquisição - residual) / (vida útil x 12 x dias por mês x horas por dia)


def depreciacao(
    aquisicao: Decimal,
    residual: Decimal,
    vida_util_anos: Decimal,
    dias_mes: Decimal,
    horas_dia: Decimal,
) -> Depreciacao:
    """Depreciação linear derivada dos critérios, sem arredondar em nenhuma etapa.

    O valor por hora é uma única divisão da base depreciável pelas horas de uso na vida útil.
    """
    with localcontext() as contexto:
        contexto.prec = 50
        base = aquisicao - residual
        meses = vida_util_anos * 12
        horas_mensais = dias_mes * horas_dia
        return Depreciacao(
            horas_mensais=horas_mensais,
            mensal=base / meses,
            por_hora=base / (meses * horas_mensais),
        )


def custo_unitario(valor_compra: Decimal, quantidade: int) -> Decimal:
    """R$/un = valor total de compra / unidades compradas (a mesma conta da coluna do banco)."""
    with localcontext() as contexto:
        contexto.prec = 28
        return valor_compra / Decimal(quantidade)


def custo_por_hora(
    energia: Decimal, mdo: Decimal, depreciacao_hora: Decimal, manutencao: Decimal | None
) -> tuple[Decimal, bool]:
    """(valor, completo): energia + MDO + depreciação + manutenção, cada uma somada em separado.

    Com a manutenção ainda sem valor, o resultado é só um subtotal (completo=False): nunca deve
    ser apresentado como o custo por hora completo.
    """
    subtotal = energia + mdo + depreciacao_hora
    if manutencao is None:
        return subtotal, False
    return subtotal + manutencao, True


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
