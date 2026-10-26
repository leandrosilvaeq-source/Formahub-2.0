"""Página Produção (quadro Kanban): repositório e Storage falsos; nada acessa o Supabase."""

import re

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from app.auth.repositorio import Usuario
from app.auth.rotas import exigir_usuario
from app.main import app
from app.pedidos_repositorio import FalhaAoMover
from tests.test_pedidos_consulta import novo_pedido

client = TestClient(app)

ETAPAS = ["fila_producao", "em_producao", "aguardando_entrega", "entregue"]


def coluna(html, etapa):
    inicio = html.index(f'<section class="coluna" data-etapa="{etapa}"')
    return html[inicio : html.index("</section>", inicio)]


def card(html, pedido_id):
    inicio = html.rindex("<li", 0, html.index(f'data-pedido="{pedido_id}"'))
    return html[inicio : html.index("</li>", inicio)]


def contador(html, etapa):
    return int(re.search(r"<span data-contador>(\d+)</span>", coluna(html, etapa)).group(1))


def mover(pedido_id, esperada, nova, **extra):
    dados = {"csrf": "csrf-de-teste", "etapa_esperada": esperada, "nova_etapa": nova, **extra}
    return client.post(f"/producao/{pedido_id}/mover", data=dados)


# Os movimentos permitidos (os mesmos da função mover_etapa_producao do banco); finalizar
# (entregue) vale de qualquer etapa anterior.
PERMITIDOS = [
    ("fila_producao", "em_producao"),
    ("fila_producao", "entregue"),
    ("em_producao", "fila_producao"),
    ("em_producao", "aguardando_entrega"),
    ("em_producao", "entregue"),
    ("aguardando_entrega", "entregue"),
]


# ---------- Quadro ----------


def test_sem_configuracao_do_banco_fica_indisponivel():
    from app.pedidos_repositorio import get_repositorio_pedidos

    app.dependency_overrides[get_repositorio_pedidos] = lambda: None

    assert client.get("/producao").status_code == 503
    assert mover(1, "fila_producao", "em_producao").status_code == 503


def test_consulta_do_quadro_nao_altera_pedidos(repo_pedidos):
    novo_pedido(repo_pedidos)
    escritas, etapas = repo_pedidos.escritas, dict(repo_pedidos.etapas)

    client.get("/producao")

    assert repo_pedidos.escritas == escritas and repo_pedidos.etapas == etapas
    assert repo_pedidos.movimentos == []


# ---------- Avanço ----------


def test_avanca_da_fila_para_em_producao(repo_pedidos):
    pedido = novo_pedido(repo_pedidos)

    resposta = mover(pedido.id, "fila_producao", "em_producao")

    assert resposta.status_code == 200
    assert resposta.json() == {
        "ok": True,
        "pedido_id": pedido.id,
        "etapa": "em_producao",
        "titulo": "Em Produção",
        "movimentos": [
            {"destino": "fila_producao", "texto": "Voltar para fila", "retorno": True},
            {"destino": "aguardando_entrega", "texto": "Finalizar produção", "retorno": False},
        ],
    }
    assert repo_pedidos.etapas[pedido.id] == "em_producao"
    assert resposta.headers["cache-control"] == "no-store"


def test_volta_de_em_producao_para_a_fila(repo_pedidos):
    pedido = novo_pedido(repo_pedidos)
    repo_pedidos.etapas[pedido.id] = "em_producao"

    resposta = mover(pedido.id, "em_producao", "fila_producao")

    assert resposta.status_code == 200
    assert resposta.json()["etapa"] == "fila_producao"
    assert resposta.json()["titulo"] == "Na Fila de Produção"
    assert resposta.json()["movimentos"] == [
        {"destino": "em_producao", "texto": "Iniciar produção", "retorno": False}
    ]
    assert repo_pedidos.etapas[pedido.id] == "fila_producao"
    assert repo_pedidos.movimentos == [(pedido.id, "em_producao", "fila_producao", 1)]


