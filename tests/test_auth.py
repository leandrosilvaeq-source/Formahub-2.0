"""Login: primeiro acesso, entrada, bloqueio, sessão, cookies, CSRF, saída e rotas protegidas.

Usa o repositório em memória (conftest): nenhum teste acessa o Supabase.
"""

import logging
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.auth import sessoes
from app.auth.repositorio import USUARIOS_FIXOS, RepositorioSupabase, ServicoIndisponivel
from app.auth.repositorio import get_repositorio as repositorio_real
from app.auth.senhas import conferir_senha, gerar_hash, validar_nova_senha
from app.core.config import ConfigError, Settings, get_settings
from app.main import app

pytestmark = pytest.mark.sem_login

SENHA = "senha-segura-123"
RAIZ = Path(__file__).resolve().parent.parent
T0 = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)


@pytest.fixture
def relogio(monkeypatch):
    """Relógio controlável: relogio.agora = ... muda a hora que o login enxerga."""

    class Relogio:
        agora = T0

    monkeypatch.setattr(sessoes, "agora", lambda: Relogio.agora)
    return Relogio


@pytest.fixture
def client():
    return TestClient(app, follow_redirects=False)


def csrf_de(html: str) -> str:
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def abrir(client, usuario="", next=None):
    consulta = {"usuario": usuario} if usuario else {}
    if next:
        consulta["next"] = next
    return client.get("/entrar", params=consulta)


def cadastrar(client, nome, senha=SENHA, confirmacao=None, **extra):
    csrf = csrf_de(abrir(client, nome).text)
    dados = {
        "usuario": nome,
        "senha": senha,
        "confirmacao": senha if confirmacao is None else confirmacao,
        "csrf": csrf,
        **extra,
    }
    return client.post("/entrar/criar-senha", data=dados)


def entrar(client, nome, senha=SENHA, **extra):
    csrf = csrf_de(abrir(client, nome).text)
    return client.post("/entrar", data={"usuario": nome, "senha": senha, "csrf": csrf, **extra})


def com_senha(repo, *nomes, senha=SENHA):
    """Deixa os usuários já com senha cadastrada (hash Argon2id)."""
    for nome in nomes:
        assert repo.definir_senha_inicial(nome, gerar_hash(senha))


def logado(client, repo, nome="Leandro"):
    com_senha(repo, nome)
    resposta = entrar(client, nome)
    assert resposta.status_code == 303
    return resposta


# ---------- Tela e primeiro acesso ----------


def test_tela_mostra_somente_os_tres_usuarios_fixos(client):
    html = abrir(client).text

    for nome in USUARIOS_FIXOS:
        assert f">{nome}</a>" in html
    assert html.count('class="acesso-nome"') == 3
    assert "Criar senha" not in html and 'type="password"' not in html


def test_primeiro_acesso_pede_criar_e_confirmar_senha(client):
    html = abrir(client, "Kassia").text

    assert "Primeiro acesso de Kassia" in html
    assert ">Criar senha</label>" in html
    assert ">Confirmar senha</label>" in html
    assert 'action="/entrar/criar-senha"' in html
    assert 'autocomplete="new-password"' in html
    assert 'aria-current="true"' in html


def test_usuario_com_senha_ve_apenas_o_login(client, repo):
    com_senha(repo, "Marise")
    html = abrir(client, "Marise").text

    assert 'action="/entrar"' in html
    assert ">Senha</label>" in html
    assert "Criar senha" not in html and "Confirmar senha" not in html
    assert "/entrar/criar-senha" not in html
    assert 'autocomplete="current-password"' in html


def test_cadastrar_senha_guarda_somente_hash_argon2id(client, repo):
    resposta = cadastrar(client, "Kassia")

    assert resposta.status_code == 303
    assert resposta.headers["location"] == "/entrar?usuario=Kassia&criada=1"
    guardado = repo.usuarios["Kassia"].senha_hash
    assert guardado.startswith("$argon2id$")
    assert SENHA not in guardado
    assert conferir_senha(SENHA, guardado)
    assert not conferir_senha("outra-senha-qualquer", guardado)
    assert "Senha cadastrada" in client.get(resposta.headers["location"]).text
    # Cadastrar a senha não entra no sistema: é preciso fazer login em seguida.
    assert sessoes.COOKIE_SESSAO not in client.cookies
    assert repo.sessoes == {}


