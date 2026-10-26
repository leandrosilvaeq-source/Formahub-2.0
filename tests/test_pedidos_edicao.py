"""Editar e cancelar pedidos: banco e Storage falsos (em memória)."""

import re
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.pedidos_repositorio import FalhaAoAlterar
from tests.test_pedidos_consulta import com_imagem, item, novo_pedido

client = TestClient(app)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def formulario(pedido_id, **extra):
    dados = {
        "cliente": "Cliente Editado",
        "contato": "(11) 91111-2222",
        "item_produto": ["Caneca"],
        "item_quantidade": ["3"],
        "item_valor": ["R$ 10,00"],
        "item_imagem_atual": [""],
        "item_imagem_acao": [""],
        "pagamento": "Dinheiro",
        "status_pagamento": "Pago",
        "entrega": "Retirada",
        "prazo_entrega": "2026-10-30",
        "observacoes": "Nova observação",
        "csrf": "csrf-de-teste",
    }
    dados.update(extra)
    return dados


def editar(pedido_id, dados=None, imagens=None):
    arquivos = [(f"item_imagem_{i}", img) for i, img in (imagens or {}).items()]
    arquivos = arquivos or [("sem_arquivo", ("", b"", "application/octet-stream"))]
    return client.post(
        f"/pedidos/{pedido_id}/editar", data=dados or formulario(pedido_id), files=arquivos
    )


@pytest.fixture
def pedido(repo_pedidos):
    return novo_pedido(repo_pedidos, itens=[item(1, "Caneca", 11, "12.00")])


# ---------- Tela de edição ----------


def test_tela_de_edicao_vem_preenchida(repo_pedidos, pedido):
    html = client.get(f"/pedidos/{pedido.id}/editar").text

    assert "Editar pedido" in html
    assert f'action="/pedidos/{pedido.id}/editar"' in html
    assert 'value="Cliente Teste"' in html
    assert 'value="(11) 98765-4321"' in html
    assert 'value="2026-10-05"' in html
    assert 'value="Caneca"' in html
    assert "Salvar alterações" in html


def test_tela_de_edicao_oferece_manter_ou_remover_a_imagem_atual(repo_pedidos):
    base = novo_pedido(repo_pedidos)
    caminho = com_imagem(repo_pedidos, base.id, 1)
    # grava o pedido com a imagem usando a edição do próprio repositório em memória
    repo_pedidos.pedidos[base.id] = repo_pedidos.pedidos[base.id].__class__(
        **{**base.__dict__, "itens": (item(1, "Caneca", 11, "12.00", caminho),)}
    )

    html = client.get(f"/pedidos/{base.id}/editar").text

    assert f'name="item_imagem_atual" value="{caminho}"' in html
    assert 'name="item_imagem_acao"' in html
    assert "Remover" in html


def test_pedido_inexistente_ou_invalido_na_edicao_da_404(repo_pedidos):
    assert client.get("/pedidos/999/editar").status_code == 404
    assert client.get("/pedidos/abc/editar").status_code == 404


# ---------- Salvar edição ----------


def test_edicao_regrava_dados_e_itens(repo_pedidos, pedido):
    resposta = editar(pedido.id)

    assert resposta.status_code == 200
    assert "atualizado" in resposta.text
    gravado = repo_pedidos.pedidos[pedido.id]
    assert gravado.cliente_nome == "Cliente Editado"
    assert gravado.forma_pagamento == "dinheiro"
    assert gravado.status_pagamento == "pago"
    assert gravado.tipo_entrega == "retirada"
    assert gravado.prazo_entrega.isoformat() == "2026-10-30"
    assert gravado.valor_total == Decimal("30.00")
    assert [i.produto for i in gravado.itens] == ["Caneca"]
    assert gravado.itens[0].quantidade == 3
    assert gravado.criado_por == 1
    assert repo_pedidos.edicoes == [(pedido.id, 1)]


def test_edicao_invalida_nao_grava_nada(repo_pedidos, pedido):
    resposta = editar(pedido.id, formulario(pedido.id, cliente=""))

    assert resposta.status_code == 422
    assert "Informe o nome do cliente." in resposta.text
    assert repo_pedidos.pedidos[pedido.id].cliente_nome == "Cliente Teste"
    assert repo_pedidos.edicoes == []


def test_edicao_exige_token_csrf(repo_pedidos, pedido):
    resposta = editar(pedido.id, formulario(pedido.id, csrf="outro"))

    assert resposta.status_code == 403
    assert repo_pedidos.edicoes == []


