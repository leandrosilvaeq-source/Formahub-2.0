"""Repositório real de custos com um cliente Supabase simulado (nada vai à rede).

As chamadas são conferidas contra a assinatura real do supabase-py (postgrest), e os valores
precisam viajar como texto nos dois sentidos: nada de float.
"""

import json
from decimal import Decimal

import pytest
from postgrest.exceptions import APIError

from app.custos import CONSUMO, PERDA, TARIFA
from app.custos_repositorio import (
    CustoRecusado,
    FalhaAoConsultarCustos,
    FalhaAoSalvarCustos,
    RepositorioCustosSupabase,
)
from tests.test_pedidos_repositorio import ClienteSimulado

LEITURA = {
    "filamentos": [
        {"id": 1, "nome": "PLA", "abrange": "Cores sólidas", "valor_kg": "100"},
        {"id": 3, "nome": "PETG", "abrange": None, "valor_kg": "85.5000"},
    ],
    "parametros": {
        "energia_tarifa_kwh": "1",
        "energia_consumo_w": "120",
        "perda_percentual": "5",
        "mdo_hora": "0.5",
        "manutencao_depreciacao_hora": "0.88",
    },
    "itens": [
        {
            "id": 4,
            "tipo": "acessorio",
            "nome": "Imã",
            "valor_compra": "10",
            "quantidade": 3,
            "custo_unitario": "3.3333333333333333",
        }
    ],
}


def test_listar_converte_tudo_para_decimal_sem_perder_precisao():
    cliente = ClienteSimulado(rpc={"listar_custos": LEITURA})

    custos = RepositorioCustosSupabase(cliente).listar()

    assert cliente.chamadas == [("rpc", ("listar_custos", {}))]
    assert [(f.id, f.nome, f.abrange, f.valor_kg) for f in custos.filamentos] == [
        (1, "PLA", "Cores sólidas", Decimal("100")),
        (3, "PETG", None, Decimal("85.5000")),
    ]
    assert custos.parametros[TARIFA] == Decimal("1") and custos.parametros[CONSUMO] == Decimal(
        "120"
    )
    (item,) = custos.itens
    assert item.custo_unitario == Decimal("3.3333333333333333")  # texto -> Decimal, sem float
    assert all(isinstance(f.valor_kg, Decimal) for f in custos.filamentos)


@pytest.mark.parametrize(
    "resposta",
    [
        APIError({"message": "x", "code": "PGRST202"}),  # função ausente (migration pendente)
        ConnectionError("sem rede"),
        {"formato": "antigo"},  # resposta sem os campos esperados
        None,
    ],
)
def test_listar_com_falha_vira_falha_ao_consultar(resposta):
    cliente = ClienteSimulado(rpc={"listar_custos": resposta})

    with pytest.raises(FalhaAoConsultarCustos):
        RepositorioCustosSupabase(cliente).listar()


def test_salvar_filamento_envia_valor_como_texto_e_usuario():
    retorno = {"id": 1, "nome": "PLA", "abrange": None, "valor_kg": "99.9999"}
    cliente = ClienteSimulado(rpc={"salvar_custo_filamento": retorno})

    filamento = RepositorioCustosSupabase(cliente).salvar_filamento(1, Decimal("99.9999"), 2)

    (_, (funcao, parametros)) = cliente.chamadas[0]
    assert funcao == "salvar_custo_filamento"
    assert parametros == {"p_id": 1, "p_valor_kg": "99.9999", "p_usuario_id": 2}
    json.dumps(parametros)
    assert filamento.valor_kg == Decimal("99.9999")


def test_salvar_parametros_envia_so_as_chaves_alteradas_como_texto():
    retorno = {"energia_tarifa_kwh": "0.95", "energia_consumo_w": "150"}
    cliente = ClienteSimulado(rpc={"salvar_custos_parametros": retorno})

    atuais = RepositorioCustosSupabase(cliente).salvar_parametros(
        {TARIFA: Decimal("0.95"), CONSUMO: Decimal("150")}, 3
    )

    (_, (funcao, parametros)) = cliente.chamadas[0]
    assert funcao == "salvar_custos_parametros"
    assert parametros == {
        "p_valores": {"energia_tarifa_kwh": "0.95", "energia_consumo_w": "150"},
        "p_usuario_id": 3,
    }
    json.dumps(parametros)
    assert atuais == {TARIFA: Decimal("0.95"), CONSUMO: Decimal("150")}


def test_numeros_pequenos_e_grandes_nao_viram_notacao_cientifica():
    cliente = ClienteSimulado(rpc={"salvar_custos_parametros": {PERDA: "0"}})

    RepositorioCustosSupabase(cliente).salvar_parametros({PERDA: Decimal("1E-7")}, 1)

    assert cliente.chamadas[0][1][1]["p_valores"] == {PERDA: "0.0000001"}


