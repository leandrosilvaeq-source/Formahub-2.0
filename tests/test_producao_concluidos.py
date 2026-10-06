"""Produção: pedido concluído (entregue e pago) sai do quadro; títulos das colunas em destaque.

Repositório e Storage falsos; nada acessa o Supabase. A migration é conferida como texto.
"""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.test_pedidos_consulta import novo_pedido
from tests.test_producao import ETAPAS, card, contador, mover
from tests.test_producao_comentarios_pagamento import pagar

client = TestClient(app)

RAIZ = Path(__file__).resolve().parent.parent
MIGRATIONS = RAIZ / "supabase" / "migrations"
MIGRATION = MIGRATIONS / "20261006120000_producao_sem_concluidos.sql"
ANTERIOR = MIGRATIONS / "20261005120000_comentarios_pagamento_producao.sql"
TESTE_SQL = RAIZ / "supabase" / "tests" / "producao_sem_concluidos_test.sql"
CSS = RAIZ / "app" / "static" / "css" / "app.css"

COMBINACOES = [(etapa, status) for etapa in ETAPAS for status in ("pendente", "pago")]


def no_quadro(pedido_id) -> bool:
    return f'data-pedido="{pedido_id}"' in client.get("/producao").text


# ---------- Regra de conclusão ----------


@pytest.mark.parametrize(("etapa", "status"), COMBINACOES)
def test_so_entregue_e_pago_sai_do_quadro(repo_pedidos, etapa, status):
    pedido = novo_pedido(repo_pedidos, status_pagamento=status)
    repo_pedidos.etapas[pedido.id] = etapa

    html = client.get("/producao").text

    concluido = etapa == "entregue" and status == "pago"
    assert (f'data-pedido="{pedido.id}"' in html) is not concluido
    if not concluido:
        assert f'data-pedido="{pedido.id}" data-etapa="{etapa}"' in html


def test_entregue_pendente_continua_na_coluna_entregue(repo_pedidos):
    pedido = novo_pedido(repo_pedidos, status_pagamento="pendente")
    repo_pedidos.etapas[pedido.id] = "entregue"

    html = client.get("/producao").text

    assert contador(html, "entregue") == 1
    assert 'data-status="pendente"' in card(html, pedido.id)


def test_concluido_continua_em_pedidos_e_nos_detalhes_sem_alteracao(repo_pedidos):
    pedido = novo_pedido(repo_pedidos, cliente="Cliente Concluído", status_pagamento="pago")
    repo_pedidos.etapas[pedido.id] = "entregue"
    escritas = repo_pedidos.escritas

    assert not no_quadro(pedido.id)
    assert "Cliente Concluído" in client.get("/pedidos").text
    detalhe = client.get(f"/pedidos/{pedido.id}")
    assert detalhe.status_code == 200 and "Cliente Concluído" in detalhe.text
    # Nada foi apagado nem alterado: o pedido e a etapa continuam gravados.
    assert repo_pedidos.pedidos[pedido.id] == pedido
    assert repo_pedidos.etapas[pedido.id] == "entregue"
    assert repo_pedidos.escritas == escritas


def test_contadores_nao_contam_o_concluido(repo_pedidos):
    for status in ("pago", "pendente", "pago"):
        p = novo_pedido(repo_pedidos, status_pagamento=status)
        repo_pedidos.etapas[p.id] = "entregue"
    novo_pedido(repo_pedidos, status_pagamento="pago")  # pago na fila: continua

    html = client.get("/producao").text

    assert [contador(html, e) for e in ETAPAS] == [1, 0, 0, 1]


def test_marcar_como_pago_na_coluna_entregue_tira_do_quadro(repo_pedidos):
    pedido = novo_pedido(repo_pedidos, status_pagamento="pendente")
    repo_pedidos.etapas[pedido.id] = "entregue"

    assert pagar(pedido.id, "pendente", "pago").json()["status"] == "pago"

    assert not no_quadro(pedido.id)
    assert repo_pedidos.etapas[pedido.id] == "entregue"
    # Voltar para pendente devolve o pedido à coluna Entregue.
    assert pagar(pedido.id, "pago", "pendente").status_code == 200
    assert no_quadro(pedido.id)


def test_pedido_pago_movido_para_entregue_sai_do_quadro(repo_pedidos):
    pedido = novo_pedido(repo_pedidos, status_pagamento="pago")
    repo_pedidos.etapas[pedido.id] = "aguardando_entrega"
    assert no_quadro(pedido.id)

    resposta = mover(pedido.id, "aguardando_entrega", "entregue")

    assert resposta.status_code == 200 and resposta.json()["etapa"] == "entregue"
    assert not no_quadro(pedido.id)
    assert repo_pedidos.pedidos[pedido.id].status_pagamento == "pago"


