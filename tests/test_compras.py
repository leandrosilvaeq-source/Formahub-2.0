"""Tela Compras (/compras): cards por categoria, filtro de período e tabela de compras.

Repositório falso em memória; nenhum teste acessa o Supabase.
"""

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.estoque_repositorio import get_repositorio_estoque
from app.main import app
from tests.fake_estoque import RepositorioEstoqueMemoria
from tests.test_estoque_compra import PNG, compra, enviar, filamento, item

client = TestClient(app)


@pytest.fixture
def estoque():
    repo = RepositorioEstoqueMemoria()
    app.dependency_overrides[get_repositorio_estoque] = lambda: repo
    return repo


def registrar(estoque, *linhas, fotos=None, **extra):
    assert enviar(compra(*linhas, **extra), fotos=fotos).status_code == 200


def card(html: str, chave: str) -> str:
    """Texto do card (compras, filamento, acessorio ou embalagem), sem as marcas HTML."""
    inicio = html.index(f'id="card-{chave}"')
    bloco = html[html.index(">", inicio) + 1 : html.index("</div>", inicio)]
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", bloco)).strip()


# ---------- Estado vazio, falha e botão ----------


def test_sem_compras_mostra_quatro_cards_zerados_e_mensagem(estoque):
    resposta = client.get("/compras")

    assert resposta.status_code == 200
    assert resposta.headers["cache-control"] == "no-store"
    html = resposta.text
    assert "<h1>Compras</h1>" in html
    for chave, titulo in (
        ("compras", "Compras"),
        ("filamento", "Filamentos"),
        ("acessorio", "Acessórios"),
        ("embalagem", "Embalagens"),
    ):
        assert card(html, chave).startswith(f"{titulo} R$ 0,00 0 compras")
    assert "Nenhuma compra registrada" in html
    assert "<table" not in html


def test_falha_de_leitura_mostra_indisponibilidade_sem_zeros(estoque):
    estoque.falhar = True

    resposta = client.get("/compras?periodo=2026-09")

    assert resposta.status_code == 503
    assert "Não foi possível carregar as compras agora" in resposta.text
    for ausente in ("card-compras", "R$ 0,00", "Nenhuma compra", "compras-filtro"):
        assert ausente not in resposta.text
    assert "compra-janela" not in resposta.text


def test_botao_registrar_compra_e_janela_preservados(estoque):
    html = client.get("/compras").text

    assert html.count("data-abrir-compra") == 1
    assert html.count(">Registrar Compra</button>") == 1
    for antiga in ('id="filamentos"', 'id="acessorios"', 'id="embalagens"'):
        assert antiga not in html
    assert '<dialog id="compra-janela"' in html
    assert 'action="/compras"' in html
    assert 'name="csrf" value="csrf-de-teste"' in html
    assert "js/estoque-compra.js" in html and "js/imagem-referencia.js" in html


def test_autocomplete_de_cores_vem_dos_filamentos_comprados(estoque):
    registrar(estoque, filamento(0, cor="Azul Royal"))

    html = client.get("/compras").text

    assert '<option value="Azul Royal">' in html


def test_registrar_compra_pela_rota_nova_e_volta_para_compras(estoque):
    resposta = client.post("/compras", data=compra(filamento(0)))

    assert resposta.status_code == 200
    assert resposta.json() == {"destino": "/compras?salvo=compra"}
    assert len(estoque.compras) == 1 and len(estoque.filamentos) == 1  # lotes gravados como antes


# ---------- Cards ----------


def test_cards_somam_itens_e_frete_sem_ratear_o_frete(estoque):
    # Compra mista: filamento 269,70 + acessório 14,00 + frete 20,00 = 303,70.
    registrar(estoque, filamento(0), item(1, "acessorio"), frete="R$ 20,00")
    registrar(estoque, filamento(0, valor_unitario="R$ 10,00"))  # 30,00, sem frete
    registrar(estoque, item(0, "embalagem", valor_total="R$ 5,00"), frete="R$ 2,00")

    html = client.get("/compras").text

    assert "R$ 340,70 3 compras" in card(html, "compras")  # 303,70 + 30,00 + 7,00
    assert "R$ 299,70 2 compras" in card(html, "filamento")  # 269,70 + 30,00
    assert "R$ 14,00 1 compra " in card(html, "acessorio")
    assert "R$ 5,00 1 compra " in card(html, "embalagem")


def test_compra_mista_conta_uma_vez_em_compras_e_em_cada_categoria(estoque):
    registrar(
        estoque,
        filamento(0),
        filamento(1, cor="Azul"),
        item(2, "acessorio"),
        item(3, "acessorio", nome="Outro"),
    )

    html = client.get("/compras").text

    assert " 1 compra " in card(html, "compras")
    assert " 1 compra " in card(html, "filamento") and " 1 compra " in card(html, "acessorio")
    assert " 0 compras " in card(html, "embalagem")
    assert "R$ 539,40" in card(html, "filamento")  # duas linhas de 269,70
    assert "R$ 28,00" in card(html, "acessorio")


