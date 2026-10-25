"""Tela Compras (/compras): resumo do histórico e últimas compras.

Repositório falso em memória; nenhum teste acessa o Supabase.
"""

from decimal import Decimal

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


def trecho(html: str, id_: str, fim: str = "</p>") -> str:
    inicio = html.index(f'id="{id_}"')
    return html[inicio : html.index(fim, inicio)]


# ---------- Estado vazio, falha e botão ----------


def test_sem_compras_mostra_cards_zerados_e_mensagem(estoque):
    resposta = client.get("/compras")

    assert resposta.status_code == 200
    assert resposta.headers["cache-control"] == "no-store"
    html = resposta.text
    assert "<h1>Compras</h1>" in html
    assert "R$ 0,00" in trecho(html, "compras-total")
    assert ">0" in trecho(html, "compras-quantidade")
    assert "Nenhuma compra registrada" in html


def test_falha_de_leitura_mostra_indisponibilidade_sem_zeros(estoque):
    estoque.falhar = True

    resposta = client.get("/compras")

    assert resposta.status_code == 503
    assert "Não foi possível carregar as compras agora" in resposta.text
    for ausente in ("compras-total", "compras-quantidade", "R$ 0,00", "Nenhuma compra registrada"):
        assert ausente not in resposta.text
    assert "compra-janela" not in resposta.text


def test_botao_registrar_compra_e_janela_preservados(estoque):
    html = client.get("/compras").text

    assert html.count("data-abrir-compra") == 1
    assert html.count(">Registrar Compra</button>") == 1
    for antiga in ('id="filamentos"', 'id="acessorios"', 'id="embalagens"', "<table"):
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


# ---------- Resumo e lista ----------


def test_cards_somam_itens_e_frete_de_todas_as_compras(estoque):
    # 3 x 89,90 = 269,70 + 14,00 + frete 20,00 = 303,70 · 3 x 10 = 30,00 (sem frete)
    registrar(estoque, filamento(0), item(1, "acessorio"), frete="R$ 20,00")
    registrar(estoque, filamento(0, valor_unitario="R$ 10,00"))

    html = client.get("/compras").text

    assert "R$ 333,70" in trecho(html, "compras-total")
    assert ">2" in trecho(html, "compras-quantidade")


def test_cards_consideram_todo_o_historico_mesmo_com_a_lista_limitada(estoque):
    for _ in range(23):
        registrar(estoque, item(0, "acessorio", quantidade="1", valor_total="R$ 1,00"))

    html = client.get("/compras").text

    assert "R$ 23,00" in trecho(html, "compras-total")
    assert ">23" in trecho(html, "compras-quantidade")
    assert html.count('class="compra-registro"') == 20


def test_ultimas_compras_por_data_decrescente_e_empate_pelo_mais_recente(estoque):
    registrar(estoque, item(0, "acessorio", nome="Primeira"), data_compra="10/09/2026")
    registrar(estoque, item(0, "acessorio", nome="Antiga"), data_compra="01/08/2026")
    registrar(estoque, item(0, "acessorio", nome="Segunda"), data_compra="10/09/2026")
    registrar(estoque, item(0, "acessorio", nome="Nova"), data_compra="05/10/2026")

    html = client.get("/compras").text

    ordem = sorted(("Primeira", "Antiga", "Segunda", "Nova"), key=html.index)
    assert ordem == ["Nova", "Segunda", "Primeira", "Antiga"]


def test_compra_mostra_data_local_total_com_frete_e_itens(estoque):
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


def test_fotos_aparecem_como_miniaturas_assinadas(estoque):
    registrar(estoque, filamento(0), item(1, "acessorio"), fotos={0: ("a.png", PNG)})

    html = client.get("/compras").text

    assert html.count('<img src="https://storage.exemplo/assinada/compras/') == 1
    assert "token=t" in html
    assert "Foto de referência: 3 rolos · PLA Silk Preto" in html
    assert "Sem foto de referência" not in html  # a compra tem ao menos uma foto


def test_compra_sem_foto_mostra_estado_discreto(estoque):
    registrar(estoque, item(0, "acessorio"))

    html = client.get("/compras").text

    assert "Sem foto de referência" in html and '<img src="https://storage' not in html


def test_falha_ao_assinar_fotos_mantem_a_lista(estoque):
    registrar(estoque, item(0, "acessorio"), fotos={0: ("a.png", PNG)})
    estoque.falhar_em.add("assinar")

    resposta = client.get("/compras")

    assert resposta.status_code == 200
    assert "Sem foto de referência" in resposta.text and "Argola Teste" in resposta.text


# ---------- Redirecionamento e migration ----------


def test_valores_de_soma_usam_decimal_exato(estoque):
    registrar(estoque, filamento(0, quantidade="3", valor_unitario="R$ 0,10"))

    assert estoque.listar_compras().total_gasto == Decimal("0.30")
