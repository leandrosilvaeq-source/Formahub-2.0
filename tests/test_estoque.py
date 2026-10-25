"""Estoque com repositório falso: atalhos para Compras, edição da descrição e cadastro bloqueado.

O fluxo Registrar Compra (entrada no estoque) está em test_estoque_compra.py.
Nenhum teste acessa o Supabase; todos os lotes são fictícios e ficam só em memória.
"""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.estoque_repositorio import get_repositorio_estoque
from app.main import app
from tests.fake_estoque import RepositorioEstoqueMemoria

client = TestClient(app)


@pytest.fixture
def estoque():
    repo = RepositorioEstoqueMemoria()
    app.dependency_overrides[get_repositorio_estoque] = lambda: repo
    return repo  # o conftest limpa as substituições ao final de cada teste


def descricao_filamento(**extra) -> dict:
    dados = {
        "csrf": "csrf-de-teste",
        "cor": "Preto",
        "material": "PLA",
        "tipo": "solido",
        "marca": "Marca Teste",
    }
    dados.update(extra)
    return dados


# ---------- Acesso e estado vazio ----------


def test_home_tem_link_para_compras():
    html = client.get("/").text
    assert 'href="/compras"' in html
    assert "Ver compras" in html
    assert 'href="/estoque"' in html
    assert "Ver estoque" in html


def test_estoque_nao_redireciona_para_compras(estoque):
    resposta = client.get("/estoque", follow_redirects=False)

    assert resposta.status_code == 200
    assert "<h1>Estoque</h1>" in resposta.text
    assert client.get("/compras", follow_redirects=False).status_code == 200


@pytest.mark.sem_login
def test_estoque_exige_login():
    for caminho in (
        "/compras",
        "/estoque",
        "/estoque/filamentos/1/editar",
        "/estoque/acessorios/1/editar",
    ):
        resposta = client.get(caminho, follow_redirects=False)
        assert resposta.status_code == 303
        assert resposta.headers["location"].startswith("/entrar")


@pytest.mark.sem_login
def test_editar_exige_login(estoque):
    lote_id = estoque.com_filamento()
    resposta = client.post(
        f"/estoque/filamentos/{lote_id}/editar", data=descricao_filamento(), follow_redirects=False
    )
    assert resposta.status_code == 303
    assert resposta.headers["location"].startswith("/entrar")
    assert not estoque.edicoes


def test_sem_banco_configurado_mostra_erro():
    app.dependency_overrides[get_repositorio_estoque] = lambda: None
    assert client.get("/compras").status_code == 503


# ---------- Cadastro direto bloqueado ----------


@pytest.mark.parametrize(
    "caminho",
    ["/estoque/filamentos/novo", "/estoque/acessorios/novo", "/estoque/embalagens/novo"],
)
def test_cadastro_direto_nao_existe_mais(estoque, caminho):
    assert client.get(caminho).status_code in (404, 405)
    dados = {
        "csrf": "csrf-de-teste",
        "cor": "Preto",
        "material": "PLA",
        "tipo": "solido",
        "marca": "M",
        "peso": "1000",
        "nome": "Argola",
        "quantidade": "10",
        "data_compra": "2026-09-01",
        "custo_kg": "100",
        "custo_unitario": "1",
    }
    assert client.post(caminho, data=dados).status_code in (404, 405)
    assert not estoque.filamentos and not estoque.itens and not estoque.compras


def test_mensagem_de_sucesso_so_para_codigos_conhecidos(estoque):
    html = client.get("/compras?salvo=<script>alert(1)</script>").text
    assert "alerta-sucesso" not in html
    assert "<script>alert(1)" not in html
    assert "Compra registrada." in client.get("/compras?salvo=compra").text


# ---------- Edição: só a descrição ----------


def test_editar_filamento_mostra_resumo_e_corrige_a_descricao(estoque):
    lote_id = estoque.com_filamento(
        cor="Azul", peso_g=Decimal("1250.50"), valor_unitario=Decimal("120.00")
    )
    html = client.get(f"/estoque/filamentos/{lote_id}/editar").text

    assert "Editar lote de filamento" in html
    assert f'action="/estoque/filamentos/{lote_id}/editar"' in html
    assert 'value="Azul"' in html
    assert '<option value="solido" selected>' in html
    # Saldo, data e custo aparecem só para leitura.
    for campo in ("peso", "data_compra", "custo_kg", "quantidade"):
        assert f'name="{campo}"' not in html
    assert "1.250,5 g" in html and "01/09/2026" in html and "R$ 120,00" in html

    resposta = client.post(
        f"/estoque/filamentos/{lote_id}/editar",
        data=descricao_filamento(cor="Azul Royal", tipo="velvet"),
        follow_redirects=False,
    )
    assert resposta.headers["location"] == "/compras?salvo=filamento-editado#filamentos"
    lote = estoque.filamentos[lote_id]
    assert (lote.cor, lote.tipo, lote.peso_g) == ("Azul Royal", "velvet", Decimal("1250.50"))
    assert estoque.edicoes == [("filamento", lote_id, 1)]  # usuário da sessão
    assert "Lote de filamento atualizado." in client.get(resposta.headers["location"]).text


def test_edicao_nao_aumenta_o_saldo(estoque):
    filamento_id = estoque.com_filamento(peso_g=Decimal("500"))
    item_id = estoque.com_item("Caixa", categoria="embalagem", quantidade=5)

    client.post(
        f"/estoque/filamentos/{filamento_id}/editar",
        data=descricao_filamento(peso="99999", peso_g="99999", custo_kg="0"),
    )
    client.post(
        f"/estoque/embalagens/{item_id}/editar",
        data={"csrf": "csrf-de-teste", "nome": "Caixa", "quantidade": "500"},
    )

    assert estoque.filamentos[filamento_id].peso_g == Decimal("500")
    assert estoque.itens[item_id].quantidade == 5
    assert len(estoque.edicoes) == 2  # só a descrição foi gravada