def test_cadastrar_item_envia_id_nulo_e_valores_exatos():
    retorno = {
        "id": 9,
        "tipo": "embalagem",
        "nome": "Caixa P",
        "valor_compra": "36.00",
        "quantidade": 12,
        "custo_unitario": "3.0000000000000000",
    }
    cliente = ClienteSimulado(rpc={"salvar_custo_item": retorno})

    item = RepositorioCustosSupabase(cliente).salvar_item(
        None, "embalagem", "Caixa P", Decimal("36.00"), 12, 1
    )

    (_, (funcao, parametros)) = cliente.chamadas[0]
    assert funcao == "salvar_custo_item"
    assert parametros == {
        "p_id": None,
        "p_tipo": "embalagem",
        "p_nome": "Caixa P",
        "p_valor_compra": "36.00",
        "p_quantidade": 12,
        "p_usuario_id": 1,
    }
    json.dumps(parametros)
    assert (item.id, item.custo_unitario) == (9, Decimal("3"))


@pytest.mark.parametrize(
    ("codigo", "motivo"),
    [
        ("PT409", "duplicado"),
        ("PT422", "invalido"),
        ("PT404", "nao_encontrado"),
        ("PT403", "usuario"),
    ],
)
def test_codigos_do_banco_viram_motivos(codigo, motivo):
    cliente = ClienteSimulado(
        rpc={"salvar_custo_filamento": APIError({"message": "x", "code": codigo})}
    )

    with pytest.raises(CustoRecusado) as recusa:
        RepositorioCustosSupabase(cliente).salvar_filamento(1, Decimal("1"), 1)

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
@pytest.mark.parametrize("metodo", ["filamento", "parametros", "item"])
def test_outras_falhas_viram_falha_ao_salvar(erro, metodo):
    funcoes = {
        "filamento": "salvar_custo_filamento",
        "parametros": "salvar_custos_parametros",
        "item": "salvar_custo_item",
    }
    cliente = ClienteSimulado(rpc={funcoes[metodo]: erro})
    repo = RepositorioCustosSupabase(cliente)
    chamadas = {
        "filamento": lambda: repo.salvar_filamento(1, Decimal("1"), 1),
        "parametros": lambda: repo.salvar_parametros({PERDA: Decimal("1")}, 1),
        "item": lambda: repo.salvar_item(None, "acessorio", "A", Decimal("1"), 1, 1),
    }

    with pytest.raises(FalhaAoSalvarCustos):
        chamadas[metodo]()


def test_manutencao_sem_valor_vem_como_none_e_nao_como_zero():
    leitura = {**LEITURA, "parametros": {**LEITURA["parametros"], "manutencao_hora": None}}
    cliente = ClienteSimulado(rpc={"listar_custos": leitura})

    custos = RepositorioCustosSupabase(cliente).listar()

    assert custos.parametros["manutencao_hora"] is None
    assert custos.parametros["manutencao_hora"] != Decimal(0)
    assert custos.parametros[TARIFA] == Decimal("1")


def test_salvar_manutencao_em_branco_envia_nulo_e_devolve_none():
    cliente = ClienteSimulado(rpc={"salvar_custos_parametros": {"manutencao_hora": None}})

    atuais = RepositorioCustosSupabase(cliente).salvar_parametros({"manutencao_hora": None}, 2)

    (_, (funcao, parametros)) = cliente.chamadas[0]
    assert funcao == "salvar_custos_parametros"
    assert parametros == {"p_valores": {"manutencao_hora": None}, "p_usuario_id": 2}
    json.dumps(parametros)  # null no JSON, não a palavra "None" nem zero
    assert atuais == {"manutencao_hora": None}


def test_salvar_criterios_da_depreciacao_envia_texto_exato():
    chaves = {
        "depreciacao_valor_aquisicao": Decimal("6000.00"),
        "depreciacao_vida_util_anos": Decimal("5"),
        "depreciacao_valor_residual": Decimal("0"),
        "depreciacao_horas_dia": Decimal("12"),
        "depreciacao_dias_mes": Decimal("30"),
    }
    retorno = {chave: str(valor) for chave, valor in chaves.items()}
    cliente = ClienteSimulado(rpc={"salvar_custos_parametros": retorno})

    atuais = RepositorioCustosSupabase(cliente).salvar_parametros(chaves, 1)

    enviados = cliente.chamadas[0][1][1]["p_valores"]
    assert enviados == {
        "depreciacao_valor_aquisicao": "6000.00",
        "depreciacao_vida_util_anos": "5",
        "depreciacao_valor_residual": "0",
        "depreciacao_horas_dia": "12",
        "depreciacao_dias_mes": "30",
    }
    assert atuais == chaves
