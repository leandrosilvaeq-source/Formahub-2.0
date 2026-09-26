"""Rotas de login (/entrar, /entrar/criar-senha, /sair) e a proteção das demais telas."""

import math
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from app.auth import sessoes
from app.auth.repositorio import (
    USUARIOS_FIXOS,
    RepositorioAuth,
    ServicoIndisponivel,
    Usuario,
    get_repositorio,
)
from app.auth.senhas import conferir_senha, gerar_hash, validar_nova_senha
from app.core.config import Settings, get_settings

LIMITE_FALHAS = 5
BLOQUEIO = timedelta(minutes=5)
INTERVALO_ULTIMO_USO = timedelta(minutes=5)
TAMANHO_MAXIMO_FORMULARIO = 8192

templates = Jinja2Templates(directory=Path(__file__).resolve().parent.parent / "templates")
router = APIRouter()


class NaoAutenticado(Exception):
    def __init__(self, destino: str):
        self.destino = destino


# ---------- Proteção das telas ----------


def exigir_usuario(request: Request, repo: RepositorioAuth = Depends(get_repositorio)) -> Usuario:
    """Dependência das rotas protegidas: exige uma sessão válida ou redireciona para /entrar."""
    destino = request.url.path
    if request.method == "GET" and request.url.query:
        destino += "?" + request.url.query

    token = request.cookies.get(sessoes.COOKIE_SESSAO)
    if not token:
        raise NaoAutenticado(destino)
    agora = sessoes.agora()
    hash_ = sessoes.hash_token(token)
    sessao = repo.buscar_sessao(hash_)
    if sessao is None:
        raise NaoAutenticado(destino)
    if sessao.expira_em <= agora:
        repo.apagar_sessao(hash_)
        raise NaoAutenticado(destino)
    if agora - sessao.ultimo_uso > INTERVALO_ULTIMO_USO:
        repo.atualizar_ultimo_uso(hash_, agora)

    request.state.usuario = sessao.usuario
    request.state.csrf = sessoes.csrf_da_sessao(token)
    return sessao.usuario


def tratar_nao_autenticado(request: Request, erro: NaoAutenticado) -> Response:
    resposta = RedirectResponse(
        "/entrar?" + urlencode({"next": sessoes.caminho_interno_seguro(erro.destino)}),
        status_code=303,
    )
    resposta.delete_cookie(sessoes.COOKIE_SESSAO, path="/")
    return resposta


def tratar_servico_indisponivel(request: Request, erro: ServicoIndisponivel) -> Response:
    return _pagina(
        request,
        {"indisponivel": True, "csrf": "", "proximo": "/"},
        status_code=503,
    )


# ---------- Utilitários ----------


def _pagina(request: Request, contexto: dict, status_code: int = 200) -> HTMLResponse:
    contexto.setdefault("nomes", [])
    resposta = templates.TemplateResponse(request, "entrar.html", contexto, status_code=status_code)
    resposta.headers["Cache-Control"] = "no-store"
    return resposta


async def _ler_formulario(request: Request) -> dict[str, str]:
    corpo = await request.body()
    if len(corpo) > TAMANHO_MAXIMO_FORMULARIO:
        raise HTTPException(status_code=413, detail="Formulário grande demais.")
    dados = parse_qs(corpo.decode(errors="replace"), keep_blank_values=True)
    return {chave: valores[0] for chave, valores in dados.items()}


def _origem_confiavel(request: Request) -> bool:
    """Se o navegador enviou Origin, ele precisa ser o próprio site."""
    origem = request.headers.get("origin")
    if origem is None:
        return True
    return urlsplit(origem).netloc == request.headers.get("host")


def _definir_cookie_sessao(resposta: Response, token: str, settings: Settings) -> None:
    resposta.set_cookie(
        sessoes.COOKIE_SESSAO,
        token,
        max_age=settings.sessao_duracao_horas * 3600,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )


def _definir_cookie_csrf(resposta: Response, token: str, settings: Settings) -> None:
    resposta.set_cookie(
        sessoes.COOKIE_CSRF,
        token,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )


def _csrf_do_cookie(request: Request) -> str | None:
    token = request.cookies.get(sessoes.COOKIE_CSRF)
    return token if token and 20 <= len(token) <= 100 else None


