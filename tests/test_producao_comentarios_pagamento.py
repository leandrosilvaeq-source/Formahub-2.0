"""Produção: card recolhível, comentários da produção e alternância do status do pagamento.

Repositório e Storage falsos; nada acessa o Supabase. A migration é conferida como texto.
"""

import re
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from app.auth.repositorio import Usuario
from app.auth.rotas import exigir_usuario
from app.main import app
from app.pedidos_repositorio import AlteracaoRecusada, FalhaAoAlterar
from tests.test_pedidos_consulta import com_imagem, item, novo_pedido
from tests.test_producao import card, mover

client = TestClient(app)

RAIZ = Path(__file__).resolve().parent.parent
MIGRATION = RAIZ / "supabase" / "migrations" / "20261005120000_comentarios_pagamento_producao.sql"


def comentar(pedido_id, comentario, esperado="", **extra):
    dados = {"csrf": "csrf-de-teste", "comentario_esperado": esperado, "comentario": comentario}
    return client.post(f"/producao/{pedido_id}/comentario", data={**dados, **extra})


def pagar(pedido_id, esperado, novo, **extra):
    dados = {"csrf": "csrf-de-teste", "status_esperado": esperado, "novo_status": novo}
    return client.post(f"/producao/{pedido_id}/pagamento", data={**dados, **extra})


def partes_do_card(c, pedido_id):
    """(sempre à vista, oculto no card recolhido): o oculto são o corpo e as ações."""
    corpo_inicio = c.index(f'<div class="producao-card-corpo" id="card-corpo-{pedido_id}"')
    corpo_fim = c.index('<div class="producao-comentario">', corpo_inicio)
    acoes_inicio = c.index(f'<div class="producao-card-acoes" id="card-acoes-{pedido_id}"')
    visivel = c[:corpo_inicio] + c[corpo_fim:acoes_inicio]
    return visivel, c[corpo_inicio:corpo_fim] + c[acoes_inicio:]


def como_kassia(request: Request) -> Usuario:
    usuario = Usuario(id=2, nome="Kassia")
    request.state.usuario = usuario
    request.state.csrf = "csrf-de-teste"
    return usuario


# ---------- Largura própria da página ----------


def test_producao_usa_largura_propria_e_as_outras_telas_nao(repo_pedidos):
    pedido = novo_pedido(repo_pedidos)

    assert '<main class="container container-producao">' in client.get("/producao").text
    for caminho in ("/pedidos", "/pedidos/novo", f"/pedidos/{pedido.id}", "/"):
        assert '<main class="container">' in client.get(caminho).text, caminho


def test_css_da_largura_nao_muda_o_container_global():
    css = (RAIZ / "app" / "static" / "css" / "app.css").read_text(encoding="utf-8")

    assert "--largura-max: 1080px;" in css
    regra = re.search(r"\.container-producao \{([^}]*)\}", css).group(1)
    assert "max-width: 1920px;" in regra and "padding: 0 clamp(" in regra
    assert "@media (min-width: 768px) {\n  .quadro { grid-template-columns: repeat(2," in css
    assert "@media (min-width: 1024px) {\n  .quadro { grid-template-columns: repeat(4," in css


# ---------- Recolher e expandir ----------


def test_card_tem_botao_de_recolher_acessivel(repo_pedidos):
    pedido = novo_pedido(repo_pedidos)

    c = card(client.get("/producao").text, pedido.id)

    botao = re.search(r'<button type="button" class="producao-recolher"[^>]*>', c).group(0)
    # O card já vem recolhido do servidor: nada depende do JavaScript para esconder.
    assert c.startswith('<li class="producao-card recolhido" ')
    assert 'aria-expanded="false"' in botao
    # Controla as duas regiões que somem no card recolhido: detalhes e ações.
    assert f'aria-controls="card-corpo-{pedido.id} card-acoes-{pedido.id}"' in botao
    assert 'title="Expandir pedido"' in botao
    assert '<span class="visualmente-oculto">Expandir pedido</span>' in c
    assert f'<div class="producao-card-corpo" id="card-corpo-{pedido.id}" hidden>' in c
    assert f'<div class="producao-card-acoes" id="card-acoes-{pedido.id}" hidden>' in c