@pytest.mark.parametrize(("atual", "nova"), PERMITIDOS)
def test_movimentos_permitidos(repo_pedidos, atual, nova):
    pedido = novo_pedido(repo_pedidos)
    repo_pedidos.etapas[pedido.id] = atual

    resposta = mover(pedido.id, atual, nova)

    assert resposta.status_code == 200 and resposta.json()["etapa"] == nova
    assert repo_pedidos.etapas[pedido.id] == nova


def test_entregue_nao_tem_mais_movimentos(repo_pedidos):
    pedido = novo_pedido(repo_pedidos)
    repo_pedidos.etapas[pedido.id] = "aguardando_entrega"

    assert mover(pedido.id, "aguardando_entrega", "entregue").json()["movimentos"] == []


def test_sequencia_completa_ate_entregue(repo_pedidos):
    pedido = novo_pedido(repo_pedidos)

    for atual, nova in zip(ETAPAS, ETAPAS[1:], strict=False):
        assert mover(pedido.id, atual, nova).status_code == 200

    final = mover(pedido.id, "aguardando_entrega", "entregue")  # já está em entregue
    assert final.status_code == 409
    assert repo_pedidos.etapas[pedido.id] == "entregue"
    assert [m[1:3] for m in repo_pedidos.movimentos] == list(zip(ETAPAS, ETAPAS[1:], strict=False))
    assert pedido.id in repo_pedidos.pedidos  # nada é excluído ao chegar em Entregue


# Toda combinação de etapas (e valores estranhos) fora das quatro permitidas.
ETAPAS_E_LIXO = [*ETAPAS, "qualquer", ""]
PROIBIDOS = [(a, n) for a in ETAPAS_E_LIXO for n in ETAPAS_E_LIXO if (a, n) not in PERMITIDOS]


def test_lista_de_proibidos_cobre_os_casos_pedidos():
    for caso in [
        ("fila_producao", "aguardando_entrega"),  # pular
        ("aguardando_entrega", "em_producao"),  # voltar
        ("aguardando_entrega", "fila_producao"),  # voltar duas
        ("entregue", "aguardando_entrega"),  # sair de entregue
        ("entregue", "em_producao"),
        ("entregue", "fila_producao"),
        ("entregue", "entregue"),
        ("fila_producao", "fila_producao"),  # ficar
    ]:
        assert caso in PROIBIDOS, caso


@pytest.mark.parametrize(("esperada", "nova"), PROIBIDOS)
def test_movimento_invalido_e_recusado(repo_pedidos, esperada, nova):
    pedido = novo_pedido(repo_pedidos)
    repo_pedidos.etapas[pedido.id] = esperada if esperada in ETAPAS else "fila_producao"

    resposta = mover(pedido.id, esperada, nova)

    assert resposta.status_code == 422
    assert resposta.json()["erro"] == "invalido"
    assert repo_pedidos.movimentos == []


def test_conflito_quando_outro_usuario_ja_moveu(repo_pedidos):
    pedido = novo_pedido(repo_pedidos)
    repo_pedidos.etapas[pedido.id] = "em_producao"  # alguém já iniciou a produção

    resposta = mover(pedido.id, "fila_producao", "em_producao")

    assert resposta.status_code == 409
    assert resposta.json() == {
        "ok": False,
        "erro": "conflito",
        "mensagem": "Este pedido foi atualizado por outro usuário.",
    }
    assert repo_pedidos.etapas[pedido.id] == "em_producao"
    assert repo_pedidos.movimentos == []


@pytest.mark.parametrize("pedido_id", ["999", "abc", "0", "1.5", "99999999999999999999"])
def test_pedido_inexistente_ou_invalido(repo_pedidos, pedido_id):
    novo_pedido(repo_pedidos)

    resposta = mover(pedido_id, "fila_producao", "em_producao")

    assert resposta.status_code == 422
    assert repo_pedidos.movimentos == []


def test_responsavel_e_o_usuario_da_sessao_e_usuario_do_navegador_e_ignorado(repo_pedidos):
    pedido = novo_pedido(repo_pedidos)

    mover(pedido.id, "fila_producao", "em_producao", usuario_id="3", p_usuario_id="2")

    assert repo_pedidos.movimentos == [(pedido.id, "fila_producao", "em_producao", 1)]