def _links_de_nomes(selecionado: str, proximo: str) -> list[dict]:
    links = []
    for nome in USUARIOS_FIXOS:
        consulta = {"usuario": nome}
        if proximo != "/":
            consulta["next"] = proximo
        links.append(
            {"nome": nome, "url": "/entrar?" + urlencode(consulta), "ativo": nome == selecionado}
        )
    return links


def _formulario(
    request: Request,
    repo: RepositorioAuth,
    settings: Settings,
    nome: str,
    proximo: str,
    *,
    erro: str = "",
    aviso: str = "",
    status_code: int = 200,
) -> HTMLResponse:
    """Renderiza a tela de entrada (com o passo de senha do usuário escolhido, se houver)."""
    usuario = repo.buscar_usuario(nome) if nome in USUARIOS_FIXOS else None
    token = _csrf_do_cookie(request)
    novo_cookie = token is None
    if novo_cookie:
        token = sessoes.novo_csrf()
    contexto = {
        "nomes": _links_de_nomes(usuario.nome if usuario else "", proximo),
        "usuario": usuario,
        "primeiro_acesso": bool(usuario and not usuario.tem_senha),
        "erro": erro,
        "aviso": aviso,
        "proximo": proximo,
        "csrf": token,
        "tamanho_minimo": 10,
        "tamanho_maximo": 128,
    }
    resposta = _pagina(request, contexto, status_code=status_code)
    if novo_cookie:
        _definir_cookie_csrf(resposta, token, settings)
    return resposta


def _csrf_recusado(request, repo, settings, nome, proximo) -> HTMLResponse:
    """Token ausente ou inválido: mostra o formulário de novo (o cookie só é criado se faltar)."""
    return _formulario(
        request,
        repo,
        settings,
        nome,
        proximo,
        erro="A página expirou. Confira os dados e tente de novo.",
        status_code=403,
    )


def _ja_tem_senha(request, repo, settings, nome, proximo) -> HTMLResponse:
    return _formulario(
        request,
        repo,
        settings,
        nome,
        proximo,
        erro="Este usuário já tem senha. Entre com a sua senha.",
        status_code=409,
    )


def _mensagem_bloqueio(bloqueado_ate, agora) -> str:
    minutos = max(1, math.ceil((bloqueado_ate - agora).total_seconds() / 60))
    unidade = "minuto" if minutos == 1 else "minutos"
    return f"Muitas tentativas. Tente novamente em {minutos} {unidade}."


# ---------- Rotas ----------


@router.get("/entrar", response_class=HTMLResponse)
def entrar_tela(
    request: Request,
    usuario: str = "",
    next: str = "/",
    criada: str = "",
    repo: RepositorioAuth = Depends(get_repositorio),
    settings: Settings = Depends(get_settings),
):
    proximo = sessoes.caminho_interno_seguro(next)
    token = request.cookies.get(sessoes.COOKIE_SESSAO)
    if token:
        sessao = repo.buscar_sessao(sessoes.hash_token(token))
        if sessao and sessao.expira_em > sessoes.agora():
            return RedirectResponse(proximo, status_code=303)  # já está logado
    aviso = "Senha cadastrada. Entre com a sua senha." if criada == "1" else ""
    return _formulario(request, repo, settings, usuario, proximo, aviso=aviso)