def test_todos_os_cards_comecam_recolhidos(repo_pedidos):
    for etapa in ("fila_producao", "em_producao", "aguardando_entrega", "entregue"):
        repo_pedidos.etapas[novo_pedido(repo_pedidos).id] = etapa

    html = client.get("/producao").text

    assert html.count('<li class="producao-card recolhido" ') == 4
    assert html.count('class="producao-recolher" aria-expanded="false"') == 4
    assert 'class="producao-recolher" aria-expanded="true"' not in html
    assert len(re.findall(r'class="producao-card-(?:corpo|acoes)" id="[^"]+" hidden>', html)) == 8


def test_recolhido_mostra_cliente_foto_produtos_unidades_e_comentario(repo_pedidos):
    pedido = novo_pedido(
        repo_pedidos,
        itens=[item(1, "Caneca", 2, "1.00", com_imagem(repo_pedidos, 1, 1))],
        observacoes="Observação original",
    )
    repo_pedidos.etapas[pedido.id] = "em_producao"
    repo_pedidos.comentarios[pedido.id] = ("Pintar de azul", datetime(2026, 10, 1, 9), 2)

    c = card(client.get("/producao").text, pedido.id)
    visivel, oculto = partes_do_card(c, pedido.id)

    for sempre in (
        "<h3>Cliente Teste</h3>",
        "Caneca",
        '<span class="pedido-card-unidades">2 unidades</span>',
        "producao-card-foto",
        "Comentários da produção",
        ">Pintar de azul</textarea>",
        ">Salvar comentário</button>",
        "Atualizado por Kassia em 01/10/2026 às 09:00",
        "producao-recolher",
    ):
        assert sempre in visivel, sempre
        assert sempre not in oculto, sempre
    for so_expandido in (
        "Prazo",
        "Pagamento",
        "Entrega",
        "Observação original",
        "Ver pedido",
        "producao-mover",
        "producao-pagamento",
    ):
        assert so_expandido not in visivel, so_expandido
        assert so_expandido in oculto, so_expandido
    # Uma única foto no card (a mesma nos dois estados): uma só URL assinada.
    assert c.count("<img") == 1 and len(repo_pedidos.assinaturas) == 1


def test_recolhido_sem_foto_nao_tem_imagem(repo_pedidos):
    pedido = novo_pedido(repo_pedidos)

    visivel, _ = partes_do_card(card(client.get("/producao").text, pedido.id), pedido.id)

    assert "<img" not in visivel and "pedido-card-aviso" not in visivel
    assert repo_pedidos.assinaturas == []


def test_css_nao_esconde_mais_a_foto_do_card_recolhido():
    css = (RAIZ / "app" / "static" / "css" / "app.css").read_text(encoding="utf-8")

    assert ".recolhido .producao-card-foto" not in css
    assert ".producao-recolher:focus-visible" in css


# ---------- Comentários da produção ----------


def test_comentario_fica_separado_das_observacoes(repo_pedidos):
    pedido = novo_pedido(repo_pedidos, observacoes="Embalar para presente")

    c = card(client.get("/producao").text, pedido.id)

    assert "<dt>Observações do pedido</dt><dd>Embalar para presente</dd>" in c
    assert f'<label for="comentario-{pedido.id}">Comentários da produção</label>' in c
    campo = re.search(r"<textarea[^>]*>([^<]*)</textarea>", c)
    assert campo.group(1) == ""  # as observações não vão para o comentário
    assert 'maxlength="2000"' in campo.group(0) and 'data-salvo=""' in campo.group(0)
    assert f'aria-describedby="comentario-{pedido.id}-info"' in campo.group(0)
    assert ">Salvar comentário</button>" in c
    assert 'aria-live="polite"' in c
    # Sem atualização ainda: nenhuma linha de auditoria.
    assert f'<p id="comentario-{pedido.id}-info" class="producao-comentario-info"></p>' in c


def test_card_mostra_comentario_e_auditoria(repo_pedidos):
    pedido = novo_pedido(repo_pedidos)
    repo_pedidos.comentarios[pedido.id] = ('Linha 1\n"Linha 2"', datetime(2026, 10, 1, 14, 32), 2)

    c = card(client.get("/producao").text, pedido.id)

    assert 'data-salvo="Linha 1\n&#34;Linha 2&#34;"' in c
    assert ">Linha 1\n&#34;Linha 2&#34;</textarea>" in c
    assert "Atualizado por Kassia em 01/10/2026 às 14:32" in c


