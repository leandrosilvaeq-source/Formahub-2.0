"""Repositório real de pedidos com um cliente Supabase simulado (nada vai à rede).

As chamadas são conferidas contra as assinaturas reais do supabase-py (postgrest e storage3),
para que um parâmetro faltando apareça aqui e não só no primeiro pedido de verdade.
"""

import inspect
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

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

    def create_signed_urls(self, *args, **kwargs):
        inspect.signature(SyncBucketActionsMixin.create_signed_urls).bind(self, *args, **kwargs)
        self._cliente.chamadas.append(("create_signed_urls", args))
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
        return self._storage or []


def pedido_exemplo():
    return PedidoParaGravar(
        id=7,
        criado_por=2,
        cliente_nome="Cliente",
        contato="(00) 00000-0000",
        forma_pagamento="pix",
        status_pagamento="pago",
        tipo_entrega="retirada",
        prazo_entrega=date(2026, 12, 15),
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
    assert parametros["p_status_pagamento"] == "pago"
    assert parametros["p_prazo_entrega"] == "2026-12-15"  # DATE do banco, em ISO
    assert parametros["p_valor_total"] == "26.00"
    json.dumps(parametros)  # o supabase-py envia como JSON: nada de date/Decimal cru
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


# ---------- Leitura ----------


LINHA_LISTAGEM = {
    "id": 1,
    "cliente_nome": "Cliente",
    "forma_pagamento": "pix",
    "status_pagamento": "pendente",
    "prazo_entrega": "2026-10-05",
    "criado_por_nome": "Leandro",
    "produtos": ["Porta doce", "Caneca"],
    "quantidade_total": 11,
    "observacoes": "Obs",
    "imagem_caminho": "pedidos/1/itens/2/x.png",
    "imagem_produto": "Caneca",
}


def test_listar_pedidos_converte_o_card():
    cliente = ClienteSimulado(rpc={"listar_pedidos": [LINHA_LISTAGEM]})

    (resumo,) = RepositorioPedidosSupabase(cliente).listar_pedidos()

    assert cliente.chamadas == [("rpc", ("listar_pedidos", {}))]
    assert resumo.status_pagamento == "pendente"
    assert resumo.produtos == ("Porta doce", "Caneca")
    assert resumo.quantidade_total == 11
    assert resumo.observacoes == "Obs"
    assert (resumo.imagem_caminho, resumo.imagem_produto) == ("pedidos/1/itens/2/x.png", "Caneca")
    assert resumo.criado_por_nome == "Leandro"
    assert resumo.prazo_entrega == date(2026, 10, 5)


def test_listagem_no_formato_antigo_vira_falha_amigavel():
    # Banco ainda sem a migration do status: a função antiga devolve outros campos.
    from app.pedidos_repositorio import FalhaAoConsultar

    antiga = {
        "id": 1,
        "cliente_nome": "Cliente",
        "contato": "(00) 00000-0000",
        "forma_pagamento": "pix",
        "tipo_entrega": "entrega",
        "valor_total": "132.00",
        "quantidade_total": 11,
        "criado_em_local": "2026-09-30T20:04:00",
        "criado_por_nome": "Leandro",
    }
    repo = RepositorioPedidosSupabase(ClienteSimulado(rpc={"listar_pedidos": [antiga]}))

    with pytest.raises(FalhaAoConsultar):
        repo.listar_pedidos()


def test_detalhe_no_formato_antigo_vira_falha_amigavel():
    from app.pedidos_repositorio import FalhaAoConsultar

    antigo = {
        "id": 1,
        "cliente_nome": "Cliente",
        "contato": None,
        "forma_pagamento": "pix",
        "tipo_entrega": "entrega",
        "observacoes": None,
        "valor_total": "5",
        "criado_em_local": "2026-09-30T20:04:00",
        "criado_por_nome": "Leandro",
        "itens": [],
    }
    repo = RepositorioPedidosSupabase(ClienteSimulado(rpc={"consultar_pedido": antigo}))

    with pytest.raises(FalhaAoConsultar):
        repo.consultar_pedido(1)


def test_listar_pedidos_vazio():
    assert (
        RepositorioPedidosSupabase(ClienteSimulado(rpc={"listar_pedidos": []})).listar_pedidos()
        == []
    )


def test_consultar_pedido_converte_itens():
    dados = {
        "id": 1,
        "cliente_nome": "Cliente",
        "contato": None,
        "forma_pagamento": "cartao",
        "status_pagamento": "pendente",
        "tipo_entrega": "retirada",
        "prazo_entrega": "2026-10-05",
        "observacoes": "Obs",
        "valor_total": "26.00",
        "criado_em_local": "2026-09-30T20:04:00",
        "criado_por_nome": "Kassia",
        "itens": [
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
                "valor_unitario": "5",
                "subtotal": "5",
                "imagem_caminho": "pedidos/1/itens/2/x.png",
            },
        ],
    }
    cliente = ClienteSimulado(rpc={"consultar_pedido": dados})

    pedido = RepositorioPedidosSupabase(cliente).consultar_pedido(1)

    assert cliente.chamadas == [("rpc", ("consultar_pedido", {"p_id": 1}))]
    assert pedido.valor_total == Decimal("26.00")
    assert pedido.prazo_entrega == date(2026, 10, 5)
    assert [i.subtotal for i in pedido.itens] == [Decimal("21.00"), Decimal("5")]
    assert pedido.itens[1].imagem_caminho == "pedidos/1/itens/2/x.png"


