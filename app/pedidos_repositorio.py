"""Pedidos no Supabase (banco e Storage), com a chave secreta, só no servidor.

Gravação e leitura passam por funções do banco (reservar_id_pedido, criar_pedido,
listar_pedidos e consultar_pedido); as tabelas não têm acesso direto. As imagens ficam no
bucket privado pedido-imagens, o banco guarda somente o caminho do arquivo e a tela de
detalhes recebe URLs assinadas temporárias.

Os testes usam um repositório em memória no lugar deste; nenhum teste acessa o Supabase.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from functools import lru_cache
from typing import Protocol

from app.core.config import ConfigError, get_supabase_admin_client

BUCKET_IMAGENS = "pedido-imagens"
VALIDADE_URL_IMAGEM_SEGUNDOS = 600  # URL assinada da imagem vale 10 minutos

logger = logging.getLogger("formahub.pedidos")


class FalhaAoConsultar(Exception):
    """O banco ou o Storage não respondeu (ou recusou) uma leitura."""


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
    status_pagamento: str
    tipo_entrega: str
    observacoes: str | None
    valor_total: Decimal
    itens: tuple[ItemParaGravar, ...]


@dataclass(frozen=True)
class PedidoResumo:
    """Um card da listagem de pedidos."""

    id: int
    cliente_nome: str
    forma_pagamento: str
    status_pagamento: str
    criado_por_nome: str
    produtos: tuple[str, ...]  # na ordem dos itens
    quantidade_total: int
    observacoes: str | None
    # Foto de destaque: item com imagem de maior quantidade (empate: o primeiro pela ordem).
    imagem_caminho: str | None = None
    imagem_produto: str | None = None


@dataclass(frozen=True)
class ItemConsultado:
    ordem: int
    produto: str
    quantidade: int
    valor_unitario: Decimal
    subtotal: Decimal
    imagem_caminho: str | None = None


@dataclass(frozen=True)
class PedidoConsultado:
    id: int
    cliente_nome: str
    contato: str | None
    forma_pagamento: str
    status_pagamento: str
    tipo_entrega: str
    observacoes: str | None
    valor_total: Decimal
    criado_em: datetime  # horário de Brasília
    criado_por_nome: str
    itens: tuple[ItemConsultado, ...]


class RepositorioPedidos(Protocol):
    def reservar_id(self) -> int: ...

    def enviar_imagem(self, caminho: str, conteudo: bytes, tipo: str) -> None: ...

    def remover_imagens(self, caminhos: list[str]) -> None: ...

    def criar_pedido(self, pedido: PedidoParaGravar) -> None:
        """Grava o pedido e todos os itens numa única transação (ou nada)."""

    def listar_pedidos(self) -> list[PedidoResumo]:
        """Todos os pedidos, do mais recente para o mais antigo."""

    def consultar_pedido(self, pedido_id: int) -> PedidoConsultado | None: ...

    def assinar_imagens(self, caminhos: list[str]) -> dict[str, str]:
        """Caminho -> URL assinada temporária, só para os arquivos que existem."""


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
            "p_status_pagamento": pedido.status_pagamento,
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

    # ---------- Leitura ----------

    def _consultar(self, operacao):
        """Executa a leitura e converte a resposta; qualquer problema vira FalhaAoConsultar.

        Inclui resposta em formato inesperado (ex.: banco ainda sem a migration mais recente).
        """
        try:
            return operacao()
        except Exception as erro:
            # Registra só o tipo do erro: a mensagem pode conter dados sensíveis.
            logger.error("Falha ao consultar pedidos no Supabase: %s", type(erro).__name__)
            raise FalhaAoConsultar from None

    def listar_pedidos(self) -> list[PedidoResumo]:
        def ler():
            linhas = self._c.rpc("listar_pedidos", {}).execute().data
            return [
                PedidoResumo(
                    id=linha["id"],
                    cliente_nome=linha["cliente_nome"],
                    forma_pagamento=linha["forma_pagamento"],
                    status_pagamento=linha["status_pagamento"],
                    criado_por_nome=linha["criado_por_nome"],
                    produtos=tuple(linha["produtos"]),
                    quantidade_total=int(linha["quantidade_total"]),
                    observacoes=linha["observacoes"],
                    imagem_caminho=linha["imagem_caminho"],
                    imagem_produto=linha["imagem_produto"],
                )
                for linha in linhas or []
            ]

        return self._consultar(ler)

    def consultar_pedido(self, pedido_id: int) -> PedidoConsultado | None:
        return self._consultar(lambda: self._ler_pedido(pedido_id))

    def _ler_pedido(self, pedido_id: int) -> PedidoConsultado | None:
        dados = self._c.rpc("consultar_pedido", {"p_id": pedido_id}).execute().data
        if not dados:
            return None
        return PedidoConsultado(
            id=dados["id"],
            cliente_nome=dados["cliente_nome"],
            contato=dados["contato"],
            forma_pagamento=dados["forma_pagamento"],
            status_pagamento=dados["status_pagamento"],
            tipo_entrega=dados["tipo_entrega"],
            observacoes=dados["observacoes"],
            valor_total=Decimal(dados["valor_total"]),
            criado_em=datetime.fromisoformat(dados["criado_em_local"]),
            criado_por_nome=dados["criado_por_nome"],
            itens=tuple(
                ItemConsultado(
                    ordem=item["ordem"],
                    produto=item["produto"],
                    quantidade=item["quantidade"],
                    valor_unitario=Decimal(item["valor_unitario"]),
                    subtotal=Decimal(item["subtotal"]),
                    imagem_caminho=item["imagem_caminho"],
                )
                for item in dados["itens"]
            ),
        )

    def assinar_imagens(self, caminhos: list[str]) -> dict[str, str]:
        if not caminhos:
            return {}
        bucket = self._c.storage.from_(BUCKET_IMAGENS)
        assinadas = self._consultar(
            lambda: bucket.create_signed_urls(list(caminhos), VALIDADE_URL_IMAGEM_SEGUNDOS)
        )
        # Arquivo inexistente volta com erro e sem URL: fica de fora.
        return {
            item["path"]: item["signedURL"]
            for item in assinadas
            if item.get("signedURL") and not item.get("error")
        }


@lru_cache
def _repositorio_supabase() -> RepositorioPedidos:
    return RepositorioPedidosSupabase(get_supabase_admin_client())


def get_repositorio_pedidos() -> RepositorioPedidos | None:
    """Repositório real; None se o acesso ao banco não estiver configurado."""
    try:
        return _repositorio_supabase()
    except ConfigError:
        return None
