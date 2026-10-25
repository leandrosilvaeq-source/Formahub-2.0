"""Tela Estoque (/estoque): categorias, agrupamento dos lotes e médias ponderadas.

Repositório falso em memória; nenhum teste acessa o Supabase.
"""

import re
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.estoque_repositorio import get_repositorio_estoque
from app.main import app
from tests.fake_estoque import RepositorioEstoqueMemoria
from tests.test_estoque_compra import PNG, compra, enviar, filamento, item

client = TestClient(app)

VALOR_DO_CARD = '<p class="compras-card-valor">{}</p>'


@pytest.fixture
def estoque():
    repo = RepositorioEstoqueMemoria()
    app.dependency_overrides[get_repositorio_estoque] = lambda: repo
    return repo


def linhas(html: str) -> list[str]:
    """Texto de cada linha do corpo da tabela, com as células separadas por ' | '."""
    corpo = html.split("<tbody>")[1].split("</tbody>")[0]
    resultado = []
    for tr in re.findall(r"<tr>(.*?)</tr>", corpo, re.S):
        celulas = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
        resultado.append(" | ".join(" ".join(re.sub(r"<[^>]+>", " ", c).split()) for c in celulas))
    return resultado


def media(html: str, titulo: str) -> str:
    rotulo = "Multicolor" if titulo == "PLA Multicolor" else titulo
    return re.search(rf"Custo/kg {rotulo}</p>\s*<p[^>]*>([^<]*)</p>", html).group(1)


def compra_mista():
    """Uma compra alimentando as três categorias, com foto no primeiro filamento."""
    resposta = enviar(
        compra(
            filamento(0, quantidade="2", valor_unitario="R$ 100,00"),
            item(1, "acessorio", nome="Argola", quantidade="10", valor_total="R$ 20,00"),
            item(2, "embalagem", nome="Caixa", quantidade="5", valor_total="R$ 15,00"),
        ),
        fotos={0: ("f.png", PNG)},
    )
    assert resposta.status_code == 200


def test_inicia_em_filamento_e_mostra_so_a_categoria_escolhida(estoque):
    compra_mista()

    html = client.get("/estoque").text
    assert html.count('aria-current="true"') == 1
    assert "Custo médio por kg" in html and "Custo médio por unidade" not in html
    assert len(linhas(html)) == 1
    assert linhas(html)[0].endswith(
        "2 rolos | PLA | Preto | Silk | 2.000 g (rolo de 1.000 g) | R$ 100,00"
    )

    acessorios = client.get("/estoque?categoria=acessorios").text
    assert [t.split(" | ", 1)[1] for t in linhas(acessorios)] == ["Argola | 10 un | R$ 2,00"]
    assert "Peso total<" not in acessorios
    embalagens = client.get("/estoque?categoria=embalagens").text
    assert [t.split(" | ", 1)[1] for t in linhas(embalagens)] == ["Caixa | 5 un | R$ 3,00"]
    assert linhas(client.get("/estoque?categoria=xyz").text) == linhas(html)


def test_compra_mista_alimenta_o_estoque_sem_duplicar(estoque):
    compra_mista()

    assert len(estoque.filamentos) == 1 and len(estoque.itens) == 2
    for caminho in ("", "?categoria=acessorios", "?categoria=embalagens"):
        assert len(linhas(client.get(f"/estoque{caminho}").text)) == 1


def test_foto_privada_vem_da_compra_com_url_assinada(estoque):
    compra_mista()

    html = client.get("/estoque").text
    assert "https://storage.exemplo/assinada/compras/1/itens/" in html
    assert "Sem foto" not in html
    assert "Sem foto" in client.get("/estoque?categoria=acessorios").text


def test_agrupa_lotes_iguais_sem_misturar_cor_tipo_material_ou_peso_do_rolo(estoque):
    estoque.com_filamento(cor="Preto", peso_g=Decimal("1000"), valor_unitario=Decimal("100"))
    estoque.com_filamento(cor=" preto ", peso_g=Decimal("500"), valor_unitario=Decimal("70"))
    estoque.com_filamento(cor="Preto", tipo="silk")
    estoque.com_filamento(cor="Preto", material="PETG")
    estoque.com_filamento(cor="Preto", peso_rolo_g=Decimal("500"), peso_g=Decimal("500"))
    estoque.com_filamento(cor="Branco")

    tabela = linhas(client.get("/estoque").text)

    assert len(tabela) == 5
    assert (
        sum("pla | preto | sólido" in t.lower() for t in tabela) == 2
    )  # rolos de 1000 g e de 500 g
    assert any("1,5 rolos | PLA | preto | Sólido | 1.500 g (rolo de 1.000 g)" in t for t in tabela)


def test_saldo_disponivel_nao_se_confunde_com_peso_comprado(estoque):
    estoque.com_filamento(peso_g=Decimal("250"), peso_comprado_g=Decimal("1000"))

    html = client.get("/estoque").text
    assert linhas(html)[0].endswith(
        "0,25 rolo | PLA | Preto | Sólido | 250 g (rolo de 1.000 g) | R$ 100,00"
    )
    assert VALOR_DO_CARD.format("250 g") in html  # peso total disponível


def test_media_de_filamento_ponderada_pelo_peso(estoque):
    # 1 kg a R$ 100/kg e 3 kg a R$ 60/kg (rolos de 500 g a R$ 30): (100 + 180) / 4 = R$ 70/kg
    estoque.com_filamento(peso_g=Decimal("1000"), valor_unitario=Decimal("100"))
    estoque.com_filamento(
        cor="Branco",
        peso_g=Decimal("1500"),
        peso_comprado_g=Decimal("3000"),
        peso_rolo_g=Decimal("500"),
        valor_unitario=Decimal("30"),
    )

    assert media(client.get("/estoque").text, "PLA") == "R$ 70,00"


