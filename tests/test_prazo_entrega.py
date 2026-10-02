"""Prazo de entrega do pedido: tela, validação no servidor, gravação e exibição.

Também confere o texto da migration do prazo e do SQL corretivo do pedido da Marise (os dois
são executados de verdade só no banco; aqui nada acessa o Supabase).
"""

import re
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.pedidos import ler_prazo
from tests.test_pedidos_consulta import novo_pedido
from tests.test_pedidos_gravacao import enviar, formulario, unico_pedido

client = TestClient(app)
RAIZ = Path(__file__).resolve().parent.parent
MIGRATION = RAIZ / "supabase" / "migrations" / "20261004120000_prazo_entrega.sql"
CORRETIVO = RAIZ / "supabase" / "operacoes" / "20261004_corrigir_etapa_pedido_marise.sql"


def campo_prazo(html):
    inicio = html.index('id="campo-prazo"')
    return html[html.rindex("<div", 0, inicio) : html.index("</div>", inicio)]


# ---------- Leitura e validação ----------


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("05/10/26", date(2026, 10, 5)),
        ("051026", date(2026, 10, 5)),  # sem JavaScript: só os seis números
        ("01/01/00", date(2000, 1, 1)),
        ("31/12/99", date(2099, 12, 31)),
        ("29/02/28", date(2028, 2, 29)),  # bissexto
        ("29/02/00", date(2000, 2, 29)),  # 2000 é bissexto
        ("31/01/26", date(2026, 1, 31)),
        ("30/04/26", date(2026, 4, 30)),
        ("01/01/20", date(2020, 1, 1)),  # data passada é aceita (não há regra de data futura)
    ],
)
def test_prazo_valido_vira_data(texto, esperado):
    assert ler_prazo(texto) == (esperado, None)


@pytest.mark.parametrize(
    ("texto", "erro"),
    [
        ("", "Informe o prazo de entrega."),
        ("5/10/26", "Use o formato dd/mm/aa."),
        ("05/10/2026", "Use o formato dd/mm/aa."),
        ("05-10-26", "Use o formato dd/mm/aa."),
        ("05/10", "Use o formato dd/mm/aa."),
        ("aa/bb/cc", "Use o formato dd/mm/aa."),
        ("05/1O/26", "Use o formato dd/mm/aa."),
        ("29/02/26", "Data inválida."),  # 2026 não é bissexto
        ("29/02/00", None),
        ("31/04/26", "Data inválida."),
        ("31/06/26", "Data inválida."),
        ("32/01/26", "Data inválida."),
        ("00/10/26", "Data inválida."),
        ("05/00/26", "Data inválida."),
        ("05/13/26", "Data inválida."),
    ],
)
def test_prazo_invalido_tem_mensagem(texto, erro):
    data, problema = ler_prazo(texto)
    assert problema == erro
    assert (data is None) == (erro is not None)


# ---------- Tela ----------


def test_tela_tem_o_campo_prazo_obrigatorio_e_acessivel():
    c = campo_prazo(client.get("/pedidos/novo").text)

    assert "data-regiao" in c
    assert '<label for="prazo_entrega">Prazo de entrega <span class="obrigatorio">*</span>' in c
    assert "(dia, mês e ano: dd/mm/aa)" in c  # formato anunciado pelo leitor de tela
    assert 'name="prazo_entrega" type="text" inputmode="numeric"' in c
    assert 'placeholder="dd/mm/aa" maxlength="8"' in c
    assert 'value=""' in c


def test_prazo_fica_na_linha_dos_dados_do_cliente():
    html = client.get("/pedidos/novo").text
    grade = html[html.index('class="grade grade-cliente"') : html.index("</section>")]

    assert grade.index('id="campo-cliente"') < grade.index('id="campo-contato"')
    assert grade.index('id="campo-contato"') < grade.index('id="campo-prazo"')


def test_javascript_tem_a_mascara_e_a_validacao_do_prazo():
    js = client.get("/static/js/pedido-novo.js").text

    assert "prazo_entrega: mascaraPrazo" in js
    assert "prazo_entrega: 6" in js  # no máximo seis números
    assert "problemaDoPrazo" in js


# ---------- Envio ----------


def test_prazo_obrigatorio_no_servidor(repo_pedidos):
    resposta = enviar(formulario(prazo_entrega=""))

    assert resposta.status_code == 422
    c = campo_prazo(resposta.text)
    assert "tem-erro" in c
    assert 'aria-invalid="true" aria-describedby="prazo_entrega-erro"' in c
    assert '<p class="campo-erro" id="prazo_entrega-erro">Informe o prazo de entrega.</p>' in c
    assert repo_pedidos.pedidos == {}


