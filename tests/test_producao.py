"""Página Produção (quadro Kanban): repositório e Storage falsos; nada acessa o Supabase."""

import re

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from app.auth.repositorio import Usuario
from app.auth.rotas import exigir_usuario
from app.main import app
from app.pedidos_repositorio import FalhaAoConsultar, FalhaAoMover
from tests.test_pedidos_consulta import caminho_de, item, novo_pedido, pedido_com_fotos
from tests.test_pedidos_gravacao import enviar

client = TestClient(app)

ETAPAS = ["fila_producao", "em_producao", "aguardando_entrega", "entregue"]


def coluna(html, etapa):
    inicio = html.index(f'<section class="coluna" data-etapa="{etapa}"')
    return html[inicio : html.index("</section>", inicio)]


def card(html, pedido_id):
    inicio = html.index(f'<li class="producao-card" data-pedido="{pedido_id}"')
    return html[inicio : html.index("</li>", inicio)]


def contador(html, etapa):
    return int(re.search(r"<span data-contador>(\d+)</span>", coluna(html, etapa)).group(1))


def mover(pedido_id, esperada, nova, **extra):
    dados = {"csrf": "csrf-de-teste", "etapa_esperada": esperada, "nova_etapa": nova, **extra}
    return client.post(f"/producao/{pedido_id}/mover", data=dados)


# Os quatro movimentos permitidos (os mesmos da função mover_etapa_producao do banco).
PERMITIDOS = [
    ("fila_producao", "em_producao"),
    ("em_producao", "fila_producao"),
    ("em_producao", "aguardando_entrega"),
    ("aguardando_entrega", "entregue"),
]


# ---------- Quadro ----------


def test_quatro_colunas_na_ordem_com_icone_e_contador(repo_pedidos):
    html = client.get("/producao").text

    titulos = re.findall(r'<h2 id="coluna-([a-z_]+)">([^<]+)</h2>', html)
    assert titulos == [
        ("fila_producao", "Na Fila de Produção"),
        ("em_producao", "Em Produção"),
        ("aguardando_entrega", "Ag. Entrega"),
        ("entregue", "Entregue"),
    ]
    for etapa in ETAPAS:
        assert '<svg class="coluna-icone"' in coluna(html, etapa)
        assert contador(html, etapa) == 0


def test_quadro_vazio(repo_pedidos):
    html = client.get("/producao").text

    assert "Nenhum pedido na produção" in html
    assert '<a class="botao botao-primario" href="/pedidos/novo">Cadastrar pedido</a>' in html
    assert html.count('<p class="coluna-vazia">Nenhum pedido nesta etapa.</p>') == 4


def test_pedido_novo_entra_na_fila_de_producao(repo_pedidos):
    assert enviar().status_code == 200
    (pedido_id,) = repo_pedidos.pedidos

    html = client.get("/producao").text

    assert repo_pedidos.etapas[pedido_id] == "fila_producao"
    assert f'data-pedido="{pedido_id}" data-etapa="fila_producao"' in coluna(html, "fila_producao")
    assert contador(html, "fila_producao") == 1


def test_contadores_e_ordem_do_mais_antigo_para_o_mais_recente(repo_pedidos):
    ids = [novo_pedido(repo_pedidos, cliente=nome).id for nome in ("Ana", "Bia", "Caio", "Davi")]
    repo_pedidos.etapas[ids[1]] = "em_producao"
    repo_pedidos.etapas[ids[3]] = "entregue"

    html = client.get("/producao").text

    assert [contador(html, e) for e in ETAPAS] == [2, 1, 0, 1]
    fila = coluna(html, "fila_producao")
    assert fila.index("<h3>Ana</h3>") < fila.index("<h3>Caio</h3>")
    assert "<h3>Bia</h3>" in coluna(html, "em_producao")
    assert "<h3>Davi</h3>" in coluna(html, "entregue")
    assert coluna(html, "aguardando_entrega").count("producao-card") == 0
    assert '<p class="coluna-vazia" hidden>' in coluna(html, "fila_producao")