def test_salvar_comentario_novo(repo_pedidos):
    pedido = novo_pedido(repo_pedidos, observacoes="Original")

    resposta = comentar(pedido.id, "  Pintar de azul  ")

    assert resposta.status_code == 200
    assert resposta.headers["cache-control"] == "no-store"
    assert resposta.json() == {
        "ok": True,
        "pedido_id": pedido.id,
        "comentario": "Pintar de azul",
        "auditoria": "Atualizado por Leandro em 01/10/2026 às 09:01",
        "mensagem": "Comentário salvo.",
    }
    assert repo_pedidos.alteracoes_comentario == [(pedido.id, None, "Pintar de azul", 1)]
    assert repo_pedidos.pedidos[pedido.id].observacoes == "Original"
    assert "Atualizado por Leandro" in card(client.get("/producao").text, pedido.id)


def test_editar_e_apagar_comentario(repo_pedidos):
    pedido = novo_pedido(repo_pedidos)
    comentar(pedido.id, "Primeiro")

    editado = comentar(pedido.id, "Linha 1\r\nLinha 2", esperado="Primeiro")
    apagado = comentar(pedido.id, "   \r\n ", esperado="Linha 1\nLinha 2")

    assert editado.json()["comentario"] == "Linha 1\nLinha 2"
    assert apagado.status_code == 200
    assert apagado.json()["comentario"] == "" and apagado.json()["mensagem"] == (
        "Comentário removido."
    )
    assert [a[2] for a in repo_pedidos.alteracoes_comentario] == [
        "Primeiro",
        "Linha 1\nLinha 2",
        None,
    ]
    c = card(client.get("/producao").text, pedido.id)
    assert 'data-salvo=""' in c and "Atualizado por Leandro" in c


def test_limite_de_2000_caracteres(repo_pedidos):
    pedido = novo_pedido(repo_pedidos)

    aceito = comentar(pedido.id, " " + "x" * 2000 + "\n")
    recusado = comentar(pedido.id, "y" * 2001, esperado="x" * 2000)

    assert aceito.status_code == 200 and len(aceito.json()["comentario"]) == 2000
    assert recusado.status_code == 422
    assert recusado.json()["mensagem"] == "O comentário pode ter no máximo 2.000 caracteres."
    assert len(repo_pedidos.alteracoes_comentario) == 1


def test_quebra_de_linha_do_formulario_conta_como_um_caractere(repo_pedidos):
    pedido = novo_pedido(repo_pedidos)

    resposta = comentar(pedido.id, ("a" * 9 + "\r\n") * 200)  # 2.000 com \n, 2.200 com \r\n

    assert resposta.status_code == 200


def test_conflito_de_comentario(repo_pedidos):
    pedido = novo_pedido(repo_pedidos)
    repo_pedidos.comentarios[pedido.id] = ("De outro usuário", datetime(2026, 10, 1, 9), 3)

    resposta = comentar(pedido.id, "Meu texto", esperado="")

    assert resposta.status_code == 409
    assert resposta.json()["erro"] == "conflito"
    assert "alterado por outro usuário" in resposta.json()["mensagem"]
    assert repo_pedidos.comentarios[pedido.id][0] == "De outro usuário"
    assert repo_pedidos.alteracoes_comentario == []


def test_comentario_usa_o_usuario_da_sessao(repo_pedidos):
    pedido = novo_pedido(repo_pedidos)

    comentar(pedido.id, "A", usuario_id="3", p_usuario_id="2")
    app.dependency_overrides[exigir_usuario] = como_kassia
    comentar(pedido.id, "B", esperado="A", usuario_id="1")

    assert [a[3] for a in repo_pedidos.alteracoes_comentario] == [1, 2]
    assert "Atualizado por Kassia" in card(client.get("/producao").text, pedido.id)


def test_comentario_nao_altera_etapa_nem_pedido(repo_pedidos):
    pedido = novo_pedido(repo_pedidos, observacoes="Original")
    repo_pedidos.etapas[pedido.id] = "em_producao"

    comentar(pedido.id, "Texto")

    assert repo_pedidos.etapas[pedido.id] == "em_producao"
    assert repo_pedidos.pedidos[pedido.id] == pedido
    assert repo_pedidos.movimentos == [] and repo_pedidos.alteracoes_pagamento == []


# ---------- Status do pagamento ----------


