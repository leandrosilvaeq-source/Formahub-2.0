"""Gravação de pedidos no Supabase (banco e Storage), com a chave secreta, só no servidor.

O pedido e os itens entram por funções do banco (reservar_id_pedido e criar_pedido); as
tabelas não têm acesso direto. As imagens vão para o bucket privado pedido-imagens e o
banco guarda somente o caminho do arquivo.

Os testes usam um repositório em memória no lugar deste; nenhum teste acessa o Supabase.
"""

import logging
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from typing import Protocol

from app.core.config import ConfigError, get_supabase_admin_client

BUCKET_IMAGENS = "pedido-imagens"

logger = logging.getLogger("formahub.pedidos")


class FalhaAoGravar(Exception):
    """O banco ou o Storage recusou a operação ou não respondeu.

    incerta=True: a resposta não chegou, então não dá para saber se a operação aconteceu.
    """

    def __init__(self, incerta: bool = False):
        super().__init__()
        self.incerta = incerta


@dataclass(frozen=True)
class ItemParaGravar:
    ordem: int
    produto: str
    quantidade: int
    valor_unitario: Decimal
    subtotal: Decimal
    imagem_caminho: str | None = None


@dataclass(frozen=True)
class PedidoParaGravar:
    id: int
    criado_por: int
    cliente_nome: str
    contato: str | None
    forma_pagamento: str
    tipo_entrega: str
    observacoes: str | None
    valor_total: Decimal
    itens: tuple[ItemParaGravar, ...]


class RepositorioPedidos(Protocol):
    def reservar_id(self) -> int: ...

    def enviar_imagem(self, caminho: str, conteudo: bytes, tipo: str) -> None: ...

    def remover_imagens(self, caminhos: list[str]) -> None: ...

    def criar_pedido(self, pedido: PedidoParaGravar) -> None:
        """Grava o pedido e todos os itens numa única transação (ou nada)."""


class RepositorioPedidosSupabase:
    def __init__(self, cliente):
        self._c = cliente

    def _executar(self, operacao):
        from postgrest.exceptions import APIError
        from storage3.exceptions import StorageApiError

        try:
            return operacao()
        except (APIError, StorageApiError) as erro:  # o servidor respondeu recusando
            logger.error("Supabase recusou a operação do pedido: %s", type(erro).__name__)
            raise FalhaAoGravar(incerta=False) from None
        except Exception as erro:  # rede, tempo esgotado...: a resposta não chegou
            # Registra só o tipo do erro: a mensagem pode conter dados sensíveis.
            logger.error("Falha de comunicação com o Supabase: %s", type(erro).__name__)
            raise FalhaAoGravar(incerta=True) from None

    def reservar_id(self) -> int:
        resposta = self._executar(lambda: self._c.rpc("reservar_id_pedido", {}).execute())
        return int(resposta.data)

    def enviar_imagem(self, caminho: str, conteudo: bytes, tipo: str) -> None:
        bucket = self._c.storage.from_(BUCKET_IMAGENS)
        opcoes = {"content-type": tipo, "upsert": "false"}
        self._executar(lambda: bucket.upload(caminho, conteudo, opcoes))

    def remover_imagens(self, caminhos: list[str]) -> None:
        bucket = self._c.storage.from_(BUCKET_IMAGENS)
        self._executar(lambda: bucket.remove(list(caminhos)))

    def criar_pedido(self, pedido: PedidoParaGravar) -> None:
        # Valores monetários vão como texto para não passar por float.
        parametros = {
            "p_id": pedido.id,
            "p_criado_por": pedido.criado_por,
            "p_cliente_nome": pedido.cliente_nome,
            "p_contato": pedido.contato,
            "p_forma_pagamento": pedido.forma_pagamento,
            "p_tipo_entrega": pedido.tipo_entrega,
            "p_observacoes": pedido.observacoes,
            "p_valor_total": str(pedido.valor_total),
            "p_itens": [
                {
                    "ordem": item.ordem,
                    "produto": item.produto,
                    "quantidade": item.quantidade,
                    "valor_unitario": str(item.valor_unitario),
                    "subtotal": str(item.subtotal),
                    "imagem_caminho": item.imagem_caminho,
                }
                for item in pedido.itens
            ],
        }
        self._executar(lambda: self._c.rpc("criar_pedido", parametros).execute())


@lru_cache
def _repositorio_supabase() -> RepositorioPedidos:
    return RepositorioPedidosSupabase(get_supabase_admin_client())


def get_repositorio_pedidos() -> RepositorioPedidos | None:
    """Repositório real; None se o acesso ao banco não estiver configurado."""
    try:
        return _repositorio_supabase()
    except ConfigError:
        return None
