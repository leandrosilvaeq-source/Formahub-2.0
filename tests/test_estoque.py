"""Tela Estoque com repositório falso: cadastro, edição, validações, busca, filtros e totais.

Nenhum teste acessa o Supabase; todos os lotes são fictícios e ficam só em memória.
"""

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.estoque_repositorio import (
    FalhaNoEstoque,
    Filamento,
    ItemEstoque,
    LoteNaoEncontrado,
    RepositorioEstoqueSupabase,
    get_repositorio_estoque,
)
from app.main import app
from tests.fake_estoque import RepositorioEstoqueMemoria

client = TestClient(app)


@pytest.fixture
def estoque():
    repo = RepositorioEstoqueMemoria()
    app.dependency_overrides[get_repositorio_estoque] = lambda: repo
    return repo  # o conftest limpa as substituições ao final de cada teste


def filamento(**extra) -> dict:
    dados = {
        "csrf": "csrf-de-teste",
        "cor": "Preto",
        "material": "PLA",
        "tipo": "solido",
        "marca": "Marca Teste",
        "peso": "1000",
        "data_compra": "2026-09-20",
        "custo_kg": "R$ 120,00",
    }
    dados.update(extra)
    return dados


def item(**extra) -> dict:
    dados = {
        "csrf": "csrf-de-teste",
        "nome": "Argola Teste",
        "quantidade": "50",
        "data_compra": "2026-09-21",
        "custo_unitario": "0,35",
    }
    dados.update(extra)
    return dados


def lote_filamento(repo, cor="Preto", material="PLA", tipo="solido", marca="Marca A", **extra):
    dados = {
        "peso_g": Decimal("1000"),
        "data_compra": date(2026, 9, 1),
        "custo_kg": Decimal("100.00"),
    }
    dados.update(extra)
    lote = Filamento(None, cor, material, tipo, marca, **dados)
    return repo.salvar_filamento(lote, 1)


def lote_item(repo, nome, categoria="acessorio", quantidade=10, **extra):
    dados = {"data_compra": date(2026, 9, 1), "custo_unitario": Decimal("1.00")}
    dados.update(extra)
    return repo.salvar_item(ItemEstoque(None, categoria, nome, quantidade, **dados), 1)


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
    for caminho in ("/estoque", "/estoque/filamentos/novo", "/estoque/acessorios/novo"):
        resposta = client.get(caminho, follow_redirects=False)
        assert resposta.status_code == 303
        assert resposta.headers["location"].startswith("/entrar")


@pytest.mark.sem_login
def test_salvar_exige_login(estoque):
    resposta = client.post("/estoque/filamentos/novo", data=filamento(), follow_redirects=False)
    assert resposta.status_code == 303
    assert resposta.headers["location"].startswith("/entrar")
    assert not estoque.gravacoes


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
        assert f"Nenhum lote de {titulo.lower()} cadastrado ainda." in area(html, id_)
        assert "<table" not in area(html, id_)
        assert "<form" not in area(html, id_)  # sem busca enquanto não há lotes
    assert "0 g" in total(html, "filamentos")
    assert "0 un" in total(html, "acessorios")
    assert "0 un" in total(html, "embalagens")
    assert 'href="/estoque/filamentos/novo"' in html
    assert 'href="/estoque/acessorios/novo"' in html
    assert 'href="/estoque/embalagens/novo"' in html


def test_falha_ao_carregar_mostra_erro(estoque):
    estoque.falhar = True
    resposta = client.get("/estoque")

    assert resposta.status_code == 503
    assert "Não foi possível carregar o estoque agora" in resposta.text
    assert "<table" not in resposta.text


def test_sem_banco_configurado_mostra_erro():
    app.dependency_overrides[get_repositorio_estoque] = lambda: None
    assert client.get("/estoque").status_code == 503


# ---------- Cadastro de filamento ----------


def test_formulario_novo_filamento(estoque):
    html = client.get("/estoque/filamentos/novo").text

    assert "Novo lote de filamento" in html
    assert 'name="csrf" value="csrf-de-teste"' in html
    for campo in ("cor", "material", "tipo", "marca", "peso", "data_compra", "custo_kg"):
        assert f'name="{campo}"' in html
    for rotulo in ("Sólido", "Velvet", "Silk", "Bicolor", "Tricolor"):
        assert f">{rotulo}</option>" in html
    assert 'type="date"' in html
    assert 'href="/estoque#filamentos">Cancelar</a>' in html