@pytest.mark.parametrize(
    ("status", "rotulo", "acao"),
    [
        ("pendente", "Pendente", "Marcar pagamento como pago"),
        ("pago", "Pago", "Marcar pagamento como pendente"),
    ],
)
def test_botao_do_pagamento(repo_pedidos, status, rotulo, acao):
    pedido = novo_pedido(repo_pedidos, status_pagamento=status)

    c = card(client.get("/producao").text, pedido.id)

    botao = c[c.index('<button type="button" class="selo-status') :]
    botao = botao[: botao.index("</button>")]
    assert f'class="selo-status selo-status-{status} producao-pagamento"' in botao
    assert f'data-status="{status}"' in botao and f'title="{acao}"' in botao
    assert f'aria-label="{rotulo}. {acao}"' in botao
    assert f'<span class="producao-pagamento-texto">{rotulo}</span>' in botao


@pytest.mark.parametrize(
    ("de", "para", "rotulo", "acao"),
    [
        ("pendente", "pago", "Pago", "Marcar pagamento como pendente"),
        ("pago", "pendente", "Pendente", "Marcar pagamento como pago"),
    ],
)
def test_alterna_o_pagamento_nos_dois_sentidos(repo_pedidos, de, para, rotulo, acao):
    pedido = novo_pedido(repo_pedidos, status_pagamento=de)

    resposta = pagar(pedido.id, de, para)

    assert resposta.status_code == 200
    assert resposta.headers["cache-control"] == "no-store"
    assert resposta.json() == {
        "ok": True,
        "pedido_id": pedido.id,
        "status": para,
        "rotulo": rotulo,
        "acao": acao,
        "mensagem": f"Pagamento marcado como {rotulo.lower()}.",
    }
    assert repo_pedidos.pedidos[pedido.id].status_pagamento == para
    assert repo_pedidos.alteracoes_pagamento == [(pedido.id, de, para, 1)]


def test_pagamento_aparece_em_pedidos_e_detalhes(repo_pedidos):
    pedido = novo_pedido(repo_pedidos, status_pagamento="pendente")

    pagar(pedido.id, "pendente", "pago")

    assert "selo-status-pago" in client.get("/pedidos").text
    assert "selo-status-pago" in client.get(f"/pedidos/{pedido.id}").text
    assert 'data-status="pago"' in card(client.get("/producao").text, pedido.id)


def test_pagamento_nao_altera_etapa_nem_o_resto_do_pedido(repo_pedidos):
    pedido = novo_pedido(repo_pedidos, status_pagamento="pendente", observacoes="Obs")
    repo_pedidos.etapas[pedido.id] = "aguardando_entrega"

    pagar(pedido.id, "pendente", "pago")

    assert repo_pedidos.etapas[pedido.id] == "aguardando_entrega"
    assert repo_pedidos.pedidos[pedido.id] == replace(pedido, status_pagamento="pago")
    assert repo_pedidos.movimentos == [] and repo_pedidos.alteracoes_comentario == []


def test_conflito_de_pagamento(repo_pedidos):
    pedido = novo_pedido(repo_pedidos, status_pagamento="pago")  # outro usuário já marcou

    resposta = pagar(pedido.id, "pendente", "pago")

    assert resposta.status_code == 409
    assert resposta.json() == {
        "ok": False,
        "erro": "conflito",
        "mensagem": "O pagamento deste pedido foi alterado por outro usuário.",
    }
    assert repo_pedidos.alteracoes_pagamento == []


@pytest.mark.parametrize(
    ("de", "para"),
    [
        ("pendente", "pendente"),
        ("pago", "pago"),
        ("pendente", "estornado"),
        ("qualquer", "pago"),
        ("", "pago"),
        ("pendente", ""),
    ],
)
def test_valores_invalidos_de_pagamento(repo_pedidos, de, para):
    pedido = novo_pedido(repo_pedidos)

    resposta = pagar(pedido.id, de, para)

    assert resposta.status_code == 422 and resposta.json()["erro"] == "invalido"
    assert repo_pedidos.alteracoes_pagamento == []


def test_pagamento_usa_o_usuario_da_sessao(repo_pedidos):
    pedido = novo_pedido(repo_pedidos)

    pagar(pedido.id, "pendente", "pago", usuario_id="3", p_usuario_id="2")
    app.dependency_overrides[exigir_usuario] = como_kassia
    pagar(pedido.id, "pago", "pendente", usuario_id="1")

    assert [a[3] for a in repo_pedidos.alteracoes_pagamento] == [1, 2]