@pytest.mark.parametrize(
    ("campo", "valor", "mensagem"),
    [
        ("cor", "", "Informe a cor."),
        ("material", "  ", "Informe o material."),
        ("marca", "", "Informe a marca."),
        ("cor", "x" * 61, "Use no máximo 60 caracteres."),
        ("tipo", "", "Escolha o tipo."),
        ("tipo", "Sólido", "Escolha o tipo."),
    ],
)
def test_validacao_da_edicao_do_filamento(estoque, campo, valor, mensagem):
    lote_id = estoque.com_filamento(cor="Verde")
    resposta = client.post(
        f"/estoque/filamentos/{lote_id}/editar", data=descricao_filamento(**{campo: valor})
    )

    assert resposta.status_code == 422
    assert "Corrija os campos destacados." in resposta.text
    assert mensagem in resposta.text
    assert f'aria-describedby="{campo}-erro"' in resposta.text
    assert "1.000 g" in resposta.text  # o resumo continua na tela
    assert not estoque.edicoes
    assert estoque.filamentos[lote_id].cor == "Verde"


def test_edicao_texto_sem_espacos_extras(estoque):
    lote_id = estoque.com_filamento()
    client.post(
        f"/estoque/filamentos/{lote_id}/editar",
        data=descricao_filamento(cor="  Azul   Royal ", marca=" X "),
    )
    lote = estoque.filamentos[lote_id]
    assert (lote.cor, lote.marca) == ("Azul Royal", "X")


def test_edicao_com_pagina_expirada_ou_outra_origem(estoque):
    lote_id = estoque.com_filamento()
    expirada = client.post(
        f"/estoque/filamentos/{lote_id}/editar", data=descricao_filamento(csrf="outro", cor="X")
    )
    assert expirada.status_code == 403
    assert "A página expirou" in expirada.text
    assert 'value="X"' in expirada.text

    origem = client.post(
        f"/estoque/filamentos/{lote_id}/editar",
        data=descricao_filamento(),
        headers={"Origin": "https://exemplo.com"},
    )
    assert origem.status_code == 403
    assert not estoque.edicoes


def test_edicao_com_falha_ao_salvar_preserva_os_dados(estoque):
    lote_id = estoque.com_filamento()
    estoque.falhar = True
    resposta = client.post(
        f"/estoque/filamentos/{lote_id}/editar", data=descricao_filamento(cor="Branco")
    )

    assert resposta.status_code == 503
    assert "Não foi possível salvar agora" in resposta.text
    assert 'value="Branco"' in resposta.text


def test_editar_filamento_inexistente(estoque):
    assert client.get("/estoque/filamentos/99/editar").status_code == 404
    assert client.get("/estoque/filamentos/abc/editar").status_code == 404
    resposta = client.post("/estoque/filamentos/99/editar", data=descricao_filamento())
    assert resposta.status_code == 404
    assert "Lote não encontrado" in resposta.text
    assert not estoque.filamentos


def test_editar_com_falha_ao_carregar(estoque):
    lote_id = estoque.com_filamento()
    estoque.falhar = True
    resposta = client.get(f"/estoque/filamentos/{lote_id}/editar")
    assert resposta.status_code == 503
    assert "Não foi possível carregar o estoque agora" in resposta.text


def test_editar_item(estoque):
    lote_id = estoque.com_item("Caixa P", categoria="embalagem", quantidade=30)
    html = client.get(f"/estoque/embalagens/{lote_id}/editar").text
    assert "Editar lote de embalagem" in html
    assert 'value="Caixa P"' in html
    assert "30 un" in html and 'name="quantidade"' not in html

    resposta = client.post(
        f"/estoque/embalagens/{lote_id}/editar",
        data={"csrf": "csrf-de-teste", "nome": "Caixa Pequena"},
        follow_redirects=False,
    )
    assert resposta.headers["location"] == "/compras?salvo=embalagem-editado#embalagens"
    lote = estoque.itens[lote_id]
    assert (lote.nome, lote.quantidade, lote.categoria) == ("Caixa Pequena", 30, "embalagem")


def test_validacao_da_edicao_do_item(estoque):
    lote_id = estoque.com_item("Argola")
    resposta = client.post(
        f"/estoque/acessorios/{lote_id}/editar", data={"csrf": "csrf-de-teste", "nome": ""}
    )
    assert resposta.status_code == 422
    assert "Informe o nome." in resposta.text
    assert estoque.itens[lote_id].nome == "Argola"


def test_item_nao_e_editado_pela_area_errada(estoque):
    lote_id = estoque.com_item("Ímã", categoria="acessorio")

    assert client.get(f"/estoque/embalagens/{lote_id}/editar").status_code == 404
    resposta = client.post(
        f"/estoque/embalagens/{lote_id}/editar", data={"csrf": "csrf-de-teste", "nome": "X"}
    )
    assert resposta.status_code == 404
    assert estoque.itens[lote_id].nome == "Ímã"


def test_area_desconhecida(estoque):
    assert client.get("/estoque/parafusos/1/editar").status_code == 404
    assert client.post("/estoque/parafusos/1/editar", data={"nome": "X"}).status_code == 404
    assert not estoque.edicoes