@pytest.mark.parametrize("etapa", ["fila_producao", "em_producao", "aguardando_entrega"])
def test_pagar_antes_da_entrega_mantem_no_quadro(repo_pedidos, etapa):
    pedido = novo_pedido(repo_pedidos, status_pagamento="pendente")
    repo_pedidos.etapas[pedido.id] = etapa

    pagar(pedido.id, "pendente", "pago")

    assert f'data-pedido="{pedido.id}" data-etapa="{etapa}"' in client.get("/producao").text


def test_javascript_retira_o_card_so_depois_do_servidor():
    js = (RAIZ / "app" / "static" / "js" / "producao.js").read_text(encoding="utf-8")

    regra = re.search(r"function concluido\(etapa, statusPagamento\) \{([^}]*)\}", js).group(1)
    assert 'etapa === "entregue" && statusPagamento === "pago"' in regra
    # Nos dois caminhos, a retirada fica dentro do "corpo.ok" (depois da resposta).
    for chamada in (
        "if (concluido(corpo.etapa, statusDoPagamento(card))) {",
        "if (concluido(card.dataset.etapa, corpo.status)) {",
    ):
        antes = js[: js.index(chamada)]
        assert antes.rstrip().endswith("if (corpo && corpo.ok) {"), chamada
    assert js.count("retirarConcluido(card);") == 2


# ---------- Tamanhos: títulos das colunas e textos do card ----------


def regra_css(css: str, seletor: str) -> str:
    return re.search(re.escape(seletor) + r" \{([^}]*)\}", css).group(1)


def tamanho_rem(regra: str) -> float:
    return float(re.search(r"font-size: ([0-9.]+)rem;", regra).group(1))


# Regra do card -> tamanho em px (com a raiz de 16 px). Mínimo de legibilidade: 11 px nos
# rótulos e na última atualização; 12 px em todo o resto.
FONTES_DO_CARD = {
    ".producao-card-topo h3": 12,  # nome do cliente
    ".producao-card-produtos": 12,  # produtos e unidades
    ".producao-card-dados dt": 11,  # rótulos: Pagamento, Entrega, Prazo, Observações
    ".producao-card-dados dd": 12,  # valores (inclui observações e forma de pagamento)
    ".producao-pagamento": 12,  # selo Pendente/Pago
    ".producao-card .pedido-card-aviso": 12,  # "Foto indisponível no momento."
    ".producao-comentario label": 11,
    ".producao-comentario-campo": 12,
    ".producao-comentario-info": 11,  # última atualização do comentário
    ".producao-comentario-mensagem": 12,
    ".producao-card .botao": 12,  # Salvar comentário, Ver pedido e movimentação
}


@pytest.mark.parametrize(("seletor", "px"), FONTES_DO_CARD.items())
def test_fontes_do_card_com_minimo_de_legibilidade(seletor, px):
    regra = regra_css(CSS.read_text(encoding="utf-8"), seletor)

    assert tamanho_rem(regra) * 16 == px
    assert px >= 11


def test_reducao_fica_so_no_card_e_preserva_icones_e_alturas():
    css = CSS.read_text(encoding="utf-8")

    # Regras gerais (outras telas) sem mudança.
    assert tamanho_rem(regra_css(css, ".botao")) == 0.875
    assert tamanho_rem(regra_css(css, ".selo-status")) == 0.8
    assert "min-height: var(--alvo-toque);" in regra_css(css, ".botao")
    # Ícones, foto e alvos de toque do card.
    for seletor, medidas in (
        (".producao-recolher svg", ("width: 22px;", "height: 22px;")),
        (".producao-card-foto", ("width: 56px;", "height: 56px;")),
        (".producao-recolher", ("width: var(--alvo-toque);", "height: var(--alvo-toque);")),
        (".producao-pagamento", ("min-height: var(--alvo-toque);",)),
        (".producao-comentario-campo", ("min-height: 4.2rem;",)),
    ):
        regra = regra_css(css, seletor)
        for medida in medidas:
            assert medida in regra, (seletor, medida)
    # O cliente continua o título principal: Sora Bold e não menor que nenhum texto do card.
    cliente = tamanho_rem(regra_css(css, ".producao-card-topo h3"))
    assert all(cliente * 16 >= px for px in FONTES_DO_CARD.values())


def test_titulo_das_colunas_com_1_5rem():
    css = CSS.read_text(encoding="utf-8")
    titulo = regra_css(css, ".coluna-topo h2")

    # Era 2rem; 25% menor = 1,5rem. Quebra entre as palavras.
    assert tamanho_rem(titulo) == 2 * 0.75 == 1.5
    assert "min-width: 0;" in titulo and "flex: 1;" in titulo
    assert "overflow-wrap: break-word;" in titulo
    # Sora Bold vem da regra geral dos títulos (h1, h2, h3), que não mudou.
    geral = re.search(r"h1,\nh2,\nh3 \{([^}]*)\}", css).group(1)
    assert "font-family: var(--fonte-titulo);" in geral and "font-weight: 700;" in geral
    # Contador e ícone não mudam.
    assert tamanho_rem(regra_css(css, ".coluna-contador")) == 0.85
    icone = regra_css(css, ".coluna-icone")
    assert "width: 22px;" in icone and "height: 22px;" in icone
    # Com 1,5rem o título cabe ao lado do ícone e do contador em qualquer largura: a regra
    # especial de 1024 a 1279 px (título abaixo deles) foi removida.
    assert "max-width: 1279.98px" not in css
    assert ".coluna-topo h2 { grid-area" not in css and ".coluna-icone { grid-area" not in css