def test_hash_usa_argon2id_com_custo_real(monkeypatch):
    from argon2 import PasswordHasher

    from app.auth import senhas

    monkeypatch.setattr(senhas, "_hasher", PasswordHasher())
    assert gerar_hash(SENHA).startswith("$argon2id$v=19$m=65536,t=3,p=4$")


def test_cada_usuario_cadastra_a_senha_uma_unica_vez(client, repo):
    assert cadastrar(client, "Kassia").status_code == 303
    original = repo.usuarios["Kassia"].senha_hash

    segunda = cadastrar(client, "Kassia", senha="outra-senha-valida-9")

    assert segunda.status_code == 409
    assert "já tem senha" in segunda.text
    assert repo.usuarios["Kassia"].senha_hash == original
    # Nem por outro navegador, nem com o token certo, a senha pode ser trocada pela tela pública.
    outro = TestClient(app, follow_redirects=False)
    csrf = csrf_de(abrir(outro, "Kassia").text)
    tentativa = outro.post(
        "/entrar/criar-senha",
        data={
            "usuario": "Kassia",
            "senha": "invasor-senha-123",
            "confirmacao": "invasor-senha-123",
            "csrf": csrf,
        },
    )
    assert tentativa.status_code == 409
    assert repo.usuarios["Kassia"].senha_hash == original


def test_gravacao_da_senha_inicial_e_atomica(repo):
    assert repo.definir_senha_inicial("Leandro", gerar_hash(SENHA)) is True
    assert repo.definir_senha_inicial("Leandro", gerar_hash("outra-senha-123")) is False
    assert repo.definir_senha_inicial("Maria", gerar_hash(SENHA)) is False


def test_depois_que_os_tres_tem_senha_nao_existe_mais_criacao(client, repo):
    com_senha(repo, *USUARIOS_FIXOS)

    for nome in USUARIOS_FIXOS:
        html = abrir(client, nome).text
        assert "Criar senha" not in html and "Primeiro acesso" not in html
        resposta = cadastrar(client, nome, senha="novo-cadastro-123")
        assert resposta.status_code == 409
        assert conferir_senha(SENHA, repo.usuarios[nome].senha_hash)
    assert set(repo.usuarios) == set(USUARIOS_FIXOS)


@pytest.mark.parametrize(
    ("senha", "confirmacao", "trecho"),
    [
        ("curta", "curta", "pelo menos 10"),
        ("a" * 9, "a" * 9, "pelo menos 10"),
        ("a" * 129, "a" * 129, "no máximo 128"),
        ("senha-segura-123", "senha-segura-124", "não conferem"),
        ("", "", "Informe a senha"),
    ],
)
def test_senha_invalida_nao_e_cadastrada(client, repo, senha, confirmacao, trecho):
    resposta = cadastrar(client, "Kassia", senha=senha, confirmacao=confirmacao)

    assert resposta.status_code == 400
    assert trecho in resposta.text
    assert repo.usuarios["Kassia"].senha_hash is None


def test_senhas_de_10_e_128_caracteres_sao_aceitas(client, repo):
    assert cadastrar(client, "Kassia", senha="x" * 10).status_code == 303
    assert cadastrar(client, "Marise", senha="y" * 128).status_code == 303


def test_senha_igual_ao_nome_e_recusada():
    assert "igual ao nome" in validar_nova_senha("Fulano Silva", "fulano silva", "fulano silva")
    assert validar_nova_senha("Fulano Silva", "senha-segura-123", "senha-segura-123") is None


