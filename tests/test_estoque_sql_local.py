"""Migration do Estoque num PostgreSQL local em memória (PGlite), sem acessar o Supabase.

Roda supabase/tests/estoque_test.sql e confere o repositório contra as funções reais: as
chamadas montadas por RepositorioEstoqueSupabase são executadas por nome de parâmetro (como o
PostgREST faz) e a resposta real de listar_estoque volta pelo conversor do repositório.
Pulado se o Node.js ou o PGlite (`npm install`) não estiverem disponíveis.
"""

import json
import shutil
import subprocess
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.estoque_repositorio import Filamento, ItemEstoque, RepositorioEstoqueSupabase

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


class _Captura:
    """Cliente falso: só guarda as chamadas RPC como o repositório as monta."""

    def __init__(self, data=1):
        self.chamadas, self.data = [], data

    def rpc(self, funcao, parametros):
        self.chamadas.append({"funcao": funcao, "parametros": parametros})
        return self

    def execute(self):
        return self


def test_estoque_test_sql_passa_com_as_migrations_do_checkout():
    resultado = executar("supabase/tests/estoque_test.sql")

    assert resultado.returncode == 0, resultado.stderr + resultado.stdout
    assert "migration aplicada: 20261009120000_estoque.sql" in resultado.stdout
    assert "estoque_test: ok" in resultado.stdout


def test_repositorio_contra_as_funcoes_reais(tmp_path):
    captura = _Captura()
    repo = RepositorioEstoqueSupabase(captura)
    preto = Filamento(
        None, "Preto", "PLA", "silk", "M", Decimal("750.5"), date(2026, 9, 1), Decimal("119.90")
    )
    repo.salvar_filamento(preto, 1)
    repo.salvar_filamento(
        Filamento(
            None, "Preto", "PLA", "silk", "M", Decimal("1000"), date(2026, 9, 20), Decimal(0)
        ),
        2,
    )
    repo.salvar_item(
        ItemEstoque(None, "acessorio", "Argola", 40, date(2026, 9, 2), Decimal("0.35")), 1
    )
    repo.salvar_item(
        ItemEstoque(None, "embalagem", "Caixa", 0, date(2026, 9, 3), Decimal("1.2")), 3
    )
    # Edições (no estoque vazio, os primeiros lotes de cada tabela recebem o id 1).
    repo.salvar_filamento(
        Filamento(1, "Preto", "PLA", "silk", "M", Decimal("500.25"), date(2026, 9, 1), Decimal(1)),
        3,
    )
    repo.salvar_item(
        ItemEstoque(1, "acessorio", "Argola", 39, date(2026, 9, 2), Decimal("0.35")), 2
    )

    entrada, saida = tmp_path / "chamadas.json", tmp_path / "listagem.json"
    entrada.write_text(json.dumps(captura.chamadas), encoding="utf-8")
    resultado = executar("--chamadas", str(entrada), "--listagem", str(saida))
    assert resultado.returncode == 0, resultado.stderr + resultado.stdout

    dados = json.loads(saida.read_text(encoding="utf-8"))
    estoque = RepositorioEstoqueSupabase(_Captura(dados)).listar_estoque()
    filamentos = {f.id: f for f in estoque.filamentos}
    itens = {i.id: i for i in estoque.itens}

    assert [f.id for f in estoque.filamentos] == [2, 1]  # compra mais recente primeiro
    assert filamentos[1].peso_g == Decimal("500.25")
    assert filamentos[1].custo_kg == Decimal("1.00")
    assert filamentos[2].peso_g == Decimal("1000.00")
    assert filamentos[2].data_compra == date(2026, 9, 20)
    assert (itens[1].categoria, itens[1].quantidade) == ("acessorio", 39)
    assert (itens[2].categoria, itens[2].quantidade) == ("embalagem", 0)
    assert itens[2].custo_unitario == Decimal("1.20")