def test_edicao_mantem_a_imagem_atual(repo_pedidos):
    base = novo_pedido(repo_pedidos)
    caminho = com_imagem(repo_pedidos, base.id, 1)
    repo_pedidos.pedidos[base.id] = type(base)(
        **{**base.__dict__, "itens": (item(1, "Caneca", 11, "12.00", caminho),)}
    )

    resposta = editar(
        base.id,
        formulario(base.id, item_imagem_atual=[caminho], item_imagem_acao=["manter"]),
    )

    assert resposta.status_code == 200
    assert repo_pedidos.pedidos[base.id].itens[0].imagem_caminho == caminho
    assert caminho in repo_pedidos.arquivos
    assert repo_pedidos.removidos == []


def test_edicao_remove_a_imagem_atual_quando_pedido(repo_pedidos):
    base = novo_pedido(repo_pedidos)
    caminho = com_imagem(repo_pedidos, base.id, 1)
    repo_pedidos.pedidos[base.id] = type(base)(
        **{**base.__dict__, "itens": (item(1, "Caneca", 11, "12.00", caminho),)}
    )

    resposta = editar(
        base.id,
        formulario(base.id, item_imagem_atual=[caminho], item_imagem_acao=["remover"]),
    )

    assert resposta.status_code == 200
    assert repo_pedidos.pedidos[base.id].itens[0].imagem_caminho is None
    assert caminho not in repo_pedidos.arquivos
    assert repo_pedidos.removidos == [caminho]


def test_edicao_troca_a_imagem_por_uma_nova(repo_pedidos):
    base = novo_pedido(repo_pedidos)
    antiga = com_imagem(repo_pedidos, base.id, 1)
    repo_pedidos.pedidos[base.id] = type(base)(
        **{**base.__dict__, "itens": (item(1, "Caneca", 11, "12.00", antiga),)}
    )

    resposta = editar(
        base.id,
        formulario(base.id, item_imagem_atual=[antiga], item_imagem_acao=["manter"]),
        imagens={0: ("nova.png", PNG, "image/png")},
    )

    assert resposta.status_code == 200
    nova = repo_pedidos.pedidos[base.id].itens[0].imagem_caminho
    assert nova != antiga
    assert re.fullmatch(rf"pedidos/{base.id}/itens/1/[0-9a-f-]{{36}}\.png", nova)
    assert nova in repo_pedidos.arquivos
    assert antiga not in repo_pedidos.arquivos


def test_caminho_de_imagem_de_outro_pedido_e_ignorado(repo_pedidos, pedido):
    estranho = "pedidos/999/itens/1/00000000-0000-4000-8000-000000000001.png"

    resposta = editar(
        pedido.id,
        formulario(pedido.id, item_imagem_atual=[estranho], item_imagem_acao=["manter"]),
    )

    assert resposta.status_code == 200
    assert repo_pedidos.pedidos[pedido.id].itens[0].imagem_caminho is None


def test_falha_ao_gravar_edicao_mostra_erro_e_mantem_o_pedido(repo_pedidos, pedido):
    repo_pedidos.falhar_em["editar_pedido"] = FalhaAoAlterar()

    resposta = editar(pedido.id)

    assert resposta.status_code == 503
    assert "Não foi possível salvar o pedido agora." in resposta.text
    assert repo_pedidos.pedidos[pedido.id].cliente_nome == "Cliente Teste"


def test_imagem_nova_e_removida_quando_o_banco_recusa_a_edicao(repo_pedidos, pedido):
    # Pedido entregue depois de aberta a tela: o banco recusa e a imagem enviada não fica.
    repo_pedidos.etapas[pedido.id] = "fila_producao"
    original = repo_pedidos.editar_pedido

    def entregar_antes(p):
        repo_pedidos.etapas[p.id] = "entregue"
        return original(p)

    repo_pedidos.editar_pedido = entregar_antes

    resposta = editar(pedido.id, imagens={0: ("a.png", PNG, "image/png")})

    assert resposta.status_code == 409
    assert repo_pedidos.arquivos == {}


# ---------- Não editável ----------


def test_pedido_entregue_nao_pode_ser_editado_nem_cancelado(repo_pedidos, pedido):
    repo_pedidos.etapas[pedido.id] = "entregue"

    assert client.get(f"/pedidos/{pedido.id}/editar").status_code == 409
    assert editar(pedido.id).status_code == 409
    assert client.get(f"/pedidos/{pedido.id}/cancelar").status_code == 409
    assert (
        client.post(f"/pedidos/{pedido.id}/cancelar", data={"csrf": "csrf-de-teste"}).status_code
        == 409
    )
    assert repo_pedidos.cancelados == {}
    assert repo_pedidos.edicoes == []