def test_nao_e_possivel_criar_outros_usuarios(client, repo):
    for nome in ("Maria", "leandro", "", "Leandro "):
        resposta = cadastrar_bruto(client, nome)
        assert resposta.status_code == 400
    assert set(repo.usuarios) == set(USUARIOS_FIXOS)
    assert all(u.senha_hash is None for u in repo.usuarios.values())


def cadastrar_bruto(client, nome):
    abrir(client)
    csrf = client.cookies[sessoes.COOKIE_CSRF]
    return client.post(
        "/entrar/criar-senha",
        data={"usuario": nome, "senha": SENHA, "confirmacao": SENHA, "csrf": csrf},
    )


# ---------- Login ----------


def test_login_correto_abre_a_sessao_e_o_cabecalho_mostra_o_nome(client, repo):
    com_senha(repo, "Leandro")
    resposta = entrar(client, "Leandro")

    assert resposta.status_code == 303
    assert resposta.headers["location"] == "/"
    pagina = client.get("/")
    assert pagina.status_code == 200
    assert 'class="topo-nome"' in pagina.text and ">Leandro</span>" in pagina.text
    assert 'action="/sair"' in pagina.text and ">Sair</button>" in pagina.text


def test_senha_incorreta_nao_entra_e_conta_tentativas(client, repo):
    com_senha(repo, "Kassia")
    resposta = entrar(client, "Kassia", senha="senha-errada-123")

    assert resposta.status_code == 401
    assert "Restam 4 tentativas" in resposta.text
    assert sessoes.COOKIE_SESSAO not in client.cookies
    assert repo.sessoes == {}
    assert repo.usuarios["Kassia"].falhas_seguidas == 1


def test_login_de_quem_ainda_nao_tem_senha_leva_ao_primeiro_acesso(client, repo):
    resposta = entrar(client, "Marise")

    assert resposta.status_code == 409
    assert "primeiro acesso" in resposta.text
    assert "Confirmar senha" in resposta.text
    assert repo.sessoes == {}


def test_login_com_usuario_desconhecido_e_recusado(client, repo):
    resposta = entrar_bruto(client, "Maria")

    assert resposta.status_code == 400
    assert repo.sessoes == {}


def entrar_bruto(client, nome):
    abrir(client)
    csrf = client.cookies[sessoes.COOKIE_CSRF]
    return client.post("/entrar", data={"usuario": nome, "senha": SENHA, "csrf": csrf})


def test_erros_anteriores_sao_zerados_apos_login_correto(client, repo):
    com_senha(repo, "Leandro")
    entrar(client, "Leandro", senha="errada-errada-1")
    entrar(client, "Leandro", senha="errada-errada-2")
    assert repo.usuarios["Leandro"].falhas_seguidas == 2

    assert entrar(client, "Leandro").status_code == 303
    assert repo.usuarios["Leandro"].falhas_seguidas == 0


# ---------- Bloqueio ----------


def test_cinco_erros_bloqueiam_por_5_minutos_fixos(client, repo, relogio):
    com_senha(repo, "Kassia")
    for i in range(1, 5):
        assert entrar(client, "Kassia", senha=f"errada-numero-{i}").status_code == 401
    quinta = entrar(client, "Kassia", senha="errada-numero-5")

    assert quinta.status_code == 401
    assert "bloqueado por 5 minutos" in quinta.text
    assert repo.usuarios["Kassia"].bloqueado_ate == T0 + timedelta(minutes=5)

    # Bloqueado: nem a senha certa entra, e o bloqueio não é estendido por novas tentativas.
    bloqueada = entrar(client, "Kassia")
    assert bloqueada.status_code == 429
    assert "Tente novamente em 5 minutos" in bloqueada.text
    assert repo.sessoes == {}
    entrar(client, "Kassia", senha="mais-uma-errada")
    assert repo.usuarios["Kassia"].bloqueado_ate == T0 + timedelta(minutes=5)

    relogio.agora = T0 + timedelta(minutes=2)
    assert "3 minutos" in entrar(client, "Kassia").text

    # Passados os 5 minutos, o acesso volta e o contador zera.
    relogio.agora = T0 + timedelta(minutes=5, seconds=1)
    assert entrar(client, "Kassia").status_code == 303
    assert repo.usuarios["Kassia"].falhas_seguidas == 0
    assert repo.usuarios["Kassia"].bloqueado_ate is None