@router.post("/entrar")
async def entrar(
    request: Request,
    repo: RepositorioAuth = Depends(get_repositorio),
    settings: Settings = Depends(get_settings),
):
    dados = await _ler_formulario(request)
    nome = dados.get("usuario", "")
    proximo = sessoes.caminho_interno_seguro(dados.get("next"))
    if not _origem_confiavel(request) or not sessoes.csrf_valido(
        _csrf_do_cookie(request), dados.get("csrf")
    ):
        return _csrf_recusado(request, repo, settings, nome, proximo)

    usuario = repo.buscar_usuario(nome) if nome in USUARIOS_FIXOS else None
    if usuario is None:
        return _formulario(
            request, repo, settings, "", proximo, erro="Escolha um dos usuários.", status_code=400
        )
    if not usuario.tem_senha:
        return _formulario(
            request,
            repo,
            settings,
            nome,
            proximo,
            aviso="Este é o seu primeiro acesso: crie a sua senha.",
            status_code=409,
        )

    agora = sessoes.agora()
    if usuario.bloqueado_ate and usuario.bloqueado_ate > agora:
        return _formulario(
            request,
            repo,
            settings,
            nome,
            proximo,
            erro=_mensagem_bloqueio(usuario.bloqueado_ate, agora),
            status_code=429,
        )

    if not conferir_senha(dados.get("senha", ""), usuario.senha_hash):
        falhas = usuario.falhas_seguidas + 1
        if falhas >= LIMITE_FALHAS:
            repo.registrar_falhas(usuario.id, 0, agora + BLOQUEIO)
            erro = "Senha incorreta. Acesso bloqueado por 5 minutos."
        else:
            repo.registrar_falhas(usuario.id, falhas, None)
            restantes = LIMITE_FALHAS - falhas
            palavra = "tentativa" if restantes == 1 else "tentativas"
            erro = f"Senha incorreta. Restam {restantes} {palavra}."
        return _formulario(request, repo, settings, nome, proximo, erro=erro, status_code=401)

    if usuario.falhas_seguidas or usuario.bloqueado_ate:
        repo.zerar_falhas(usuario.id)
    anterior = request.cookies.get(sessoes.COOKIE_SESSAO)
    if anterior:
        repo.apagar_sessao(sessoes.hash_token(anterior))
    repo.apagar_sessoes_expiradas(agora)

    token = sessoes.novo_token()
    expira_em = agora + timedelta(hours=settings.sessao_duracao_horas)
    repo.criar_sessao(sessoes.hash_token(token), usuario.id, expira_em)

    resposta = RedirectResponse(proximo, status_code=303)
    _definir_cookie_sessao(resposta, token, settings)
    resposta.delete_cookie(sessoes.COOKIE_CSRF, path="/")
    return resposta


@router.post("/entrar/criar-senha")
async def criar_senha(
    request: Request,
    repo: RepositorioAuth = Depends(get_repositorio),
    settings: Settings = Depends(get_settings),
):
    dados = await _ler_formulario(request)
    nome = dados.get("usuario", "")
    proximo = sessoes.caminho_interno_seguro(dados.get("next"))
    if not _origem_confiavel(request) or not sessoes.csrf_valido(
        _csrf_do_cookie(request), dados.get("csrf")
    ):
        return _csrf_recusado(request, repo, settings, nome, proximo)

    usuario = repo.buscar_usuario(nome) if nome in USUARIOS_FIXOS else None
    if usuario is None:
        return _formulario(
            request, repo, settings, "", proximo, erro="Escolha um dos usuários.", status_code=400
        )

    if usuario.tem_senha:
        return _ja_tem_senha(request, repo, settings, nome, proximo)

    problema = validar_nova_senha(
        usuario.nome, dados.get("senha", ""), dados.get("confirmacao", "")
    )
    if problema:
        return _formulario(request, repo, settings, nome, proximo, erro=problema, status_code=400)

    # A gravação é atômica no banco: só vale se a senha ainda não existir.
    if not repo.definir_senha_inicial(usuario.nome, gerar_hash(dados["senha"])):
        return _ja_tem_senha(request, repo, settings, nome, proximo)

    consulta = {"usuario": usuario.nome, "criada": "1"}
    if proximo != "/":
        consulta["next"] = proximo
    return RedirectResponse("/entrar?" + urlencode(consulta), status_code=303)


@router.post("/sair")
async def sair(
    request: Request,
    repo: RepositorioAuth = Depends(get_repositorio),
):
    token = request.cookies.get(sessoes.COOKIE_SESSAO)
    if not token:
        return RedirectResponse("/entrar", status_code=303)

    dados = await _ler_formulario(request)
    if not _origem_confiavel(request) or not sessoes.csrf_valido(
        sessoes.csrf_da_sessao(token), dados.get("csrf")
    ):
        return HTMLResponse(
            "<!doctype html><meta charset=utf-8><title>Requisição recusada</title>"
            "<p>Requisição recusada. <a href='/'>Voltar ao FormaHub</a></p>",
            status_code=403,
        )

    repo.apagar_sessao(sessoes.hash_token(token))
    resposta = RedirectResponse("/entrar", status_code=303)
    resposta.delete_cookie(sessoes.COOKIE_SESSAO, path="/")
    return resposta
