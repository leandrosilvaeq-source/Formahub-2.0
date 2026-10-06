# FormaHub 2.0

Produtos, estoque e pedidos em um só lugar.

Aplicação web simples em Python (FastAPI + Jinja2), com banco PostgreSQL hospedado no Supabase.

## Requisitos

- Python 3.12
- Node.js (para o Supabase CLI e o PGlite dos testes SQL locais, instalados via `npm install`)

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

### Testes SQL locais (PGlite)

`npm run test:sql` aplica todas as migrations deste checkout num PostgreSQL em memória (PGlite,
versão fixa no `package.json`) e roda `supabase/tests/estoque_test.sql` (Estoque e Registrar
Compra, com as migrations 20261009 e 20261010). Nada se conecta ao
Supabase nem grava em disco. Outros arquivos de teste podem ser passados ao executor:

```powershell
npm run test:sql
node supabase/tests/executar_pglite.mjs supabase/tests/estoque_test.sql
```

O ambiente do Supabase é simulado só no que as migrations usam (papéis `anon`, `authenticated` e
`service_role`, privilégios padrão do schema `public` e `storage.buckets`). O `pytest` também roda
esse teste e confere o repositório do Estoque contra as funções reais (é pulado sem Node/PGlite).
Depois de aplicar uma migration no Supabase, o teste correspondente ainda deve rodar lá.

### Estoque: entrada por compra (migrations 20261009 e 20261010)

- `20261009120000_estoque.sql` cria as tabelas de lotes (filamentos, acessórios e embalagens).
- `20261010120000_compras_estoque.sql` faz toda entrada passar por uma compra: cria `compras` e
  `compra_itens`, liga cada lote ao item que o originou (`compra_item_id`), troca o cadastro
  direto por `registrar_compra()` (compra, itens e lotes numa transação; a `chave_envio` impede
  duplicar a mesma submissão), limita a edição à descrição do lote e cria o bucket privado
  `compra-imagens`. Um gatilho impede que o saldo de um lote aumente por UPDATE.
- As duas são aplicadas juntas e nessa ordem. A 20261010 **exige as tabelas de lotes vazias**
  (lotes antigos não têm compra) e que o bucket `compra-imagens` ainda não exista; se não,
  ela para com erro sem alterar nada.
- Depois de aplicar no Supabase (por pedido explícito), rodar lá
  `supabase db query --linked -f supabase/tests/estoque_test.sql` (tudo em ROLLBACK).

`supabase/operacoes/` guarda correções de dados de execução única: são rodadas manualmente, uma
vez, por decisão explícita, e nunca pelo `db push`. Cada arquivo explica o que confere e o que altera.

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
