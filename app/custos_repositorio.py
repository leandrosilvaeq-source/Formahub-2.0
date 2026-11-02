"""Custos no Supabase, com a chave secreta, só no servidor.

Leitura e gravação passam por funções do banco (listar_custos, salvar_custos_parametros,
salvar_ajuste_insumo e definir_periodo_manutencao); as tabelas não têm acesso direto. Aqui ficam
só parâmetros, ajustes manuais e o período da manutenção: os insumos vêm das compras do estoque e
os gastos de manutenção, dos registros da tela Manutenção (somente leitura aqui). Valores
numéricos viajam como TEXTO nos dois sentidos e viram Decimal aqui: nada passa por float.

Os testes usam um repositório em memória no lugar deste; nenhum teste acessa o Supabase.
"""

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from functools import lru_cache
from typing import Protocol

from app.core.config import ConfigError, get_supabase_admin_client
from app.custos import para_banco

logger = logging.getLogger("formahub.custos")


class FalhaAoConsultarCustos(Exception):
    """O banco não respondeu (ou recusou) a leitura dos custos."""


class FalhaAoSalvarCustos(Exception):
    """O banco não respondeu (ou recusou por outro motivo) a gravação."""


class CustoRecusado(Exception):
    """O banco recusou por um motivo conhecido.

    motivo: "duplicado" (PT409), "invalido" (PT422), "nao_encontrado" (PT404) ou
    "usuario" (PT403).
    """

    def __init__(self, motivo: str):
        super().__init__(motivo)
        self.motivo = motivo


MOTIVOS_RECUSA = {
    "PT409": "duplicado",
    "PT422": "invalido",
    "PT404": "nao_encontrado",
    "PT403": "usuario",
}


@dataclass(frozen=True)
class GastoManutencao:
    id: int
    data: date
    descricao: str
    valor: Decimal


@dataclass(frozen=True)
class PeriodoManutencao:
    """Período de referência e as horas impressas nele (o custo/h usa os dois)."""

    inicio: date
    fim: date
    horas: Decimal


@dataclass(frozen=True)
class Custos:
    parametros: dict[str, Decimal | None]  # chave -> valor (None = sem valor)
    provisorios: frozenset[str]  # ajustes manuais que entraram pela migration, ainda não revistos
    datas: dict[str, date]  # chave -> data da última alteração (data-base das horas mensais)
    ajustes: dict[str, Decimal]  # chave do insumo -> custo manual (R$/kg ou R$/un)
    gastos: list[GastoManutencao]  # manutenções com custo definido (zero incluído)
    periodo: PeriodoManutencao | None
    manutencoes_sem_custo: tuple[date, ...] = ()  # datas das manutenções com custo "A definir"


class RepositorioCustos(Protocol):
    def listar(self) -> Custos:
        """Todos os custos (FalhaAoConsultarCustos se o banco não responder)."""

    def salvar_parametros(
        self, valores: dict[str, Decimal | None], usuario_id: int
    ) -> dict[str, Decimal | None]:
        """Altera um ou mais valores únicos de uma vez (CustoRecusado ou FalhaAoSalvarCustos).

        None só é aceito na manutenção e nos ajustes manuais: volta ao calculado (ausência de
        valor, não zero).
        """

    def salvar_ajuste_insumo(self, chave: str, valor: Decimal | None, usuario_id: int) -> None:
        """Ajuste manual do custo de um insumo; None volta ao calculado pelas compras."""

    def definir_periodo_manutencao(
        self, inicio: date, fim: date, horas: Decimal, usuario_id: int
    ) -> PeriodoManutencao:
        """Define o período de referência e as horas impressas nele."""


def _decimal_ou_none(valor: str | None) -> Decimal | None:
    return None if valor is None else Decimal(valor)


def _gasto(dados: dict) -> GastoManutencao:
    return GastoManutencao(
        id=dados["id"],
        data=date.fromisoformat(dados["data"]),
        descricao=dados["descricao"],
        valor=Decimal(dados["valor"]),
    )