def test_prazo_ausente_do_formulario_e_recusado(repo_pedidos):
    dados = formulario()
    del dados["prazo_entrega"]

    assert enviar(dados).status_code == 422
    assert repo_pedidos.pedidos == {}


@pytest.mark.parametrize("prazo", ["31/04/26", "29/02/27", "5/10/26", "abc"])
def test_prazo_invalido_e_recusado_e_preservado(repo_pedidos, prazo):
    resposta = enviar(formulario(prazo_entrega=prazo))

    assert resposta.status_code == 422
    c = campo_prazo(resposta.text)
    assert f'value="{prazo}"' in c and 'id="prazo_entrega-erro"' in c
    assert repo_pedidos.pedidos == {}


@pytest.mark.parametrize("prazo", ["05/10/26", "051026", " 05/10/26 "])
def test_prazo_e_gravado_como_data(repo_pedidos, prazo):
    assert enviar(formulario(prazo_entrega=prazo)).status_code == 200

    assert unico_pedido(repo_pedidos).prazo_entrega == date(2026, 10, 5)


def test_prazo_bissexto_e_gravado(repo_pedidos):
    assert enviar(formulario(prazo_entrega="29/02/28")).status_code == 200

    assert unico_pedido(repo_pedidos).prazo_entrega == date(2028, 2, 29)


def test_prazo_valido_e_preservado_quando_outro_campo_tem_erro(repo_pedidos):
    resposta = enviar(formulario(cliente="", prazo_entrega="051026"))

    assert resposta.status_code == 422
    c = campo_prazo(resposta.text)
    assert 'value="05/10/26"' in c  # já normalizado com as barras
    assert "tem-erro" not in c
    assert repo_pedidos.pedidos == {}


def test_prazo_e_limpo_depois_de_salvar(repo_pedidos):
    resposta = enviar(formulario(prazo_entrega="05/10/26"))

    assert "salvo com sucesso" in resposta.text
    assert 'value=""' in campo_prazo(resposta.text)
    assert "05/10/26" not in resposta.text


def test_falha_ao_gravar_preserva_o_prazo(repo_pedidos):
    from app.pedidos_repositorio import FalhaAoGravar

    repo_pedidos.falhar_em["criar_pedido"] = FalhaAoGravar()

    resposta = enviar(formulario(prazo_entrega="05/10/26"))

    assert resposta.status_code == 503
    assert 'value="05/10/26"' in campo_prazo(resposta.text)


def test_csrf_invalido_preserva_o_prazo(repo_pedidos):
    resposta = enviar(formulario(csrf="outro", prazo_entrega="05/10/26"))

    assert resposta.status_code == 403
    assert 'value="05/10/26"' in campo_prazo(resposta.text)
    assert repo_pedidos.pedidos == {}


# ---------- Exibição ----------


def test_detalhes_mostram_o_prazo(repo_pedidos):
    pedido = novo_pedido(repo_pedidos, prazo_entrega=date(2026, 10, 5))

    html = client.get(f"/pedidos/{pedido.id}").text

    assert '<dt>Prazo de entrega</dt><dd><time datetime="2026-10-05">05/10/26</time></dd>' in html


def test_listagem_de_pedidos_preserva_o_card_sem_prazo(repo_pedidos):
    novo_pedido(repo_pedidos, prazo_entrega=date(2026, 10, 5))

    html = client.get("/pedidos").text

    assert "05/10/26" not in html and "Prazo" not in html


# ---------- Migration do prazo (texto) ----------


def sql_sem_comentarios(caminho: Path) -> str:
    return re.sub(r"--[^\n]*", "", caminho.read_text(encoding="utf-8")).lower()


def test_migration_confere_antes_de_alterar_e_nao_inventa_prazo():
    sql = sql_sem_comentarios(MIGRATION)

    conferencia = sql.index("do $confere$")
    assert conferencia < sql.index("alter table public.pedidos add column prazo_entrega date;")
    assert "where not (id = 1 and lower(btrim(cliente_nome)) = 'marise')" in sql
    # Só o pedido 1 da Marise, só se ainda estiver sem prazo; nada mais é alterado.
    antes_das_funcoes = sql[: sql.index("drop function")]
    (atualizacao,) = re.findall(r"update public\.pedidos\s+set[^;]*;", antes_das_funcoes)
    assert "set prazo_entrega = date '2026-10-05'" in atualizacao
    assert "where id = 1" in atualizacao and "and prazo_entrega is null" in atualizacao
    assert "lower(btrim(cliente_nome)) = 'marise'" in atualizacao
    assert re.findall(r"(\w+)\s*=", atualizacao.split("where")[0]) == ["prazo_entrega"]
    assert sql.index(atualizacao) < sql.index("alter column prazo_entrega set not null")
    assert "set default" not in sql  # nenhum prazo padrão no banco


