"""Rotas da tela Estoque (/estoque): consulta das três áreas, Registrar Compra e edição.

Busca, filtros e ordenação são parâmetros da URL (cada área com os seus), sem JavaScript.
Toda entrada no estoque é feita pela janela Registrar Compra (POST /estoque/compras, enviado
pelo JavaScript da tela, com resposta em JSON); não há cadastro direto de lotes. A edição de
um lote só corrige a descrição: o saldo, a data e o custo vêm da compra.
"""

import re
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.datastructures import UploadFile

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
from app.estoque_compra import (
    CATEGORIAS,
    LIMITE_ITENS,
    LOCAIS,
    LOCAIS_COM_NOME,
    CompraNaoSalva,
    gravar_compra,
    validar_compra,
)
from app.estoque_repositorio import (
    FalhaNoEstoque,
    LoteNaoEncontrado,
    RepositorioEstoque,
    get_repositorio_estoque,
)
from app.pedidos import LIMITE_IMAGEM_BYTES, LIMITE_IMAGEM_MB, formatar_brl

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).resolve().parent / "templates")
templates.env.filters["brl"] = formatar_brl
templates.env.filters["peso"] = formatar_peso
templates.env.filters["unidades"] = formatar_unidades
templates.env.filters["data_compra"] = formatar_data

# Id do lote na URL: só dígitos, dentro do limite de um bigint.
ID_LOTE = re.compile(r"[0-9]{1,18}")
# Foto da linha na posição n da janela Registrar Compra.
CAMPO_FOTO = re.compile(r"item_(\d{1,3})_foto")
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
    "compra": "Compra registrada. Os itens já estão no estoque.",
    "filamento-editado": "Lote de filamento atualizado.",
}
for _area in AREAS_ITENS.values():
    SUCESSO[f"{_area.categoria}-editado"] = f"Lote de {_area.singular.lower()} atualizado."


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
        # Janela Registrar Compra
        "locais": LOCAIS,
        "locais_com_nome": LOCAIS_COM_NOME,
        "categorias": CATEGORIAS,
        "limite_itens": LIMITE_ITENS,
        "limite_imagem_mb": LIMITE_IMAGEM_MB,
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


# ---------- Registrar Compra ----------


def _json(conteudo: dict, status_code: int = 200) -> JSONResponse:
    return JSONResponse(conteudo, status_code=status_code, headers={"Cache-Control": "no-store"})


@router.post("/estoque/compras")
async def registrar_compra(
    request: Request,
    usuario: Usuario = Depends(exigir_usuario),
    repo: RepositorioEstoque | None = Depends(get_repositorio_estoque),
):
    async with request.form() as formulario:
        # Só os campos de texto e as fotos; qualquer usuário enviado pelo navegador é
        # ignorado: o responsável pela compra é sempre o usuário da sessão.
        textos = {c: v for c, v in formulario.multi_items() if isinstance(v, str)}
        valida = origem_confiavel(request) and sessoes.csrf_valido(
            request.state.csrf, textos.get("csrf", "")
        )
        if not valida:
            return _json({"erro": MENSAGEM_EXPIRADA}, status_code=403)
        imagens = {}
        for chave, valor in formulario.multi_items():
            encontrado = CAMPO_FOTO.fullmatch(chave)
            if encontrado and isinstance(valor, UploadFile):
                # Lê no máximo 1 byte além do limite: o excesso basta para recusar.
                imagens[int(encontrado.group(1))] = await valor.read(LIMITE_IMAGEM_BYTES + 1)
        compra = validar_compra(textos, imagens)

    if compra.chave_envio is None:
        # Sem a chave da submissão não há proteção contra duplicação: a janela é antiga.
        return _json({"erro": MENSAGEM_EXPIRADA}, status_code=403)
    if compra.erros:
        return _json({"erro": MENSAGEM_CORRIGIR, "campos": compra.erros}, status_code=422)

    try:
        if repo is None:
            raise CompraNaoSalva
        gravar_compra(repo, compra, usuario.id)
    except CompraNaoSalva:
        return _json({"erro": MENSAGEM_NAO_SALVO}, status_code=503)
    return _json({"destino": "/estoque?salvo=compra"})


# ---------- Edição da descrição do lote ----------


async def _ler_post(request: Request) -> tuple[dict[str, str], bool]:
    """(campos de texto, página válida): confere a origem e o token CSRF da sessão."""
    async with request.form() as formulario:
        dados = {chave: valor for chave, valor in formulario.items() if isinstance(valor, str)}
    valida = origem_confiavel(request) and sessoes.csrf_valido(
        request.state.csrf, dados.get("csrf", "")
    )
    return dados, valida


def _voltar(codigo: str, ancora: str) -> RedirectResponse:
    return RedirectResponse(f"/estoque?salvo={codigo}#{ancora}", status_code=303)


def _lote_da_url(lote_id: str) -> int | None:
    return int(lote_id) if ID_LOTE.fullmatch(lote_id) else None


