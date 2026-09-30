-- Autenticação do FormaHub 2.0: três usuários fixos, senha definida uma única vez e sessões.
--
-- Não usa Supabase Auth. Somente o servidor (chave secreta / role service_role) acessa estas
-- tabelas: RLS ligado e nenhuma policy, então anon e authenticated não veem nada.
-- Migration LOCAL: aplicar somente por pedido explícito (supabase db push).

-- ---------- Usuários ----------
create table public.usuarios (
  id                bigint generated always as identity primary key,
  nome              text        not null unique
                    check (nome in ('Leandro', 'Kassia', 'Marise')),
  senha_hash        text,
  senha_definida_em timestamptz,
  falhas_seguidas   integer     not null default 0 check (falhas_seguidas >= 0),
  bloqueado_ate     timestamptz,
  criado_em         timestamptz not null default now(),
  -- ausência de senha identifica o primeiro acesso
  constraint usuarios_senha_coerente check ((senha_hash is null) = (senha_definida_em is null))
);

insert into public.usuarios (nome) values ('Leandro'), ('Kassia'), ('Marise');

-- ---------- Sessões ----------
-- Guarda somente o SHA-256 (hex) do token; o token em si só existe no cookie do navegador.
create table public.sessoes (
  token_hash text        primary key check (token_hash ~ '^[0-9a-f]{64}$'),
  usuario_id bigint      not null references public.usuarios (id) on delete cascade,
  criada_em  timestamptz not null default now(),
  expira_em  timestamptz not null,
  ultimo_uso timestamptz not null default now(),
  check (expira_em > criada_em)
);

create index sessoes_usuario_id_idx on public.sessoes (usuario_id);
create index sessoes_expira_em_idx on public.sessoes (expira_em);

-- ---------- Segurança: RLS sem policies e privilégios mínimos ----------
alter table public.usuarios enable row level security;
alter table public.sessoes enable row level security;

revoke all on public.usuarios from anon, authenticated, service_role;
revoke all on public.sessoes from anon, authenticated, service_role;
-- A sequence da identity também recebe os privilégios padrão do projeto; ninguém insere usuários.
revoke all on sequence public.usuarios_id_seq from anon, authenticated, service_role;

-- O servidor lê usuários e só altera o controle de tentativas. Não cria nem apaga usuários e
-- não grava senha_hash diretamente: a senha só entra pela função abaixo.
grant select on public.usuarios to service_role;
grant update (falhas_seguidas, bloqueado_ate) on public.usuarios to service_role;
grant select, insert, update, delete on public.sessoes to service_role;

-- ---------- Definição atômica da senha inicial ----------
-- Só grava quando senha_hash ainda é NULL e o valor é um hash argon2id. Devolve true se gravou.
-- Com dois acessos simultâneos, apenas um vence; depois disso a função nunca mais altera a senha.
create function public.definir_senha_inicial(p_nome text, p_senha_hash text)
returns boolean
language plpgsql
security definer
set search_path = ''
as $corpo$
declare
  v_linhas integer;
begin
  if p_senha_hash is null or p_senha_hash not like '$argon2id$%' then
    return false;
  end if;

  update public.usuarios
     set senha_hash        = p_senha_hash,
         senha_definida_em = now(),
         falhas_seguidas   = 0,
         bloqueado_ate     = null
   where nome = p_nome
     and senha_hash is null;

  get diagnostics v_linhas = row_count;
  return v_linhas = 1;
end;
$corpo$;

revoke all on function public.definir_senha_inicial(text, text) from public, anon, authenticated;
grant execute on function public.definir_senha_inicial(text, text) to service_role;
