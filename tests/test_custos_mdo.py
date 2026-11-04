"""Card Mão de Obra: fórmula, comparação, edição da base e revisão trimestral.

Nenhum teste acessa o Supabase: o repositório de custos é o de memória. A data de "hoje" é
trocada por uma data fixa onde o vencimento da revisão importa.
"""

from datetime import date
from decimal import Decimal, localcontext

import pytest
from fastapi.testclient import TestClient

from app.custos import (
    MDO,
    MDO_DIAS_MES,
    MDO_HORAS_DIA,
    MDO_REVISOES,
    RETIRADA,
    comparar_monetario,
    formatar_custo_curto,
    mao_de_obra,
    mdo_calculado,
    revisao_do_mdo,
    somar_meses,
)
from app.custos_repositorio import FalhaAoSalvarCustos
from app.estoque_repositorio import get_repositorio_estoque
from app.main import app
from tests.fake_estoque import RepositorioEstoqueMemoria

client = TestClient(app)
CSRF = "csrf-de-teste"
FETCH = {"x-requested-with": "fetch", "accept": "application/json"}


@pytest.fixture(autouse=True)
def estoque():
    repo = RepositorioEstoqueMemoria()
    app.dependency_overrides[get_repositorio_estoque] = lambda: repo
    return repo


def em(monkeypatch, dia: date):
    """Fixa o "hoje" da tela Custos e da página inicial."""

    class Hoje(date):
        @classmethod
        def today(cls):
            return dia

    monkeypatch.setattr("app.custos_rotas.date", Hoje)
    monkeypatch.setattr("app.main.date", Hoje)


def enviar(caminho, dados=None, **extra):
    return client.post(
        caminho, data={"csrf": CSRF, **(dados or {}), **extra}, follow_redirects=False
    )


def cartao_mdo():
    html = client.get("/custos").text
    inicio = html.index('id="mdo"')
    return html[inicio : html.index("\n</li>", inicio)]


# ---------- Fórmula ----------


def test_formula_500_sobre_120_mantem_a_precisao_interna():
    calculado, horas_mes = mdo_calculado(Decimal(500), Decimal(4), Decimal(30))

    assert horas_mes == 120
    with localcontext() as contexto:
        contexto.prec = 60
        assert abs(calculado * 120 - 500) < Decimal("1e-40")  # não foi arredondado
    assert calculado != Decimal("4.17") and len(calculado.as_tuple().digits) > 28
    assert formatar_custo_curto(calculado) == "R$ 4,17"  # só a exibição arredonda


def test_formula_acompanha_retirada_horas_e_dias():
    assert mdo_calculado(Decimal(600), Decimal(5), Decimal(20))[0] == Decimal(6)
    assert mdo_calculado(Decimal("500"), Decimal("2.5"), Decimal(20))[0] == Decimal(10)


@pytest.mark.parametrize(
    ("retirada", "horas_dia", "dias_mes"),
    [(None, Decimal(4), Decimal(30)), (Decimal(500), None, Decimal(30)),
     (Decimal(500), Decimal(4), None), (Decimal(500), Decimal(0), Decimal(30))],
)  # fmt: skip
def test_formula_sem_dados_ou_com_zero_horas_nao_calcula(retirada, horas_dia, dias_mes):
    assert mdo_calculado(retirada, horas_dia, dias_mes)[0] is None


def test_mao_de_obra_usa_o_manual_existente_e_o_calculado_de_referencia():
    m = mao_de_obra(
        {
            MDO: Decimal(15),
            RETIRADA: Decimal(500),
            MDO_HORAS_DIA: Decimal(4),
            MDO_DIAS_MES: Decimal(30),
        }
    )

    assert m.manual == 15 and m.horas_mes == 120 and m.comparacao == "maior"
    assert formatar_custo_curto(m.calculado) == "R$ 4,17"


# ---------- Comparação na precisão monetária exibida ----------


@pytest.mark.parametrize(
    ("manual", "esperado"),
    [
        ("4.17", "igual"),  # R$ 4,17 contra 4,1666…: sem alerta
        ("4.1650", "igual"),  # meio centavo sobe para 4,17
        ("4.1649", "menor"),
        ("4.16", "menor"),
        ("4.18", "maior"),
        ("15", "maior"),
    ],
)
def test_comparacao_em_centavos(manual, esperado):
    calculado = mdo_calculado(Decimal(500), Decimal(4), Decimal(30))[0]

    assert comparar_monetario(Decimal(manual), calculado) == esperado


def test_comparacao_sem_manual_ou_sem_calculado_nao_existe():
    assert comparar_monetario(None, Decimal(4)) is None
    assert comparar_monetario(Decimal(4), None) is None


def test_card_manual_maior_e_verde_com_referencia_visivel(repo_custos):
    html = cartao_mdo()

    assert 'class="custos-fil-nome">Mão de Obra</strong>' in html and ">MDO<" not in html
    assert "custos-fil-valor-maior" in html and "R$ 15,00" in html
    assert "Calculado (referência): R$ 4,17/h" in html
    assert "Acima do custo calculado" in html