def test_consultar_pedido_inexistente():
    cliente = ClienteSimulado(rpc={"consultar_pedido": None})

    assert RepositorioPedidosSupabase(cliente).consultar_pedido(999) is None


def test_assinar_imagens_usa_so_as_que_existem():
    from app.pedidos_repositorio import VALIDADE_URL_IMAGEM_SEGUNDOS

    resposta = [
        {"path": "a.png", "signedURL": "https://exemplo/a?token=1", "error": None},
        {"path": "b.png", "signedURL": None, "error": "Either the object does not exist"},
    ]
    cliente = ClienteSimulado(storage=resposta)

    urls = RepositorioPedidosSupabase(cliente).assinar_imagens(["a.png", "b.png"])

    assert urls == {"a.png": "https://exemplo/a?token=1"}
    assert (
        "create_signed_urls",
        (["a.png", "b.png"], VALIDADE_URL_IMAGEM_SEGUNDOS),
    ) in cliente.chamadas
    assert VALIDADE_URL_IMAGEM_SEGUNDOS <= 600


def test_assinar_lista_vazia_nao_chama_o_storage():
    cliente = ClienteSimulado()

    assert RepositorioPedidosSupabase(cliente).assinar_imagens([]) == {}
    assert cliente.chamadas == []


@pytest.mark.parametrize("metodo", ["listar_pedidos", "consultar_pedido", "assinar_imagens"])
def test_falhas_de_leitura_viram_falha_ao_consultar(metodo):
    from app.pedidos_repositorio import FalhaAoConsultar

    erro = APIError({"message": "x", "code": "42501"})
    cliente = ClienteSimulado(rpc={"listar_pedidos": erro, "consultar_pedido": erro}, storage=erro)
    repo = RepositorioPedidosSupabase(cliente)
    argumentos = {"listar_pedidos": (), "consultar_pedido": (1,), "assinar_imagens": (["a.png"],)}

    with pytest.raises(FalhaAoConsultar):
        getattr(repo, metodo)(*argumentos[metodo])


# ---------- Produção ----------


def test_listar_producao_converte_os_cards():
    linha = {
        "id": 1,
        "etapa_producao": "em_producao",
        "cliente_nome": "Cliente",
        "forma_pagamento": "pix",
        "status_pagamento": "pago",
        "tipo_entrega": "retirada",
        "prazo_entrega": "2026-02-28",
        "produtos": ["A", "B"],
        "quantidade_total": 3,
        "observacoes": None,
        "imagem_caminho": "pedidos/1/itens/1/x.png",
        "imagem_produto": "A",
    }
    cliente = ClienteSimulado(rpc={"listar_producao": [linha]})

    (c,) = RepositorioPedidosSupabase(cliente).listar_producao()

    assert cliente.chamadas == [("rpc", ("listar_producao", {}))]
    assert (c.etapa, c.status_pagamento, c.tipo_entrega) == ("em_producao", "pago", "retirada")
    assert c.produtos == ("A", "B") and c.quantidade_total == 3
    assert (c.imagem_caminho, c.imagem_produto) == ("pedidos/1/itens/1/x.png", "A")
    assert c.prazo_entrega == date(2026, 2, 28)
    assert not hasattr(c, "criado_por_nome")  # saiu do card