# ---------- Recusas comuns (comentário e pagamento) ----------

ROTAS = [
    ("comentario", lambda pid, **e: comentar(pid, "Texto", **e)),
    ("pagamento", lambda pid, **e: pagar(pid, "pendente", "pago", **e)),
]


@pytest.mark.parametrize(("nome", "enviar"), ROTAS)
@pytest.mark.parametrize("csrf", ["", "outro-token"])
def test_csrf_errado_e_recusado(repo_pedidos, nome, enviar, csrf):
    pedido = novo_pedido(repo_pedidos)

    resposta = enviar(pedido.id, csrf=csrf)

    assert resposta.status_code == 403 and resposta.json()["erro"] == "expirada"
    assert repo_pedidos.alteracoes_comentario == [] and repo_pedidos.alteracoes_pagamento == []


@pytest.mark.parametrize(
    ("nome", "caminho"), [("comentario", "comentario"), ("pagamento", "pagamento")]
)
def test_sem_csrf_e_recusado(repo_pedidos, nome, caminho):
    pedido = novo_pedido(repo_pedidos)

    resposta = client.post(
        f"/producao/{pedido.id}/{caminho}",
        data={"comentario": "x", "status_esperado": "pendente", "novo_status": "pago"},
    )

    assert resposta.status_code == 403
    assert repo_pedidos.alteracoes_comentario == [] and repo_pedidos.alteracoes_pagamento == []


@pytest.mark.parametrize(("nome", "enviar"), ROTAS)
def test_origem_de_outro_site_e_recusada(repo_pedidos, nome, enviar):
    pedido = novo_pedido(repo_pedidos)
    dados = {
        "csrf": "csrf-de-teste",
        "comentario": "x",
        "status_esperado": "pendente",
        "novo_status": "pago",
    }

    resposta = client.post(
        f"/producao/{pedido.id}/{nome}", data=dados, headers={"origin": "https://outro.example"}
    )

    assert resposta.status_code == 403
    assert repo_pedidos.alteracoes_comentario == [] and repo_pedidos.alteracoes_pagamento == []


@pytest.mark.parametrize(("nome", "enviar"), ROTAS)
@pytest.mark.parametrize("pedido_id", ["999", "abc", "0", "99999999999999999999"])
def test_pedido_inexistente_ou_invalido(repo_pedidos, nome, enviar, pedido_id):
    novo_pedido(repo_pedidos)

    resposta = enviar(pedido_id)

    assert resposta.status_code == 404 and resposta.json()["erro"] == "nao_encontrado"


@pytest.mark.parametrize(("nome", "enviar"), ROTAS)
@pytest.mark.parametrize(
    ("motivo", "status"),
    [("usuario", 403), ("nao_encontrado", 404), ("conflito", 409), ("invalido", 422)],
)
def test_recusas_do_banco_viram_respostas(repo_pedidos, nome, enviar, motivo, status):
    pedido = novo_pedido(repo_pedidos)
    etapa = "salvar_comentario" if nome == "comentario" else "alterar_pagamento"
    repo_pedidos.falhar_em[etapa] = AlteracaoRecusada(motivo)

    resposta = enviar(pedido.id)

    assert resposta.status_code == status and resposta.json()["erro"] == motivo
    assert resposta.json()["ok"] is False and resposta.json()["mensagem"]


@pytest.mark.parametrize(("nome", "enviar"), ROTAS)
def test_falha_de_banco_e_mensagem_simples(repo_pedidos, nome, enviar):
    pedido = novo_pedido(repo_pedidos)
    etapa = "salvar_comentario" if nome == "comentario" else "alterar_pagamento"
    repo_pedidos.falhar_em[etapa] = FalhaAoAlterar()

    resposta = enviar(pedido.id)

    assert resposta.status_code == 503
    assert resposta.json()["mensagem"].startswith("Não foi possível")
    assert "FalhaAoAlterar" not in resposta.text
    assert repo_pedidos.pedidos[pedido.id] == pedido


def test_sem_configuracao_do_banco_fica_indisponivel():
    from app.pedidos_repositorio import get_repositorio_pedidos

    app.dependency_overrides[get_repositorio_pedidos] = lambda: None

    assert comentar(1, "x").status_code == 503
    assert pagar(1, "pendente", "pago").status_code == 503


