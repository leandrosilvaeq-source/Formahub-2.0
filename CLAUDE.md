# CLAUDE.md — FormaHub 2.0

## Objetivo

Sistema simples para gerenciar **Produtos, Estoque e Pedidos**, nessa ordem de prioridade.
Projeto novo, criado do zero.

## Arquitetura

- Python 3.12, FastAPI, Jinja2
- HTMX apenas quando realmente necessário
- CSS próprio (`app/static/css/app.css`); paleta centralizada em variáveis no topo do arquivo
- Banco PostgreSQL no Supabase, acessado via `supabase-py`
- Configuração centralizada em `app/core/config.py` (lê `.env`)
- Pytest para testes, Ruff para lint/formatação
- Sem React, sem Docker, sem banco local
- Código versionado no GitHub e usado em dois computadores

## Regras

- **Manter o sistema simples.** Nada de complexidade antecipada, abstrações ou camadas "para o futuro".
- **Não reutilizar** código, banco ou migrations do FormaHub antigo.
- **Não criar módulos, tabelas ou regras de negócio sem aprovação prévia.**
- **Migrations são a única forma de alterar o banco** (`supabase/migrations/`). Nunca alterar o banco remoto manualmente, e não executar `db push` sem pedido explícito.
- **Nunca versionar segredos.** `.env` fica fora do Git; `.env.example` só tem nomes de variáveis, sem valores.
- **Trabalhar em incrementos pequenos**, cada um testado (`pytest`) e com `ruff check .` limpo.
- **Interface em português** (textos, rótulos e mensagens ao usuário).

## Comandos

```powershell
.venv\Scripts\Activate.ps1
uvicorn app.main:app --reload
pytest
ruff check .
ruff format .
```