def test_card_manual_menor_e_vermelho_com_aviso_de_revisao(repo_custos):
    repo_custos.parametros[MDO] = Decimal("4.16")

    html = cartao_mdo()

    assert "custos-fil-valor-menor" in html and "custos-fil-comparacao-menor" in html
    assert "Revisar mão de obra: valor manual abaixo do calculado" in html


def test_card_manual_igual_em_centavos_e_neutro_sem_alerta(repo_custos):
    repo_custos.parametros[MDO] = Decimal("4.17")

    html = cartao_mdo()

    assert "custos-fil-valor-igual" in html and "Igual ao custo calculado" in html
    assert "Revisar" not in html and "custos-fil-valor-menor" not in html


def test_detalhes_explicam_a_retirada_sem_somar_de_novo():
    detalhes = " ".join(cartao_mdo().split("Ver detalhes")[1].split())

    assert (
        "retirada mensal de R$ 500,00 ÷ (4 horas de trabalho por dia × 30 dias por mês)" in detalhes
    )
    assert "= 120 horas/mês = aproximadamente R$ 4,17/h" in detalhes
    assert "remunera o trabalho e não deve ser somada novamente como outro custo" in detalhes


def test_o_valor_manual_existente_e_preservado(repo_custos):
    antes = dict(repo_custos.parametros)

    cartao_mdo()
    enviar("/custos/valores/mdo-base", {"retirada": "600,00", "horas_dia": "5", "dias_mes": "20"})

    assert repo_custos.parametros[MDO] == antes[MDO] == Decimal(15)


# ---------- Edição: retirada, horas/dia e dias/mês ----------


def test_editar_a_base_recalcula_o_calculado(repo_custos):
    resposta = enviar(
        "/custos/valores/mdo-base", {"retirada": "600,00", "horas_dia": "5", "dias_mes": "20"}
    )

    assert resposta.status_code == 303 and resposta.headers["location"].endswith("#mdo")
    p = repo_custos.parametros
    assert (p[RETIRADA], p[MDO_HORAS_DIA], p[MDO_DIAS_MES]) == (
        Decimal(600),
        Decimal(5),
        Decimal(20),
    )
    html = cartao_mdo()
    assert "Calculado (referência): R$ 6,00/h" in html and "= 100 horas/mês" in html


def test_formulario_de_edicao_traz_os_tres_campos_preenchidos():
    html = client.get("/custos", params={"editar": "valores-mdo-base"}).text
    bloco = html[html.index('id="mdo"') :]

    for nome, valor in (("retirada", "500,00"), ("horas_dia", "4"), ("dias_mes", "30")):
        assert f'name="{nome}"' in bloco and f'value="{valor}"' in bloco


@pytest.mark.parametrize(
    ("dados", "campo"),
    [
        ({"horas_dia": "0"}, "horas_dia"),
        ({"horas_dia": "25"}, "horas_dia"),
        ({"horas_dia": "abc"}, "horas_dia"),
        ({"dias_mes": "0"}, "dias_mes"),
        ({"dias_mes": "32"}, "dias_mes"),
        ({"dias_mes": "20,5"}, "dias_mes"),
        ({"retirada": "-1"}, "retirada"),
        ({"retirada": ""}, "retirada"),
    ],
)
def test_edicao_invalida_nao_grava_e_mostra_o_erro(repo_custos, dados, campo):
    base = {"retirada": "500,00", "horas_dia": "4", "dias_mes": "30"}

    resposta = enviar("/custos/valores/mdo-base", {**base, **dados})

    assert resposta.status_code == 422 and f"{campo}-erro" in resposta.text
    assert repo_custos.gravacoes == []


def test_edicao_do_manual_continua_direta_sem_recarregar(repo_custos):
    resposta = client.post(
        "/custos/valores/mdo",
        data={"csrf": CSRF, "mdo_hora": "4,17"},
        headers=FETCH,
        follow_redirects=False,
    )

    corpo = resposta.json()
    assert resposta.status_code == 200 and corpo["ok"] and corpo["salvo"]
    assert repo_custos.parametros[MDO] == Decimal("4.17")
    assert "custos-fil-valor-igual" in corpo["html"] and "Mão de Obra" in corpo["html"]


def test_card_traz_o_necessario_para_a_edicao_direta():
    html = cartao_mdo()

    assert 'data-rota="/custos/valores/mdo"' in html and 'data-tipo="moeda"' in html
    assert 'data-valor="15,00"' in html and 'title="Editar"' in html


# ---------- Revisão trimestral ----------