def test_bloqueio_e_por_usuario(client, repo, relogio):
    com_senha(repo, "Kassia", "Marise")
    for i in range(5):
        entrar(client, "Kassia", senha=f"errada-numero-{i}")

    assert entrar(client, "Kassia").status_code == 429
    assert entrar(client, "Marise").status_code == 303


# ---------- Sessão e cookies ----------


def test_cookie_de_sessao_tem_os_atributos_de_seguranca(client, repo):
    com_senha(repo, "Leandro")
    resposta = entrar(client, "Leandro")
    cookie = next(
        c for c in resposta.headers.get_list("set-cookie") if c.startswith("formahub_sessao=")
    )

    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert "Path=/" in cookie
    assert "Max-Age=43200" in cookie  # 12 horas por padrão
    assert "Secure" not in cookie  # COOKIE_SECURE=false por padrão


def test_cookie_secure_e_duracao_sao_configuraveis(repo):
    client = TestClient(app, base_url="https://testserver", follow_redirects=False)
    app.dependency_overrides[get_settings] = lambda: Settings(
        supabase_url="", supabase_publishable_key="", cookie_secure=True, sessao_duracao_horas=2
    )
    com_senha(repo, "Leandro")
    csrf = next(
        c for c in abrir(client).headers.get_list("set-cookie") if c.startswith("formahub_csrf=")
    )
    resposta = entrar(client, "Leandro")
    cookie = next(
        c for c in resposta.headers.get_list("set-cookie") if c.startswith("formahub_sessao=")
    )

    assert "Secure" in csrf
    assert "Secure" in cookie
    assert "Max-Age=7200" in cookie


def test_banco_guarda_so_o_sha256_do_token(client, repo, relogio):
    logado(client, repo)
    token = client.cookies[sessoes.COOKIE_SESSAO]

    assert len(token) >= 43
    (token_hash,) = repo.sessoes
    assert token_hash == sessoes.hash_token(token)
    assert re.fullmatch(r"[0-9a-f]{64}", token_hash)
    assert token not in repr(repo.sessoes)
    assert repo.sessoes[token_hash]["expira_em"] == T0 + timedelta(hours=12)


def test_cada_login_gera_um_token_novo_e_encerra_o_anterior(client, repo):
    logado(client, repo)
    primeiro = client.cookies[sessoes.COOKIE_SESSAO]

    # Novo login no mesmo navegador (ainda com o cookie antigo): token novo, sessão antiga apagada.
    client.cookies.set(sessoes.COOKIE_CSRF, "token-csrf-de-teste-1234567890")
    resposta = client.post(
        "/entrar",
        data={"usuario": "Leandro", "senha": SENHA, "csrf": "token-csrf-de-teste-1234567890"},
    )

    assert resposta.status_code == 303
    segundo = client.cookies[sessoes.COOKIE_SESSAO]
    assert segundo != primeiro
    assert list(repo.sessoes) == [sessoes.hash_token(segundo)]


def test_sessao_expirada_e_recusada_e_apagada(client, repo, relogio):
    logado(client, repo)
    assert client.get("/").status_code == 200

    relogio.agora = T0 + timedelta(hours=12, seconds=1)
    resposta = client.get("/pedidos/novo")

    assert resposta.status_code == 303
    assert resposta.headers["location"] == "/entrar?next=%2Fpedidos%2Fnovo"
    assert repo.sessoes == {}


def test_token_desconhecido_nao_da_acesso(client):
    client.cookies.set(sessoes.COOKIE_SESSAO, "token-forjado-que-nao-existe-no-banco")

    resposta = client.get("/")

    assert resposta.status_code == 303
    assert resposta.headers["location"].startswith("/entrar")


