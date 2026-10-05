"""Rotas da tela Estoque (/estoque): consulta das três áreas, cadastro e edição dos lotes.

Sem JavaScript: busca, filtros e ordenação são parâmetros da URL (cada área com os seus);
o formulário salva por POST e, se der certo, volta para /estoque com a mensagem de sucesso.
"""

import re
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.auth import sessoes
from app.auth.repositorio import Usuario
from app.auth.rotas import exigir_usuario, origem_confiavel
from app.estoque import (
    AREAS_ITENS,
    ORDENS_FILAMENTO,
    ORDENS_ITEM,
    TIPOS_FILAMENTO,
    Area,
    FormFilamento,
    FormItem,
    consulta_filamentos,
    consulta_itens,
    filtrar_filamentos,
    filtrar_itens,
    form_de_filamento,
    form_de_item,
    formatar_data,
    formatar_peso,
    formatar_unidades,
    ler_filamento,
    ler_item,
    materiais,
    peso_total,
    unidades_total,
    validar_filamento,
    validar_item,
)
from app.estoque_repositorio import (
    FalhaNoEstoque,
    LoteNaoEncontrado,
    RepositorioEstoque,
    get_repositorio_estoque,
)
from app.pedidos import formatar_brl

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).resolve().parent / "templates")
templates.env.filters["brl"] = formatar_brl
templates.env.filters["peso"] = formatar_peso
templates.env.filters["unidades"] = formatar_unidades
templates.env.filters["data_compra"] = formatar_data

# Id do lote na URL: só dígitos, dentro do limite de um bigint.
ID_LOTE = re.compile(r"[0-9]{1,18}")
# Prefixo dos parâmetros de busca de cada área na URL.
PREFIXOS = {"filamentos": "f", "acessorios": "a", "embalagens": "e"}
PARAMETROS_CONSULTA = ("f_busca", "f_material", "f_tipo", "f_ordem") + tuple(
    f"{p}_{campo}" for p in ("a", "e") for campo in ("busca", "ordem")
)

MENSAGEM_INDISPONIVEL = "Não foi possível carregar o estoque agora. Tente novamente em instantes."
MENSAGEM_NAO_SALVO = "Não foi possível salvar agora. Tente novamente em instantes."
MENSAGEM_EXPIRADA = "A página expirou. Confira os dados e salve de novo."
MENSAGEM_CORRIGIR = "Não foi possível salvar. Corrija os campos destacados."

# Sucesso: código na URL depois de salvar -> mensagem (nada do navegador vai para a tela).
SUCESSO = {
    "filamento-novo": "Lote de filamento cadastrado.",
    "filamento-editado": "Lote de filamento atualizado.",
}
for _area in AREAS_ITENS.values():
    _nome = _area.singular.lower()
    SUCESSO[f"{_area.categoria}-novo"] = f"Lote de {_nome} cadastrado."
    SUCESSO[f"{_area.categoria}-editado"] = f"Lote de {_nome} atualizado."


def _pagina(request: Request, modelo: str, contexto: dict, status_code: int = 200):
    resposta = templates.TemplateResponse(request, modelo, contexto, status_code=status_code)
    resposta.headers["Cache-Control"] = "no-store"
    return resposta


def _nao_encontrado(request: Request):
    return _pagina(request, "estoque/nao_encontrado.html", {}, status_code=404)


def _indisponivel(request: Request):
    return _pagina(request, "estoque/aviso.html", {"erro": MENSAGEM_INDISPONIVEL}, status_code=503)


# ---------- Consulta ----------