@pytest.mark.sem_login
def test_rotas_exigem_login(repo_pedidos):
    pedido = novo_pedido(repo_pedidos)
    anonimo = TestClient(app)

    for caminho in ("comentario", "pagamento"):
        resposta = anonimo.post(
            f"/producao/{pedido.id}/{caminho}",
            data={"comentario": "x", "status_esperado": "pendente", "novo_status": "pago"},
            follow_redirects=False,
        )
        assert resposta.status_code == 303
    assert repo_pedidos.alteracoes_comentario == [] and repo_pedidos.alteracoes_pagamento == []


def test_movimentacao_continua_funcionando(repo_pedidos):
    pedido = novo_pedido(repo_pedidos)
    comentar(pedido.id, "Texto")
    pagar(pedido.id, "pendente", "pago")

    assert mover(pedido.id, "fila_producao", "em_producao").status_code == 200
    assert repo_pedidos.comentarios[pedido.id][0] == "Texto"
    assert repo_pedidos.pedidos[pedido.id].status_pagamento == "pago"


def test_javascript_nao_envia_usuario_nem_guarda_estado():
    js = client.get("/static/js/producao.js").text
    codigo = " ".join(linha.split("//")[0] for linha in js.splitlines()).lower()

    for proibido in ("usuario_id", "localstorage", "sessionstorage", "indexeddb", "cookie"):
        assert proibido not in codigo, proibido
    for esperado in ("comentario_esperado", "status_esperado", "aria-expanded", "csrf"):
        assert esperado in codigo, esperado


# ---------- Migration (texto) ----------


def sql_sem_comentarios() -> str:
    return re.sub(r"--[^\n]*", "", MIGRATION.read_text(encoding="utf-8")).lower()


def funcao(sql, nome):
    corpo = sql[sql.index(f"create function public.{nome}(") :]
    return corpo[: corpo.index("$corpo$;")]


def test_migration_existe_e_nao_mexe_em_dados():
    migrations = sorted(p.name for p in (RAIZ / "supabase" / "migrations").glob("*.sql"))
    sql = sql_sem_comentarios()

    assert MIGRATION.name in migrations
    assert re.search(r"observacoes\s+text", sql)  # devolvida, nunca alterada
    for proibido in (
        "drop column",
        "alter column",
        "insert into",
        "delete from",
        "sequence",
        "storage.",
        "sessoes",
        "update public.usuarios",
        "set default",
    ):
        assert proibido not in sql, proibido
    # Só as duas funções novas atualizam pedidos (e só os campos previstos).
    atualizacoes = re.findall(r"update public\.pedidos\s+set([^;]*?)\s+where", sql)
    campos = [re.findall(r"(\w+)\s*=", a) for a in atualizacoes]
    assert campos == [
        [
            "comentario_producao",
            "comentario_producao_atualizado_em",
            "comentario_producao_atualizado_por",
        ],
        ["status_pagamento", "pagamento_atualizado_em", "pagamento_atualizado_por"],
    ]


def test_migration_cria_os_campos_e_as_restricoes():
    sql = sql_sem_comentarios()

    assert "add column comentario_producao text," in sql
    assert "add column comentario_producao_atualizado_em timestamptz," in sql
    assert "add column pagamento_atualizado_em timestamptz," in sql
    for coluna in ("comentario_producao_atualizado_por", "pagamento_atualizado_por"):
        definicao = re.search(rf"add column {coluna} bigint\s+([^,;]*)", sql).group(1)
        assert "references public.usuarios (id) on delete restrict" in definicao
    assert "char_length(comentario_producao) between 1 and 2000" in sql
    assert "btrim(comentario_producao, e' \\t\\n\\r\\f\\x0b')" in sql
    assert "\\v" not in sql  # no postgresql, \v não é tabulação vertical


@pytest.mark.parametrize("nome", ["atualizar_comentario_producao", "alterar_status_pagamento"])
def test_migration_funcoes_protegidas_com_bloqueio_e_codigos(nome):
    sql = sql_sem_comentarios()
    corpo = funcao(sql, nome)
    assinatura = f"public.{nome}(bigint, text, text, bigint)"

    assert "security definer" in corpo and "set search_path = ''" in corpo
    assert "for update" in corpo and "is distinct from" in corpo
    for codigo in ("pt403", "pt404", "pt409", "pt422"):
        assert f"errcode = '{codigo}'" in corpo
    assert f"revoke all on function {assinatura}\n  from public, anon, authenticated;" in sql
    assert f"grant execute on function {assinatura}\n  to service_role;" in sql
    assert "grant " not in sql.replace("grant execute on function", "")