def test_ultimo_uso_e_atualizado_de_tempos_em_tempos(client, repo, relogio):
    logado(client, repo)
    (token_hash,) = repo.sessoes
    repo.sessoes[token_hash]["ultimo_uso"] = T0

    relogio.agora = T0 + timedelta(minutes=2)
    client.get("/")
    assert repo.sessoes[token_hash]["ultimo_uso"] == T0  # ainda dentro do intervalo

    relogio.agora = T0 + timedelta(minutes=10)
    client.get("/")
    assert repo.sessoes[token_hash]["ultimo_uso"] == T0 + timedelta(minutes=10)


# ---------- Rotas protegidas ----------


@pytest.mark.parametrize(
    ("metodo", "caminho", "destino"),
    [
        ("get", "/", "/entrar?next=%2F"),
        ("get", "/pedidos/novo", "/entrar?next=%2Fpedidos%2Fnovo"),
        ("post", "/pedidos/novo", "/entrar?next=%2Fpedidos%2Fnovo"),
    ],
)
def test_rotas_protegidas_redirecionam_para_o_login(client, metodo, caminho, destino):
    resposta = getattr(client, metodo)(caminho)

    assert resposta.status_code == 303
    assert resposta.headers["location"] == destino


def test_rotas_publicas_continuam_abertas(client):
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/static/css/app.css").status_code == 200
    assert client.get("/static/js/entrar.js").status_code == 200
    assert client.get("/entrar").status_code == 200


def test_depois_do_login_volta_para_a_tela_pedida(client, repo):
    com_senha(repo, "Leandro")
    location = client.get("/pedidos/novo").headers["location"]
    link = re.search(r'href="(/entrar\?usuario=Leandro[^"]*)"', client.get(location).text).group(1)
    assert 'name="next" value="/pedidos/novo"' in client.get(link.replace("&amp;", "&")).text

    resposta = entrar(client, "Leandro", next="/pedidos/novo")

    assert resposta.headers["location"] == "/pedidos/novo"
    assert client.get("/pedidos/novo").status_code == 200


def test_quem_ja_esta_logado_nao_ve_a_tela_de_entrada(client, repo):
    logado(client, repo)

    resposta = client.get("/entrar?next=/pedidos/novo")

    assert resposta.status_code == 303
    assert resposta.headers["location"] == "/pedidos/novo"


@pytest.mark.parametrize(
    "destino",
    [
        "https://evil.example",
        "http://evil.example/x",
        "//evil.example",
        "///evil.example",
        "/\\evil.example",
        "\\\\evil.example",
        "javascript:alert(1)",
        "evil.example",
        "pedidos/novo",
        "/x\r\nSet-Cookie: a=b",
        "/x\tevil",
        "/entrar",
        "/sair",
        "/" + "a" * 600,
        "",
    ],
)
def test_next_externo_ou_estranho_vira_a_pagina_inicial(client, repo, destino):
    assert sessoes.caminho_interno_seguro(destino) == "/"
    com_senha(repo, "Leandro")

    resposta = entrar(client, "Leandro", next=destino)

    assert resposta.status_code == 303
    assert resposta.headers["location"] == "/"


@pytest.mark.parametrize("destino", ["/", "/pedidos/novo", "/pedidos/novo?a=1"])
def test_next_interno_e_aceito(destino):
    assert sessoes.caminho_interno_seguro(destino) == destino


# ---------- CSRF ----------


def test_formularios_levam_token_igual_ao_cookie(client):
    resposta = abrir(client, "Kassia")
    cookie = client.cookies[sessoes.COOKIE_CSRF]
    set_cookie = next(c for c in resposta.headers.get_list("set-cookie") if "formahub_csrf" in c)

    assert csrf_de(resposta.text) == cookie
    assert "HttpOnly" in set_cookie and "SameSite=lax" in set_cookie
    assert len(cookie) >= 40


