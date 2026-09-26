from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="FormaHub 2.0")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

MODULOS = [
    {"titulo": "Produtos", "descricao": "Cadastro e consulta dos produtos."},
    {"titulo": "Estoque", "descricao": "Saldos e movimentações de estoque."},
    {"titulo": "Pedidos", "descricao": "Registro e acompanhamento de pedidos."},
]


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(request, "index.html", {"modulos": MODULOS})