@router.get("/estoque", response_class=HTMLResponse, dependencies=[Depends(exigir_usuario)])
def estoque(request: Request, repo: RepositorioEstoque | None = Depends(get_repositorio_estoque)):
    parametros = request.query_params
    # Valores atuais de todas as áreas: cada formulário de busca repete os das outras.
    atuais = {nome: parametros.get(nome, "") for nome in PARAMETROS_CONSULTA}
    contexto = {
        "erro": "",
        "sucesso": SUCESSO.get(parametros.get("salvo", ""), ""),
        "atuais": atuais,
        "tipos": TIPOS_FILAMENTO,
        "ordens_filamento": ORDENS_FILAMENTO,
        "ordens_item": ORDENS_ITEM,
    }
    try:
        if repo is None:
            raise FalhaNoEstoque
        dados = repo.listar_estoque()
    except FalhaNoEstoque:
        contexto["erro"] = MENSAGEM_INDISPONIVEL
        return _pagina(request, "estoque/lista.html", contexto, status_code=503)

    consulta = consulta_filamentos(parametros)
    filamentos = filtrar_filamentos(dados.filamentos, consulta)
    contexto["filamentos"] = {
        "consulta": consulta,
        "lotes": filamentos,
        "cadastrados": len(dados.filamentos),
        "total": peso_total(filamentos),
        "materiais": materiais(dados.filamentos),
    }
    contexto["areas"] = []
    for area in AREAS_ITENS.values():
        todos = [i for i in dados.itens if i.categoria == area.categoria]
        consulta = consulta_itens(parametros, PREFIXOS[area.slug])
        lotes = filtrar_itens(todos, consulta)
        contexto["areas"].append(
            {
                "area": area,
                "prefixo": PREFIXOS[area.slug],
                "consulta": consulta,
                "lotes": lotes,
                "cadastrados": len(todos),
                "total": unidades_total(lotes),
            }
        )
    return _pagina(request, "estoque/lista.html", contexto)


# ---------- Formulários ----------


async def _ler_post(request: Request) -> tuple[dict[str, str], bool]:
    """(campos de texto, página válida): confere a origem e o token CSRF da sessão."""
    async with request.form() as formulario:
        dados = {chave: valor for chave, valor in formulario.items() if isinstance(valor, str)}
    valida = origem_confiavel(request) and sessoes.csrf_valido(
        request.state.csrf, dados.get("csrf", "")
    )
    return dados, valida


def _tela_filamento(
    request: Request, form: FormFilamento, lote_id: int | None, erro: str = "", status_code=200
):
    contexto = {"form": form, "lote_id": lote_id, "tipos": TIPOS_FILAMENTO, "erro": erro}
    return _pagina(request, "estoque/filamento.html", contexto, status_code=status_code)


def _tela_item(
    request: Request, area: Area, form: FormItem, lote_id: int | None, erro="", status_code=200
):
    contexto = {"area": area, "form": form, "lote_id": lote_id, "erro": erro}
    return _pagina(request, "estoque/item.html", contexto, status_code=status_code)


def _voltar(codigo: str, ancora: str) -> RedirectResponse:
    return RedirectResponse(f"/estoque?salvo={codigo}#{ancora}", status_code=303)


def _lote_da_url(lote_id: str) -> int | None:
    return int(lote_id) if ID_LOTE.fullmatch(lote_id) else None


# ---------- Filamentos ----------


@router.get(
    "/estoque/filamentos/novo",
    response_class=HTMLResponse,
    dependencies=[Depends(exigir_usuario)],
)
def novo_filamento(request: Request):
    return _tela_filamento(request, FormFilamento(), None)


@router.get(
    "/estoque/filamentos/{lote_id}/editar",
    response_class=HTMLResponse,
    dependencies=[Depends(exigir_usuario)],
)
def editar_filamento(
    request: Request,
    lote_id: str,
    repo: RepositorioEstoque | None = Depends(get_repositorio_estoque),
):
    numero = _lote_da_url(lote_id)
    if numero is None:
        return _nao_encontrado(request)
    try:
        if repo is None:
            raise FalhaNoEstoque
        lote = next((f for f in repo.listar_estoque().filamentos if f.id == numero), None)
    except FalhaNoEstoque:
        return _indisponivel(request)
    if lote is None:
        return _nao_encontrado(request)
    return _tela_filamento(request, form_de_filamento(lote), numero)