def test_cards_consideram_todo_o_periodo_mesmo_com_a_tabela_paginada(estoque):
    for _ in range(23):
        registrar(estoque, item(0, "acessorio", quantidade="1", valor_total="R$ 1,00"))

    html = client.get("/compras").text

    assert "R$ 23,00 23 compras" in card(html, "compras")
    assert html.count('class="compra-registro"') == 20
    assert "Página 1 de 2" in html
    pagina2 = client.get("/compras?pagina=2").text
    assert pagina2.count('class="compra-registro"') == 3
    assert "R$ 23,00 23 compras" in card(pagina2, "compras")
    assert "Página 2 de 2" in pagina2 and 'rel="prev"' in pagina2
    assert client.get("/compras?pagina=99").text.count('class="compra-registro"') == 3
    assert client.get("/compras?pagina=abc").text.count('class="compra-registro"') == 20


# ---------- Filtro de período ----------


def compras_em_meses(estoque):
    registrar(estoque, item(0, "acessorio", nome="De agosto"), data_compra="30/08/2026")
    registrar(estoque, item(0, "acessorio", nome="Início de setembro"), data_compra="01/09/2026")
    registrar(estoque, item(0, "embalagem", nome="Fim de setembro"), data_compra="30/09/2026")
    registrar(estoque, item(0, "acessorio", nome="De outubro"), data_compra="01/10/2026")
    registrar(estoque, item(0, "acessorio", nome="De dezembro"), data_compra="31/12/2026")


def test_filtro_lista_todos_e_os_meses_com_compras(estoque):
    compras_em_meses(estoque)

    html = client.get("/compras").text

    opcoes = re.findall(r'<option value="([^"]+)"[^>]*>([^<]+)</option>', html)
    assert opcoes[:5] == [
        ("todos", "Todos"),
        ("2026-12", "Dezembro/2026"),
        ("2026-10", "Outubro/2026"),
        ("2026-09", "Setembro/2026"),
        ("2026-08", "Agosto/2026"),
    ]
    assert '<option value="todos" selected>' in html
    assert "5 compras" in card(html, "compras")


def test_filtro_atualiza_cards_e_tabela_pela_data_da_compra(estoque):
    compras_em_meses(estoque)

    html = client.get("/compras?periodo=2026-09").text

    assert '<option value="2026-09" selected>' in html
    assert "Compras de Setembro/2026" in html
    assert "Início de setembro" in html and "Fim de setembro" in html
    assert "De agosto" not in html and "De outubro" not in html  # limites do mês
    assert "R$ 28,00 2 compras" in card(html, "compras")
    assert "R$ 14,00 1 compra " in card(html, "acessorio")
    assert "R$ 14,00 1 compra " in card(html, "embalagem")
    assert " 0 compras " in card(html, "filamento")


def test_dezembro_vai_ate_o_fim_do_ano(estoque):
    compras_em_meses(estoque)

    html = client.get("/compras?periodo=2026-12").text

    assert "De dezembro" in html and "De outubro" not in html
    assert "1 compra " in card(html, "compras")


def test_periodo_sem_compras_difere_de_falha_de_leitura(estoque):
    compras_em_meses(estoque)

    resposta = client.get("/compras?periodo=2026-11")

    assert resposta.status_code == 200
    html = resposta.text
    assert "Nenhuma compra em Novembro/2026" in html
    assert "R$ 0,00 0 compras" in card(html, "compras")
    assert '<option value="2026-11" selected>' in html
    assert "<table" not in html and "Não foi possível carregar" not in html


@pytest.mark.parametrize("periodo", ["", "todos", "2026-13", "2026-00", "0000-01", "x", "2026-9"])
def test_periodo_invalido_vale_todos(estoque, periodo):
    compras_em_meses(estoque)

    html = client.get("/compras", params={"periodo": periodo}).text

    assert "5 compras" in card(html, "compras")
    assert '<option value="todos" selected>' in html


def test_paginas_preservam_o_periodo_e_a_ordem(estoque):
    for _ in range(21):
        registrar(estoque, item(0, "acessorio"), data_compra="05/09/2026")
    registrar(estoque, item(0, "acessorio"), data_compra="05/10/2026")

    html = client.get("/compras?periodo=2026-09").text

    assert "21 compras" in card(html, "compras")
    assert 'href="/compras?periodo=2026-09&amp;ordem=recentes&amp;pagina=2"' in html
    html = client.get("/compras?periodo=2026-09&ordem=antigas").text
    assert 'href="/compras?periodo=2026-09&amp;ordem=antigas&amp;pagina=2"' in html