def test_responsavel_acompanha_quem_esta_logado(repo_pedidos):
    def kassia(request: Request) -> Usuario:
        usuario = Usuario(id=2, nome="Kassia")
        request.state.usuario = usuario
        request.state.csrf = "csrf-de-teste"
        return usuario

    pedido = novo_pedido(repo_pedidos)
    app.dependency_overrides[exigir_usuario] = kassia

    mover(pedido.id, "fila_producao", "em_producao", usuario_id="1")

    assert repo_pedidos.movimentos[-1][3] == 2


@pytest.mark.parametrize("csrf", [None, "", "outro-token"])
def test_csrf_ausente_ou_errado_e_recusado(repo_pedidos, csrf):
    pedido = novo_pedido(repo_pedidos)
    dados = {"etapa_esperada": "fila_producao", "nova_etapa": "em_producao"}
    if csrf is not None:
        dados["csrf"] = csrf

    resposta = client.post(f"/producao/{pedido.id}/mover", data=dados)

    assert resposta.status_code == 403
    assert resposta.json()["erro"] == "expirada"
    assert repo_pedidos.movimentos == []


def test_origem_de_outro_site_e_recusada(repo_pedidos):
    pedido = novo_pedido(repo_pedidos)

    resposta = client.post(
        f"/producao/{pedido.id}/mover",
        data={
            "csrf": "csrf-de-teste",
            "etapa_esperada": "fila_producao",
            "nova_etapa": "em_producao",
        },
        headers={"origin": "https://outro-site.example"},
    )

    assert resposta.status_code == 403
    assert repo_pedidos.movimentos == []


def test_falha_de_banco_no_avanco_e_mensagem_simples(repo_pedidos):
    pedido = novo_pedido(repo_pedidos)
    repo_pedidos.falhar_em["mover_etapa"] = FalhaAoMover()

    resposta = mover(pedido.id, "fila_producao", "em_producao")

    assert resposta.status_code == 503
    assert resposta.json()["mensagem"] == "Não foi possível mover o pedido agora. Tente novamente."
    assert repo_pedidos.etapas[pedido.id] == "fila_producao"


@pytest.mark.sem_login
def test_rotas_exigem_login(repo_pedidos):
    pedido = novo_pedido(repo_pedidos)
    anonimo = TestClient(app)

    pagina = anonimo.get("/producao", follow_redirects=False)
    movimento = anonimo.post(
        f"/producao/{pedido.id}/mover",
        data={"etapa_esperada": "fila_producao", "nova_etapa": "em_producao"},
        follow_redirects=False,
    )

    assert pagina.status_code == 303 and pagina.headers["location"].startswith("/entrar")
    assert movimento.status_code == 303
    assert repo_pedidos.movimentos == []


# ---------- Navegação e segurança da página ----------


# ---------- Tela descontinuada ----------


def test_producao_redireciona_para_pedidos(repo_pedidos):
    resposta = client.get("/producao", follow_redirects=False)

    assert resposta.status_code == 307 and resposta.headers["location"] == "/pedidos"


def test_home_e_pedidos_nao_levam_mais_a_producao(repo_pedidos):
    home = client.get("/").text
    pedidos = client.get("/pedidos").text

    assert "/producao" not in home and "<h2>Produção</h2>" not in home
    assert "/producao" not in pedidos.replace('"/producao/', "")
    assert 'href="/pedidos">Ver pedidos</a>' in home


def test_detalhe_mostra_etapa_e_salva_comentario_da_producao(repo_pedidos):
    pedido = novo_pedido(repo_pedidos)

    html = client.get(f"/pedidos/{pedido.id}").text
    assert "Na Fila" in html and 'id="comentario-campo"' in html

    resposta = client.post(
        f"/producao/{pedido.id}/comentario",
        data={"csrf": "csrf-de-teste", "comentario_esperado": "", "comentario": "Imprimir em PLA"},
    )
    assert resposta.json()["ok"] is True
    assert "Imprimir em PLA" in client.get(f"/pedidos/{pedido.id}").text