def test_entrar_sem_token_ou_com_token_errado_e_recusado(client, repo):
    com_senha(repo, "Leandro")
    csrf = csrf_de(abrir(client, "Leandro").text)
    base = {"usuario": "Leandro", "senha": SENHA}

    assert client.post("/entrar", data=base).status_code == 403
    assert client.post("/entrar", data={**base, "csrf": "token-errado"}).status_code == 403
    assert client.post("/entrar", data={**base, "csrf": ""}).status_code == 403
    assert repo.sessoes == {}
    assert client.post("/entrar", data={**base, "csrf": csrf}).status_code == 303


def test_entrar_sem_o_cookie_de_csrf_e_recusado(client, repo):
    com_senha(repo, "Leandro")
    csrf = csrf_de(abrir(client, "Leandro").text)
    client.cookies.clear()

    resposta = client.post("/entrar", data={"usuario": "Leandro", "senha": SENHA, "csrf": csrf})

    assert resposta.status_code == 403
    assert repo.sessoes == {}


def test_recusa_por_csrf_nao_conta_como_senha_errada(client, repo):
    com_senha(repo, "Leandro")
    client.post("/entrar", data={"usuario": "Leandro", "senha": "errada-errada-1"})

    assert repo.usuarios["Leandro"].falhas_seguidas == 0


def test_cadastro_de_senha_sem_token_valido_e_recusado(client, repo):
    csrf = csrf_de(abrir(client, "Kassia").text)
    dados = {"usuario": "Kassia", "senha": SENHA, "confirmacao": SENHA}

    assert client.post("/entrar/criar-senha", data=dados).status_code == 403
    assert client.post("/entrar/criar-senha", data={**dados, "csrf": "errado"}).status_code == 403
    assert repo.usuarios["Kassia"].senha_hash is None
    assert client.post("/entrar/criar-senha", data={**dados, "csrf": csrf}).status_code == 303


def test_origem_de_outro_site_e_recusada_mesmo_com_token_valido(client, repo):
    com_senha(repo, "Leandro")
    csrf = csrf_de(abrir(client, "Leandro").text)

    resposta = client.post(
        "/entrar",
        data={"usuario": "Leandro", "senha": SENHA, "csrf": csrf},
        headers={"Origin": "https://evil.example"},
    )

    assert resposta.status_code == 403
    assert repo.sessoes == {}
    ok = client.post(
        "/entrar",
        data={"usuario": "Leandro", "senha": SENHA, "csrf": csrf},
        headers={"Origin": "http://testserver"},
    )
    assert ok.status_code == 303


def test_token_do_formulario_de_sair_deriva_da_sessao(client, repo):
    logado(client, repo)
    pagina = client.get("/")
    token = client.cookies[sessoes.COOKIE_SESSAO]

    assert csrf_de(pagina.text) == sessoes.csrf_da_sessao(token)
    assert csrf_de(pagina.text) != client.cookies.get(sessoes.COOKIE_CSRF)


# ---------- Sair ----------


def test_sair_por_post_encerra_a_sessao(client, repo):
    logado(client, repo)
    csrf = csrf_de(client.get("/").text)

    resposta = client.post("/sair", data={"csrf": csrf})

    assert resposta.status_code == 303
    assert resposta.headers["location"] == "/entrar"
    assert repo.sessoes == {}
    cookie = next(c for c in resposta.headers.get_list("set-cookie") if "formahub_sessao" in c)
    assert 'formahub_sessao=""' in cookie or "Max-Age=0" in cookie
    assert client.get("/").status_code == 303


def test_sair_nao_aceita_get(client, repo):
    logado(client, repo)

    assert client.get("/sair").status_code == 405
    assert len(repo.sessoes) == 1


def test_sair_sem_token_ou_com_token_errado_e_recusado(client, repo):
    logado(client, repo)
    csrf = csrf_de(client.get("/").text)

    assert client.post("/sair").status_code == 403
    assert client.post("/sair", data={"csrf": "errado"}).status_code == 403
    assert client.post("/sair", data={"csrf": sessoes.novo_csrf()}).status_code == 403
    assert (
        client.post(
            "/sair", data={"csrf": csrf}, headers={"Origin": "https://x.example"}
        ).status_code
        == 403
    )
    assert len(repo.sessoes) == 1
    assert client.get("/").status_code == 200


