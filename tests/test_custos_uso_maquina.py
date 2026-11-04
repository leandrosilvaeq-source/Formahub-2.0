"""Uso da Impressora: edição de Energia, Depreciação e Manutenção em "Ver detalhes".

Os cards de Energia, Depreciação e Manutenção não existem mais na tela; os formulários e as rotas
são os de sempre, agora dentro do card Uso da Impressora. Repositório de custos em memória.
"""

import re
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.custos import (
    AQUISICAO,
    CONSUMO_KWH,
    DEPRECIACAO_MANUAL,
    ENERGIA_MANUAL,
    MANUTENCAO,
    RESIDUAL,
    TARIFA,
    VIDA_UTIL,
)
from app.custos_repositorio import GastoManutencao, PeriodoManutencao
from app.estoque_repositorio import get_repositorio_estoque
from app.main import app
from tests.fake_estoque import RepositorioEstoqueMemoria

client = TestClient(app)
CSRF = "csrf-de-teste"


@pytest.fixture(autouse=True)
def estoque():
    repo = RepositorioEstoqueMemoria()
    app.dependency_overrides[get_repositorio_estoque] = lambda: repo
    return repo


def enviar(caminho, dados=None, **extra):
    corpo = {"csrf": CSRF, **(dados or {}), **extra}
    return client.post(caminho, data=corpo, follow_redirects=False)


def uso(**params):
    html = client.get("/custos", params=params).text
    inicio = html.index('id="uso-maquina"')
    return html[inicio : html.index("\n</li>", inicio)]


