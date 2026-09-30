import re
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.datastructures import UploadFile

from app.auth import sessoes
from app.auth.repositorio import ServicoIndisponivel, Usuario
from app.auth.rotas import (
    NaoAutenticado,
    exigir_usuario,
    origem_confiavel,
    tratar_nao_autenticado,
    tratar_servico_indisponivel,
)
from app.auth.rotas import router as rotas_auth
from app.pedidos import (
    FORMAS_ENTREGA,
    FORMAS_PAGAMENTO,
    LIMITE_IMAGEM_BYTES,
    Item,
    PedidoNaoSalvo,
    formatar_brl,
    gravar_pedido,
    ler_formulario,
    pedido_vazio,
    validar,
)
from app.pedidos_repositorio import RepositorioPedidos, get_repositorio_pedidos

BASE_DIR = Path(__file__).resolve().parent

# Imagem do item na posição n do formulário (enviada pelo JavaScript da tela).
CAMPO_IMAGEM = re.compile(r"item_imagem_(\d{1,5})")

app = FastAPI(title="FormaHub 2.0")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
app.include_router(rotas_auth)
app.add_exception_handler(NaoAutenticado, tratar_nao_autenticado)
app.add_exception_handler(ServicoIndisponivel, tratar_servico_indisponivel)
templates = Jinja2Templates(directory=BASE_DIR / "templates")
templates.env.filters["brl"] = formatar_brl

MODULOS = [
    {"titulo": "Produtos", "descricao": "Cadastro e consulta dos produtos."},
    {"titulo": "Estoque", "descricao": "Saldos e movimentações de estoque."},
    {
        "titulo": "Pedidos",
        "descricao": "Registro e acompanhamento de pedidos.",
        "link": "/pedidos/novo",
        "link_texto": "Novo pedido",
    },
]


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse, dependencies=[Depends(exigir_usuario)])
def home(request: Request):
    return templates.TemplateResponse(request, "index.html", {"modulos": MODULOS})


def _tela_pedido(
    request: Request, pedido, sucesso: str = "", erro_geral: str = "", status_code: int = 200
):
    return templates.TemplateResponse(
        request,
        "pedidos/novo.html",
        {
            "pedido": pedido,
            "novo_item": Item(),
            "sucesso": sucesso,
            "erro_geral": erro_geral,
            "formas_pagamento": FORMAS_PAGAMENTO,
            "formas_entrega": FORMAS_ENTREGA,
        },
        status_code=status_code,
    )


@app.get("/pedidos/novo", response_class=HTMLResponse, dependencies=[Depends(exigir_usuario)])
def novo_pedido(request: Request):
    return _tela_pedido(request, pedido_vazio())


async def _ler_imagens(formulario) -> dict[int, bytes]:
    """Posição do item -> conteúdo da imagem (lê no máximo 1 byte além do limite)."""
    imagens = {}
    for chave, valor in formulario.multi_items():
        encontrado = CAMPO_IMAGEM.fullmatch(chave)
        if encontrado and isinstance(valor, UploadFile):
            imagens[int(encontrado.group(1))] = await valor.read(LIMITE_IMAGEM_BYTES + 1)
    return imagens


@app.post("/pedidos/novo", response_class=HTMLResponse)
async def salvar_pedido(
    request: Request,
    usuario: Usuario = Depends(exigir_usuario),
    repo: RepositorioPedidos | None = Depends(get_repositorio_pedidos),
):
    async with request.form() as formulario:
        # Só os campos de texto; qualquer usuário enviado pelo navegador é ignorado:
        # o criador do pedido é sempre o usuário da sessão.
        textos: dict[str, list[str]] = {}
        for chave, valor in formulario.multi_items():
            if isinstance(valor, str):
                textos.setdefault(chave, []).append(valor)

        csrf = textos.get("csrf", [""])[0]
        if not origem_confiavel(request) or not sessoes.csrf_valido(request.state.csrf, csrf):
            return _tela_pedido(
                request,
                ler_formulario(textos),
                erro_geral="A página expirou. Confira os dados e salve de novo.",
                status_code=403,
            )

        pedido = validar(ler_formulario(textos, await _ler_imagens(formulario)))

    if not pedido.valido:
        return _tela_pedido(request, pedido, status_code=422)

    try:
        if repo is None:
            raise PedidoNaoSalvo
        gravar_pedido(repo, pedido, usuario.id)
    except PedidoNaoSalvo:
        return _tela_pedido(
            request,
            pedido,
            erro_geral="Não foi possível salvar o pedido agora. Tente novamente em instantes.",
            status_code=503,
        )

    unidades = "item" if pedido.quantidade_total == 1 else "itens"
    sucesso = (
        f"Pedido de {pedido.cliente} salvo com sucesso — "
        f"{pedido.quantidade_total} {unidades}, total {formatar_brl(pedido.total)}."
    )
    return _tela_pedido(request, pedido_vazio(), sucesso=sucesso)
