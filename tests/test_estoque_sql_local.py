"""Migration do Estoque num PostgreSQL local em memória (PGlite), sem acessar o Supabase.

Roda supabase/tests/estoque_test.sql e confere o repositório contra as funções reais: as
chamadas montadas por RepositorioEstoqueSupabase (compras e edições) são executadas por nome
de parâmetro (como o PostgREST faz) e a resposta real de listar_estoque volta pelo conversor do
repositório.
Pulado se o Node.js ou o PGlite (`npm install`) não estiverem disponíveis.
"""

import json
import shutil
import subprocess
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.estoque_repositorio import (
    CompraParaGravar,
    ItemCompraParaGravar,
    RepositorioEstoqueSupabase,
)

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


class _Captura:
    """Cliente falso: só guarda as chamadas RPC como o repositório as monta.

    reservar_id_compra devolve 1, 2...: os mesmos ids que o banco vazio vai reservar.
    """

    def __init__(self, data=1):
        self.chamadas, self.data, self.reservas = [], data, 0

    def rpc(self, funcao, parametros):
        self.chamadas.append({"funcao": funcao, "parametros": parametros})
        if funcao == "reservar_id_compra":
            self.reservas += 1
            return _Resposta(self.reservas)
        if funcao == "registrar_compra":
            return _Resposta({"duplicada": False})
        return _Resposta(self.data)


def test_estoque_test_sql_passa_com_as_migrations_do_checkout():
    resultado = executar("supabase/tests/estoque_test.sql")

    assert resultado.returncode == 0, resultado.stderr + resultado.stdout
    assert "migration aplicada: 20261009120000_estoque.sql" in resultado.stdout
    assert "migration aplicada: 20261010120000_compras_estoque.sql" in resultado.stdout
    assert "estoque_test: ok" in resultado.stdout


def _compra(captura, data, local, local_nome, itens, usuario_id):
    """Grava uma compra pelo repositório (reserva o id e registra), como o servidor faz."""
    repo = RepositorioEstoqueSupabase(captura)
    compra_id = repo.reservar_id_compra()
    repo.registrar_compra(
        CompraParaGravar(compra_id, uuid.uuid4(), data, local, local_nome, usuario_id, itens)
    )
    return compra_id


def test_repositorio_contra_as_funcoes_reais(tmp_path):
    captura = _Captura()
    repo = RepositorioEstoqueSupabase(captura)
    primeira = _compra(
        captura,
        date(2026, 9, 1),
        "mercado_livre",
        None,
        (
            ItemCompraParaGravar(
                ordem=1,
                categoria="filamento",
                quantidade=3,
                cor="Preto",
                material="PLA",
                tipo="silk",
                marca="M",
                peso_rolo_g=Decimal("750.5"),
                valor_unitario=Decimal("119.90"),
            ),
            ItemCompraParaGravar(
                ordem=2,
                categoria="acessorio",
                quantidade=40,
                nome="Argola",
                valor_total=Decimal("14"),
            ),
        ),
        1,
    )
    _compra(
        captura,
        date(2026, 9, 20),
        "loja_fisica",
        "Papelaria",
        (
            ItemCompraParaGravar(
                ordem=1,
                categoria="filamento",
                quantidade=1,
                cor="Preto",
                material="PLA",
                tipo="silk",
                marca="M",
                peso_rolo_g=Decimal("1000"),
                valor_unitario=Decimal("0"),
                imagem_caminho=f"compras/2/itens/1/{uuid.uuid4()}.webp",
            ),
            ItemCompraParaGravar(
                ordem=2,
                categoria="embalagem",
                quantidade=3,
                nome="Caixa",
                valor_total=Decimal("10.00"),
            ),
        ),
        3,
    )
    # Edições (no estoque vazio, os primeiros lotes de cada tabela recebem o id 1).
    repo.editar_filamento(1, "Preto Fosco", "PLA", "velvet", "M", 2)
    repo.editar_item(1, "acessorio", "Argola Inox", 2)

    entrada, saida = tmp_path / "chamadas.json", tmp_path / "listagem.json"
    entrada.write_text(json.dumps(captura.chamadas), encoding="utf-8")
    resultado = executar("--chamadas", str(entrada), "--listagem", str(saida))
    assert resultado.returncode == 0, resultado.stderr + resultado.stdout

    dados = json.loads(saida.read_text(encoding="utf-8"))
    estoque = RepositorioEstoqueSupabase(_Captura(dados)).listar_estoque()
    filamentos = {f.id: f for f in estoque.filamentos}
    itens = {i.id: i for i in estoque.itens}

    assert primeira == 1
    assert [f.id for f in estoque.filamentos] == [2, 1]  # compra mais recente primeiro
    assert (filamentos[1].cor, filamentos[1].tipo) == ("Preto Fosco", "velvet")
    assert filamentos[1].peso_g == Decimal("2251.50")  # 3 x 750,5
    assert filamentos[1].data_compra == date(2026, 9, 1)
    assert filamentos[1].custo_kg == Decimal("119.90") * 1000 / Decimal("750.50")
    assert (filamentos[2].peso_g, filamentos[2].custo_kg) == (Decimal("1000.00"), 0)
    assert (itens[1].nome, itens[1].quantidade, itens[1].categoria) == (
        "Argola Inox",
        40,
        "acessorio",
    )
    assert itens[1].custo_unitario == Decimal("0.35")
    assert (itens[2].categoria, itens[2].quantidade) == ("embalagem", 3)
    assert itens[2].custo_unitario == Decimal("10.00") / 3
