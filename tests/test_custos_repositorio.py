"""Repositório real de custos com um cliente Supabase simulado (nada vai à rede).

As chamadas são conferidas contra a assinatura real do supabase-py (postgrest), e os valores
precisam viajar como texto nos dois sentidos: nada de float.
"""

import json
from datetime import date
from decimal import Decimal

import pytest
from postgrest.exceptions import APIError

from app.custos import ENERGIA_MANUAL, HORAS_MES, MANUTENCAO, PERDA, TARIFA
from app.custos_repositorio import (
    CustoRecusado,
    FalhaAoConsultarCustos,
    FalhaAoSalvarCustos,
    RepositorioCustosSupabase,
)
from tests.test_pedidos_repositorio import ClienteSimulado

LEITURA = {
    "filamentos": [{"id": 1, "nome": "PLA", "abrange": None, "valor_kg": "100"}],  # legado
    "itens": [],  # legado
    "parametros": {
        "energia_tarifa_kwh": "1.0393",
        "perda_percentual": "3",
        "horas_impressas_mes": "305",
        "energia_hora_manual": "0.14",
        "manutencao_hora": None,
    },
    "provisorios": ["energia_hora_manual"],
    "datas": {"horas_impressas_mes": "2026-11-01"},
    "ajustes": {"filamento:petg": "85.5000", "acessorio:Imã": "3.3333333333333333"},
    "manutencao_gastos": [
        {"id": 2, "data": "2026-10-20", "descricao": "Correia", "valor": "62.50"},
        {"id": 1, "data": "2026-10-10", "descricao": "Bico", "valor": "62.50"},
    ],
    "manutencao_sem_custo": ["2026-10-12"],
    "manutencao_periodo": {"inicio": "2026-10-01", "fim": "2026-10-31", "horas": "250"},
}


def test_listar_converte_tudo_para_decimal_e_data_sem_perder_precisao():
    cliente = ClienteSimulado(rpc={"listar_custos": LEITURA})

    custos = RepositorioCustosSupabase(cliente).listar()

    assert cliente.chamadas == [("rpc", ("listar_custos", {}))]
    assert custos.parametros[TARIFA] == Decimal("1.0393")
    assert custos.parametros[PERDA] == Decimal("3")
    assert custos.provisorios == {ENERGIA_MANUAL}
    assert custos.datas == {HORAS_MES: date(2026, 11, 1)}
    assert custos.ajustes == {
        "filamento:petg": Decimal("85.5000"),
        "acessorio:Imã": Decimal("3.3333333333333333"),
    }
    assert [(g.id, g.data, g.descricao, g.valor) for g in custos.gastos] == [
        (2, date(2026, 10, 20), "Correia", Decimal("62.50")),
        (1, date(2026, 10, 10), "Bico", Decimal("62.50")),
    ]
    assert custos.manutencoes_sem_custo == (date(2026, 10, 12),)
    assert (custos.periodo.inicio, custos.periodo.fim, custos.periodo.horas) == (
        date(2026, 10, 1),
        date(2026, 10, 31),
        Decimal("250"),
    )
    assert all(isinstance(v, Decimal) for v in custos.ajustes.values())


def test_resposta_antiga_sem_manutencoes_sem_custo_continua_valida():
    antiga = {k: v for k, v in LEITURA.items() if k != "manutencao_sem_custo"}

    custos = RepositorioCustosSupabase(ClienteSimulado(rpc={"listar_custos": antiga})).listar()

    assert custos.manutencoes_sem_custo == ()


def test_sem_periodo_definido_vem_none():
    cliente = ClienteSimulado(rpc={"listar_custos": {**LEITURA, "manutencao_periodo": None}})

    assert RepositorioCustosSupabase(cliente).listar().periodo is None


def test_manutencao_sem_valor_vem_como_none_e_nao_como_zero():
    cliente = ClienteSimulado(rpc={"listar_custos": LEITURA})

    custos = RepositorioCustosSupabase(cliente).listar()

    assert custos.parametros[MANUTENCAO] is None
    assert custos.parametros[MANUTENCAO] != Decimal(0)


@pytest.mark.parametrize(
    "resposta",
    [
        APIError({"message": "x", "code": "PGRST202"}),  # função ausente (migration pendente)
        ConnectionError("sem rede"),
        {"formato": "antigo"},  # resposta sem os campos novos (migration ainda não aplicada)
        {k: v for k, v in LEITURA.items() if k != "ajustes"},
        None,
    ],
)
def test_listar_com_falha_vira_falha_ao_consultar(resposta):
    cliente = ClienteSimulado(rpc={"listar_custos": resposta})

    with pytest.raises(FalhaAoConsultarCustos):
        RepositorioCustosSupabase(cliente).listar()


def test_salvar_parametros_envia_so_as_chaves_alteradas_como_texto():
    retorno = {"energia_tarifa_kwh": "0.95", "energia_consumo_kwh_mes": "42"}
    cliente = ClienteSimulado(rpc={"salvar_custos_parametros": retorno})

    atuais = RepositorioCustosSupabase(cliente).salvar_parametros(
        {TARIFA: Decimal("0.95"), "energia_consumo_kwh_mes": Decimal("42")}, 3
    )

    (_, (funcao, parametros)) = cliente.chamadas[0]
    assert funcao == "salvar_custos_parametros"
    assert parametros == {
        "p_valores": {"energia_tarifa_kwh": "0.95", "energia_consumo_kwh_mes": "42"},
        "p_usuario_id": 3,
    }
    json.dumps(parametros)
    assert atuais == {TARIFA: Decimal("0.95"), "energia_consumo_kwh_mes": Decimal("42")}