def test_cadastra_filamento_com_decimais(estoque):
    resposta = client.post(
        "/estoque/filamentos/novo",
        data=filamento(peso="750,5", custo_kg="R$ 1.234,56"),
        follow_redirects=False,
    )

    assert resposta.status_code == 303
    assert resposta.headers["location"] == "/estoque?salvo=filamento-novo#filamentos"
    (lote,) = estoque.filamentos.values()
    assert lote.peso_g == Decimal("750.5")
    assert lote.custo_kg == Decimal("1234.56")
    assert lote.data_compra == date(2026, 9, 20)
    assert lote.tipo == "solido"
    assert estoque.gravacoes == [("filamento", None, 1)]  # gravado pelo usuário da sessão

    html = client.get(resposta.headers["location"]).text
    assert "Lote de filamento cadastrado." in html
    assert "750,5 g" in html
    assert "R$ 1.234,56" in html
    assert "20/09/2026" in html


def test_peso_com_ponto_decimal_e_saldo_zero(estoque):
    client.post("/estoque/filamentos/novo", data=filamento(peso="12.25"))
    client.post("/estoque/filamentos/novo", data=filamento(peso="0", custo_kg="0"))

    pesos = sorted(f.peso_g for f in estoque.filamentos.values())
    assert pesos == [Decimal("0"), Decimal("12.25")]


def test_texto_sem_espacos_extras(estoque):
    client.post("/estoque/filamentos/novo", data=filamento(cor="  Azul   Royal ", marca=" X "))
    (lote,) = estoque.filamentos.values()
    assert (lote.cor, lote.marca) == ("Azul Royal", "X")


def test_mesmo_filamento_em_compras_diferentes(estoque):
    client.post("/estoque/filamentos/novo", data=filamento(data_compra="2026-08-01", peso="300"))
    client.post(
        "/estoque/filamentos/novo",
        data=filamento(data_compra="2026-09-15", peso="1000", custo_kg="135,90"),
    )

    assert len(estoque.filamentos) == 2
    html = client.get("/estoque").text
    secao = area(html, "filamentos")
    assert secao.count("<tr>") == 3  # cabeçalho + dois lotes
    assert "01/08/2026" in secao and "15/09/2026" in secao
    assert "R$ 120,00" in secao and "R$ 135,90" in secao
    assert "1.300 g" in total(html, "filamentos")
    assert "2 lotes" in total(html, "filamentos")


@pytest.mark.parametrize(
    ("campo", "valor", "mensagem"),
    [
        ("cor", "", "Informe a cor."),
        ("material", "  ", "Informe o material."),
        ("marca", "", "Informe a marca."),
        ("cor", "x" * 61, "Use no máximo 60 caracteres."),
        ("tipo", "", "Escolha o tipo."),
        ("tipo", "Sólido", "Escolha o tipo."),
        ("tipo", "metalico", "Escolha o tipo."),
        ("peso", "", "Informe o peso disponível."),
        ("peso", "-5", "O peso não pode ser negativo."),
        ("peso", "1,555", "Use só números, com até 2 casas decimais"),
        ("peso", "1.000,5", "Use só números, com até 2 casas decimais"),
        ("peso", "abc", "Use só números, com até 2 casas decimais"),
        ("peso", "99999999", "Use só números, com até 2 casas decimais"),
        ("data_compra", "", "Informe a data de compra."),
        ("data_compra", "2026-02-30", "Data inválida."),
        ("data_compra", "1999-12-31", "Data inválida."),
        ("data_compra", "ontem", "Use o formato dd/mm/aaaa."),
        ("custo_kg", "", "Informe o custo por kg."),
        ("custo_kg", "-1", "O custo não pode ser negativo."),
        ("custo_kg", "R$ -0,01", "O custo não pode ser negativo."),
        ("custo_kg", "dez reais", "Valor inválido."),
        ("custo_kg", "10000000", "Valor alto demais."),
    ],
)
def test_validacao_do_filamento(estoque, campo, valor, mensagem):
    resposta = client.post("/estoque/filamentos/novo", data=filamento(**{campo: valor}))

    assert resposta.status_code == 422
    assert "Não foi possível salvar. Corrija os campos destacados." in resposta.text
    assert mensagem in resposta.text
    assert f'aria-describedby="{campo}-erro"' in resposta.text
    assert not estoque.filamentos and not estoque.gravacoes