def test_meses_de_calendario():
    assert somar_meses(date(2026, 11, 1), 3) == date(2027, 2, 1)
    assert somar_meses(date(2026, 11, 1), 6) == date(2027, 5, 1)
    assert somar_meses(date(2026, 11, 1), 15) == date(2028, 2, 1)
    assert somar_meses(date(2026, 11, 30), 3) == date(2027, 2, 28)  # dia inexistente: último dia
    assert somar_meses(date(2027, 12, 31), 2) == date(2028, 2, 29)  # ano bissexto


@pytest.mark.parametrize(
    ("hoje", "confirmadas", "esperado"),
    [
        (date(2026, 11, 4), 0, (date(2027, 2, 1), False, 0)),
        (date(2027, 1, 31), 0, (date(2027, 2, 1), False, 0)),
        (date(2027, 2, 1), 0, (date(2027, 2, 1), True, 1)),
        (date(2027, 2, 10), 1, (date(2027, 5, 1), False, 1)),
        (date(2027, 5, 1), 1, (date(2027, 5, 1), True, 2)),
        (date(2027, 8, 15), 0, (date(2027, 2, 1), True, 3)),  # atrasada: confirma até o último
        (date(2027, 8, 15), None, (date(2027, 2, 1), True, 3)),
    ],
)
def test_vencimento_da_revisao(hoje, confirmadas, esperado):
    valor = None if confirmadas is None else Decimal(confirmadas)

    assert revisao_do_mdo(valor, hoje) == esperado


def test_antes_do_vencimento_mostra_a_proxima_data_sem_aviso_nem_na_home(monkeypatch):
    em(monkeypatch, date(2026, 11, 4))

    html = cartao_mdo()
    assert "Próxima revisão: 01/02/2027" in html
    assert "mdo-alerta-revisao" not in html and "Confirmar revisão" not in html
    assert "custos-alerta-mdo" not in client.get("/").text


def test_aviso_vence_em_01_02_2027_na_tela_e_na_home(monkeypatch, repo_custos):
    em(monkeypatch, date(2027, 2, 1))

    html = cartao_mdo()
    assert "mdo-alerta-revisao" in html and "pendente desde 01/02/2027" in html
    assert "Confirmar revisão" in html
    home = client.get("/").text
    assert "custos-alerta-mdo" in home and "pendente desde 01/02/2027" in home
    assert 'href="/custos#mdo"' in home


def test_abrir_a_pagina_nao_encerra_o_aviso(monkeypatch, repo_custos):
    em(monkeypatch, date(2027, 3, 10))

    for _ in range(3):
        assert "mdo-alerta-revisao" in cartao_mdo()
        assert "custos-alerta-mdo" in client.get("/").text
    assert repo_custos.gravacoes == []


def test_confirmar_encerra_o_aviso_e_mostra_a_proxima_data(monkeypatch, repo_custos):
    em(monkeypatch, date(2027, 2, 10))

    resposta = enviar("/custos/mdo/revisao")

    assert resposta.status_code == 303 and resposta.headers["location"].endswith("#mdo")
    assert repo_custos.parametros[MDO_REVISOES] == 1
    assert repo_custos.gravacoes == [("parametros", {MDO_REVISOES: Decimal(1)}, 1)]
    html = cartao_mdo()
    assert "mdo-alerta-revisao" not in html and "Próxima revisão: 01/05/2027" in html
    assert "custos-alerta-mdo" not in client.get("/").text


def test_aviso_volta_no_vencimento_seguinte(monkeypatch, repo_custos):
    em(monkeypatch, date(2027, 2, 10))
    enviar("/custos/mdo/revisao")

    em(monkeypatch, date(2027, 5, 1))

    assert "pendente desde 01/05/2027" in cartao_mdo()
    assert "custos-alerta-mdo" in client.get("/").text


def test_confirmar_sem_revisao_pendente_nao_grava(monkeypatch, repo_custos):
    em(monkeypatch, date(2026, 11, 4))

    resposta = enviar("/custos/mdo/revisao")

    assert resposta.status_code == 303 and "mdo-revisao-nenhuma" in resposta.headers["location"]
    assert repo_custos.gravacoes == []
    assert repo_custos.parametros[MDO_REVISOES] == 0


def test_confirmar_exige_csrf_e_nao_grava(monkeypatch, repo_custos):
    em(monkeypatch, date(2027, 2, 10))

    resposta = enviar("/custos/mdo/revisao", csrf="falso")

    assert resposta.status_code == 403 and repo_custos.gravacoes == []
    assert repo_custos.parametros[MDO_REVISOES] == 0


def test_falha_ao_gravar_mantem_o_aviso_ativo(monkeypatch, repo_custos):
    em(monkeypatch, date(2027, 2, 10))
    repo_custos.falhar_em["salvar_parametros"] = FalhaAoSalvarCustos()

    resposta = enviar("/custos/mdo/revisao")

    assert resposta.status_code == 503 and "mdo-alerta-revisao" in resposta.text
    assert repo_custos.parametros[MDO_REVISOES] == 0