def _periodo(dados: dict | None) -> PeriodoManutencao | None:
    if dados is None:
        return None
    return PeriodoManutencao(
        inicio=date.fromisoformat(dados["inicio"]),
        fim=date.fromisoformat(dados["fim"]),
        horas=Decimal(dados["horas"]),
    )


class RepositorioCustosSupabase:
    def __init__(self, cliente):
        self._c = cliente

    def _chamar(self, funcao: str, parametros: dict):
        """Executa a função do banco e devolve os dados (erros sem traduzir)."""
        return self._c.rpc(funcao, parametros).execute().data

    def listar(self) -> Custos:
        try:
            dados = self._chamar("listar_custos", {})
            return Custos(
                parametros={
                    chave: _decimal_ou_none(valor) for chave, valor in dados["parametros"].items()
                },
                provisorios=frozenset(dados["provisorios"]),
                datas={chave: date.fromisoformat(d) for chave, d in dados["datas"].items()},
                ajustes={chave: Decimal(valor) for chave, valor in dados["ajustes"].items()},
                gastos=[_gasto(g) for g in dados["manutencao_gastos"]],
                periodo=_periodo(dados["manutencao_periodo"]),
                manutencoes_sem_custo=tuple(
                    date.fromisoformat(d) for d in dados.get("manutencao_sem_custo", [])
                ),
            )
        except Exception as erro:  # rede, função ausente (migration pendente), formato antigo
            # Registra só o tipo do erro: a mensagem pode conter dados sensíveis.
            logger.error("Falha ao ler os custos no Supabase: %s", type(erro).__name__)
            raise FalhaAoConsultarCustos from None

    def _salvar(self, funcao: str, parametros: dict, converter):
        from postgrest.exceptions import APIError

        try:
            return converter(self._chamar(funcao, parametros))
        except APIError as erro:
            if erro.code in MOTIVOS_RECUSA:
                raise CustoRecusado(MOTIVOS_RECUSA[erro.code]) from None
            logger.error("Supabase recusou %s: %s", funcao, erro.code)
            raise FalhaAoSalvarCustos from None
        except Exception as erro:
            logger.error("Falha ao executar %s no Supabase: %s", funcao, type(erro).__name__)
            raise FalhaAoSalvarCustos from None

    def salvar_parametros(
        self, valores: dict[str, Decimal | None], usuario_id: int
    ) -> dict[str, Decimal | None]:
        parametros = {
            "p_valores": {chave: para_banco(valor) for chave, valor in valores.items()},
            "p_usuario_id": usuario_id,
        }
        return self._salvar(
            "salvar_custos_parametros",
            parametros,
            lambda dados: {chave: _decimal_ou_none(valor) for chave, valor in dados.items()},
        )

    def salvar_ajuste_insumo(self, chave: str, valor: Decimal | None, usuario_id: int) -> None:
        parametros = {"p_chave": chave, "p_valor": para_banco(valor), "p_usuario_id": usuario_id}
        self._salvar("salvar_ajuste_insumo", parametros, lambda dados: None)

    def definir_periodo_manutencao(
        self, inicio: date, fim: date, horas: Decimal, usuario_id: int
    ) -> PeriodoManutencao:
        parametros = {
            "p_inicio": inicio.isoformat(),
            "p_fim": fim.isoformat(),
            "p_horas": para_banco(horas),
            "p_usuario_id": usuario_id,
        }
        return self._salvar("definir_periodo_manutencao", parametros, _periodo)


@lru_cache
def _repositorio_supabase() -> RepositorioCustos:
    return RepositorioCustosSupabase(get_supabase_admin_client())


def get_repositorio_custos() -> RepositorioCustos | None:
    """Repositório real; None se o acesso ao banco não estiver configurado."""
    try:
        return _repositorio_supabase()
    except ConfigError:
        return None