def _buscar_lote(repo: RepositorioEstoque | None, encontrar):
    """O lote procurado (None se não existir); FalhaNoEstoque se o banco não responder."""
    if repo is None:
        raise FalhaNoEstoque
    return encontrar(repo.listar_estoque())


def _lote_para_resumo(repo: RepositorioEstoque | None, encontrar):
    """Lote para o resumo da tela de edição; sem banco, a tela abre sem o resumo."""
    try:
        return _buscar_lote(repo, encontrar)
    except FalhaNoEstoque:
        return None


# ---------- Filamentos ----------


def _tela_filamento(
    request: Request, form: FormFilamento, lote, numero: int, erro: str = "", status_code=200
):
    contexto = {
        "form": form,
        "lote": lote,
        "lote_id": numero,
        "tipos": TIPOS_FILAMENTO,
        "erro": erro,
    }
    return _pagina(request, "estoque/filamento.html", contexto, status_code=status_code)


def _filamento(numero: int):
    return lambda estoque: next((f for f in estoque.filamentos if f.id == numero), None)


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
        lote = _buscar_lote(repo, _filamento(numero))
    except FalhaNoEstoque:
        return _indisponivel(request)
    if lote is None:
        return _nao_encontrado(request)
    return _tela_filamento(request, form_de_filamento(lote), lote, numero)


@router.post("/estoque/filamentos/{lote_id}/editar", response_class=HTMLResponse)
async def salvar_filamento(
    request: Request,
    lote_id: str,
    usuario: Usuario = Depends(exigir_usuario),
    repo: RepositorioEstoque | None = Depends(get_repositorio_estoque),
):
    numero = _lote_da_url(lote_id)
    if numero is None:
        return _nao_encontrado(request)

    # Só a descrição é lida: saldo, data ou custo enviados pelo navegador são ignorados.
    dados, valida = await _ler_post(request)
    form = ler_filamento(dados)
    if not valida or not validar_filamento(form):
        erro, status = (MENSAGEM_CORRIGIR, 422) if valida else (MENSAGEM_EXPIRADA, 403)
        lote = _lote_para_resumo(repo, _filamento(numero))
        return _tela_filamento(request, form, lote, numero, erro, status_code=status)

    try:
        if repo is None:
            raise FalhaNoEstoque
        repo.editar_filamento(numero, form.cor, form.material, form.tipo, form.marca, usuario.id)
    except LoteNaoEncontrado:
        return _nao_encontrado(request)
    except FalhaNoEstoque:
        lote = _lote_para_resumo(repo, _filamento(numero))
        return _tela_filamento(request, form, lote, numero, MENSAGEM_NAO_SALVO, status_code=503)
    return _voltar("filamento-editado", "filamentos")


# ---------- Acessórios e embalagens ----------


def _tela_item(
    request: Request, area: Area, form: FormItem, lote, numero: int, erro="", status_code=200
):
    contexto = {"area": area, "form": form, "lote": lote, "lote_id": numero, "erro": erro}
    return _pagina(request, "estoque/item.html", contexto, status_code=status_code)


def _item(area: Area, numero: int):
    return lambda estoque: next(
        (i for i in estoque.itens if i.id == numero and i.categoria == area.categoria), None
    )


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
        lote = _buscar_lote(repo, _item(area, numero))
    except FalhaNoEstoque:
        return _indisponivel(request)
    if lote is None:
        return _nao_encontrado(request)
    return _tela_item(request, area, form_de_item(lote), lote, numero)


@router.post("/estoque/{slug}/{lote_id}/editar", response_class=HTMLResponse)
async def salvar_item(
    request: Request,
    slug: str,
    lote_id: str,
    usuario: Usuario = Depends(exigir_usuario),
    repo: RepositorioEstoque | None = Depends(get_repositorio_estoque),
):
    area = AREAS_ITENS.get(slug)
    numero = _lote_da_url(lote_id)
    if area is None or numero is None:
        return _nao_encontrado(request)

    # Só o nome é lido: quantidade, data ou custo enviados pelo navegador são ignorados.
    dados, valida = await _ler_post(request)
    form = ler_item(dados)
    if not valida or not validar_item(form):
        erro, status = (MENSAGEM_CORRIGIR, 422) if valida else (MENSAGEM_EXPIRADA, 403)
        lote = _lote_para_resumo(repo, _item(area, numero))
        return _tela_item(request, area, form, lote, numero, erro, status_code=status)

    try:
        if repo is None:
            raise FalhaNoEstoque
        repo.editar_item(numero, area.categoria, form.nome, usuario.id)
    except LoteNaoEncontrado:
        return _nao_encontrado(request)
    except FalhaNoEstoque:
        lote = _lote_para_resumo(repo, _item(area, numero))
        return _tela_item(request, area, form, lote, numero, MENSAGEM_NAO_SALVO, status_code=503)
    return _voltar(f"{area.categoria}-editado", area.slug)
