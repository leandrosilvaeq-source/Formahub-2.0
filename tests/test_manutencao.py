"""Tela Manutenção: leitura dos campos, duração, indicadores, rotas e integração com Custos."""

import re
from datetime import date, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.custos import MANUTENCAO, custo_de_manutencao, formatar_custo_aprox
from app.custos_repositorio import (
    FalhaAoConsultarCustos,
    GastoManutencao,
    PeriodoManutencao,
)
from app.estoque_repositorio import get_repositorio_estoque
from app.main import app
from app.manutencao import (
    formatar_data_curta,
    formatar_duracao,
    indicadores,
    ler_custo,
    ler_data,
    ler_duracao,
    ler_formulario,
    lubrificacao_pendente_desde,
    registra_lubrificacao,
)
from app.manutencao_repositorio import (
    FalhaAoConsultarManutencao,
    FalhaAoSalvarManutencao,
    Manutencao,
    ManutencaoRecusada,
)
from tests.fake_estoque import RepositorioEstoqueMemoria

client = TestClient(app)
CSRF = "csrf-de-teste"
FETCH = {"X-Requested-With": "fetch"}


@pytest.fixture
def estoque_vazio():
    app.dependency_overrides[get_repositorio_estoque] = RepositorioEstoqueMemoria


def formulario(**extra):
    base = {
        "data": "2026-10-10",
        "tipo": "corretiva",
        "tempo": "01h30min",
        "pecas": "",
        "custo": "62,50",
        "comentarios": "",
    }
    return {**base, **extra}


def enviar(caminho="/manutencao", dados=None, fetch=True, **extra):
    corpo = {"csrf": CSRF, **(dados if dados is not None else formulario()), **extra}
    return client.post(caminho, data=corpo, headers=FETCH if fetch else {}, follow_redirects=False)


def manutencao(id=1, **extra):
    base = {
        "id": id,
        "data": date(2026, 10, 10),
        "tipo": "corretiva",
        "minutos_parados": 90,
        "troca_pecas": False,
        "pecas": None,
        "custo": Decimal("62.50"),
        "comentarios": None,
        "criado_por": "Leandro",
        "criado_em": datetime(2026, 10, 10, 9, 0),
        "atualizado_por": "Leandro",
        "atualizado_em": datetime(2026, 10, 10, 9, 0),
    }
    return Manutencao(**{**base, **extra})