def test_token_de_sair_de_outra_sessao_nao_vale(client, repo):
    com_senha(repo, "Leandro", "Kassia")
    outro = TestClient(app, follow_redirects=False)
    entrar(outro, "Kassia")
    csrf_do_outro = csrf_de(outro.get("/").text)
    entrar(client, "Leandro")

    assert client.post("/sair", data={"csrf": csrf_do_outro}).status_code == 403
    assert len(repo.sessoes) == 2


def test_sair_sem_sessao_apenas_volta_para_o_login(client):
    resposta = client.post("/sair")

    assert resposta.status_code == 303
    assert resposta.headers["location"] == "/entrar"


# ---------- Senha nunca exposta ----------


def test_senha_nunca_aparece_em_respostas_nem_em_logs(client, repo, caplog):
    caplog.set_level(logging.DEBUG)
    segredo = "segredo-que-nao-pode-vazar-987"
    respostas = [
        cadastrar(client, "Kassia", senha=segredo),
        cadastrar(client, "Marise", senha=segredo, confirmacao="diferente-1234"),
        entrar(TestClient(app, follow_redirects=False), "Kassia", senha="errada-errada-1"),
        entrar(client, "Kassia", senha=segredo),
    ]

    for resposta in respostas:
        assert segredo not in resposta.text
        assert segredo not in str(resposta.headers)
    assert segredo not in caplog.text and "errada-errada-1" not in caplog.text
    assert segredo not in repr(repo.usuarios) and segredo not in repr(repo.sessoes)
    assert 'value="' + segredo not in client.get("/entrar?usuario=Kassia").text


def test_paginas_de_login_nao_ficam_em_cache(client):
    assert abrir(client, "Kassia").headers["cache-control"] == "no-store"


# ---------- Serviço indisponível ----------


def test_banco_indisponivel_mostra_aviso_sem_detalhes(client, repo):
    def falha():
        raise ServicoIndisponivel

    app.dependency_overrides[repositorio_real] = falha

    for caminho in ("/entrar", "/", "/pedidos/novo"):
        resposta = client.get(caminho)
        assert resposta.status_code == 503
        assert "indisponível" in resposta.text
        assert "supabase" not in resposta.text.lower()
    assert client.get("/health").status_code == 200


def test_sem_configuracao_do_supabase_o_repositorio_real_fica_indisponivel(monkeypatch):
    def sem_configuracao():
        raise ConfigError("Acesso ao banco não configurado.")

    repositorio_real.cache_clear()
    monkeypatch.setattr("app.auth.repositorio.get_supabase_admin_client", sem_configuracao)
    with pytest.raises(ServicoIndisponivel):
        repositorio_real()
    repositorio_real.cache_clear()


# ---------- Repositório Supabase (com cliente falso, sem rede) ----------


class Consulta:
    def __init__(self, cliente, dados):
        self.cliente, self.dados = cliente, dados

    def __getattr__(self, nome):
        def registrar(*args, **kwargs):
            self.cliente.chamadas.append((nome, args))
            return self

        return registrar

    def execute(self):
        if isinstance(self.dados, Exception):
            raise self.dados
        return type("Resposta", (), {"data": self.dados})()


class ClienteFalso:
    def __init__(self, dados):
        self.dados, self.chamadas = dados, []

    def table(self, nome):
        self.chamadas.append(("table", (nome,)))
        return Consulta(self, self.dados)

    def rpc(self, nome, parametros):
        self.chamadas.append(("rpc", (nome, parametros)))
        return Consulta(self, self.dados)


def test_repositorio_supabase_define_senha_pela_funcao_atomica():
    cliente = ClienteFalso(True)
    repo = RepositorioSupabase(cliente)

    assert repo.definir_senha_inicial("Kassia", "$argon2id$x") is True
    assert cliente.chamadas == [
        ("rpc", ("definir_senha_inicial", {"p_nome": "Kassia", "p_senha_hash": "$argon2id$x"}))
    ]
    assert RepositorioSupabase(ClienteFalso(False)).definir_senha_inicial("Kassia", "h") is False