def test_conteudo_do_card(repo_pedidos):
    pedido = novo_pedido(
        repo_pedidos,
        itens=[item(1, "Caneca", 11, "12.00"), item(2, "Chaveiro", 2, "5.00")],
        status_pagamento="pago",
        tipo_entrega="retirada",
        observacoes="Embalar para presente",
        criado_por=2,
    )

    c = card(client.get("/producao").text, pedido.id)

    assert "<h3>Cliente Teste</h3>" in c
    assert "Caneca, Chaveiro" in c and '<span class="pedido-card-unidades">13 unidades</span>' in c
    assert (
        '<dd class="pedido-card-pagamento">PIX <button type="button"'
        ' class="selo-status selo-status-pago producao-pagamento"'
    ) in c
    assert "<dt>Entrega</dt><dd>Retirada</dd>" in c
    assert '<dt>Prazo</dt><dd><time datetime="2026-10-05">05/10/26</time></dd>' in c
    assert "Cadastrado por" not in c and "Kassia" not in c
    assert "<dt>Observações do pedido</dt><dd>Embalar para presente</dd>" in c
    assert f'href="/pedidos/{pedido.id}"' in c and ">Ver pedido</a>" in c
    assert 'draggable="true"' in c
    assert 'data-destino="em_producao"' in c and ">Iniciar produção</button>" in c


def test_card_sem_observacoes_nao_mostra_o_campo(repo_pedidos):
    pedido = novo_pedido(repo_pedidos, observacoes=None)

    assert "Observações" not in card(client.get("/producao").text, pedido.id)


@pytest.mark.parametrize(
    ("etapa", "acao", "proxima"),
    [
        ("fila_producao", "Iniciar produção", "em_producao"),
        ("em_producao", "Finalizar produção", "aguardando_entrega"),
        ("aguardando_entrega", "Marcar como entregue", "entregue"),
    ],
)
def test_botao_da_proxima_acao(repo_pedidos, etapa, acao, proxima):
    pedido = novo_pedido(repo_pedidos)
    repo_pedidos.etapas[pedido.id] = etapa

    c = card(client.get("/producao").text, pedido.id)

    principal = re.search(r'class="botao botao-primario[^"]*"\s+data-destino="([a-z_]+)"', c)
    assert principal.group(1) == proxima
    assert f'aria-label="{acao}: pedido de Cliente Teste">{acao}</button>' in c


def test_em_producao_tem_voltar_para_fila_secundario_antes_de_finalizar(repo_pedidos):
    pedido = novo_pedido(repo_pedidos)
    repo_pedidos.etapas[pedido.id] = "em_producao"

    c = card(client.get("/producao").text, pedido.id)

    voltar = c.index(">Voltar para fila</button>")
    assert voltar < c.index(">Finalizar produção</button>")
    trecho = c[c.rindex("<button", 0, voltar) : voltar]
    assert 'class="botao botao-secundario producao-voltar producao-mover"' in trecho
    assert 'data-destino="fila_producao"' in trecho
    assert 'aria-label="Voltar para fila: pedido de Cliente Teste"' in trecho


@pytest.mark.parametrize("etapa", ["fila_producao", "aguardando_entrega", "entregue"])
def test_voltar_para_fila_so_aparece_em_producao(repo_pedidos, etapa):
    pedido = novo_pedido(repo_pedidos)
    repo_pedidos.etapas[pedido.id] = etapa

    c = card(client.get("/producao").text, pedido.id)

    assert "Voltar para fila" not in c and "producao-voltar" not in c


def test_entregue_nao_tem_botao_de_avanco_nem_arraste(repo_pedidos):
    pedido = novo_pedido(repo_pedidos)
    repo_pedidos.etapas[pedido.id] = "entregue"

    c = card(client.get("/producao").text, pedido.id)

    assert "producao-mover" not in c and "draggable" not in c
    assert ">Ver pedido</a>" in c


def test_card_sem_imagem_nao_tem_foto(repo_pedidos):
    pedido = pedido_com_fotos(repo_pedidos, [(2, False)])

    c = card(client.get("/producao").text, pedido.id)

    assert "<img" not in c and "pedido-card-aviso" not in c
    assert repo_pedidos.assinaturas == []