@router.post("/estoque/filamentos/novo", response_class=HTMLResponse)
@router.post("/estoque/filamentos/{lote_id}/editar", response_class=HTMLResponse)
async def salvar_filamento(
    request: Request,
    lote_id: str | None = None,
    usuario: Usuario = Depends(exigir_usuario),
    repo: RepositorioEstoque | None = Depends(get_repositorio_estoque),
):
    numero = None
    if lote_id is not None:
        numero = _lote_da_url(lote_id)
        if numero is None:
            return _nao_encontrado(request)

    dados, valida = await _ler_post(request)
    form = ler_filamento(dados)
    if not valida:
        return _tela_filamento(request, form, numero, MENSAGEM_EXPIRADA, status_code=403)
    lote = validar_filamento(form, numero)
    if lote is None:
        return _tela_filamento(request, form, numero, MENSAGEM_CORRIGIR, status_code=422)

    try:
        if repo is None:
            raise FalhaNoEstoque
        repo.salvar_filamento(lote, usuario.id)
    except LoteNaoEncontrado:
        return _nao_encontrado(request)
    except FalhaNoEstoque:
        return _tela_filamento(request, form, numero, MENSAGEM_NAO_SALVO, status_code=503)
    return _voltar("filamento-editado" if numero else "filamento-novo", "filamentos")


# ---------- Acessórios e embalagens ----------


@router.get(
    "/estoque/{slug}/novo", response_class=HTMLResponse, dependencies=[Depends(exigir_usuario)]
)
def novo_item(request: Request, slug: str):
    area = AREAS_ITENS.get(slug)
    if area is None:
        return _nao_encontrado(request)
    return _tela_item(request, area, FormItem(), None)


@router.get(
    "/estoque/{slug}/{lote_id}/editar",
    response_class=HTMLResponse,
    dependencies=[Depends(exigir_usuario)],
)
def editar_item(
    request: Request,
    slug: str,
    lote_id: str,
    repo: RepositorioEstoque | None = Depends(get_repositorio_estoque),
):
    area = AREAS_ITENS.get(slug)
    numero = _lote_da_url(lote_id)
    if area is None or numero is None:
        return _nao_encontrado(request)
    try:
        if repo is None:
            raise FalhaNoEstoque
        itens = repo.listar_estoque().itens
    except FalhaNoEstoque:
        return _indisponivel(request)
    lote = next((i for i in itens if i.id == numero and i.categoria == area.categoria), None)
    if lote is None:
        return _nao_encontrado(request)
    return _tela_item(request, area, form_de_item(lote), numero)


@router.post("/estoque/{slug}/novo", response_class=HTMLResponse)
@router.post("/estoque/{slug}/{lote_id}/editar", response_class=HTMLResponse)
async def salvar_item(
    request: Request,
    slug: str,
    lote_id: str | None = None,
    usuario: Usuario = Depends(exigir_usuario),
    repo: RepositorioEstoque | None = Depends(get_repositorio_estoque),
):
    area = AREAS_ITENS.get(slug)
    numero = None
    if lote_id is not None:
        numero = _lote_da_url(lote_id)
        if numero is None:
            return _nao_encontrado(request)
    if area is None:
        return _nao_encontrado(request)

    dados, valida = await _ler_post(request)
    form = ler_item(dados)
    if not valida:
        return _tela_item(request, area, form, numero, MENSAGEM_EXPIRADA, status_code=403)
    lote = validar_item(form, area, numero)
    if lote is None:
        return _tela_item(request, area, form, numero, MENSAGEM_CORRIGIR, status_code=422)

    try:
        if repo is None:
            raise FalhaNoEstoque
        repo.salvar_item(lote, usuario.id)
    except LoteNaoEncontrado:
        return _nao_encontrado(request)
    except FalhaNoEstoque:
        return _tela_item(request, area, form, numero, MENSAGEM_NAO_SALVO, status_code=503)
    return _voltar(f"{area.categoria}-{'editado' if numero else 'novo'}", area.slug)
