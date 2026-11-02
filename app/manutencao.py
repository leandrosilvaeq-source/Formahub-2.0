"""Manutenção da máquina: leitura e validação do que é digitado, duração e indicadores.

Tudo aqui é lógica pura (sem banco nem tela). Valores em reais são Decimal; o tempo parado é
guardado como TOTAL EM MINUTOS (inteiro) e só vira texto na hora de mostrar.
"""

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.custos import ler_decimal
from app.pedidos import formatar_brl

TIPOS = {"preventiva": "Preventiva", "corretiva": "Corretiva"}
MINUTOS_POR_DIA = 24 * 60
LIMITE_MINUTOS = 365 * MINUTOS_POR_DIA  # até um ano parada
LIMITE_PECAS = 300
LIMITE_COMENTARIOS = 1000
DATA_MINIMA, DATA_MAXIMA = date(2000, 1, 1), date(2100, 12, 31)

CAMPOS = (
    "data",
    "tipo",
    "dias",
    "horas",
    "minutos",
    "troca_pecas",
    "pecas",
    "custo",
    "comentarios",
)


# ---------- Duração ----------


def formatar_duracao(minutos: int | None) -> str:
    """Sempre em horas totais e minutos, sem virar dias: '02h30min', '53h00min'.

    Sem valor (gasto antigo que nunca teve tempo informado), '—'.
    """
    if minutos is None:
        return "—"
    horas, resto = divmod(minutos, 60)
    return f"{horas:02d}h{resto:02d}min"


# Formatos aceitos (sem diferenciar maiúsculas, espaços ignorados):
#   hh:mm            02:30, 27:15   (horas totais, inclusive acima de 24)
#   xxhyymin         27h15min, 27h15, 27h
#   NdHHh / NdHH     02d05h, 02d05, 02d:05h, 02d:05   (dias e horas; 2 dias e 5 horas = 53h00min)
_HORAS_MINUTOS = re.compile(r"(\d{1,5}):(\d{1,2})")
_HORAS_TOTAIS = re.compile(r"(\d{1,5})h(?:(\d{1,2})(?:min)?)?")
_DIAS = re.compile(r"(\d{1,4})d:?(?:(\d{1,2})h?)?(?::?(\d{1,2})(?:min)?)?")
MSG_FORMATO_DURACAO = "Use hh:mm (02:30), 27h15min ou dias e horas (02d05h)."


def ler_duracao(texto: str):
    """(total em minutos, erro). Minutos de 0 a 59 e, na parte de dias, horas de 0 a 23: nada é
    arredondado nem descartado. O total fica entre 0 e um ano."""
    t = "".join(texto.lower().split())
    if not t:
        return (
            None,
            "Informe o tempo parado, por exemplo 02:30, 27h15min ou 02d05h (00:00 se não parou).",
        )
    dias = horas = minutos = 0
    if (m := _HORAS_MINUTOS.fullmatch(t)) or (m := _HORAS_TOTAIS.fullmatch(t)):
        horas, minutos = int(m.group(1)), int(m.group(2) or 0)
    elif m := _DIAS.fullmatch(t):
        dias, horas, minutos = int(m.group(1)), int(m.group(2) or 0), int(m.group(3) or 0)
        if horas > 23:
            return None, "As horas da parte de dias devem estar entre 0 e 23."
    else:
        return None, MSG_FORMATO_DURACAO
    if minutos > 59:
        return None, "Os minutos devem estar entre 0 e 59."
    total = dias * MINUTOS_POR_DIA + horas * 60 + minutos
    if total > LIMITE_MINUTOS:
        return None, "O tempo parado não pode passar de 365 dias."
    return total, ""


# ---------- Campos ----------


def ler_data(texto: str):
    """(data, erro): aaaa-mm-dd, a data completa que o campo de data envia."""
    try:
        valor = date.fromisoformat(texto.strip())
    except ValueError:
        return None, "Informe uma data válida."
    if not DATA_MINIMA <= valor <= DATA_MAXIMA:
        return None, "Informe uma data entre 2000 e 2100."
    return valor, ""


def formatar_data_curta(valor: date) -> str:
    """'25/10/26' (dd/mm/aa)."""
    return valor.strftime("%d/%m/%y")


def ler_custo(texto: str):
    """(custo, erro): vazio = 'A definir' (None, sem erro); zero é um custo conhecido."""
    if not texto.strip():
        return None, ""
    return ler_decimal(texto, casas=2)


def _texto_livre(texto: str) -> str:
    return " ".join(texto.split())


def ler_formulario(dados: dict[str, str]):
    """(valores, erros): valores prontos para gravar, ou erros por campo (nunca os dois).

    A coluna "Peça substituída" é uma só: vazia significa sem troca; com texto, houve troca (vários
    nomes cabem no mesmo campo).
    """
    erros: dict[str, str] = {}

    data, erro = ler_data(dados.get("data", ""))
    if erro:
        erros["data"] = erro

    tipo = dados.get("tipo", "")
    if tipo not in TIPOS:
        erros["tipo"] = "Escolha Preventiva ou Corretiva."

    minutos, erro = ler_duracao(dados.get("tempo", ""))
    if erro:
        erros["tempo"] = erro

    pecas = _texto_livre(dados.get("pecas", "")) or None
    if pecas and len(pecas) > LIMITE_PECAS:
        erros["pecas"] = f"Use no máximo {LIMITE_PECAS} caracteres."

    custo, erro = ler_custo(dados.get("custo", ""))
    if erro:
        erros["custo"] = erro

    comentarios = dados.get("comentarios", "").strip() or None
    if comentarios and len(comentarios) > LIMITE_COMENTARIOS:
        erros["comentarios"] = f"Use no máximo {LIMITE_COMENTARIOS} caracteres."

    if erros:
        return None, erros
    return {
        "data": data,
        "tipo": tipo,
        "minutos": minutos,
        "troca_pecas": pecas is not None,
        "pecas": pecas,
        "custo": custo,
        "comentarios": comentarios,
    }, {}


# ---------- Indicadores (sobre todos os registros) ----------


@dataclass(frozen=True)
class Indicadores:
    numero: int
    minutos_parados: int
    custo_conhecido: Decimal  # soma dos custos definidos (zero conta; "A definir" não soma)
    sem_custo: int  # manutenções com custo "A definir"

    @property
    def custo_completo(self) -> bool:
        """True quando todos os custos são conhecidos (então a soma não é só um subtotal)."""
        return self.sem_custo == 0

    @property
    def custo_indefinido(self) -> bool:
        """Há manutenções, mas nenhum custo foi definido: o total é desconhecido."""
        return self.numero > 0 and self.sem_custo == self.numero


def indicadores(manutencoes) -> Indicadores:
    lista = list(manutencoes)
    custos = [m.custo for m in lista if m.custo is not None]
    return Indicadores(
        numero=len(lista),
        minutos_parados=sum(m.minutos_parados or 0 for m in lista),
        custo_conhecido=sum(custos, Decimal(0)),
        sem_custo=len(lista) - len(custos),
    )


def formatar_custo(valor: Decimal | None) -> str:
    return "A definir" if valor is None else formatar_brl(valor)
