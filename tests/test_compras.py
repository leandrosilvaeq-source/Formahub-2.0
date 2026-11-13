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


def pagina(html: str) -> str:
    """A tela sem a janela Registrar Compra (que traz sugestões de todas as compras)."""
    return html[: html.index('<dialog id="compra-janela"')]


def card(html: str, chave: str) -> str:
    """Texto do card (compras, filamento, acessorio ou embalagem), sem as marcas HTML."""
    inicio = html.index(f'id="card-{chave}"')
    bloco = html[html.index(">", inicio) + 1 : html.index("</div>", inicio)]
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", bloco)).strip()


# ---------- Estado vazio, falha e botão ----------


def test_sem_compras_mostra_seis_cards_zerados_e_mensagem(estoque):
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
        ("peca_manutencao", "Peças para manutenção"),
        ("outro", "Itens gerais"),
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
    registrar(
        estoque, item(0, "embalagem", quantidade="10", valor_total="R$ 5,00"), frete="R$ 2,00"
    )

    html = client.get("/compras").text

    assert "R$ 340,70 3 compras" in card(html, "compras")  # 303,70 + 30,00 + 7,00
    assert "R$ 299,70 2 compras" in card(html, "filamento")  # 269,70 + 30,00
    assert "R$ 14,00 1 compra " in card(html, "acessorio")
    assert "R$ 5,00 1 compra " in card(html, "embalagem")


def test_cards_das_categorias_novas_e_total_sem_contar_item_duas_vezes(estoque):
    # filamento 269,70 + peça 30,00 + outro 7,50 + frete 10,00 = 317,20.
    registrar(
        estoque,
        filamento(0),
        item(1, "peca_manutencao", nome="Bico", quantidade="2", valor_total="R$ 30,00"),
        item(2, "outro", nome="Fita", quantidade="1", valor_total="R$ 7,50"),
        frete="R$ 10,00",
    )
    registrar(estoque, item(0, "peca_manutencao", nome="Correia", valor_total="R$ 12,00"))

    html = client.get("/compras").text

    assert "R$ 329,20 2 compras" in card(html, "compras")  # 317,20 + 12,00
    assert "R$ 42,00 2 compras" in card(html, "peca_manutencao")
    assert "R$ 7,50 1 compra " in card(html, "outro")
    assert "R$ 269,70 1 compra " in card(html, "filamento")
    assert "2 un · Bico" in html and "1 un · Fita" in html and "40 un · Correia" in html


def test_janela_tem_lista_unica_com_as_cinco_categorias(estoque):
    html = client.get("/compras").text

    assert html.count('<template id="modelo-') == 1  # um só modelo de linha
    modelo = html[html.index('<template id="modelo-item">') :]
    modelo = modelo[: modelo.index("</template>")]
    for codigo, rotulo in (
        ("filamento", "Filamento"),
        ("acessorio", "Acessórios"),
        ("embalagem", "Embalagens"),
        ("peca_manutencao", "Peças para manutenção"),
        ("outro", "Itens gerais"),
    ):
        assert f'<option value="{codigo}">{rotulo}</option>' in modelo
    for campo in (
        "categoria",
        "nome",
        "material",
        "cor",
        "tipo",
        "peso_rolo",
        "quantidade",
        "marca",
        "medidas",
        "valor_unitario",
    ):
        assert f'data-campo="{campo}"' in modelo
    assert 'data-campo="valor_total"' not in modelo
    assert "upload-item" in modelo
    assert "compra-item-duplicar" in modelo and "compra-item-remover" in modelo
    assert html.count("data-adicionar") == 1 and ">+ Adicionar item</button>" in html
    # Campos de cada categoria (na ordem) vêm do servidor.
    assert '"peca_manutencao": ["nome", "quantidade", "marca", "valor_unitario"]' in html
    assert 'name="desconto"' in html and 'name="frete"' in html


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
    tela = pagina(html)
    assert "Início de setembro" in tela and "Fim de setembro" in tela
    assert "De agosto" not in tela and "De outubro" not in tela  # limites do mês
    assert "R$ 28,00 2 compras" in card(html, "compras")
    assert "R$ 14,00 1 compra " in card(html, "acessorio")
    assert "R$ 14,00 1 compra " in card(html, "embalagem")
    assert " 0 compras " in card(html, "filamento")


def test_dezembro_vai_ate_o_fim_do_ano(estoque):
    compras_em_meses(estoque)

    html = client.get("/compras?periodo=2026-12").text

    assert "De dezembro" in pagina(html) and "De outubro" not in pagina(html)
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

    # Com período, uma segunda leitura (todas as compras) alimenta as sugestões da janela.
    assert chamadas == [(date(2026, 12, 1), date(2027, 1, 1)), (), ()]


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
    assert "FotoPrevia.iniciar(lista, { lupa: true })" in (
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


# ---------- Filtro de local ----------


def test_filtro_de_local_lista_todos_e_os_locais(estoque):
    html = client.get("/compras").text

    assert 'id="compras-local" name="local"' in html
    assert '<option value="todos" selected>Todos</option>' in html
    assert '<option value="shopee">Shopee</option>' in html


def test_filtro_de_local_atualiza_cards_e_tabela(estoque):
    registrar(estoque, item(0, "acessorio", nome="Da Shopee"), local="shopee")
    registrar(estoque, item(0, "acessorio", nome="Do Mercado"), local="mercado_livre")

    html = client.get("/compras?local=shopee").text

    assert '<option value="shopee" selected>' in html
    tela = pagina(html)
    assert "Da Shopee" in tela and "Do Mercado" not in tela
    assert "1 compra " in card(html, "compras")


def test_filtro_de_local_combina_com_o_periodo(estoque):
    registrar(estoque, item(0, "acessorio", nome="Shopee setembro"), local="shopee")
    outubro = {"local": "shopee", "data_compra": "05/10/2026"}
    registrar(estoque, item(0, "acessorio", nome="Shopee outubro"), **outubro)
    registrar(estoque, item(0, "acessorio", nome="Mercado setembro"))

    tela = pagina(client.get("/compras?periodo=2026-09&local=shopee").text)

    assert "Shopee setembro" in tela
    assert "Shopee outubro" not in tela and "Mercado setembro" not in tela


def test_local_sem_compras_mostra_mensagem(estoque):
    registrar(estoque, item(0, "acessorio"))

    html = client.get("/compras?local=aliexpress").text

    assert "Nenhuma compra em AliExpress" in html
    assert "R$ 0,00 0 compras" in card(html, "compras")


@pytest.mark.parametrize("local", ["", "x", "LOJA"])
def test_local_invalido_vale_todos(estoque, local):
    registrar(estoque, item(0, "acessorio"))

    html = client.get("/compras", params={"local": local}).text

    assert "1 compra " in card(html, "compras")


def test_ordem_e_paginas_preservam_o_local(estoque):
    for _ in range(21):
        registrar(estoque, item(0, "acessorio"), local="shopee")

    html = client.get("/compras?local=shopee").text

    assert 'href="/compras?periodo=todos&amp;ordem=antigas&amp;local=shopee"' in html
    assert 'href="/compras?periodo=todos&amp;ordem=recentes&amp;pagina=2&amp;local=shopee"' in html
