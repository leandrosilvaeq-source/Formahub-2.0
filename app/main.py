from pathlib import Path
from urllib.parse import parse_qs

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.auth.repositorio import ServicoIndisponivel
from app.auth.rotas import (
    NaoAutenticado,
    exigir_usuario,
    tratar_nao_autenticado,
    tratar_servico_indisponivel,
)
from app.auth.rotas import router as rotas_auth
from app.pedidos import (
    FORMAS_ENTREGA,
    FORMAS_PAGAMENTO,
    Item,
    formatar_brl,
    ler_formulario,
    pedido_vazio,
    validar,
)

BASE_DIR = Path(__file__).resolve().parent

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


def _tela_pedido(request: Request, pedido, sucesso: str = "", status_code: int = 200):
    return templates.TemplateResponse(
        request,
        "pedidos/novo.html",
        {
            "pedido": pedido,
            "novo_item": Item(),
            "sucesso": sucesso,
            "formas_pagamento": FORMAS_PAGAMENTO,
            "formas_entrega": FORMAS_ENTREGA,
        },
        status_code=status_code,
    )


@app.get("/pedidos/novo", response_class=HTMLResponse, dependencies=[Depends(exigir_usuario)])
def novo_pedido(request: Request):
    return _tela_pedido(request, pedido_vazio())


@app.post("/pedidos/novo", response_class=HTMLResponse, dependencies=[Depends(exigir_usuario)])
async def salvar_pedido(request: Request):
    # Nesta versão o pedido só é validado; nada é gravado no banco.
    dados = parse_qs((await request.body()).decode(), keep_blank_values=True)
    pedido = validar(ler_formulario(dados))
    if not pedido.valido:
        return _tela_pedido(request, pedido, status_code=422)
    unidades = "item" if pedido.quantidade_total == 1 else "itens"
    sucesso = (
        f"Pedido de {pedido.cliente} validado com sucesso — "
        f"{pedido.quantidade_total} {unidades}, total {formatar_brl(pedido.total)}. "
        "(Ainda não é gravado no banco.)"
    )
    return _tela_pedido(request, pedido_vazio(), sucesso=sucesso)