@pytest.mark.parametrize("funcao", ["listar_pedidos", "listar_producao", "consultar_pedido"])
def test_banco_sem_a_migration_do_prazo_vira_falha_amigavel(funcao):
    # A função antiga não devolve prazo_entrega: a tela mostra a mensagem de indisponível.
    from app.pedidos_repositorio import FalhaAoConsultar

    linha = {**LINHA_LISTAGEM, "etapa_producao": "fila_producao", "tipo_entrega": "entrega"}
    del linha["prazo_entrega"]
    resposta = linha if funcao == "consultar_pedido" else [linha]
    repo = RepositorioPedidosSupabase(ClienteSimulado(rpc={funcao: resposta}))

    with pytest.raises(FalhaAoConsultar):
        getattr(repo, funcao)(*((1,) if funcao == "consultar_pedido" else ()))


def test_listar_producao_com_falha_vira_falha_ao_consultar():
    from app.pedidos_repositorio import FalhaAoConsultar

    cliente = ClienteSimulado(rpc={"listar_producao": ConnectionError()})

    with pytest.raises(FalhaAoConsultar):
        RepositorioPedidosSupabase(cliente).listar_producao()


def test_mover_etapa_envia_os_parametros_e_le_a_resposta():
    resposta = {
        "id": 7,
        "etapa_producao": "em_producao",
        "etapa_atualizada_em": "2026-10-01T10:00:00",
        "etapa_atualizada_por_nome": "Leandro",
    }
    cliente = ClienteSimulado(rpc={"mover_etapa_producao": resposta})

    atualizada = RepositorioPedidosSupabase(cliente).mover_etapa(
        7, "em_producao", "fila_producao", 1
    )

    assert cliente.chamadas == [
        (
            "rpc",
            (
                "mover_etapa_producao",
                {
                    "p_pedido_id": 7,
                    "p_etapa_esperada": "em_producao",
                    "p_nova_etapa": "fila_producao",
                    "p_usuario_id": 1,
                },
            ),
        )
    ]
    assert (atualizada.id, atualizada.etapa, atualizada.atualizada_por_nome) == (
        7,
        "em_producao",
        "Leandro",
    )


@pytest.mark.parametrize(
    ("erro", "esperada"),
    [
        (APIError({"message": "x", "code": "PT409"}), "ConflitoDeEtapa"),
        (APIError({"message": "x", "code": "PT422"}), "MovimentoInvalido"),
        (APIError({"message": "x", "code": "PT404"}), "MovimentoInvalido"),
        (APIError({"message": "x", "code": "PT403"}), "FalhaAoMover"),
        (APIError({"message": "x", "code": "PGRST202"}), "FalhaAoMover"),  # função ausente
        (ConnectionError("sem rede"), "FalhaAoMover"),
    ],
)
def test_mover_etapa_traduz_os_erros_do_banco(erro, esperada):
    import app.pedidos_repositorio as modulo

    cliente = ClienteSimulado(rpc={"mover_etapa_producao": erro})

    with pytest.raises(getattr(modulo, esperada)):
        RepositorioPedidosSupabase(cliente).mover_etapa(1, "fila_producao", "em_producao", 1)


def test_funcao_antiga_de_avanco_nao_e_mais_usada():
    import app.pedidos_repositorio as modulo

    codigo = Path(modulo.__file__).read_text(encoding="utf-8")
    assert "avancar_etapa_producao" not in codigo
    assert not hasattr(modulo.RepositorioPedidosSupabase, "avancar_etapa")


# ---------- Comentário da produção e status do pagamento ----------


def test_listar_producao_converte_o_comentario_e_a_auditoria():
    from datetime import datetime

    linha = {
        **LINHA_LISTAGEM,
        "etapa_producao": "fila_producao",
        "tipo_entrega": "entrega",
        "comentario_producao": "Pintar de azul",
        "comentario_producao_atualizado_em": "2026-10-01T14:32:10.123456",
        "comentario_producao_atualizado_por_nome": "Kassia",
    }
    cliente = ClienteSimulado(rpc={"listar_producao": [linha]})

    (c,) = RepositorioPedidosSupabase(cliente).listar_producao()

    assert c.comentario_producao == "Pintar de azul"
    assert c.comentario_atualizado_em == datetime(2026, 10, 1, 14, 32, 10, 123456)
    assert c.comentario_atualizado_por_nome == "Kassia"
    assert c.observacoes == LINHA_LISTAGEM["observacoes"]  # campos separados


def test_listar_producao_sem_a_migration_dos_comentarios_abre_sem_comentario():
    linha = {**LINHA_LISTAGEM, "etapa_producao": "fila_producao", "tipo_entrega": "entrega"}
    cliente = ClienteSimulado(rpc={"listar_producao": [linha]})

    (c,) = RepositorioPedidosSupabase(cliente).listar_producao()

    assert c.comentario_producao is None and c.comentario_atualizado_em is None
    assert c.comentario_atualizado_por_nome is None