def texto(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def com_manutencao(repo_custos, manual=None):
    repo_custos.periodo = PeriodoManutencao(date(2026, 8, 3), date(2026, 11, 1), Decimal("902.4"))
    repo_custos.gastos = [GastoManutencao(1, date(2026, 10, 29), "Corretiva", Decimal("107.62"))]
    repo_custos.parametros[MANUTENCAO] = manual


def test_cards_removidos_continuam_removidos_e_o_uso_traz_a_edicao():
    html = client.get("/custos").text

    for identificador in ("energia", "depreciacao", "manutencao"):
        assert f'id="{identificador}"' not in html
    detalhes = uso()
    for titulo in ("Energia elétrica", "Depreciação gerencial", "Manutenção"):
        assert f">{titulo}</h5>" in detalhes
    assert "Para alterar, use os campos abaixo." in texto(detalhes)
    assert "edite o card" not in html


@pytest.mark.parametrize(
    ("editar", "campos"),
    [
        ("valores-energia", {"consumo_kwh": "40", "tarifa_kwh": "1,0393"}),
        (
            "valores-depreciacao",
            {"valor_aquisicao": "5.000,00", "valor_residual": "0,00", "vida_util_anos": "5"},
        ),
        ("valores-energia-ajuste", {"valor": "0,14"}),
        ("valores-depreciacao-ajuste", {"valor": "0,27"}),
        ("valores-manutencao-ajuste", {"valor": ""}),
    ],
)
def test_cada_link_abre_o_formulario_certo_dentro_do_uso(editar, campos):
    detalhes = uso(editar=editar)

    assert f'action="/custos/valores/{editar.removeprefix("valores-")}"' in detalhes
    assert "<details" in detalhes and " open" in detalhes.split("Ver detalhes")[0]
    for nome, valor in campos.items():
        assert f'name="{nome}"' in detalhes and f'value="{valor}"' in detalhes


def test_periodo_e_horas_da_manutencao_abrem_no_uso(repo_custos):
    com_manutencao(repo_custos)

    detalhes = uso(editar="manutencao-periodo")

    assert 'action="/custos/manutencao/periodo"' in detalhes and " open" in detalhes
    for nome, valor in (("inicio", "2026-08-03"), ("fim", "2026-11-01"), ("horas", "902,4")):
        assert f'name="{nome}"' in detalhes and f'value="{valor}"' in detalhes


def test_sem_periodo_oferece_definir_e_a_referencia_para_confirmar(repo_custos):
    detalhes = uso()

    assert "Definir período e horas" in detalhes
    assert "/custos?editar=manutencao-periodo#uso-maquina" in detalhes
    assert "Confirmar referência" in detalhes


def test_com_periodo_mostra_periodo_e_horas_e_oferece_editar(repo_custos):
    com_manutencao(repo_custos)

    detalhes = texto(uso())

    assert "03/08/2026 a 01/11/2026" in detalhes and "902,4 h" in detalhes
    assert "Editar período e horas" in detalhes and "Confirmar referência" not in detalhes


def test_editar_energia_recalcula_e_volta_ao_uso(repo_custos):
    resposta = enviar("/custos/valores/energia", {"consumo_kwh": "50", "tarifa_kwh": "1,20"})

    assert resposta.status_code == 303 and resposta.headers["location"].endswith("#uso-maquina")
    assert (repo_custos.parametros[CONSUMO_KWH], repo_custos.parametros[TARIFA]) == (
        Decimal(50),
        Decimal("1.20"),
    )
    assert "Calculado: ≈ R$ 0,1967/h" in texto(uso())  # 50 x 1,20 / 305


def test_editar_depreciacao_valida_residual_e_grava(repo_custos):
    invalido = enviar(
        "/custos/valores/depreciacao",
        {"valor_aquisicao": "1000", "valor_residual": "2000", "vida_util_anos": "5"},
    )
    assert invalido.status_code == 422 and repo_custos.gravacoes == []

    ok = enviar(
        "/custos/valores/depreciacao",
        {"valor_aquisicao": "6000", "valor_residual": "600", "vida_util_anos": "6"},
    )
    assert ok.status_code == 303 and ok.headers["location"].endswith("#uso-maquina")
    p = repo_custos.parametros
    assert (p[AQUISICAO], p[RESIDUAL], p[VIDA_UTIL]) == (Decimal(6000), Decimal(600), Decimal(6))


def test_ajuste_manual_muda_o_valor_utilizado_e_o_total(repo_custos):
    resposta = enviar("/custos/valores/energia-ajuste", {"valor": "0,20"})

    assert resposta.status_code == 303 and resposta.headers["location"].endswith("#uso-maquina")
    assert repo_custos.parametros[ENERGIA_MANUAL] == Decimal("0.20")
    assert "R$ 0,47" in texto(uso())  # 0,20 + 0,27 (depreciação manual); manutenção a definir


def test_ajuste_manual_zero_e_um_valor(repo_custos):
    enviar("/custos/valores/energia-ajuste", {"valor": "0"})

    assert repo_custos.parametros[ENERGIA_MANUAL] == Decimal(0)
    assert "Ajuste manual: R$ 0,00/h" in texto(uso())


def test_voltar_ao_calculado_apaga_o_ajuste_e_nao_zera(repo_custos):
    assert "Voltar ao calculado" in uso()

    resposta = enviar("/custos/valores/depreciacao-ajuste/calculado")

    assert resposta.status_code == 303 and resposta.headers["location"].endswith("#uso-maquina")
    assert repo_custos.parametros[DEPRECIACAO_MANUAL] is None
    detalhes = texto(uso())
    assert "Ajuste manual: nenhum (vale o calculado)" in detalhes
    assert "Depreciação gerencial ≈ R$ 0,2732 /h" in detalhes


def test_ajuste_da_manutencao_pelo_uso(repo_custos):
    com_manutencao(repo_custos)

    resposta = enviar("/custos/valores/manutencao-ajuste", {"valor": "0,10"})

    assert resposta.status_code == 303 and resposta.headers["location"].endswith("#uso-maquina")
    assert repo_custos.parametros[MANUTENCAO] == Decimal("0.10")
    assert "R$ 0,51" in texto(uso())  # 0,14 + 0,27 + 0,10


def test_periodo_e_horas_da_manutencao_salvam_e_validam(repo_custos):
    ok = enviar(
        "/custos/manutencao/periodo",
        {"inicio": "2026-08-03", "fim": "2026-11-01", "horas": "902,4"},
    )
    assert ok.status_code == 303 and ok.headers["location"].endswith("#uso-maquina")
    assert repo_custos.periodo == PeriodoManutencao(
        date(2026, 8, 3), date(2026, 11, 1), Decimal("902.4")
    )

    invalido = enviar(
        "/custos/manutencao/periodo", {"inicio": "2026-11-02", "fim": "2026-11-01", "horas": "0"}
    )
    assert invalido.status_code == 422
    assert "horas-erro" in invalido.text and "fim-erro" in invalido.text
    assert repo_custos.periodo.horas == Decimal("902.4")  # nada foi alterado


def test_formularios_do_uso_exigem_csrf(repo_custos):
    for caminho in (
        "/custos/valores/energia",
        "/custos/valores/depreciacao-ajuste/calculado",
        "/custos/manutencao/periodo",
    ):
        assert enviar(caminho, csrf="falso").status_code == 403
    assert repo_custos.gravacoes == []


def test_link_da_home_abre_o_ajuste_da_manutencao(repo_custos):
    com_manutencao(repo_custos, manual=Decimal("0.10"))

    home = client.get("/").text
    assert 'href="/custos?editar=valores-manutencao-ajuste#uso-maquina"' in home

    detalhes = uso(editar="valores-manutencao-ajuste")  # o link leva ao formulário aberto
    assert 'action="/custos/valores/manutencao-ajuste"' in detalhes and 'value="0,10"' in detalhes


def test_link_da_tela_manutencao_abre_o_periodo(repo_custos):
    html = client.get("/manutencao").text

    assert 'href="/custos?editar=manutencao-periodo#uso-maquina"' in html
    assert 'action="/custos/manutencao/periodo"' in uso(editar="manutencao-periodo")
