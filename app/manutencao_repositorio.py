"""Manutenções no Supabase, com a chave secreta, só no servidor.

Leitura e gravação passam por funções do banco (listar_manutencoes e salvar_manutencao); a
tabela não tem acesso direto. Valores numéricos viajam como TEXTO e viram Decimal aqui.

Os testes usam um repositório em memória no lugar deste; nenhum teste acessa o Supabase.
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from functools import lru_cache
from typing import Protocol

from app.core.config import ConfigError, get_supabase_admin_client
from app.custos import para_banco
from app.custos_repositorio import MOTIVOS_RECUSA

logger = logging.getLogger("formahub.manutencao")


class FalhaAoConsultarManutencao(Exception):
    """O banco não respondeu (ou recusou) a leitura das manutenções."""


class FalhaAoSalvarManutencao(Exception):
    """O banco não respondeu (ou recusou por outro motivo) a gravação."""


class ManutencaoRecusada(Exception):
    """O banco recusou por um motivo conhecido: "invalido", "nao_encontrado" ou "usuario"."""

    def __init__(self, motivo: str):
        super().__init__(motivo)
        self.motivo = motivo


@dataclass(frozen=True)
class Manutencao:
    id: int
    data: date
    tipo: str | None  # "preventiva" | "corretiva"; None só nos gastos antigos
    minutos_parados: int | None  # None só nos gastos antigos
    troca_pecas: bool
    pecas: str | None
    custo: Decimal | None  # None = "A definir" (diferente de zero)
    comentarios: str | None
    legado: bool = False  # veio de um gasto de manutenção anterior
    criado_por: str = ""
    criado_em: datetime | None = None
    atualizado_por: str = ""
    atualizado_em: datetime | None = None


class RepositorioManutencao(Protocol):
    def listar(self) -> list[Manutencao]:
        """Todas as manutenções, da mais recente para a mais antiga."""

    def salvar(self, manutencao_id: int | None, valores: dict, usuario_id: int) -> Manutencao:
        """Cria (id None) ou altera uma manutenção; devolve o que ficou gravado.

        `valores`: data, tipo, minutos, troca_pecas, pecas, custo e comentarios, já validados.
        """


def _momento(texto: str | None) -> datetime | None:
    return None if texto is None else datetime.fromisoformat(texto)


def _manutencao(dados: dict) -> Manutencao:
    return Manutencao(
        id=dados["id"],
        data=date.fromisoformat(dados["data"]),
        tipo=dados["tipo"],
        minutos_parados=dados["minutos_parados"],
        troca_pecas=dados["troca_pecas"],
        pecas=dados["pecas"],
        custo=None if dados["custo"] is None else Decimal(dados["custo"]),
        comentarios=dados["comentarios"],
        legado=dados["legado"],
        criado_por=dados["criado_por"],
        criado_em=_momento(dados["criado_em"]),
        atualizado_por=dados["atualizado_por"],
        atualizado_em=_momento(dados["atualizado_em"]),
    )


class RepositorioManutencaoSupabase:
    def __init__(self, cliente):
        self._c = cliente

    def _chamar(self, funcao: str, parametros: dict):
        return self._c.rpc(funcao, parametros).execute().data

    def listar(self) -> list[Manutencao]:
        try:
            return [_manutencao(m) for m in self._chamar("listar_manutencoes", {})]
        except Exception as erro:  # rede, função ausente (migration pendente), formato antigo
            # Registra só o tipo do erro: a mensagem pode conter dados sensíveis.
            logger.error("Falha ao ler as manutenções no Supabase: %s", type(erro).__name__)
            raise FalhaAoConsultarManutencao from None

    def salvar(self, manutencao_id: int | None, valores: dict, usuario_id: int) -> Manutencao:
        from postgrest.exceptions import APIError

        parametros = {
            "p_id": manutencao_id,
            "p_data": valores["data"].isoformat(),
            "p_tipo": valores["tipo"],
            "p_minutos": valores["minutos"],
            "p_troca_pecas": valores["troca_pecas"],
            "p_pecas": valores["pecas"],
            "p_custo": para_banco(valores["custo"]),
            "p_comentarios": valores["comentarios"],
            "p_usuario_id": usuario_id,
        }
        try:
            return _manutencao(self._chamar("salvar_manutencao", parametros))
        except APIError as erro:
            if erro.code in MOTIVOS_RECUSA:
                raise ManutencaoRecusada(MOTIVOS_RECUSA[erro.code]) from None
            logger.error("Supabase recusou salvar_manutencao: %s", erro.code)
            raise FalhaAoSalvarManutencao from None
        except Exception as erro:
            logger.error("Falha ao salvar a manutenção no Supabase: %s", type(erro).__name__)
            raise FalhaAoSalvarManutencao from None


@lru_cache
def _repositorio_supabase() -> RepositorioManutencao:
    return RepositorioManutencaoSupabase(get_supabase_admin_client())


def get_repositorio_manutencao() -> RepositorioManutencao | None:
    """Repositório real; None se o acesso ao banco não estiver configurado."""
    try:
        return _repositorio_supabase()
    except ConfigError:
        return None