def test_migration_nao_altera_migrations_anteriores():
    migrations = sorted(p.name for p in (RAIZ / "supabase" / "migrations").glob("*.sql"))

    assert migrations[-1] == MIGRATION.name
    assert MIGRATION.name > "20261003120000_producao.sql"


def test_migration_troca_a_funcao_de_avanco_por_mover_etapa_producao():
    sql = sql_sem_comentarios(MIGRATION)

    assert "drop function public.avancar_etapa_producao(bigint, text, text, bigint);" in sql
    assert "create function public.avancar" not in sql
    corpo = sql[sql.index("create function public.mover_etapa_producao(") :]
    corpo = corpo[: corpo.index("$corpo$;")]
    assert "security definer" in corpo and "set search_path = ''" in corpo
    assert "for update" in corpo
    for codigo in ("pt403", "pt404", "pt409", "pt422"):
        assert f"errcode = '{codigo}'" in corpo
    permitidos = re.findall(r"\('([a-z_]+)', '([a-z_]+)'\)", corpo)
    assert permitidos == [
        ("fila_producao", "em_producao"),
        ("em_producao", "fila_producao"),
        ("em_producao", "aguardando_entrega"),
        ("aguardando_entrega", "entregue"),
    ]


@pytest.mark.parametrize(
    "assinatura",
    [
        "public.criar_pedido(\n"
        "  bigint, bigint, text, text, text, text, text, date, text, numeric, jsonb\n)",
        "public.listar_pedidos()",
        "public.consultar_pedido(bigint)",
        "public.listar_producao()",
        "public.mover_etapa_producao(bigint, text, text, bigint)",
    ],
)
def test_migration_protege_as_funcoes(assinatura):
    sql = sql_sem_comentarios(MIGRATION)

    assert f"revoke all on function {assinatura}" in sql
    assert f"grant execute on function {assinatura}" in sql
    revogacao = sql[sql.index(f"revoke all on function {assinatura}") :]
    assert revogacao[: revogacao.index(";")].endswith("from public, anon, authenticated")
    concessao = sql[sql.index(f"grant execute on function {assinatura}") :]
    assert concessao[: concessao.index(";")].endswith("to service_role")


def test_migration_remove_as_versoes_antigas_antes_de_recriar():
    sql = sql_sem_comentarios(MIGRATION)

    for antiga in (
        "drop function public.criar_pedido(bigint, bigint, text, text, text, text, text, text,"
        " numeric, jsonb);",
        "drop function public.listar_pedidos();",
        "drop function public.listar_producao();",
    ):
        assert antiga in sql, antiga
    assert len(re.findall(r"create (or replace )?function", sql)) == 5
    assert sql.count("security definer") == 5
    assert sql.count("set search_path = ''") == 5


# ---------- SQL corretivo do pedido da Marise (texto) ----------


def test_sql_corretivo_fica_fora_das_migrations_e_marcado_como_operacao_unica():
    texto = CORRETIVO.read_text(encoding="utf-8")

    assert CORRETIVO.parent.name == "operacoes"
    assert not list((RAIZ / "supabase" / "migrations").glob("*corrigir*"))
    assert "OPERAÇÃO ÚNICA, MANUAL E NÃO AUTOMÁTICA. NÃO É MIGRATION." in texto


def test_sql_corretivo_confere_o_alvo_e_altera_so_a_etapa():
    sql = sql_sem_comentarios(CORRETIVO)

    assert sql.lstrip().startswith("begin;") and sql.rstrip().endswith("commit;")
    assert "where nome = 'leandro'" in sql
    assert "where id = 1\n     for update" in sql
    for conferencia in (
        "lower(btrim(v_pedido.cliente_nome)) is distinct from 'marise'",
        "v_pedido.etapa_producao is distinct from 'entregue'",
        "v_pedido.valor_total is distinct from 132.00",
        "v_unidades <> 11",
        "v_linhas <> 1",
    ):
        assert conferencia in sql, conferencia
    (atualizacao,) = re.findall(r"update public\.pedidos\s+set[^;]*;", sql)
    campos = re.findall(r"(\w+)\s*=", atualizacao.split("where")[0])
    assert campos == ["etapa_producao", "etapa_atualizada_em", "etapa_atualizada_por"]
    assert "'em_producao'" in atualizacao and "now()" in atualizacao
    for condicao in ("id = 1", "'marise'", "etapa_producao = 'entregue'", "valor_total = 132.00"):
        assert condicao in atualizacao.split("where")[1], condicao
    for proibido in ("prazo_entrega =", "status_pagamento =", "pedido_itens set", "delete "):
        assert proibido not in sql, proibido
    assert sql.count("update ") == 1