def texto(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


# ---------- Duração ----------


@pytest.mark.parametrize(
    ("minutos", "texto"),
    [
        (0, "00h00min"),
        (1, "00h01min"),
        (150, "02h30min"),
        (1439, "23h59min"),
        (1440, "24h00min"),  # nunca vira dias
        (1635, "27h15min"),
        (53 * 60, "53h00min"),
        (525600, "8760h00min"),
    ],
)
def test_duracao_sempre_em_horas_totais_e_minutos(minutos, texto):
    assert formatar_duracao(minutos) == texto
    assert ler_duracao(texto) == (minutos, "")  # o que se exibe também é aceito na edição


def test_gasto_antigo_sem_tempo_parado_nao_inventa_duracao():
    assert formatar_duracao(None) == "—"


@pytest.mark.parametrize(
    ("texto", "minutos"),
    [
        ("02:30", 150),  # horas:minutos
        ("27:15", 1635),  # acima de 24 h
        ("0:05", 5),
        ("00:00", 0),
        ("27h15min", 1635),
        ("27h15", 1635),
        ("27h", 1620),
        ("02h30min", 150),
        ("02d05h", 53 * 60),  # 2 dias e 5 horas
        ("02d05", 53 * 60),
        ("02d:05h", 53 * 60),
        ("02d:05", 53 * 60),
        ("2d5h", 53 * 60),
        ("02d", 48 * 60),
        ("1d02h30min", 1590),  # minutos preservados junto dos dias
        ("1d02:30", 1590),
        ("  02D05H  ", 53 * 60),
        ("02 d 05 h", 53 * 60),
        ("365d", 525600),
    ],
)
def test_ler_duracao_aceita_hh_mm_horas_totais_e_dias(texto, minutos):
    assert ler_duracao(texto) == (minutos, "")


@pytest.mark.parametrize(
    ("texto", "trecho"),
    [
        ("", "Informe o tempo parado"),
        ("   ", "Informe o tempo parado"),
        ("02:60", "minutos devem estar entre 0 e 59"),
        ("27h75min", "minutos devem estar entre 0 e 59"),
        ("02d24h", "entre 0 e 23"),
        ("02d05h60min", "minutos devem estar entre 0 e 59"),
        ("02d99", "entre 0 e 23"),
        ("90", "Use hh:mm"),  # número solto é ambíguo
        ("abc", "Use hh:mm"),
        ("-1:30", "Use hh:mm"),
        ("1,5h", "Use hh:mm"),
        ("2:3:4", "Use hh:mm"),
        ("366d", "365 dias"),
        ("8761:00", "365 dias"),
    ],
)
def test_ler_duracao_recusa_o_invalido_sem_arredondar(texto, trecho):
    total, erro = ler_duracao(texto)

    assert total is None and trecho in erro


# ---------- Campos ----------


def test_data_aceita_so_a_data_completa_do_campo_de_data():
    assert ler_data("2026-10-25") == (date(2026, 10, 25), "")
    assert ler_data("25/10/26")[1] and ler_data("")[1] and ler_data("1999-12-31")[1]
    assert formatar_data_curta(date(2026, 10, 5)) == "05/10/26"


def test_custo_vazio_e_a_definir_e_zero_e_um_custo():
    assert ler_custo("") == (None, "") and ler_custo("   ") == (None, "")
    zero, erro = ler_custo("0")
    assert zero == Decimal("0") and zero is not None and erro == ""
    assert ler_custo("R$ 1.234,56") == (Decimal("1234.56"), "")
    assert ler_custo("-1")[1] and ler_custo("1,234")[1] and ler_custo("abc")[1]


def test_formulario_valido_vira_valores_prontos_para_gravar():
    valores, erros = ler_formulario(
        formulario(tempo="1d02h30min", pecas="  Bico,   correia ", comentarios="  ok\nlinha 2  ")
    )

    assert erros == {}
    assert valores == {
        "data": date(2026, 10, 10),
        "tipo": "corretiva",
        "minutos": 1590,
        "troca_pecas": True,
        "pecas": "Bico, correia",  # várias peças no mesmo campo
        "custo": Decimal("62.50"),
        "comentarios": "ok\nlinha 2",  # quebra de linha preservada
    }


def test_peca_vazia_significa_sem_troca_e_texto_significa_troca():
    sem, erros = ler_formulario(formulario(pecas="   "))
    assert erros == {} and sem["pecas"] is None and sem["troca_pecas"] is False

    com, erros = ler_formulario(formulario(pecas="Bico"))
    assert erros == {} and com["pecas"] == "Bico" and com["troca_pecas"] is True

    _, erros = ler_formulario(formulario(pecas="x" * 301))
    assert "pecas" in erros


def test_erros_por_campo_sem_valores_parciais():
    valores, erros = ler_formulario(
        formulario(data="", tipo="outra", tempo="", custo="-5", comentarios="x" * 1001)
    )

    assert valores is None
    assert set(erros) == {"data", "tipo", "tempo", "custo", "comentarios"}


def test_custo_vazio_e_zero_chegam_diferentes_ao_banco():
    vazio, _ = ler_formulario(formulario(custo=""))
    zero, _ = ler_formulario(formulario(custo="0"))

    assert vazio["custo"] is None and zero["custo"] == Decimal("0")


# ---------- Indicadores ----------


def test_indicadores_somam_todos_os_registros():
    lista = [
        manutencao(1, minutos_parados=90, custo=Decimal("62.50")),
        manutencao(2, minutos_parados=1590, custo=Decimal("0")),
        manutencao(3, minutos_parados=30, custo=None),
        manutencao(4, minutos_parados=None, tipo=None, custo=Decimal("10")),  # gasto antigo
    ]

    ind = indicadores(lista)

    assert ind.numero == 4
    assert ind.minutos_parados == 90 + 1590 + 30
    assert ind.custo_conhecido == Decimal("72.50") and ind.sem_custo == 1
    assert not ind.custo_completo and not ind.custo_indefinido


def test_indicadores_vazios_ou_so_com_custo_a_definir():
    assert indicadores([]).custo_completo and not indicadores([]).custo_indefinido
    so_definir = indicadores([manutencao(1, custo=None), manutencao(2, custo=None)])
    assert so_definir.custo_indefinido and so_definir.custo_conhecido == Decimal("0")


# ---------- Página ----------


def test_pagina_vazia_mostra_indicadores_zerados_e_o_botao():
    resposta = client.get("/manutencao")
    html = resposta.text

    assert resposta.status_code == 200 and resposta.headers["cache-control"] == "no-store"
    assert (
        "Registrar manutenção" in html
        and "Adicionar manutenção" not in html
        and "Nenhuma manutenção registrada ainda" in html
    )
    assert re.search(r">Manutenções\s*</span>\s*<strong[^>]*>0<", html)
    assert "Número de manutenções" not in html and "Os custos alimentam" not in html
    assert "Horas paradas" in html and "Custos com manutenção (R$)" in html
    assert "subtotal conhecido" not in html
    for coluna in (
        "Data",
        "Tipo de manutenção",
        "Tempo de máquina parada",
        "Peça substituída",
        "Custo (R$)",
        "Comentários",
        "Ações",
    ):
        assert f">{coluna}</th>" in html
    tabela = html[html.index('id="manut-tabela"') :]
    assert "Troca de peças?" not in html and "Ver detalhes" not in tabela


def test_pagina_mostra_registros_formatados_e_indicadores(repo_manutencao):
    repo_manutencao.manutencoes = [
        manutencao(
            1,
            data=date(2026, 10, 10),
            minutos_parados=90,
            custo=Decimal("62.50"),
            troca_pecas=True,
            pecas="Bico, correia",
            comentarios="Obs 1",
        ),
        manutencao(
            2, data=date(2026, 10, 20), tipo="preventiva", minutos_parados=1590, custo=Decimal("0")
        ),
        manutencao(3, data=date(2026, 10, 15), minutos_parados=30, custo=None),
    ]

    html = client.get("/manutencao").text
    linhas = re.findall(r"<tr id=\"manut-\d+\".*?</tr>", html, re.S)

    assert [re.search(r'id="manut-(\d+)"', linha).group(1) for linha in linhas] == ["2", "3", "1"]
    texto2, texto3, texto1 = (texto(linha) for linha in linhas)
    assert "20/10/26" in texto2 and "Preventiva" in texto2
    assert "26h30min" in texto2 and "dd:hh" not in texto2  # duração exata, visível na célula
    assert "R$ 0,00" in texto2 and "A definir" not in texto2  # zero é custo conhecido
    assert "A definir" in texto3 and "00h30min" in texto3 and "Sem troca" in texto3
    assert "01h30min" in texto1 and "Bico, correia" in texto1 and "Sem troca" not in texto1
    assert "R$ 62,50" in texto1 and "Obs 1" in texto1
    assert "Registrado por" not in texto1 and "Atualizado por" not in texto1

    inicio = html.index('id="manut-indicadores"')
    indicadores_html = texto(html[inicio : html.index('id="manut-ajuda"')])
    assert "Manutenções 3" in indicadores_html
    assert "Horas paradas 28h30min" in indicadores_html  # 90 + 1590 + 30 = 1710 min
    assert "Custos com manutenção (R$) · subtotal conhecido R$ 62,50" in indicadores_html
    assert "1 manutenção com custo a definir fora da soma" in indicadores_html


def test_indicador_de_custo_a_definir_quando_nenhum_custo_foi_informado(repo_manutencao):
    repo_manutencao.manutencoes = [manutencao(1, custo=None)]

    bloco = texto(client.get("/manutencao").text)

    assert "subtotal conhecido A definir" in bloco


def test_gasto_antigo_aparece_sem_tipo_nem_tempo(repo_manutencao):
    repo_manutencao.manutencoes = [
        manutencao(1, tipo=None, minutos_parados=None, comentarios="Lubrificante", legado=True)
    ]

    linha = texto(client.get("/manutencao").text)

    assert "Não informado" in linha and "Gasto anterior" in linha
    assert "Lubrificante" in linha


# ---------- Custo de manutenção por hora ----------


def com_referencia(repo_custos, manual=None, sem_custo=()):
    repo_custos.periodo = PeriodoManutencao(date(2026, 8, 3), date(2026, 11, 1), Decimal("902.4"))
    repo_custos.gastos = [
        GastoManutencao(1, date(2026, 10, 29), "Corretiva", Decimal("107.62")),
        GastoManutencao(2, date(2026, 10, 27), "Preventiva", Decimal("0")),
        GastoManutencao(3, date(2026, 8, 2), "Fora do período", Decimal("500")),
    ]
    repo_custos.manutencoes_sem_custo = sem_custo
    repo_custos.parametros[MANUTENCAO] = manual


def cartao_custos():
    html = client.get("/custos").text
    inicio = html.index('<li class="custos-item custos-fil" id="manutencao"')
    return html[inicio : html.index("</li>", inicio)]


def test_custo_por_hora_usa_decimal_e_so_arredonda_na_exibicao(repo_custos):
    com_referencia(repo_custos)

    base, custo = custo_de_manutencao(repo_custos.listar())

    assert str(base.calculado).startswith("0.119259751773049645390070921985")  # sem arredondar
    assert base.situacao == "completo"
    assert base.total == Decimal("107.62") and base.quantidade == 2  # o gasto de agosto fica fora
    assert formatar_custo_aprox(base.calculado) == "≈ R$ 0,1193"
    assert custo.calculado == base.calculado


def test_custo_por_hora_e_o_mesmo_em_manutencao_e_em_custos(repo_custos, estoque_vazio):
    com_referencia(repo_custos)

    card = texto(client.get("/manutencao").text)

    assert "Custo com manutenção (R$/h) R$0,12" in card  # mesma base; duas casas só na exibição
    assert "03/08/2026 a 01/11/2026" in card and "902,4 h" in card and "R$ 107,62" in card
    assert "Calculado (referência) ≈ R$ 0,1193 /h" in texto(cartao_custos())


def test_card_recalcula_ao_salvar_na_propria_tabela(repo_custos):
    com_referencia(repo_custos)
    repo_custos.gastos[0] = GastoManutencao(1, date(2026, 10, 29), "Corretiva", Decimal("902.4"))

    corpo = enviar().json()

    assert "Custo com manutenção (R$/h) R$1,00" in texto(corpo["indicadores"])


def test_card_formata_milhar_e_so_oferece_definir_sem_periodo(repo_custos):
    html = client.get("/manutencao").text
    assert "Ver detalhes" in html and "Ver critérios" not in html
    assert "Definir período e horas" in html  # sem período: o caminho para defini-lo continua

    repo_custos.periodo = PeriodoManutencao(date(2026, 8, 3), date(2026, 11, 1), Decimal(1))
    repo_custos.gastos = [GastoManutencao(1, date(2026, 10, 29), "Peça", Decimal("1234.5"))]
    html = client.get("/manutencao").text

    assert "Custo com manutenção (R$/h) R$1.234,50" in texto(html)
    assert "Editar período e horas" not in html and "Definir período e horas" not in html


@pytest.mark.parametrize(
    ("preparo", "esperado"),
    [
        (lambda r: None, "Defina o período e as horas impressas."),
        (
            lambda r: setattr(
                r, "periodo", PeriodoManutencao(date(2026, 8, 3), date(2026, 11, 1), Decimal(0))
            ),
            "Defina o período e as horas impressas.",
        ),
        (
            lambda r: setattr(
                r, "periodo", PeriodoManutencao(date(2026, 8, 3), date(2026, 11, 1), Decimal(10))
            ),
            "Nenhum custo registrado no período.",
        ),
    ],
)
def test_card_indisponivel_sem_periodo_horas_ou_gastos(repo_custos, preparo, esperado):
    preparo(repo_custos)

    html = texto(client.get("/manutencao").text)

    assert "Custo com manutenção (R$/h) Indisponível" in html and esperado in html


def test_card_sinaliza_calculo_incompleto_com_custo_a_definir(repo_custos):
    com_referencia(repo_custos, sem_custo=(date(2026, 10, 1), date(2020, 1, 1)))

    html = texto(client.get("/manutencao").text)

    assert "Cálculo incompleto: 1 manutenção do período com custo “A definir”." in html


def test_card_indisponivel_quando_custos_nao_responde(repo_custos):
    repo_custos.falhar_em["listar"] = FalhaAoConsultarCustos()

    resposta = client.get("/manutencao")

    assert resposta.status_code == 200
    assert "Não foi possível carregar o período agora." in resposta.text


def test_referencia_so_e_gravada_ao_confirmar_e_preserva_o_manual(repo_custos, estoque_vazio):
    repo_custos.parametros[MANUTENCAO] = Decimal("0.12")

    html = client.get("/manutencao").text

    assert "Confirmar referência" in html and "915" not in texto(html)
    assert repo_custos.gravacoes == [] and repo_custos.periodo is None

    resposta = client.post(
        "/custos/manutencao/periodo",
        data={"csrf": CSRF, "inicio": "2026-08-03", "fim": "2026-11-01", "horas": "902,4"},
        follow_redirects=False,
    )

    assert resposta.status_code == 303
    assert repo_custos.periodo == PeriodoManutencao(
        date(2026, 8, 3), date(2026, 11, 1), Decimal("902.4")
    )
    assert repo_custos.parametros[MANUTENCAO] == Decimal("0.12")
    assert "Confirmar referência" not in client.get("/manutencao").text


def test_referencia_sai_do_card_com_o_formulario_de_periodo_aberto(repo_custos, estoque_vazio):
    assert "Confirmar referência" in client.get("/custos").text

    aberto = client.get("/custos", params={"editar": "manutencao-periodo"}).text

    assert "Confirmar referência" not in aberto


def test_referencia_nao_aparece_com_periodo_ja_definido(repo_custos, estoque_vazio):
    repo_custos.periodo = PeriodoManutencao(date(2026, 1, 1), date(2026, 1, 31), Decimal(10))

    assert "Confirmar referência" not in client.get("/custos").text
    assert "Confirmar referência" not in client.get("/manutencao").text
    assert repo_custos.gravacoes == []


@pytest.mark.parametrize(
    ("manual", "classe", "aviso"),
    [
        (Decimal("0.12"), "maior", False),
        (Decimal("0.10"), "menor", True),
        (None, "neutro", False),
    ],
)
def test_comparacao_no_card_de_custos(repo_custos, estoque_vazio, manual, classe, aviso):
    com_referencia(repo_custos, manual=manual)

    card = cartao_custos()

    assert f"custos-fil-valor-{classe}" in card
    assert ("Revisar custo de manutenção: valor manual abaixo do custo calculado" in card) == aviso


def test_manual_igual_ao_calculado_fica_neutro_sem_alerta(repo_custos, estoque_vazio):
    com_referencia(repo_custos)
    repo_custos.parametros[MANUTENCAO] = custo_de_manutencao(repo_custos.listar())[0].calculado

    assert "custos-fil-valor-igual" in cartao_custos()
    assert "Revisar custo de manutenção" not in client.get("/").text


def test_custo_a_definir_tira_a_comparacao(repo_custos, estoque_vazio):
    com_referencia(repo_custos, manual=Decimal("0.01"), sem_custo=(date(2026, 10, 1),))

    card = cartao_custos()

    assert "custos-fil-valor-neutro" in card and "Revisar custo de manutenção" not in card
    assert "Cálculo incompleto" in card
    assert "Revisar custo de manutenção" not in client.get("/").text


def test_alerta_na_home_acompanha_os_valores_atuais(repo_custos):
    com_referencia(repo_custos, manual=Decimal("0.10"))

    resposta = client.get("/")
    assert "manual R$ 0,10/h; calculado ≈ R$ 0,1193/h" in texto(resposta.text)
    assert 'href="/custos#manutencao"' in resposta.text

    repo_custos.parametros[MANUTENCAO] = Decimal("0.11")
    assert "manual R$ 0,11" in texto(client.get("/").text)

    repo_custos.parametros[MANUTENCAO] = Decimal("0.12")  # acima do calculado: sai
    assert "Revisar custo de manutenção" not in client.get("/").text

    repo_custos.parametros[MANUTENCAO] = None  # sem manual
    assert "Revisar custo de manutenção" not in client.get("/").text


def test_home_abre_sem_o_alerta_de_custo_se_custos_falhar(repo_custos):
    com_referencia(repo_custos, manual=Decimal("0.10"))
    repo_custos.falhar_em["listar"] = FalhaAoConsultarCustos()

    resposta = client.get("/")

    assert resposta.status_code == 200 and "Revisar custo de manutenção" not in resposta.text


# ---------- Alerta de lubrificação ----------


class Hoje(date):
    """Fixa o "hoje" das rotas: o alerta depende do mês corrente."""

    @classmethod
    def today(cls):
        return date(2026, 11, 2)


@pytest.mark.parametrize(
    "comentario",
    [
        "Limpeza Eixo X e Y",
        "lubrificação dos eixos x e y",
        "LUBRIFICACAO DOS EIXOS X E Y",
        "Lubrificação do eixo X e eixo Y, sem folga",
        "Eixos X e Y: limpeza",
    ],
)
def test_comentario_que_registra_a_lubrificacao(comentario):
    assert registra_lubrificacao(manutencao(tipo="preventiva", comentarios=comentario))


@pytest.mark.parametrize(
    ("tipo", "comentario"),
    [
        ("corretiva", "Lubrificação dos eixos X e Y"),  # só a preventiva encerra
        (None, "Lubrificação dos eixos X e Y"),
        ("preventiva", None),
        ("preventiva", "Limpeza do bico"),
        ("preventiva", "Troca dos eixos X e Y"),
        ("preventiva", "Lubrificação do eixo Z"),
    ],
)
def test_o_que_nao_registra_a_lubrificacao(tipo, comentario):
    assert not registra_lubrificacao(manutencao(tipo=tipo, comentarios=comentario))


def lubrificada(dia):
    return manutencao(data=dia, tipo="preventiva", comentarios="Lubrificação dos eixos X e Y")


@pytest.mark.parametrize(
    ("registros", "hoje", "esperado"),
    [
        ([], date(2026, 10, 31), None),  # antes do início
        ([], date(2026, 11, 1), date(2026, 11, 1)),  # começa no dia 01/11/2026
        ([lubrificada(date(2026, 10, 27))], date(2026, 11, 2), date(2026, 11, 1)),  # outubro
        ([lubrificada(date(2026, 11, 2))], date(2026, 11, 2), None),
        ([lubrificada(date(2026, 11, 20))], date(2026, 11, 30), None),
        ([lubrificada(date(2026, 11, 20))], date(2026, 12, 1), date(2026, 12, 1)),  # volta no mês
        ([], date(2027, 1, 15), date(2026, 11, 1)),  # não sai sozinho com a virada do mês
        ([lubrificada(date(2026, 11, 20))], date(2027, 2, 3), date(2026, 12, 1)),
        ([lubrificada(date(2026, 12, 31))], date(2027, 1, 1), date(2027, 1, 1)),  # virada do ano
        ([lubrificada(date(2026, 11, 30))], date(2026, 11, 2), date(2026, 11, 1)),  # data futura
    ],
)
def test_lubrificacao_pendente_desde(registros, hoje, esperado):
    assert lubrificacao_pendente_desde(registros, hoje) == esperado


def test_pagina_mostra_o_alerta_enquanto_a_lubrificacao_esta_pendente(repo_manutencao, monkeypatch):
    monkeypatch.setattr("app.manutencao_rotas.date", Hoje)

    html = texto(client.get("/manutencao").text)
    assert "Lubrificação mensal dos eixos X e Y pendente desde 01/11/26" in html

    repo_manutencao.manutencoes = [lubrificada(date(2026, 11, 2))]
    assert "Lubrificação mensal" not in client.get("/manutencao").text


def test_pagina_inicial_mostra_o_alerta_com_link_ate_registrar(repo_manutencao, monkeypatch):
    monkeypatch.setattr("app.main.date", Hoje)

    resposta = client.get("/")
    assert resposta.headers["cache-control"] == "no-store"
    assert "pendente desde 01/11/26" in texto(resposta.text)
    assert '<a href="/manutencao">Ver manutenção</a>' in resposta.text

    repo_manutencao.manutencoes = [lubrificada(date(2026, 11, 2))]
    assert "Lubrificação mensal" not in client.get("/").text


def test_pagina_inicial_abre_sem_o_alerta_se_o_banco_falhar(repo_manutencao, monkeypatch):
    monkeypatch.setattr("app.main.date", Hoje)
    repo_manutencao.falhar_em["listar"] = FalhaAoConsultarManutencao()

    resposta = client.get("/")

    assert resposta.status_code == 200 and "Lubrificação mensal" not in resposta.text
    assert "Ver manutenção" in resposta.text  # o card do módulo continua lá


def test_salvar_a_preventiva_devolve_os_indicadores_sem_o_alerta(repo_manutencao, monkeypatch):
    monkeypatch.setattr("app.manutencao_rotas.date", Hoje)

    corretiva = enviar(
        dados=formulario(data="2026-11-02", comentarios="Lubrificação dos eixos X e Y")
    ).json()
    assert 'id="manut-indicadores"' in corretiva["indicadores"]
    assert "Lubrificação mensal" in corretiva["indicadores"]

    preventiva = enviar(
        dados=formulario(
            data="2026-11-02", tipo="preventiva", comentarios="Limpeza dos eixos X e Y"
        )
    ).json()
    assert preventiva["ok"] and "Lubrificação mensal" not in preventiva["indicadores"]


def test_linha_em_edicao_pelo_servidor_sem_javascript(repo_manutencao):
    repo_manutencao.manutencoes = [manutencao(7)]

    nova = client.get("/manutencao", params={"editar": "nova"}).text
    existente = client.get("/manutencao", params={"editar": "7"}).text
    desconhecida = client.get("/manutencao", params={"editar": "99"}).text

    assert 'id="manut-nova-form"' in nova and 'action="/manutencao"' in nova
    assert 'id="manut-7-form"' in existente and 'action="/manutencao/7"' in existente
    assert 'name="custo"' in existente and 'value="62,50"' in existente
    assert 'name="tempo"' in existente and 'value="01h30min"' in existente
    assert 'name="troca_pecas"' not in existente and 'name="dias"' not in existente
    assert "manut-editando" not in desconhecida
    assert 'name="csrf" value="csrf-de-teste"' in nova


def test_campos_do_editor_tem_rotulos_e_mensagens_acessiveis():
    html = client.get("/manutencao", params={"editar": "nova"}).text

    for campo in (
        "data",
        "tipo",
        "tempo",
        "pecas",
        "custo",
        "comentarios",
    ):
        assert "<label" in html and f'for="manut-nova-{campo}"' in html
    assert 'inputmode="numeric"' in html and 'role="alert"' in html
    assert (
        'aria-label="Salvar nova manutenção"' in html and 'aria-label="Cancelar a edição"' in html
    )


def test_falha_ao_carregar_mostra_aviso(repo_manutencao):
    repo_manutencao.falhar_em["listar"] = FalhaAoConsultarManutencao()

    resposta = client.get("/manutencao")

    assert resposta.status_code == 503
    assert "Não foi possível carregar as manutenções agora" in resposta.text


def test_fragmento_da_linha_de_edicao(repo_manutencao):
    repo_manutencao.manutencoes = [manutencao(4)]

    nova = client.get("/manutencao/linha", params={"editar": "nova"})
    existente = client.get("/manutencao/linha", params={"editar": "4"})

    assert nova.status_code == 200 and nova.text.lstrip().startswith("<tr")
    assert 'id="manut-4"' in existente.text and "manut-editando" in existente.text
    assert client.get("/manutencao/linha", params={"editar": "99"}).status_code == 404
    assert client.get("/manutencao/linha", params={"editar": "abc"}).status_code == 404


# ---------- Cadastro e edição (fetch) ----------


def test_cadastro_por_fetch_grava_com_o_usuario_da_sessao(repo_manutencao):
    resposta = enviar(dados=formulario(tempo="1d02h30min", pecas="Bico"))
    corpo = resposta.json()

    assert resposta.status_code == 200 and corpo["ok"] and corpo["salvo"]
    assert resposta.headers["cache-control"] == "no-store"
    ((id_, valores, usuario),) = repo_manutencao.gravacoes
    assert id_ == 1 and usuario == 1
    assert valores["minutos"] == 1590 and valores["pecas"] == "Bico"
    assert valores["custo"] == Decimal("62.50")
    assert 'id="manut-1"' in corpo["linha"] and "26h30min" in texto(corpo["linha"])
    assert "Manutenções 1" in texto(corpo["indicadores"])
    assert corpo["data"] == "2026-10-10"


def test_edicao_por_fetch_altera_a_mesma_manutencao(repo_manutencao):
    repo_manutencao.manutencoes = [manutencao(5)]

    resposta = enviar("/manutencao/5", formulario(tipo="preventiva", custo="", tempo="00:00"))

    assert resposta.status_code == 200 and resposta.json()["id"] == 5
    assert len(repo_manutencao.manutencoes) == 1
    salva = repo_manutencao.manutencoes[0]
    assert salva.tipo == "preventiva" and salva.custo is None and salva.minutos_parados == 0
    assert salva.criado_por == "Leandro"
    assert "A definir" in texto(resposta.json()["linha"])


def test_usuario_enviado_pelo_navegador_e_ignorado(repo_manutencao):
    enviar(dados=formulario(usuario_id="3", criado_por="Marise"))

    assert repo_manutencao.gravacoes[0][2] == 1


def test_erros_de_validacao_voltam_por_campo_e_nada_e_gravado(repo_manutencao):
    resposta = enviar(dados=formulario(tipo="", tempo="", custo="-1"))
    corpo = resposta.json()

    assert resposta.status_code == 422 and not corpo["ok"] and not corpo["salvo"]
    assert set(corpo["erros"]) == {"tipo", "tempo", "custo"}
    assert corpo["erro"] == "Confira os campos destacados."
    assert repo_manutencao.manutencoes == [] and repo_manutencao.gravacoes == []


@pytest.mark.parametrize("caminho", ["/manutencao", "/manutencao/1"])
def test_sem_csrf_valido_nada_e_gravado(repo_manutencao, caminho):
    repo_manutencao.manutencoes = [manutencao(1)]

    resposta = client.post(caminho, data={"csrf": "errado", **formulario()}, headers=FETCH)

    assert resposta.status_code == 403 and "A página expirou" in resposta.json()["erro"]
    assert repo_manutencao.gravacoes == []


def test_origem_de_outro_site_e_recusada(repo_manutencao):
    resposta = client.post(
        "/manutencao",
        data={"csrf": CSRF, **formulario()},
        headers={**FETCH, "origin": "https://outro-site.exemplo"},
    )

    assert resposta.status_code == 403 and repo_manutencao.gravacoes == []


def test_falha_do_banco_nao_grava_e_informa(repo_manutencao):
    repo_manutencao.falhar_em["salvar"] = FalhaAoSalvarManutencao()

    resposta = enviar()

    assert resposta.status_code == 503 and not resposta.json()["salvo"]
    assert "Não foi possível salvar agora" in resposta.json()["erro"]
    assert repo_manutencao.manutencoes == []


@pytest.mark.parametrize(
    ("motivo", "status"), [("usuario", 403), ("nao_encontrado", 404), ("invalido", 422)]
)
def test_recusas_do_banco_viram_mensagens(repo_manutencao, motivo, status):
    repo_manutencao.falhar_em["salvar"] = ManutencaoRecusada(motivo)

    resposta = enviar()

    assert resposta.status_code == status and resposta.json()["erro"]


def test_salvo_mas_indicadores_indisponiveis_continua_sendo_sucesso(repo_manutencao):
    class Instavel(type(repo_manutencao)):
        def listar(self):
            raise FalhaAoConsultarManutencao

    repo_manutencao.__class__ = Instavel

    corpo = enviar().json()

    assert corpo["ok"] and corpo["salvo"] and corpo["indicadores"] == "" and corpo["linha"]
    assert len(repo_manutencao.gravacoes) == 1


def test_id_invalido_na_url_e_404(repo_manutencao):
    assert enviar("/manutencao/abc").status_code == 404
    assert enviar("/manutencao/1;drop").status_code == 404


# ---------- Formulário comum (sem JavaScript) ----------


def test_formulario_comum_redireciona_ao_salvar(repo_manutencao):
    resposta = enviar(fetch=False)

    assert resposta.status_code == 303
    assert resposta.headers["location"] == "/manutencao?salvo=1"
    assert "Manutenção salva." in client.get("/manutencao?salvo=1").text
    assert len(repo_manutencao.manutencoes) == 1


def test_formulario_comum_com_erro_preserva_o_que_foi_digitado(repo_manutencao):
    resposta = enviar(
        fetch=False,
        dados=formulario(tipo="", tempo="2d3h4min", custo="12,30", comentarios="Anotei"),
    )

    assert resposta.status_code == 422
    html = resposta.text
    assert "manut-editando" in html and 'value="2d3h4min"' in html and 'value="12,30"' in html
    assert ">Anotei</textarea>" in html and "Escolha Preventiva ou Corretiva." in html
    assert repo_manutencao.manutencoes == []


# ---------- Login e home ----------


@pytest.mark.sem_login
@pytest.mark.parametrize(
    ("metodo", "caminho"),
    [
        ("get", "/manutencao"),
        ("get", "/manutencao/linha"),
        ("post", "/manutencao"),
        ("post", "/manutencao/1"),
    ],
)
def test_manutencao_exige_login(metodo, caminho):
    resposta = getattr(TestClient(app), metodo)(caminho, follow_redirects=False)

    assert resposta.status_code == 303 and resposta.headers["location"].startswith("/entrar")


@pytest.mark.sem_login
def test_sessao_expirada_no_fetch_leva_ao_login_de_volta_para_manutencao():
    resposta = TestClient(app).post("/manutencao", data=formulario(), headers=FETCH)

    assert resposta.status_code == 401
    assert resposta.json()["expirou"] and resposta.json()["login"] == "/entrar?next=%2Fmanutencao"


def test_home_tem_um_card_proprio_para_manutencao():
    html = client.get("/").text

    assert "<h2>Manutenção</h2>" in html and 'href="/manutencao"' in html
