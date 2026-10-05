"""Estoque no Supabase (lotes de filamentos, acessórios e embalagens), só no servidor.

Leitura e gravação passam pelas funções do banco listar_estoque, salvar_estoque_filamento e
salvar_estoque_item (migration 20261009120000_estoque); as tabelas não têm acesso direto.
Números vão e voltam como texto, para não passar por float.

Os testes usam um repositório em memória no lugar deste; nenhum teste acessa o Supabase.
"""

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from functools import lru_cache
from typing import Protocol

from app.core.config import ConfigError, get_supabase_admin_client

logger = logging.getLogger("formahub.estoque")


class FalhaNoEstoque(Exception):
    """O banco não respondeu (ou recusou) a leitura ou a gravação do estoque."""


class LoteNaoEncontrado(Exception):
    """O lote a editar não existe (ou é de outra categoria)."""


@dataclass(frozen=True)
class Filamento:
    """Um lote de compra de filamento. id None: lote ainda não cadastrado."""

    id: int | None
    cor: str
    material: str
    tipo: str  # código: solido, velvet, silk, bicolor ou tricolor
    marca: str
    peso_g: Decimal
    data_compra: date
    custo_kg: Decimal


@dataclass(frozen=True)
class ItemEstoque:
    """Um lote de compra de acessório ou embalagem. id None: lote ainda não cadastrado."""

    id: int | None
    categoria: str  # acessorio ou embalagem
    nome: str
    quantidade: int
    data_compra: date
    custo_unitario: Decimal


@dataclass(frozen=True)
class Estoque:
    filamentos: list[Filamento]
    itens: list[ItemEstoque]  # acessórios e embalagens


class RepositorioEstoque(Protocol):
    def listar_estoque(self) -> Estoque:
        """Todos os lotes (FalhaNoEstoque se o banco não responder)."""

    def salvar_filamento(self, filamento: Filamento, usuario_id: int) -> int:
        """Cadastra (id None) ou edita o lote e devolve o id (LoteNaoEncontrado ou
        FalhaNoEstoque)."""

    def salvar_item(self, item: ItemEstoque, usuario_id: int) -> int:
        """Cadastra (id None) ou edita o lote e devolve o id (LoteNaoEncontrado ou
        FalhaNoEstoque)."""


class RepositorioEstoqueSupabase:
    def __init__(self, cliente):
        self._c = cliente

    def _rpc(self, funcao: str, parametros: dict):
        from postgrest.exceptions import APIError

        try:
            return self._c.rpc(funcao, parametros).execute().data
        except APIError as erro:
            if erro.code == "PT404":
                raise LoteNaoEncontrado from None
            logger.error("Supabase recusou %s: %s", funcao, erro.code)
            raise FalhaNoEstoque from None
        except Exception as erro:
            # Registra só o tipo do erro: a mensagem pode conter dados sensíveis.
            logger.error("Falha ao executar %s no Supabase: %s", funcao, type(erro).__name__)
            raise FalhaNoEstoque from None

    def listar_estoque(self) -> Estoque:
        dados = self._rpc("listar_estoque", {})
        try:
            return Estoque(
                filamentos=[
                    Filamento(
                        id=f["id"],
                        cor=f["cor"],
                        material=f["material"],
                        tipo=f["tipo"],
                        marca=f["marca"],
                        peso_g=Decimal(f["peso_disponivel_g"]),
                        data_compra=date.fromisoformat(f["data_compra"]),
                        custo_kg=Decimal(f["custo_kg"]),
                    )
                    for f in dados["filamentos"]
                ],
                itens=[
                    ItemEstoque(
                        id=i["id"],
                        categoria=i["categoria"],
                        nome=i["nome"],
                        quantidade=int(i["quantidade_disponivel"]),
                        data_compra=date.fromisoformat(i["data_compra"]),
                        custo_unitario=Decimal(i["custo_unitario"]),
                    )
                    for i in dados["itens"]
                ],
            )
        except Exception as erro:
            # Resposta em formato inesperado (ex.: banco ainda sem a migration do estoque).
            logger.error("Resposta inesperada de listar_estoque: %s", type(erro).__name__)
            raise FalhaNoEstoque from None

    def salvar_filamento(self, filamento: Filamento, usuario_id: int) -> int:
        parametros = {
            "p_id": filamento.id,
            "p_cor": filamento.cor,
            "p_material": filamento.material,
            "p_tipo": filamento.tipo,
            "p_marca": filamento.marca,
            "p_peso_disponivel_g": str(filamento.peso_g),
            "p_data_compra": filamento.data_compra.isoformat(),
            "p_custo_kg": str(filamento.custo_kg),
            "p_usuario_id": usuario_id,
        }
        return int(self._rpc("salvar_estoque_filamento", parametros))

    def salvar_item(self, item: ItemEstoque, usuario_id: int) -> int:
        parametros = {
            "p_id": item.id,
            "p_categoria": item.categoria,
            "p_nome": item.nome,
            "p_quantidade_disponivel": item.quantidade,
            "p_data_compra": item.data_compra.isoformat(),
            "p_custo_unitario": str(item.custo_unitario),
            "p_usuario_id": usuario_id,
        }
        return int(self._rpc("salvar_estoque_item", parametros))


@lru_cache
def _repositorio_supabase() -> RepositorioEstoque:
    return RepositorioEstoqueSupabase(get_supabase_admin_client())


def get_repositorio_estoque() -> RepositorioEstoque | None:
    """Repositório real; None se o acesso ao banco não estiver configurado."""
    try:
        return _repositorio_supabase()
    except ConfigError:
        return None
