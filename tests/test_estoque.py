"""Tela Estoque com repositório falso: consulta, edição da descrição, busca, filtros e totais.

O fluxo Registrar Compra (entrada no estoque) está em test_estoque_compra.py.
Nenhum teste acessa o Supabase; todos os lotes são fictícios e ficam só em memória.
"""

from datetime import date
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


def area(html: str, id_: str) -> str:
    """HTML da seção da área (de <section id=...> até </section>)."""
    inicio = html.index(f'id="{id_}"')
    return html[inicio : html.index("</section>", inicio)]


def total(html: str, id_: str) -> str:
    trecho = area(html, id_)
    inicio = trecho.index(f'id="{id_}-total"')
    return trecho[inicio : trecho.index("</p>", inicio)]


# ---------- Acesso e estado vazio ----------


def test_home_tem_link_para_o_estoque():
    html = client.get("/").text
    assert 'href="/estoque"' in html
    assert "Ver estoque" in html


@pytest.mark.sem_login
def test_estoque_exige_login():
    for caminho in ("/estoque", "/estoque/filamentos/1/editar", "/estoque/acessorios/1/editar"):
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


def test_estoque_vazio_mostra_as_tres_areas(estoque):
    resposta = client.get("/estoque")

    assert resposta.status_code == 200
    assert resposta.headers["cache-control"] == "no-store"
    html = resposta.text
    for id_, titulo in (
        ("filamentos", "Filamentos"),
        ("acessorios", "Acessórios"),
        ("embalagens", "Embalagens"),
    ):
        assert f'<h2 id="{id_}-titulo">{titulo}</h2>' in html
        assert f"Nenhum lote de {titulo.lower()} ainda." in area(html, id_)
        assert "<table" not in area(html, id_)
        assert "<form" not in area(html, id_)  # sem busca enquanto não há lotes
    assert "0 g" in total(html, "filamentos")
    assert "0 un" in total(html, "acessorios")
    assert "0 un" in total(html, "embalagens")


def test_botoes_registrar_compra_no_lugar_do_cadastro_direto(estoque):
    html = client.get("/estoque").text

    for id_, categoria in (
        ("filamentos", "filamento"),
        ("acessorios", "acessorio"),
        ("embalagens", "embalagem"),
    ):
        secao = area(html, id_)
        assert f'data-abrir-compra="{categoria}"' in secao
        assert ">Registrar Compra</button>" in secao
    assert "/novo" not in html
    assert "Novo filamento" not in html and "Novo acessório" not in html
    # A janela vem junto com a página, com o token da sessão e o script do envio.
    assert '<dialog id="compra-janela"' in html
    assert 'action="/estoque/compras"' in html
    assert 'name="csrf" value="csrf-de-teste"' in html
    assert "js/estoque-compra.js" in html
    assert "js/imagem-referencia.js" in html


def test_falha_ao_carregar_mostra_erro(estoque):
    estoque.falhar = True
    resposta = client.get("/estoque")

    assert resposta.status_code == 503
    assert "Não foi possível carregar o estoque agora" in resposta.text
    assert "<table" not in resposta.text
    assert "compra-janela" not in resposta.text


def test_sem_banco_configurado_mostra_erro():
    app.dependency_overrides[get_repositorio_estoque] = lambda: None
    assert client.get("/estoque").status_code == 503


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


# ---------- Lotes na consulta (valores da compra) ----------


def test_lote_mostra_data_e_custo_da_compra(estoque):
    estoque.com_filamento(
        cor="Azul",
        peso_g=Decimal("2250"),
        peso_rolo_g=Decimal("750"),
        valor_unitario=Decimal("100.00"),
        data_compra=date(2026, 9, 20),
    )
    estoque.com_item("Argola", quantidade=3, valor_total=Decimal("10.00"))

    html = client.get("/estoque").text
    secao = area(html, "filamentos")
    assert "2.250 g" in secao
    assert "20/09/2026" in secao
    assert "R$ 133,33" in secao  # 100 x 1000 / 750, arredondado só na tela
    assert "R$ 3,33" in area(html, "acessorios")  # 10 / 3