def test_cards_agrupam_multicolor_e_petg_e_tpu_so_entra_no_peso(estoque):
    estoque.com_filamento(tipo="silk", valor_unitario=Decimal("100"))
    estoque.com_filamento(tipo="bicolor", valor_unitario=Decimal("200"))
    estoque.com_filamento(tipo="tricolor", valor_unitario=Decimal("400"))
    estoque.com_filamento(material="PETG", valor_unitario=Decimal("90"))
    estoque.com_filamento(material="TPU", peso_g=Decimal("500"), valor_unitario=Decimal("500"))

    html = client.get("/estoque").text

    assert media(html, "PLA") == "R$ 100,00"
    assert media(html, "PLA Multicolor") == "R$ 300,00"  # (200 + 400) / 2 kg
    assert media(html, "PETG") == "R$ 90,00"
    assert any("TPU" in t for t in linhas(html))
    assert VALOR_DO_CARD.format("4.500 g") in html  # 4 x 1000 g + 500 g de TPU


def test_sem_dados_mostra_traco_e_nao_custo_ficticio(estoque):
    html = client.get("/estoque").text

    assert "Nada em estoque" in html
    assert html.count(VALOR_DO_CARD.format("—")) == 3
    assert "R$" not in html.split("</nav>")[1].split("</main>")[0]

    estoque.com_filamento(material="TPU")  # sem PLA nem PETG: continua sem média
    assert client.get("/estoque").text.count(VALOR_DO_CARD.format("—")) == 3


def test_media_de_itens_ponderada_pela_quantidade(estoque):
    # 10 un por R$ 10 e 30 un por R$ 60 (R$ 70 no total): 70 / 40 = R$ 1,75/un
    for quantidade, total in (("10", "10,00"), ("30", "60,00")):
        enviar(
            compra(item(0, "acessorio", nome="Argola", quantidade=quantidade, valor_total=total))
        )

    tabela = linhas(client.get("/estoque?categoria=acessorios").text)
    assert [t.split(" | ", 1)[1] for t in tabela] == ["Argola | 40 un | R$ 1,75"]


def test_frete_nao_entra_na_media(estoque):
    enviar(compra(filamento(0, quantidade="1", valor_unitario="R$ 100,00"), frete="R$ 50,00"))

    assert linhas(client.get("/estoque").text)[0].endswith("R$ 100,00")


def test_falha_do_banco_mostra_aviso(estoque):
    estoque.falhar = True

    resposta = client.get("/estoque")
    assert resposta.status_code == 503
    assert "Não foi possível carregar o estoque agora" in resposta.text


def test_foto_indisponivel_nao_derruba_a_lista(estoque):
    compra_mista()
    estoque.falhar_em.add("assinar")

    resposta = client.get("/estoque")
    assert resposta.status_code == 200 and "Sem foto" in resposta.text


def card(html: str, rotulo: str) -> str:
    """O HTML do card (a abertura da div até o fim dela) com o rótulo dado."""
    return re.search(
        rf'<div class="compras-card[^"]*">(?:(?!</div>).)*{rotulo}</p>.*?</div>', html, re.S
    ).group(0)


def test_card_fica_vermelho_com_alerta_quando_a_media_passa_do_valor_de_custos(
    estoque, repo_custos
):
    # Custos: PLA R$ 100/kg, PLA Multicolor R$ 125/kg, PETG R$ 85/kg
    estoque.com_filamento(tipo="silk", valor_unitario=Decimal("100.01"))  # PLA acima
    estoque.com_filamento(tipo="bicolor", valor_unitario=Decimal("125"))  # Multicolor igual
    estoque.com_filamento(material="PETG", valor_unitario=Decimal("80"))  # PETG abaixo

    html = client.get("/estoque").text

    pla = card(html, "Custo/kg PLA")
    assert "compras-card-alerta" in pla and "R$ 0,01 acima do custo/kg em Custos" in pla
    petg = card(html, "Custo/kg PETG")
    assert "compras-card-ok" in petg and "R$ 5,00 abaixo do custo/kg em Custos" in petg
    multicolor = card(html, "Custo/kg Multicolor")  # igual ao de Custos: sem observação
    assert "compras-card-alerta" not in multicolor and "compras-card-ok" not in multicolor
    assert "do custo/kg em Custos" not in multicolor
    assert "Média ponderada" not in html


def test_sem_media_nao_ha_alerta(estoque):
    html = client.get("/estoque").text

    assert "compras-card-alerta" not in html


def test_card_de_rolos_soma_o_saldo_em_rolos(estoque):
    estoque.com_filamento(peso_g=Decimal("1500"), peso_comprado_g=Decimal("2000"))  # 1,5 rolo
    estoque.com_filamento(cor="Azul", peso_g=Decimal("250"), peso_rolo_g=Decimal("500"))  # 0,5

    html = client.get("/estoque").text

    rolos = card(html, "Rolos disponíveis")
    assert VALOR_DO_CARD.format("2") in rolos
    assert "PLA: 2" in rolos and "PETG" not in rolos and "Multicolor" not in rolos  # sem zerados


def test_comentario_dos_rolos_por_material(estoque):
    estoque.com_filamento(peso_g=Decimal("1000"))
    estoque.com_filamento(cor="Rosa", material="PETG", peso_g=Decimal("500"))
    estoque.com_filamento(
        cor="Duo", tipo="bicolor", peso_g=Decimal("2000"), peso_rolo_g=Decimal("1000")
    )
    estoque.com_filamento(cor="Flex", material="TPU")

    assert "PLA: 1 · PETG: 0,5 · PLA Multicolor: 2</p>" in card(
        client.get("/estoque").text, "Rolos disponíveis"
    )
