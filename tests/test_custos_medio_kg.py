"""Custos Diretos: custo médio por kg dos filamentos, a partir das compras do estoque."""

from decimal import Decimal

from fastapi.testclient import TestClient

from app.custos import custo_medio_por_kg
from app.estoque_repositorio import get_repositorio_estoque
from app.main import app
from tests.fake_estoque import RepositorioEstoqueMemoria

client = TestClient(app)


def estoque_com_compras():
    repo = RepositorioEstoqueMemoria()
    # PLA sólido: 2 kg a R$ 100/kg + 1 kg a R$ 130/kg (o saldo não pesa: o uso não muda a média).
    repo.com_filamento(tipo="solido", peso_g=Decimal("500"), peso_comprado_g=Decimal("2000"))
    repo.com_filamento(
        tipo="silk",
        peso_g=Decimal("1000"),
        peso_comprado_g=Decimal("1000"),
        valor_unitario=Decimal("130"),
    )
    repo.com_filamento(tipo="bicolor", valor_unitario=Decimal("125"))
    repo.com_filamento(material="TPU", valor_unitario=Decimal("999"))  # sem grupo
    return repo


def test_media_ponderada_pelo_peso_comprado_e_por_grupo():
    repo = estoque_com_compras()

    petg, pla, multicolor = custo_medio_por_kg(repo.listar_estoque().filamentos)

    assert (petg.custo_kg, petg.compras) == (None, 0)
    assert pla.titulo == "PLA (Sólido, Velvet, Silk)"
    assert pla.custo_kg == Decimal("330") / 3  # (2 x 100 + 1 x 130) / 3 kg
    assert (pla.kg_comprados, pla.compras) == (3, 2)
    assert (multicolor.custo_kg, multicolor.compras) == (Decimal("125"), 1)


def test_rolo_de_outro_peso_usa_o_valor_por_kg():
    repo = RepositorioEstoqueMemoria()
    repo.com_filamento(
        material="PETG",
        peso_rolo_g=Decimal("750"),
        valor_unitario=Decimal("60"),
        peso_comprado_g=Decimal("1500"),
    )

    (petg, *_) = custo_medio_por_kg(repo.listar_estoque().filamentos)

    assert petg.custo_kg == Decimal("80")  # 2 rolos x R$ 60 / 1,5 kg


def test_tela_mostra_a_media_de_cada_grupo():
    app.dependency_overrides[get_repositorio_estoque] = lambda: estoque_com_compras()

    html = client.get("/custos").text

    assert "Custo médio por kg (compras do estoque)" in html
    assert "PLA Multicolor (Bicolor, Tricolor)" in html
    assert "R$ 125,00" in html and "Nenhuma compra registrada" in html
    assert html.index('id="filamentos"') < html.index('id="custo-medio-kg"')
    assert html.index('id="custo-medio-kg"') < html.index('id="energia"')


def test_tela_continua_sem_o_estoque():
    repo = RepositorioEstoqueMemoria()
    repo.falhar = True
    app.dependency_overrides[get_repositorio_estoque] = lambda: repo

    resposta = client.get("/custos")

    assert resposta.status_code == 200
    assert "Não foi possível carregar as compras do estoque agora." in resposta.text
    assert 'id="filamentos"' in resposta.text