def test_mesmo_item_em_compras_diferentes_e_total(estoque):
    estoque.com_item("Argola", quantidade=40, data_compra=date(2026, 8, 1))
    estoque.com_item("Argola", quantidade=1200, data_compra=date(2026, 9, 1))
    estoque.com_item("Caixa", categoria="embalagem", quantidade=7)

    html = client.get("/estoque").text
    assert area(html, "acessorios").count("Argola") >= 2
    assert "1.240 un" in total(html, "acessorios")
    assert "2 lotes" in total(html, "acessorios")
    assert "7 un" in total(html, "embalagens")
    assert "1 lote" in total(html, "embalagens")


def test_mensagem_de_sucesso_so_para_codigos_conhecidos(estoque):
    html = client.get("/estoque?salvo=<script>alert(1)</script>").text
    assert "alerta-sucesso" not in html
    assert "<script>alert(1)" not in html
    assert "Compra registrada." in client.get("/estoque?salvo=compra").text


def test_texto_do_lote_e_escapado(estoque):
    estoque.com_item("<b>Argola</b>")
    html = client.get("/estoque").text
    assert "<b>Argola</b>" not in html
    assert "&lt;b&gt;Argola&lt;/b&gt;" in html


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
    assert resposta.headers["location"] == "/estoque?salvo=filamento-editado#filamentos"
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
    assert resposta.headers["location"] == "/estoque?salvo=embalagem-editado#embalagens"
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


# ---------- Busca, filtros, ordenação e totais ----------


@pytest.fixture
def varios(estoque):
    # custo/kg = valor unitário (rolo de 1 kg)
    estoque.com_filamento(
        "Preto",
        "PLA",
        "solido",
        "Marca A",
        peso_g=Decimal("1000"),
        data_compra=date(2026, 9, 1),
        valor_unitario=Decimal("100"),
    )
    estoque.com_filamento(
        "Azul Céu",
        "PETG",
        "silk",
        "Marca B",
        peso_g=Decimal("500.5"),
        data_compra=date(2026, 9, 10),
        valor_unitario=Decimal("150"),
    )
    estoque.com_filamento(
        "Branco",
        "pla",
        "velvet",
        "Marca C",
        peso_g=Decimal("250"),
        data_compra=date(2026, 8, 5),
        valor_unitario=Decimal("90"),
    )
    estoque.com_item("Argola", quantidade=10, data_compra=date(2026, 9, 1))
    estoque.com_item("Ímã", quantidade=5, data_compra=date(2026, 9, 5))
    estoque.com_item("Caixa", categoria="embalagem", quantidade=3)
    return estoque


def ordem_na_area(html, id_, nomes):
    secao = area(html, id_)
    return sorted(nomes, key=secao.index)


def test_ordem_padrao_compra_mais_recente(varios):
    html = client.get("/estoque").text
    assert ordem_na_area(html, "filamentos", ["Preto", "Azul Céu", "Branco"]) == [
        "Azul Céu",
        "Preto",
        "Branco",
    ]
    assert ordem_na_area(html, "acessorios", ["Argola", "Ímã"]) == ["Ímã", "Argola"]


@pytest.mark.parametrize(
    ("ordem", "esperada"),
    [
        ("antigas", ["Branco", "Preto", "Azul Céu"]),
        ("cor", ["Azul Céu", "Branco", "Preto"]),
        ("maior_peso", ["Preto", "Azul Céu", "Branco"]),
        ("menor_peso", ["Branco", "Azul Céu", "Preto"]),
        ("menor_custo", ["Branco", "Preto", "Azul Céu"]),
        ("maior_custo", ["Azul Céu", "Preto", "Branco"]),
        ("marca", ["Preto", "Azul Céu", "Branco"]),
        ("invalida", ["Azul Céu", "Preto", "Branco"]),
    ],
)
def test_ordenacao_dos_filamentos(varios, ordem, esperada):
    html = client.get(f"/estoque?f_ordem={ordem}").text
    assert ordem_na_area(html, "filamentos", ["Preto", "Azul Céu", "Branco"]) == esperada


