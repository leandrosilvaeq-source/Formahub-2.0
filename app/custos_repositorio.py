"""Custos no Supabase, com a chave secreta, só no servidor.

Leitura e gravação passam por funções do banco (listar_custos, salvar_custo_filamento,
salvar_custos_parametros e salvar_custo_item); as tabelas não têm acesso direto. Valores
numéricos viajam como TEXTO nos dois sentidos e viram Decimal aqui: nada passa por float.

Os testes usam um repositório em memória no lugar deste; nenhum teste acessa o Supabase.
"""

import logging
from dataclasses import dataclass
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

    motivo: "duplicado" (PT409, já existe item com o nome), "invalido" (PT422),
    "nao_encontrado" (PT404) ou "usuario" (PT403).
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
class Filamento:
    id: int
    nome: str
    abrange: str | None
    valor_kg: Decimal


@dataclass(frozen=True)
class ItemCusto:
    id: int
    tipo: str  # "acessorio" ou "embalagem"
    nome: str
    valor_compra: Decimal
    quantidade: int
    custo_unitario: Decimal  # valor_compra / quantidade, sem arredondar


@dataclass(frozen=True)
class Custos:
    filamentos: list[Filamento]
    parametros: dict[str, Decimal | None]  # chave -> valor (None = sem valor, "A definir")
    itens: list[ItemCusto]


class RepositorioCustos(Protocol):
    def listar(self) -> Custos:
        """Todos os custos (FalhaAoConsultarCustos se o banco não responder)."""

    def salvar_filamento(self, filamento_id: int, valor_kg: Decimal, usuario_id: int) -> Filamento:
        """Altera o R$/kg (CustoRecusado ou FalhaAoSalvarCustos)."""

    def salvar_parametros(
        self, valores: dict[str, Decimal | None], usuario_id: int
    ) -> dict[str, Decimal | None]:
        """Altera um ou mais valores únicos de uma vez (CustoRecusado ou FalhaAoSalvarCustos).

        None só é aceito na manutenção: volta a "A definir" (ausência de valor, não zero).
        """

    def salvar_item(
        self,
        item_id: int | None,
        tipo: str,
        nome: str,
        valor_compra: Decimal,
        quantidade: int,
        usuario_id: int,
    ) -> ItemCusto:
        """Cadastra (item_id None) ou edita um acessório ou embalagem."""


def _decimal_ou_none(valor: str | None) -> Decimal | None:
    return None if valor is None else Decimal(valor)


def _filamento(dados: dict) -> Filamento:
    return Filamento(
        id=dados["id"],
        nome=dados["nome"],
        abrange=dados["abrange"],
        valor_kg=Decimal(dados["valor_kg"]),
    )


def _item(dados: dict) -> ItemCusto:
    return ItemCusto(
        id=dados["id"],
        tipo=dados["tipo"],
        nome=dados["nome"],
        valor_compra=Decimal(dados["valor_compra"]),
        quantidade=dados["quantidade"],
        custo_unitario=Decimal(dados["custo_unitario"]),
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
                filamentos=[_filamento(f) for f in dados["filamentos"]],
                parametros={
                    chave: _decimal_ou_none(valor) for chave, valor in dados["parametros"].items()
                },
                itens=[_item(i) for i in dados["itens"]],
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

    def salvar_filamento(self, filamento_id: int, valor_kg: Decimal, usuario_id: int) -> Filamento:
        parametros = {
            "p_id": filamento_id,
            "p_valor_kg": para_banco(valor_kg),
            "p_usuario_id": usuario_id,
        }
        return self._salvar("salvar_custo_filamento", parametros, _filamento)

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

    def salvar_item(
        self,
        item_id: int | None,
        tipo: str,
        nome: str,
        valor_compra: Decimal,
        quantidade: int,
        usuario_id: int,
    ) -> ItemCusto:
        parametros = {
            "p_id": item_id,
            "p_tipo": tipo,
            "p_nome": nome,
            "p_valor_compra": para_banco(valor_compra),
            "p_quantidade": quantidade,
            "p_usuario_id": usuario_id,
        }
        return self._salvar("salvar_custo_item", parametros, _item)


@lru_cache
def _repositorio_supabase() -> RepositorioCustos:
    return RepositorioCustosSupabase(get_supabase_admin_client())


def get_repositorio_custos() -> RepositorioCustos | None:
    """Repositório real; None se o acesso ao banco não estiver configurado."""
    try:
        return _repositorio_supabase()
    except ConfigError:
        return None