def test_migration_pagamento_so_alterna_entre_pendente_e_pago():
    corpo = funcao(sql_sem_comentarios(), "alterar_status_pagamento")

    pares = re.findall(r"\('([a-z]+)', '([a-z]+)'\)", corpo)
    assert pares == [("pendente", "pago"), ("pago", "pendente")]
    for proibido in ("etapa_producao", "pedido_itens", "prazo_entrega", "imagem"):
        assert proibido not in corpo, proibido


def test_migration_recria_listar_producao_sem_sobrecarga():
    sql = sql_sem_comentarios()

    assert "drop function public.listar_producao();" in sql
    assert sql.index("drop function public.listar_producao();") < sql.index(
        "create function public.listar_producao()"
    )
    assert len(re.findall(r"create (or replace )?function", sql)) == 3
    assert "listar_pedidos" not in sql and "consultar_pedido" not in sql
    corpo = funcao(sql, "listar_producao")
    assert "comentario_producao_atualizado_por_nome text" in corpo
    assert "left join public.usuarios as autor" in corpo
    assert (
        "revoke all on function public.listar_producao() from public, anon, authenticated;" in sql
    )
    assert "grant execute on function public.listar_producao() to service_role;" in sql


def test_teste_sql_e_transacional_e_usa_ids_negativos():
    texto = (RAIZ / "supabase" / "tests" / "comentarios_pagamento_test.sql").read_text(
        encoding="utf-8"
    )
    sql = re.sub(r"--[^\n]*", "", texto).lower()

    assert sql.lstrip().startswith("begin;") and "\nrollback;" in sql
    assert "commit" not in sql and "setval" not in sql and "nextval" not in sql
    assert "values\n    (-1, " in sql and "(-2, " in sql
    ids_alterados = re.findall(r"update public\.pedidos set [^;]* where id = (-?\d+)", sql)
    assert ids_alterados and all(int(i) < 0 for i in ids_alterados)
    for chamada in re.findall(
        r"public\.(?:atualizar_comentario_producao|alterar_status_pagamento)\(\s*(-?\d+)", sql
    ):
        assert int(chamada) < 0


# ---------- Ocultar e mostrar o comentário (botão do card) ----------


def botao_alternar_comentario(c):
    return re.search(r'<button[^>]*class="producao-comentario-alternar"[^>]*>', c).group(0)


def test_botao_de_ocultar_comentario_so_aparece_em_card_com_comentario(repo_pedidos):
    com = novo_pedido(repo_pedidos)
    sem = novo_pedido(repo_pedidos)
    repo_pedidos.comentarios[com.id] = ("Pintar de azul", datetime(2026, 10, 1, 9), 2)

    html = client.get("/producao").text
    com_botao, sem_botao = (
        botao_alternar_comentario(card(html, com.id)),
        botao_alternar_comentario(card(html, sem.id)),
    )

    # Com comentário: visível, começa mostrando o texto e aponta para o conteúdo do comentário.
    assert " hidden" not in com_botao
    assert 'aria-expanded="true"' in com_botao and 'title="Ocultar comentário"' in com_botao
    assert f'aria-controls="comentario-corpo-{com.id}"' in com_botao
    assert f'id="comentario-corpo-{com.id}"' in card(html, com.id)
    assert ">Ocultar comentário</span>" in card(html, com.id)
    # Sem comentário: o botão existe só para o JS poder mostrá-lo após salvar, mas fica oculto.
    assert " hidden" in sem_botao


def test_ocultar_comentario_nao_envolve_o_servidor_nem_o_dado_salvo(repo_pedidos):
    pedido = novo_pedido(repo_pedidos)
    repo_pedidos.comentarios[pedido.id] = ("Pintar de azul", datetime(2026, 10, 1, 9), 2)
    escritas = repo_pedidos.escritas
    c = card(client.get("/producao").text, pedido.id)

    # O texto salvo e a última atualização continuam no HTML; ocultar é só na tela.
    assert ">Pintar de azul</textarea>" in c and 'data-salvo="Pintar de azul"' in c
    assert "Atualizado por Kassia em 01/10/2026 às 09:00" in c
    assert repo_pedidos.alteracoes_comentario == [] and repo_pedidos.escritas == escritas
