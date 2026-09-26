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

## Login

Três usuários fixos (Leandro, Kassia e Marise), sem e-mail, sem Supabase Auth e sem código de ativação.
No primeiro acesso cada pessoa cria e confirma a própria senha; depois disso, só o login normal. A senha
é guardada apenas como hash Argon2id, e cada usuário só pode cadastrá-la uma vez pela tela pública.

- Variáveis no `.env` (veja `.env.example`): `SUPABASE_SECRET_KEY` (só no servidor), `COOKIE_SECURE`
  (`true` em produção com HTTPS) e `SESSAO_DURACAO_HORAS`.
- Tabelas em `supabase/migrations/20260926120000_criar_autenticacao.sql`. A migration só é aplicada
  ao Supabase por pedido explícito (`supabase db push`).
- Os testes usam um repositório em memória e nunca acessam o Supabase.

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
