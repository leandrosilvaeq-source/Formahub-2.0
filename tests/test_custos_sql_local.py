"""Migrations de Custos num PostgreSQL local em memória (PGlite), sem acessar o Supabase.

Roda supabase/tests/custos_test.sql, custos_gestao_test.sql e manutencoes_test.sql e confere que a
resposta real de listar_custos() é lida corretamente pelo repositório (Decimal, datas e nulos).
Pulado se o Node.js ou o PGlite (`npm install`) não estiverem disponíveis.
"""

import json
import shutil
import subprocess
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.custos import (
    DEPRECIACAO_MANUAL,
    ENERGIA_MANUAL,
    HORAS_MES,
    MANUTENCAO,
    MDO,
    MEI,
    PERDA,
    RETIRADA,
    TARIFA,
    depreciacao,
    energia_por_hora,
)
from app.custos_repositorio import RepositorioCustosSupabase

RAIZ = Path(__file__).resolve().parent.parent
EXECUTOR = RAIZ / "supabase" / "tests" / "executar_pglite.mjs"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(
    not NODE or not (RAIZ / "node_modules" / "@electric-sql" / "pglite").exists(),
    reason="Node.js ou PGlite ausente (rode npm install).",
)


def executar(*argumentos) -> subprocess.CompletedProcess:
    return subprocess.run(
        [NODE, str(EXECUTOR), *argumentos],
        cwd=RAIZ,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
    )


class _Resposta:
    def __init__(self, data):
        self.data = data

    def execute(self):
        return self


class _Cliente:
    def __init__(self, data):
        self.data = data

    def rpc(self, funcao, parametros):
        assert funcao == "listar_custos"
        return _Resposta(self.data)


def test_testes_sql_de_custos_passam_com_as_migrations_do_checkout():
    resultado = executar(
        "supabase/tests/custos_test.sql",
        "supabase/tests/custos_gestao_test.sql",
        "supabase/tests/manutencoes_test.sql",
    )

    assert resultado.returncode == 0, resultado.stderr + resultado.stdout
    assert "migration aplicada: 20261019120000_custos_gestao.sql" in resultado.stdout
    assert "custos_test: ok" in resultado.stdout
    assert "custos_gestao_test: ok" in resultado.stdout
    assert "migration aplicada: 20261020120000_manutencoes.sql" in resultado.stdout
    assert "manutencoes_test: ok" in resultado.stdout


def test_listagem_real_do_banco_alimenta_o_repositorio_e_os_calculos(tmp_path):
    saida = tmp_path / "custos.json"
    resultado = executar("--custos", str(saida))
    assert resultado.returncode == 0, resultado.stderr + resultado.stdout

    custos = RepositorioCustosSupabase(_Cliente(json.loads(saida.read_text()))).listar()

    p = custos.parametros
    # Valores iniciais da migration num banco vazio de edições.
    assert (p[PERDA], p[MDO], p[MEI], p[RETIRADA]) == (3, 15, 100, 500)
    assert p[TARIFA] == Decimal("1.0393") and p[HORAS_MES] == 305
    assert p[ENERGIA_MANUAL] == Decimal("0.14") and p[DEPRECIACAO_MANUAL] == Decimal("0.27")
    assert p[MANUTENCAO] is None  # "A definir": ausência de valor, não zero
    assert custos.provisorios == {ENERGIA_MANUAL, DEPRECIACAO_MANUAL}
    assert custos.datas[HORAS_MES] == date(2026, 11, 1)
    assert custos.ajustes == {} and custos.gastos == [] and custos.periodo is None
    # E os cálculos com esses valores batem com o pedido: ≈ 0,1363/h e ≈ 0,2732/h.
    energia = energia_por_hora(p["energia_consumo_kwh_mes"], p[TARIFA], p[HORAS_MES])
    deprec = depreciacao(
        p["depreciacao_valor_aquisicao"],
        p["depreciacao_valor_residual"],
        p["depreciacao_vida_util_anos"],
        p[HORAS_MES],
    )
    assert energia.quantize(Decimal("0.0001")) == Decimal("0.1363")
    assert deprec.por_hora.quantize(Decimal("0.0001")) == Decimal("0.2732")
