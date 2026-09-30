"""Repositório real de pedidos com um cliente Supabase simulado (nada vai à rede).

As chamadas são conferidas contra as assinaturas reais do supabase-py (postgrest e storage3),
para que um parâmetro faltando apareça aqui e não só no primeiro pedido de verdade.
"""

import inspect
from decimal import Decimal

import pytest
from postgrest._sync.client import SyncPostgrestClient
from postgrest.exceptions import APIError
from storage3._sync.file_api import SyncBucketActionsMixin

from app.pedidos_repositorio import (
    BUCKET_IMAGENS,
    FalhaAoGravar,
    ItemParaGravar,
    PedidoParaGravar,
    RepositorioPedidosSupabase,
)


class Resposta:
    def __init__(self, data):
        self.data = data


class Consulta:
    def __init__(self, resultado):
        self._resultado = resultado

    def execute(self):
        if isinstance(self._resultado, Exception):
            raise self._resultado
        return Resposta(self._resultado)


class Bucket:
    def __init__(self, cliente):
        self._cliente = cliente

    def upload(self, *args, **kwargs):
        inspect.signature(SyncBucketActionsMixin.upload).bind(self, *args, **kwargs)
        self._cliente.chamadas.append(("upload", args))
        return self._cliente.resultado_storage()

    def remove(self, *args, **kwargs):
        inspect.signature(SyncBucketActionsMixin.remove).bind(self, *args, **kwargs)
        self._cliente.chamadas.append(("remove", args))
        return self._cliente.resultado_storage()


class Storage:
    def __init__(self, cliente):
        self._cliente = cliente

    def from_(self, bucket):
        self._cliente.chamadas.append(("bucket", bucket))
        return Bucket(self._cliente)


class ClienteSimulado:
    def __init__(self, rpc=None, storage=None):
        self.chamadas = []
        self._rpc = rpc or {}
        self._storage = storage
        self.storage = Storage(self)

    def rpc(self, *args, **kwargs):
        inspect.signature(SyncPostgrestClient.rpc).bind(self, *args, **kwargs)
        self.chamadas.append(("rpc", args))
        return Consulta(self._rpc.get(args[0]))

    def resultado_storage(self):
        if isinstance(self._storage, Exception):
            raise self._storage
        return []


def pedido_exemplo():
    return PedidoParaGravar(
        id=7,
        criado_por=2,
        cliente_nome="Cliente",
        contato="(00) 00000-0000",
        forma_pagamento="pix",
        tipo_entrega="retirada",
        observacoes=None,
        valor_total=Decimal("26.00"),
        itens=(
            ItemParaGravar(1, "A", 2, Decimal("10.50"), Decimal("21.00")),
            ItemParaGravar(2, "B", 1, Decimal("5.00"), Decimal("5.00"), "pedidos/7/itens/2/x.png"),
        ),
    )


def test_reservar_id_chama_a_funcao_do_banco():
    cliente = ClienteSimulado(rpc={"reservar_id_pedido": 7})

    assert RepositorioPedidosSupabase(cliente).reservar_id() == 7
    assert cliente.chamadas == [("rpc", ("reservar_id_pedido", {}))]


def test_criar_pedido_envia_valores_como_texto_e_criador():
    cliente = ClienteSimulado(rpc={"criar_pedido": 7})

    RepositorioPedidosSupabase(cliente).criar_pedido(pedido_exemplo())

    (_, (funcao, parametros)) = cliente.chamadas[0]
    assert funcao == "criar_pedido"
    assert parametros["p_id"] == 7
    assert parametros["p_criado_por"] == 2
    assert parametros["p_valor_total"] == "26.00"
    assert parametros["p_itens"] == [
        {
            "ordem": 1,
            "produto": "A",
            "quantidade": 2,
            "valor_unitario": "10.50",
            "subtotal": "21.00",
            "imagem_caminho": None,
        },
        {
            "ordem": 2,
            "produto": "B",
            "quantidade": 1,
            "valor_unitario": "5.00",
            "subtotal": "5.00",
            "imagem_caminho": "pedidos/7/itens/2/x.png",
        },
    ]


def test_imagens_vao_para_o_bucket_privado_sem_sobrescrever():
    cliente = ClienteSimulado()
    repo = RepositorioPedidosSupabase(cliente)

    repo.enviar_imagem("pedidos/7/itens/1/x.png", b"\x89PNG", "image/png")
    repo.remover_imagens(["pedidos/7/itens/1/x.png"])

    assert cliente.chamadas == [
        ("bucket", BUCKET_IMAGENS),
        (
            "upload",
            (
                "pedidos/7/itens/1/x.png",
                b"\x89PNG",
                {"content-type": "image/png", "upsert": "false"},
            ),
        ),
        ("bucket", BUCKET_IMAGENS),
        ("remove", (["pedidos/7/itens/1/x.png"],)),
    ]


def test_recusa_do_banco_e_falha_certa():
    erro = APIError({"message": "recusado", "code": "23514"})
    cliente = ClienteSimulado(rpc={"criar_pedido": erro})

    with pytest.raises(FalhaAoGravar) as falha:
        RepositorioPedidosSupabase(cliente).criar_pedido(pedido_exemplo())
    assert falha.value.incerta is False


def test_falha_de_rede_e_falha_incerta():
    cliente = ClienteSimulado(rpc={"criar_pedido": ConnectionError("sem rede")})

    with pytest.raises(FalhaAoGravar) as falha:
        RepositorioPedidosSupabase(cliente).criar_pedido(pedido_exemplo())
    assert falha.value.incerta is True


def test_falha_no_storage_vira_falha_ao_gravar():
    cliente = ClienteSimulado(storage=TimeoutError())

    with pytest.raises(FalhaAoGravar):
        RepositorioPedidosSupabase(cliente).enviar_imagem("p.png", b"x", "image/png")