def test_salvar_comentario_envia_os_parametros_e_le_a_resposta():
    from datetime import datetime

    resposta = {
        "id": 7,
        "comentario_producao": "Novo",
        "comentario_producao_atualizado_em": "2026-10-01T10:00:00",
        "comentario_producao_atualizado_por_nome": "Leandro",
    }
    cliente = ClienteSimulado(rpc={"atualizar_comentario_producao": resposta})

    salvo = RepositorioPedidosSupabase(cliente).salvar_comentario_producao(7, None, "Novo", 1)

    assert cliente.chamadas == [
        (
            "rpc",
            (
                "atualizar_comentario_producao",
                {
                    "p_pedido_id": 7,
                    "p_comentario_esperado": None,
                    "p_novo_comentario": "Novo",
                    "p_usuario_id": 1,
                },
            ),
        )
    ]
    assert (salvo.id, salvo.comentario, salvo.atualizado_por_nome) == (7, "Novo", "Leandro")
    assert salvo.atualizado_em == datetime(2026, 10, 1, 10, 0)


def test_comentario_removido_volta_como_none():
    resposta = {
        "id": 7,
        "comentario_producao": None,
        "comentario_producao_atualizado_em": "2026-10-01T10:00:00",
        "comentario_producao_atualizado_por_nome": "Leandro",
    }
    cliente = ClienteSimulado(rpc={"atualizar_comentario_producao": resposta})

    salvo = RepositorioPedidosSupabase(cliente).salvar_comentario_producao(7, "Velho", None, 1)

    assert salvo.comentario is None


def test_alterar_pagamento_envia_os_parametros_e_le_a_resposta():
    resposta = {
        "id": 7,
        "status_pagamento": "pago",
        "pagamento_atualizado_em": "2026-10-01T10:00:00",
        "pagamento_atualizado_por_nome": "Kassia",
    }
    cliente = ClienteSimulado(rpc={"alterar_status_pagamento": resposta})

    alterado = RepositorioPedidosSupabase(cliente).alterar_status_pagamento(
        7, "pendente", "pago", 2
    )

    assert cliente.chamadas == [
        (
            "rpc",
            (
                "alterar_status_pagamento",
                {
                    "p_pedido_id": 7,
                    "p_status_esperado": "pendente",
                    "p_novo_status": "pago",
                    "p_usuario_id": 2,
                },
            ),
        )
    ]
    assert (alterado.id, alterado.status_pagamento, alterado.atualizado_por_nome) == (
        7,
        "pago",
        "Kassia",
    )


ALTERACOES = [
    ("atualizar_comentario_producao", "salvar_comentario_producao", (1, None, "x", 1)),
    ("alterar_status_pagamento", "alterar_status_pagamento", (1, "pendente", "pago", 1)),
]


@pytest.mark.parametrize(("funcao", "metodo", "argumentos"), ALTERACOES)
@pytest.mark.parametrize(
    ("codigo", "motivo"),
    [
        ("PT409", "conflito"),
        ("PT422", "invalido"),
        ("PT404", "nao_encontrado"),
        ("PT403", "usuario"),
    ],
)
def test_alteracoes_traduzem_os_codigos_do_banco(funcao, metodo, argumentos, codigo, motivo):
    from app.pedidos_repositorio import AlteracaoRecusada

    cliente = ClienteSimulado(rpc={funcao: APIError({"message": "x", "code": codigo})})

    with pytest.raises(AlteracaoRecusada) as recusa:
        getattr(RepositorioPedidosSupabase(cliente), metodo)(*argumentos)

    assert recusa.value.motivo == motivo


@pytest.mark.parametrize(("funcao", "metodo", "argumentos"), ALTERACOES)
@pytest.mark.parametrize(
    "erro",
    [
        APIError({"message": "x", "code": "PGRST202"}),  # função ausente (migration pendente)
        APIError({"message": "x", "code": "23514"}),
        ConnectionError("sem rede"),
        {"formato": "inesperado"},  # resposta sem os campos esperados
    ],
)
def test_alteracoes_com_outras_falhas_viram_falha_ao_alterar(funcao, metodo, argumentos, erro):
    from app.pedidos_repositorio import FalhaAoAlterar

    cliente = ClienteSimulado(rpc={funcao: erro})

    with pytest.raises(FalhaAoAlterar):
        getattr(RepositorioPedidosSupabase(cliente), metodo)(*argumentos)