def test_erro_preserva_o_que_foi_digitado(estoque):
    dados = filamento(cor="Verde", peso="-3", data_compra="2026-09-02", tipo="silk")
    html = client.post("/estoque/filamentos/novo", data=dados).text

    assert 'value="Verde"' in html
    assert 'value="-3"' in html
    assert 'value="2026-09-02"' in html
    assert '<option value="silk" selected>' in html
    assert 'value="R$ 120,00"' in html  # campos válidos voltam formatados


def test_data_digitada_dd_mm_aaaa(estoque):
    client.post("/estoque/filamentos/novo", data=filamento(data_compra="05/09/2026"))
    (lote,) = estoque.filamentos.values()
    assert lote.data_compra == date(2026, 9, 5)


def test_pagina_expirada_nao_salva(estoque):
    resposta = client.post("/estoque/filamentos/novo", data=filamento(csrf="outro"))

    assert resposta.status_code == 403
    assert "A página expirou" in resposta.text
    assert 'value="Preto"' in resposta.text
    assert not estoque.gravacoes


def test_outra_origem_nao_salva(estoque):
    resposta = client.post(
        "/estoque/filamentos/novo", data=filamento(), headers={"Origin": "https://exemplo.com"}
    )
    assert resposta.status_code == 403
    assert not estoque.gravacoes


def test_falha_ao_salvar_preserva_os_dados(estoque):
    estoque.falhar = True
    resposta = client.post("/estoque/filamentos/novo", data=filamento(cor="Branco"))

    assert resposta.status_code == 503
    assert "Não foi possível salvar agora" in resposta.text
    assert 'value="Branco"' in resposta.text


# ---------- Edição de filamento ----------


def test_editar_filamento_preenche_e_atualiza(estoque):
    lote_id = lote_filamento(estoque, cor="Azul", peso_g=Decimal("1250.50"))
    html = client.get(f"/estoque/filamentos/{lote_id}/editar").text

    assert "Editar lote de filamento" in html
    assert f'action="/estoque/filamentos/{lote_id}/editar"' in html
    assert 'value="Azul"' in html
    assert 'value="1250,5"' in html  # sem separador de milhar, para editar
    assert 'value="2026-09-01"' in html
    assert 'value="R$ 100,00"' in html
    assert '<option value="solido" selected>' in html

    resposta = client.post(
        f"/estoque/filamentos/{lote_id}/editar",
        data=filamento(cor="Azul", peso="980,25", tipo="velvet"),
        follow_redirects=False,
    )
    assert resposta.headers["location"] == "/estoque?salvo=filamento-editado#filamentos"
    lote = estoque.filamentos[lote_id]
    assert (lote.peso_g, lote.tipo) == (Decimal("980.25"), "velvet")
    assert len(estoque.filamentos) == 1
    assert "Lote de filamento atualizado." in client.get(resposta.headers["location"]).text


def test_editar_filamento_inexistente(estoque):
    assert client.get("/estoque/filamentos/99/editar").status_code == 404
    assert client.get("/estoque/filamentos/abc/editar").status_code == 404
    resposta = client.post("/estoque/filamentos/99/editar", data=filamento())
    assert resposta.status_code == 404
    assert "Lote não encontrado" in resposta.text
    assert not estoque.filamentos


def test_editar_com_falha_ao_carregar(estoque):
    lote_id = lote_filamento(estoque)
    estoque.falhar = True
    resposta = client.get(f"/estoque/filamentos/{lote_id}/editar")
    assert resposta.status_code == 503
    assert "Não foi possível carregar o estoque agora" in resposta.text


# ---------- Acessórios e embalagens ----------