def test_voltar_ao_calculado_envia_nulo_e_devolve_none():
    cliente = ClienteSimulado(rpc={"salvar_custos_parametros": {ENERGIA_MANUAL: None}})

    atuais = RepositorioCustosSupabase(cliente).salvar_parametros({ENERGIA_MANUAL: None}, 2)

    (_, (funcao, parametros)) = cliente.chamadas[0]
    assert parametros == {"p_valores": {ENERGIA_MANUAL: None}, "p_usuario_id": 2}
    json.dumps(parametros)  # null no JSON, não a palavra "None" nem zero
    assert atuais == {ENERGIA_MANUAL: None}


def test_numeros_pequenos_e_grandes_nao_viram_notacao_cientifica():
    cliente = ClienteSimulado(rpc={"salvar_custos_parametros": {PERDA: "0"}})

    RepositorioCustosSupabase(cliente).salvar_parametros({PERDA: Decimal("1E-7")}, 1)

    assert cliente.chamadas[0][1][1]["p_valores"] == {PERDA: "0.0000001"}


def test_salvar_ajuste_de_insumo_envia_chave_valor_exato_e_usuario():
    cliente = ClienteSimulado(rpc={"salvar_ajuste_insumo": {"filamento:petg": "90.5"}})

    RepositorioCustosSupabase(cliente).salvar_ajuste_insumo("filamento:petg", Decimal("90.50"), 2)

    (_, (funcao, parametros)) = cliente.chamadas[0]
    assert funcao == "salvar_ajuste_insumo"
    assert parametros == {"p_chave": "filamento:petg", "p_valor": "90.50", "p_usuario_id": 2}
    json.dumps(parametros)


def test_voltar_o_insumo_ao_calculado_envia_valor_nulo():
    cliente = ClienteSimulado(rpc={"salvar_ajuste_insumo": {}})

    RepositorioCustosSupabase(cliente).salvar_ajuste_insumo("acessorio:Imã", None, 1)

    assert cliente.chamadas[0][1][1] == {
        "p_chave": "acessorio:Imã",
        "p_valor": None,
        "p_usuario_id": 1,
    }


def test_definir_periodo_de_manutencao():
    retorno = {"inicio": "2026-10-01", "fim": "2026-10-31", "horas": "250.5"}
    cliente = ClienteSimulado(rpc={"definir_periodo_manutencao": retorno})

    periodo = RepositorioCustosSupabase(cliente).definir_periodo_manutencao(
        date(2026, 10, 1), date(2026, 10, 31), Decimal("250.5"), 3
    )

    (_, (funcao, parametros)) = cliente.chamadas[0]
    assert funcao == "definir_periodo_manutencao"
    assert parametros == {
        "p_inicio": "2026-10-01",
        "p_fim": "2026-10-31",
        "p_horas": "250.5",
        "p_usuario_id": 3,
    }
    assert periodo.horas == Decimal("250.5")


CHAMADAS = {
    "parametros": (
        "salvar_custos_parametros",
        lambda r: r.salvar_parametros({PERDA: Decimal("1")}, 1),
    ),
    "ajuste": (
        "salvar_ajuste_insumo",
        lambda r: r.salvar_ajuste_insumo("filamento:petg", Decimal("1"), 1),
    ),
    "periodo": (
        "definir_periodo_manutencao",
        lambda r: r.definir_periodo_manutencao(date(2026, 10, 1), date(2026, 10, 2), Decimal(1), 1),
    ),
}


@pytest.mark.parametrize(
    ("codigo", "motivo"),
    [
        ("PT409", "duplicado"),
        ("PT422", "invalido"),
        ("PT404", "nao_encontrado"),
        ("PT403", "usuario"),
    ],
)
@pytest.mark.parametrize("operacao", list(CHAMADAS))
def test_codigos_do_banco_viram_motivos(operacao, codigo, motivo):
    funcao, chamar = CHAMADAS[operacao]
    cliente = ClienteSimulado(rpc={funcao: APIError({"message": "x", "code": codigo})})

    with pytest.raises(CustoRecusado) as recusa:
        chamar(RepositorioCustosSupabase(cliente))

    assert recusa.value.motivo == motivo


@pytest.mark.parametrize(
    "erro",
    [
        APIError({"message": "x", "code": "PGRST202"}),  # função ausente (migration pendente)
        APIError({"message": "x", "code": "23514"}),
        ConnectionError("sem rede"),
        {"formato": "inesperado"},
    ],
)
@pytest.mark.parametrize("operacao", list(CHAMADAS))
def test_outras_falhas_viram_falha_ao_salvar(erro, operacao):
    funcao, chamar = CHAMADAS[operacao]
    cliente = ClienteSimulado(rpc={funcao: erro})

    if isinstance(erro, dict) and operacao == "ajuste":
        pytest.skip("o ajuste de insumo não lê a resposta: só a falha de gravação importa")
    with pytest.raises(FalhaAoSalvarCustos):
        chamar(RepositorioCustosSupabase(cliente))