def test_repositorio_supabase_le_usuario_e_sessao():
    linha = {
        "id": 2,
        "nome": "Kassia",
        "senha_hash": None,
        "falhas_seguidas": 0,
        "bloqueado_ate": "2026-01-15T12:05:00+00:00",
    }
    usuario = RepositorioSupabase(ClienteFalso([linha])).buscar_usuario("Kassia")
    assert usuario.nome == "Kassia" and not usuario.tem_senha
    assert usuario.bloqueado_ate == T0 + timedelta(minutes=5)
    assert RepositorioSupabase(ClienteFalso([])).buscar_usuario("Kassia") is None

    sessao = RepositorioSupabase(
        ClienteFalso(
            [
                {
                    "expira_em": "2026-01-16T00:00:00+00:00",
                    "ultimo_uso": "2026-01-15T12:00:00+00:00",
                    "usuarios": {"id": 2, "nome": "Kassia"},
                }
            ]
        )
    ).buscar_sessao("a" * 64)
    assert sessao.usuario.nome == "Kassia" and sessao.expira_em > sessao.ultimo_uso


def test_erro_do_banco_vira_servico_indisponivel_sem_vazar_dados(caplog):
    repo = RepositorioSupabase(ClienteFalso(RuntimeError("senha_hash=$argon2id$segredo")))

    with pytest.raises(ServicoIndisponivel):
        repo.buscar_usuario("Kassia")
    assert "segredo" not in caplog.text


# ---------- Migration local e configuração ----------


def test_migration_cria_somente_o_combinado():
    sql = next((RAIZ / "supabase" / "migrations").glob("*_criar_autenticacao.sql")).read_text(
        encoding="utf-8"
    )
    minusculo = sql.lower()

    assert re.findall(r"create table (public\.\w+)", minusculo) == [
        "public.usuarios",
        "public.sessoes",
    ]
    assert len(re.findall(r"create (?:or replace )?function", minusculo)) == 1
    assert "definir_senha_inicial" in sql and "senha_hash is null" in minusculo
    assert "$argon2id$%" in sql
    for nome in USUARIOS_FIXOS:
        assert f"'{nome}'" in sql
    assert re.search(r"values \('leandro'\), \('kassia'\), \('marise'\);", minusculo)
    assert "enable row level security" in minusculo
    assert "create policy" not in minusculo
    assert "eventos_acesso" not in minusculo and "pedidos" not in minusculo
    assert not re.search(r"grant [^;]*\bto (anon|authenticated)", minusculo)
    assert "revoke all on public.usuarios from anon, authenticated" in minusculo


def test_variaveis_novas_no_env_example_sem_valores():
    linhas = (RAIZ / ".env.example").read_text(encoding="utf-8").splitlines()

    assert "SUPABASE_SECRET_KEY=" in linhas
    assert "COOKIE_SECURE=false" in linhas
    assert "SESSAO_DURACAO_HORAS=12" in linhas
    assert all(not v.split("=", 1)[1].strip() for v in linhas if v.startswith("SUPABASE_"))


def test_argon2_esta_nas_dependencias():
    assert "argon2-cffi==" in (RAIZ / "requirements.txt").read_text(encoding="utf-8")


def test_configuracao_le_cookie_e_duracao(monkeypatch):
    monkeypatch.setattr("app.core.config.load_dotenv", lambda: None)  # não lê o .env real
    monkeypatch.setenv("COOKIE_SECURE", "true")
    monkeypatch.setenv("SESSAO_DURACAO_HORAS", "3")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", " chave ")
    get_settings.cache_clear()
    try:
        s = get_settings()
        assert s.cookie_secure is True and s.sessao_duracao_horas == 3
        assert s.supabase_secret_key == "chave"
        monkeypatch.setenv("SESSAO_DURACAO_HORAS", "abc")
        get_settings.cache_clear()
        assert get_settings().sessao_duracao_horas == 12
    finally:
        get_settings.cache_clear()