@pytest.mark.parametrize(
    ("slug", "categoria", "titulo", "sucesso"),
    [
        ("acessorios", "acessorio", "Novo lote de acessório", "Lote de acessório cadastrado."),
        ("embalagens", "embalagem", "Novo lote de embalagem", "Lote de embalagem cadastrado."),
    ],
)
def test_cadastra_item(estoque, slug, categoria, titulo, sucesso):
    html = client.get(f"/estoque/{slug}/novo").text
    assert titulo in html
    assert f'href="/estoque#{slug}">Cancelar</a>' in html

    resposta = client.post(f"/estoque/{slug}/novo", data=item(), follow_redirects=False)

    assert resposta.status_code == 303
    assert resposta.headers["location"] == f"/estoque?salvo={categoria}-novo#{slug}"
    (lote,) = estoque.itens.values()
    assert lote.categoria == categoria
    assert (lote.quantidade, lote.custo_unitario) == (50, Decimal("0.35"))
    html = client.get(resposta.headers["location"]).text
    assert sucesso in html
    assert "Argola Teste" in area(html, slug)
    outra = "embalagens" if slug == "acessorios" else "acessorios"
    assert "Argola Teste" not in area(html, outra)


@pytest.mark.parametrize(
    ("campo", "valor", "mensagem"),
    [
        ("nome", "", "Informe o nome."),
        ("quantidade", "", "Informe a quantidade disponível."),
        ("quantidade", "1,5", "Use um número inteiro."),
        ("quantidade", "2.5", "Use um número inteiro."),
        ("quantidade", "-1", "A quantidade não pode ser negativa."),
        ("quantidade", "²", "Use um número inteiro."),
        ("quantidade", "1000000", "Quantidade alta demais."),
        ("data_compra", "31/04/2026", "Data inválida."),
        ("custo_unitario", "", "Informe o custo por unidade."),
        ("custo_unitario", "-0,50", "O custo não pode ser negativo."),
    ],
)
def test_validacao_do_item(estoque, campo, valor, mensagem):
    resposta = client.post("/estoque/acessorios/novo", data=item(**{campo: valor}))

    assert resposta.status_code == 422
    assert mensagem in resposta.text
    assert not estoque.itens


def test_quantidade_zero_e_aceita(estoque):
    client.post("/estoque/embalagens/novo", data=item(quantidade="0", custo_unitario="0"))
    (lote,) = estoque.itens.values()
    assert lote.quantidade == 0


def test_editar_item(estoque):
    lote_id = lote_item(estoque, "Caixa P", categoria="embalagem", quantidade=30)
    html = client.get(f"/estoque/embalagens/{lote_id}/editar").text
    assert "Editar lote de embalagem" in html
    assert 'value="Caixa P"' in html and 'value="30"' in html

    resposta = client.post(
        f"/estoque/embalagens/{lote_id}/editar",
        data=item(nome="Caixa P", quantidade="12"),
        follow_redirects=False,
    )
    assert resposta.headers["location"] == "/estoque?salvo=embalagem-editado#embalagens"
    assert estoque.itens[lote_id].quantidade == 12
    assert estoque.itens[lote_id].categoria == "embalagem"


def test_item_nao_e_editado_pela_area_errada(estoque):
    lote_id = lote_item(estoque, "Ímã", categoria="acessorio")

    assert client.get(f"/estoque/embalagens/{lote_id}/editar").status_code == 404
    resposta = client.post(f"/estoque/embalagens/{lote_id}/editar", data=item())
    assert resposta.status_code == 404
    assert estoque.itens[lote_id].nome == "Ímã"


def test_area_desconhecida(estoque):
    assert client.get("/estoque/parafusos/novo").status_code == 404
    assert client.post("/estoque/parafusos/novo", data=item()).status_code == 404
    assert client.get("/estoque/parafusos/1/editar").status_code == 404
    assert not estoque.gravacoes


def test_mesmo_item_em_compras_diferentes_e_total(estoque):
    lote_item(estoque, "Argola", quantidade=40, data_compra=date(2026, 8, 1))
    lote_item(estoque, "Argola", quantidade=1200, data_compra=date(2026, 9, 1))
    lote_item(estoque, "Caixa", categoria="embalagem", quantidade=7)

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


def test_texto_do_lote_e_escapado(estoque):
    lote_item(estoque, "<b>Argola</b>")
    html = client.get("/estoque").text
    assert "<b>Argola</b>" not in html
    assert "&lt;b&gt;Argola&lt;/b&gt;" in html


# ---------- Busca, filtros, ordenação e totais ----------