def test_textos_das_colunas_nao_mudam(repo_pedidos):
    html = client.get("/producao").text

    assert re.findall(r'<h2 id="coluna-[a-z_]+">([^<]+)</h2>', html) == [
        "Na Fila de Produção",
        "Em Produção",
        "Ag. Entrega",
        "Entregue",
    ]


def test_nome_longo_aparece_inteiro_no_card(repo_pedidos):
    nome = "Maria " + "Aparecida" * 12 + " da Silva"
    pedido = novo_pedido(repo_pedidos, cliente=nome)

    assert f"<h3>{nome}</h3>" in card(client.get("/producao").text, pedido.id)


# ---------- Migration (texto) ----------


def sql_sem_comentarios(caminho=MIGRATION) -> str:
    return re.sub(r"--[^\n]*", "", caminho.read_text(encoding="utf-8")).lower()


def corpo_da_funcao(sql: str) -> str:
    """De "public.listar_producao()" (após o create) até o fim do corpo."""
    inicio = re.search(r"create (?:or replace )?function public\.listar_producao\(\)", sql).end()
    return sql[inicio : sql.index("$corpo$;", inicio)]


def sem_espacos_extras(texto: str) -> str:
    return re.sub(r"\s+", " ", texto).strip()


def test_migration_so_recria_listar_producao():
    migrations = sorted(p.name for p in MIGRATIONS.glob("*.sql"))
    sql = sql_sem_comentarios()

    assert MIGRATION.name in migrations  # as seguintes (ex.: custos) não a alteram
    assert MIGRATION.name > "20261005120000_comentarios_pagamento_producao.sql"
    assert re.findall(r"create (?:or replace )?function public\.(\w+)", sql) == ["listar_producao"]
    assert "create or replace function public.listar_producao()" in sql
    # Sem drop (nada de sobrecarga nem perda de permissões) e sem mexer em dados ou tabelas.
    for proibido in (
        "drop ",
        "alter table",
        "add column",
        "insert into",
        "update ",
        "delete from",
        "truncate",
        "sequence",
        "setval",
        "conclu",
    ):
        assert proibido not in sql, proibido


def test_migration_preserva_assinatura_retorno_e_seguranca():
    novo = corpo_da_funcao(sql_sem_comentarios())
    anterior = corpo_da_funcao(sql_sem_comentarios(ANTERIOR))

    def cabecalho(corpo):
        return corpo[: corpo.index("as $corpo$")]

    assert cabecalho(novo) == cabecalho(anterior)  # retorno, stable, definer, search_path
    assert "security definer" in novo and "set search_path = ''" in novo
    sql = sql_sem_comentarios()
    assert (
        "revoke all on function public.listar_producao() from public, anon, authenticated;" in sql
    )
    assert "grant execute on function public.listar_producao() to service_role;" in sql
    assert "grant " not in sql.replace("grant execute on function public.listar_producao()", "")


def test_migration_so_acrescenta_o_filtro_de_concluido():
    novo = corpo_da_funcao(sql_sem_comentarios())
    anterior = corpo_da_funcao(sql_sem_comentarios(ANTERIOR))
    filtro = "where (p.etapa_producao = 'entregue' and p.status_pagamento = 'pago') is not true\n"

    assert filtro in novo
    assert sem_espacos_extras(novo.replace(filtro, "")) == sem_espacos_extras(anterior)


def test_teste_sql_e_transacional_e_usa_ids_negativos():
    sql = sql_sem_comentarios(TESTE_SQL)

    assert sql.lstrip().startswith("begin;") and "\nrollback;" in sql
    assert "commit" not in sql and "setval" not in sql and "nextval" not in sql
    assert "overriding system value" in sql
    ids = [int(i) for i in re.findall(r"\n    \((-?\d+), '", sql)]
    assert ids == [-1, -2, -3, -4, -5, -6, -7, -8]
    # As 8 combinações de etapa e pagamento.
    combinacoes = set(
        re.findall(r"'(pendente|pago)', '(?:entrega|retirada)'[^)]*'([a-z_]+)'\)", sql)
    )
    assert combinacoes == {(s, e) for e in ETAPAS for s in ("pendente", "pago")}
    for chamada in re.findall(
        r"public\.(?:alterar_status_pagamento|mover_etapa_producao|consultar_pedido)\((-?\d+)", sql
    ):
        assert int(chamada) < 0
    for verificacao in ("pg_get_function_result", "prosecdef", "has_function_privilege"):
        assert verificacao in sql, verificacao