# ---------- Cancelar ----------


def test_tela_de_confirmacao_do_cancelamento(repo_pedidos, pedido):
    html = client.get(f"/pedidos/{pedido.id}/cancelar").text

    assert "Cancelar pedido" in html
    assert "Pedido de Cliente Teste" in html
    assert f'action="/pedidos/{pedido.id}/cancelar"' in html
    assert repo_pedidos.cancelados == {}


def test_cancelar_marca_o_pedido_e_ele_sai_da_producao(repo_pedidos, pedido):
    resposta = client.post(
        f"/pedidos/{pedido.id}/cancelar",
        data={"csrf": "csrf-de-teste"},
        follow_redirects=False,
    )

    assert resposta.status_code == 303
    assert resposta.headers["location"] == f"/pedidos/{pedido.id}"
    assert pedido.id in repo_pedidos.cancelados
    assert pedido.id in repo_pedidos.pedidos  # nada é apagado
    assert pedido.id not in {c.id for c in repo_pedidos.listar_producao()}
    assert "Pedido cancelado" in client.get(f"/pedidos/{pedido.id}").text


def test_cancelar_exige_token_csrf(repo_pedidos, pedido):
    resposta = client.post(f"/pedidos/{pedido.id}/cancelar", data={"csrf": "errado"})

    assert resposta.status_code == 403
    assert repo_pedidos.cancelados == {}


def test_pedido_cancelado_nao_pode_ser_editado_nem_cancelado_de_novo(repo_pedidos, pedido):
    repo_pedidos.cancelar_pedido(pedido.id, 1)

    assert client.get(f"/pedidos/{pedido.id}/editar").status_code == 409
    assert editar(pedido.id).status_code == 409
    assert client.get(f"/pedidos/{pedido.id}/cancelar").status_code == 409
    assert repo_pedidos.edicoes == []


def test_falha_ao_cancelar_mostra_erro(repo_pedidos, pedido):
    repo_pedidos.falhar_em["cancelar_pedido"] = FalhaAoAlterar()

    resposta = client.post(f"/pedidos/{pedido.id}/cancelar", data={"csrf": "csrf-de-teste"})

    assert resposta.status_code == 503
    assert "Não foi possível cancelar o pedido agora." in resposta.text
    assert repo_pedidos.cancelados == {}


# ---------- Lista ----------


def test_lista_mostra_acoes_e_esconde_cancelados_no_padrao(repo_pedidos):
    ativo = novo_pedido(repo_pedidos, cliente="Ativo")
    cancelado = novo_pedido(repo_pedidos, cliente="Cancelado Um")
    repo_pedidos.cancelar_pedido(cancelado.id, 1)

    html = client.get("/pedidos").text

    assert 'data-cliente="Ativo"' in html
    assert 'data-cliente="Cancelado Um"' not in html
    assert f'href="/pedidos/{ativo.id}/editar"' in html
    assert f'href="/pedidos/{ativo.id}/cancelar"' in html
    assert re.search(r"Total de Pedidos</span>\s*<strong[^>]*>1</strong>", html)


def test_filtro_cancelado_lista_os_cancelados_sem_acoes(repo_pedidos):
    novo_pedido(repo_pedidos, cliente="Ativo")
    cancelado = novo_pedido(repo_pedidos, cliente="Cancelado Um")
    repo_pedidos.cancelar_pedido(cancelado.id, 1)

    html = client.get("/pedidos?status=cancelado").text

    assert 'data-cliente="Cancelado Um"' in html
    assert 'data-cliente="Ativo"' not in html
    assert f'href="/pedidos/{cancelado.id}/editar"' not in html
    assert 'Cancelado</span><span class="pedidos-filtro-qtd">1</span>' in html


def test_detalhe_mostra_editar_e_cancelar_so_quando_permitido(repo_pedidos, pedido):
    html = client.get(f"/pedidos/{pedido.id}").text
    assert f'href="/pedidos/{pedido.id}/editar"' in html
    assert f'href="/pedidos/{pedido.id}/cancelar"' in html

    repo_pedidos.etapas[pedido.id] = "entregue"
    html = client.get(f"/pedidos/{pedido.id}").text
    assert "/editar" not in html
    assert "/cancelar" not in html