def test_card_com_imagem_usa_a_foto_de_destaque_assinada(repo_pedidos):
    pedido = pedido_com_fotos(repo_pedidos, [(2, True), (5, True), (9, False)])

    html = client.get("/producao").text

    escolhido = caminho_de(pedido.id, 2)
    assert repo_pedidos.assinaturas == [[escolhido]]
    c = card(html, pedido.id)
    url = "https://armazenamento.exemplo/assinada/0.img?token=falso"
    assert f'<img class="producao-card-foto" src="{url}"' in c
    assert 'alt="Foto de referência: Produto 2"' in c
    assert escolhido not in html


def test_no_maximo_uma_url_assinada_por_pedido(repo_pedidos):
    pedido_com_fotos(repo_pedidos, [(1, True), (2, True)], cliente="Ana")
    pedido_com_fotos(repo_pedidos, [(1, False)], cliente="Bia")
    pedido_com_fotos(repo_pedidos, [(3, True), (3, True)], cliente="Caio")

    html = client.get("/producao").text

    assert len(repo_pedidos.assinaturas) == 1
    (caminhos,) = repo_pedidos.assinaturas
    assert caminhos == [caminho_de(1, 2), caminho_de(3, 1)]
    assert html.count('class="producao-card-foto"') == 2


def test_falha_de_imagem_nao_impede_o_quadro(repo_pedidos):
    pedido = pedido_com_fotos(repo_pedidos, [(1, True)])
    repo_pedidos.falhar_em["assinar_imagens"] = FalhaAoConsultar()

    resposta = client.get("/producao")

    assert resposta.status_code == 200
    c = card(resposta.text, pedido.id)
    assert "<img" not in c and "Foto indisponível no momento." in c


def test_falha_de_banco_mostra_erro_com_tentar_novamente(repo_pedidos):
    repo_pedidos.falhar_em["listar_producao"] = FalhaAoConsultar()

    resposta = client.get("/producao")

    assert resposta.status_code == 503
    assert "Não foi possível carregar a produção agora" in resposta.text
    assert '<a class="botao botao-primario" href="/producao">Tentar novamente</a>' in resposta.text
    assert 'id="quadro"' not in resposta.text
    assert "FalhaAoConsultar" not in resposta.text


def test_sem_configuracao_do_banco_fica_indisponivel():
    from app.pedidos_repositorio import get_repositorio_pedidos

    app.dependency_overrides[get_repositorio_pedidos] = lambda: None

    assert client.get("/producao").status_code == 503
    assert mover(1, "fila_producao", "em_producao").status_code == 503


def test_quadro_tem_o_token_csrf_e_nao_vai_para_cache(repo_pedidos):
    resposta = client.get("/producao")

    assert 'id="quadro" class="quadro" data-csrf="csrf-de-teste"' in resposta.text
    assert resposta.headers["cache-control"] == "no-store"


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
        ("fila_producao", "entregue"),  # pular
        ("aguardando_entrega", "em_producao"),  # voltar
        ("aguardando_entrega", "fila_producao"),  # voltar duas
        ("entregue", "aguardando_entrega"),  # sair de entregue
        ("entregue", "em_producao"),
        ("entregue", "fila_producao"),
        ("entregue", "entregue"),
        ("em_producao", "entregue"),  # pular
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


def test_navegacao_para_producao_pedidos_e_novo_pedido():
    home = client.get("/").text
    producao = client.get("/producao").text
    pedidos = client.get("/pedidos").text

    assert "<h2>Produção</h2>" in home and 'href="/producao">Ver produção</a>' in home
    assert 'href="/pedidos">Ver pedidos</a>' in home and 'href="/pedidos/novo">' in home
    assert (
        'href="/pedidos">Pedidos</a>' in producao
        and 'href="/pedidos/novo">Novo pedido</a>' in producao
    )
    assert 'href="/producao">Produção</a>' in pedidos
    assert 'class="topo-nome"' in producao and 'action="/sair"' in producao


def test_javascript_nao_acessa_o_supabase_nem_envia_usuario():
    js = client.get("/static/js/producao.js").text
    codigo = " ".join(linha.split("//")[0] for linha in js.splitlines()).lower()

    for proibido in ("supabase", "secret", "service_role", "usuario_id", "localstorage"):
        assert proibido not in codigo, proibido
    assert "etapa_esperada" in codigo and "csrf" in codigo
    html = client.get("/producao").text.lower()
    assert "supabase" not in html and "secret" not in html
