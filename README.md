# FormaHub 2.0

Produtos, estoque e pedidos em um só lugar.

Aplicação web simples em Python (FastAPI + Jinja2), com banco PostgreSQL hospedado no Supabase.

## Requisitos

- Python 3.12
- Node.js (apenas para o Supabase CLI, instalado via `npm install`)

## Primeira execução (em cada computador)

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
npm install
copy .env.example .env   # preencha os valores do Supabase
```

O `.env` nunca vai para o Git. A página inicial e o `/health` funcionam mesmo sem ele preenchido.

## Rodar

```powershell
uvicorn app.main:app --reload
```

- http://127.0.0.1:8000/ — página inicial
- http://127.0.0.1:8000/health — verificação de saúde

## Qualidade

```powershell
pytest
ruff check .
ruff format --check .
```

## Banco de dados

O banco só é alterado por migrations em `supabase/migrations/`, criadas com o Supabase CLI
(`npx supabase migration new <nome>`). Não existe banco local.

## Estrutura

```
app/
  main.py            rotas
  core/config.py     configuração centralizada (.env)
  templates/         páginas Jinja2
  static/css/app.css estilos (paleta no topo do arquivo)
tests/               testes Pytest
supabase/            configuração e migrations do Supabase
```