@pytest.mark.parametrize(
    ("ordem", "esperada"),
    [
        ("nome", ["Argola", "Ímã"]),
        ("maior_quantidade", ["Argola", "Ímã"]),
        ("menor_quantidade", ["Ímã", "Argola"]),
        ("antigas", ["Argola", "Ímã"]),
    ],
)
def test_ordenacao_dos_acessorios(varios, ordem, esperada):
    html = client.get(f"/estoque?a_ordem={ordem}").text
    assert ordem_na_area(html, "acessorios", ["Argola", "Ímã"]) == esperada


def test_busca_ignora_maiusculas_e_acentos(varios):
    html = client.get("/estoque?f_busca=azul ceu").text
    secao = area(html, "filamentos")
    assert "Azul Céu" in secao and "Preto" not in secao and "Branco" not in secao
    assert "500,5 g" in total(html, "filamentos")
    assert "Total filtrado" in total(html, "filamentos")
    assert "1 lote de 3" in total(html, "filamentos")


def test_busca_por_marca_e_tipo(varios):
    assert "Branco" in area(client.get("/estoque?f_busca=marca c").text, "filamentos")
    secao = area(client.get("/estoque?f_busca=velvet").text, "filamentos")
    assert "Branco" in secao and "Preto" not in secao


def test_filtro_por_material_ignora_maiusculas(varios):
    html = client.get("/estoque?f_material=PLA").text
    secao = area(html, "filamentos")
    assert "Preto" in secao and "Branco" in secao and "Azul Céu" not in secao
    assert "1.250 g" in total(html, "filamentos")
    # A lista de materiais não repete PLA/pla.
    assert secao.count('<option value="PLA"') + secao.count('<option value="pla"') == 1
    assert '<option value="PETG">' in secao


def test_filtro_por_tipo(varios):
    html = client.get("/estoque?f_tipo=silk").text
    secao = area(html, "filamentos")
    assert "Azul Céu" in secao and "Preto" not in secao
    assert '<option value="silk" selected>' in secao


def test_filtros_combinados_sem_resultado(varios):
    html = client.get("/estoque?f_material=PETG&f_tipo=solido").text
    secao = area(html, "filamentos")
    assert "Nenhum lote encontrado com essa busca." in secao
    assert "<table" not in secao
    assert "0 g" in total(html, "filamentos")
    assert 'href="/estoque#filamentos">Limpar' in secao


def test_tipo_invalido_na_url_e_ignorado(varios):
    html = client.get("/estoque?f_tipo=<x>").text
    assert "3 lotes" in total(html, "filamentos")


def test_busca_dos_acessorios_nao_afeta_as_outras_areas(varios):
    html = client.get("/estoque?a_busca=ima&f_tipo=silk").text
    assert "Ímã" in area(html, "acessorios") and "Argola" not in area(html, "acessorios")
    assert "5 un" in total(html, "acessorios")
    assert "Caixa" in area(html, "embalagens")
    # O formulário de embalagens repete os filtros das outras áreas, e não os próprios.
    form = area(html, "embalagens")
    assert '<input type="hidden" name="a_busca" value="ima">' in form
    assert '<input type="hidden" name="f_tipo" value="silk">' in form
    assert 'type="hidden" name="e_' not in form


def test_total_de_embalagens_com_busca(varios):
    html = client.get("/estoque?e_busca=xyz").text
    assert "0 un" in total(html, "embalagens")
    assert "Nenhum lote encontrado com essa busca." in area(html, "embalagens")