def test_leitura_pede_o_periodo_ao_repositorio(estoque):
    chamadas = []
    original = estoque.listar_compras
    estoque.listar_compras = lambda *a: chamadas.append(a) or original(*a)

    client.get("/compras?periodo=2026-12")
    client.get("/compras")

    assert chamadas == [(date(2026, 12, 1), date(2027, 1, 1)), ()]


# ---------- Tabela ----------


def test_tabela_tem_colunas_da_lista_de_pedidos_e_ordem_recente_primeiro(estoque):
    registrar(estoque, item(0, "acessorio", nome="Primeira"), data_compra="10/09/2026")
    registrar(estoque, item(0, "acessorio", nome="Antiga"), data_compra="01/08/2026")
    registrar(estoque, item(0, "acessorio", nome="Segunda"), data_compra="10/09/2026")
    registrar(estoque, item(0, "acessorio", nome="Nova"), data_compra="05/10/2026")

    html = client.get("/compras").text

    assert 'class="tabela-pedidos tabela-compras"' in html
    cabecalhos = re.findall(r'<th scope="col"[^>]*>\s*(?:<span[^>]*>\s*<span>)?([^<]+)<', html)
    assert cabecalhos == ["Data", "Local", "Itens", "Fotos", "Valor total", "Ações"]
    ordem = sorted(("Primeira", "Antiga", "Segunda", "Nova"), key=html.index)
    assert ordem == ["Nova", "Segunda", "Primeira", "Antiga"]
    assert "Últimas compras" not in html and 'class="compras-lista"' not in html


def test_linha_mostra_data_local_total_com_frete_e_itens(estoque):
    registrar(
        estoque,
        filamento(0, cor="Preto", material="PLA", tipo="silk", marca="Marca X", quantidade="3"),
        item(1, "embalagem", nome="Caixa P", quantidade="5", valor_total="R$ 10,00"),
        local="loja_fisica",
        local_nome="Papelaria Centro",
        frete="R$ 12,50",
        data_compra="10/09/2026",
    )

    html = client.get("/compras").text

    assert "10/09/2026" in html and 'datetime="2026-09-10"' in html
    assert "Loja Física · Papelaria Centro" in html
    assert "R$ 292,20" in html  # 269,70 + 10,00 + 12,50
    assert "inclui frete de R$ 12,50" in html
    assert "3 rolos · PLA Silk Preto · Marca X · 1.000 g" in html
    assert "5 un · Caixa P" in html


def test_compra_sem_frete_nao_menciona_frete(estoque):
    registrar(estoque, item(0, "acessorio"))

    assert "inclui frete" not in client.get("/compras").text


def test_texto_da_compra_e_escapado(estoque):
    registrar(estoque, item(0, "acessorio", nome="<b>Argola</b>"))

    html = client.get("/compras").text

    assert "<b>Argola</b>" not in html and "&lt;b&gt;Argola&lt;/b&gt;" in html


# ---------- Fotos ----------


def test_fotos_viram_botoes_de_previa_com_url_assinada(estoque):
    registrar(estoque, filamento(0), item(1, "acessorio"), fotos={0: ("a.png", PNG)})

    html = client.get("/compras").text

    assert html.count('class="foto-previa-botao"') == 1
    assert html.count('src="https://storage.exemplo/assinada/compras/') == 1
    assert "token=t" in html
    assert "Foto de referência: 3 rolos · PLA Silk Preto" in html
    assert 'aria-label="Ampliar a foto de 3 rolos' in html
    assert "Sem foto" not in html  # a compra tem ao menos uma foto


def test_scripts_da_previa_com_lupa_sao_carregados(estoque):
    html = client.get("/compras").text

    assert html.index("js/foto-previa.js") < html.index("js/compras-lista.js")
    assert "FotoPrevia.iniciar(tabela, { lupa: true })" in (
        Path("app/static/js/compras-lista.js").read_text(encoding="utf-8")
    )


def test_compra_sem_foto_mostra_estado_discreto(estoque):
    registrar(estoque, item(0, "acessorio"))

    html = client.get("/compras").text

    assert "Sem foto" in html and "foto-previa-botao" not in html


def test_falha_ao_assinar_fotos_mantem_a_lista_e_avisa(estoque):
    registrar(estoque, item(0, "acessorio"), fotos={0: ("a.png", PNG)})
    estoque.falhar_em.add("assinar")

    resposta = client.get("/compras")

    assert resposta.status_code == 200
    assert "Indisponível" in resposta.text and "Argola Teste" in resposta.text
    assert "foto-previa-botao" not in resposta.text


# ---------- Valores ----------


def test_valores_de_soma_usam_decimal_exato(estoque):
    registrar(estoque, filamento(0, quantidade="3", valor_unitario="R$ 0,10"))

    assert estoque.listar_compras().compras[0].total == Decimal("0.30")