@pytest.fixture
def varios(estoque):
    lote_filamento(
        estoque,
        "Preto",
        "PLA",
        "solido",
        "Marca A",
        peso_g=Decimal("1000"),
        data_compra=date(2026, 9, 1),
        custo_kg=Decimal("100"),
    )
    lote_filamento(
        estoque,
        "Azul Céu",
        "PETG",
        "silk",
        "Marca B",
        peso_g=Decimal("500.5"),
        data_compra=date(2026, 9, 10),
        custo_kg=Decimal("150"),
    )
    lote_filamento(
        estoque,
        "Branco",
        "pla",
        "velvet",
        "Marca C",
        peso_g=Decimal("250"),
        data_compra=date(2026, 8, 5),
        custo_kg=Decimal("90"),
    )
    lote_item(estoque, "Argola", quantidade=10, data_compra=date(2026, 9, 1))
    lote_item(estoque, "Ímã", quantidade=5, data_compra=date(2026, 9, 5))
    lote_item(estoque, "Caixa", categoria="embalagem", quantidade=3)
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


# ---------- Repositório do Supabase (cliente falso) ----------


class _Resposta:
    def __init__(self, data):
        self.data = data


class _ClienteFalso:
    def __init__(self, data=None, erro=None):
        self.data, self.erro, self.chamadas = data, erro, []

    def rpc(self, funcao, parametros):
        self.chamadas.append((funcao, parametros))
        return self

    def execute(self):
        if self.erro:
            raise self.erro
        return _Resposta(self.data)


def test_repositorio_converte_numeros_de_texto_para_decimal():
    dados = {
        "filamentos": [
            {
                "id": 1,
                "cor": "Preto",
                "material": "PLA",
                "tipo": "solido",
                "marca": "M",
                "peso_disponivel_g": "750.50",
                "data_compra": "2026-09-01",
                "custo_kg": "120.00",
            }
        ],
        "itens": [
            {
                "id": 2,
                "categoria": "embalagem",
                "nome": "Caixa",
                "quantidade_disponivel": 3,
                "data_compra": "2026-09-02",
                "custo_unitario": "0.35",
            }
        ],
    }
    estoque = RepositorioEstoqueSupabase(_ClienteFalso(dados)).listar_estoque()

    assert estoque.filamentos[0].peso_g == Decimal("750.50")
    assert estoque.filamentos[0].data_compra == date(2026, 9, 1)
    assert estoque.itens[0].custo_unitario == Decimal("0.35")


def test_repositorio_envia_numeros_como_texto():
    cliente = _ClienteFalso(7)
    lote = Filamento(
        None, "Preto", "PLA", "solido", "M", Decimal("750.5"), date(2026, 9, 1), Decimal("120.00")
    )
    assert RepositorioEstoqueSupabase(cliente).salvar_filamento(lote, 2) == 7

    funcao, parametros = cliente.chamadas[0]
    assert funcao == "salvar_estoque_filamento"
    assert parametros["p_id"] is None
    assert parametros["p_peso_disponivel_g"] == "750.5"
    assert parametros["p_custo_kg"] == "120.00"
    assert parametros["p_data_compra"] == "2026-09-01"
    assert parametros["p_usuario_id"] == 2


def test_repositorio_traduz_erros():
    from postgrest.exceptions import APIError

    lote = ItemEstoque(5, "acessorio", "Argola", 1, date(2026, 9, 1), Decimal("1"))
    nao_encontrado = _ClienteFalso(erro=APIError({"code": "PT404", "message": "x"}))
    with pytest.raises(LoteNaoEncontrado):
        RepositorioEstoqueSupabase(nao_encontrado).salvar_item(lote, 1)

    recusado = _ClienteFalso(erro=APIError({"code": "23514", "message": "x"}))
    with pytest.raises(FalhaNoEstoque):
        RepositorioEstoqueSupabase(recusado).salvar_item(lote, 1)

    sem_rede = _ClienteFalso(erro=TimeoutError())
    with pytest.raises(FalhaNoEstoque):
        RepositorioEstoqueSupabase(sem_rede).listar_estoque()

    # Banco ainda sem a migration: resposta em formato inesperado.
    with pytest.raises(FalhaNoEstoque):
        RepositorioEstoqueSupabase(_ClienteFalso({"outra": []})).listar_estoque()
