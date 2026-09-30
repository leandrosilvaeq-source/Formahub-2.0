-- Teste transacional da migration de autenticação (20260926120000_criar_autenticacao.sql).
--
-- Tudo roda dentro de BEGIN/ROLLBACK: nenhuma senha ou sessão de teste permanece.
-- Qualquer falha interrompe com RAISE EXCEPTION. Usa apenas um hash fictício, nunca senha real.
-- Execução: supabase db query --linked -f supabase/tests/autenticacao_test.sql

begin;

do $teste$
declare
  v_papel text;
  v_hash_teste constant text := '$argon2id$v=19$m=65536,t=3,p=4$dGVzdGUtc2FsdA$dGVzdGUtaGFzaC1maWN0aWNpbw';
  v_outro_hash constant text := '$argon2id$v=19$m=65536,t=3,p=4$b3V0cm8tc2FsdA$b3V0cm8taGFzaC1maWN0aWNpbw';
begin
  -- Tabelas: somente usuarios e sessoes, com RLS e sem policies
  if (select count(*) from pg_tables where schemaname = 'public'
       and tablename in ('usuarios', 'sessoes')) <> 2 then
    raise exception 'tabelas usuarios e sessoes deveriam existir';
  end if;

  if exists (select 1 from pg_tables where schemaname = 'public'
              and tablename not in ('usuarios', 'sessoes')) then
    raise exception 'há tabelas inesperadas em public';
  end if;

  if exists (select 1 from pg_class
              where oid in ('public.usuarios'::regclass, 'public.sessoes'::regclass)
                and not relrowsecurity) then
    raise exception 'RLS deveria estar habilitado nas duas tabelas';
  end if;

  if exists (select 1 from pg_policies where schemaname = 'public') then
    raise exception 'não deveria haver policies em public';
  end if;

  -- Dados iniciais: exatamente os três usuários, sem senha, e nenhuma sessão
  if (select array_agg(nome order by nome) from public.usuarios)
     is distinct from array['Kassia', 'Leandro', 'Marise'] then
    raise exception 'usuarios deveria conter exatamente Kassia, Leandro e Marise';
  end if;

  if exists (select 1 from public.usuarios
              where senha_hash is not null or senha_definida_em is not null) then
    raise exception 'senhas deveriam começar NULL';
  end if;

  if (select count(*) from public.sessoes) <> 0 then
    raise exception 'sessoes deveria estar vazia';
  end if;

  -- Nome fora da lista é rejeitado
  begin
    insert into public.usuarios (nome) values ('Intruso');
    raise exception 'nome fora da lista foi aceito';
  exception when check_violation then
    null;
  end;

  -- anon e authenticated: nenhum privilégio em tabelas, colunas, sequence ou função
  foreach v_papel in array array['anon', 'authenticated'] loop
    if has_table_privilege(v_papel, 'public.usuarios', 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
       or has_table_privilege(v_papel, 'public.sessoes', 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
       or has_any_column_privilege(v_papel, 'public.usuarios', 'SELECT,INSERT,UPDATE,REFERENCES')
       or has_any_column_privilege(v_papel, 'public.sessoes', 'SELECT,INSERT,UPDATE,REFERENCES')
       or has_sequence_privilege(v_papel, 'public.usuarios_id_seq', 'USAGE,SELECT,UPDATE')
       or has_function_privilege(v_papel, 'public.definir_senha_inicial(text, text)', 'EXECUTE') then
      raise exception 'papel % não deveria ter nenhum privilégio', v_papel;
    end if;
  end loop;

  -- service_role: só o necessário para o backend
  if not has_table_privilege('service_role', 'public.usuarios', 'SELECT')
     or has_table_privilege('service_role', 'public.usuarios', 'INSERT,DELETE,TRUNCATE')
     or has_column_privilege('service_role', 'public.usuarios', 'senha_hash', 'UPDATE')
     or not has_column_privilege('service_role', 'public.usuarios', 'falhas_seguidas', 'UPDATE')
     or not has_column_privilege('service_role', 'public.usuarios', 'bloqueado_ate', 'UPDATE')
     or not has_table_privilege('service_role', 'public.sessoes', 'SELECT,INSERT,UPDATE,DELETE')
     or not has_function_privilege('service_role', 'public.definir_senha_inicial(text, text)', 'EXECUTE') then
    raise exception 'privilégios de service_role diferentes do esperado';
  end if;

  -- Função: rejeita hash que não seja argon2id
  if public.definir_senha_inicial('Leandro', null)
     or public.definir_senha_inicial('Leandro', 'texto-puro')
     or public.definir_senha_inicial('Leandro', '$argon2i$v=19$m=65536,t=3,p=4$eA$eA')
     or public.definir_senha_inicial('Leandro', '$2b$12$bcryptficticio') then
    raise exception 'hash não argon2id foi aceito';
  end if;

  if (select senha_hash from public.usuarios where nome = 'Leandro') is not null then
    raise exception 'senha foi gravada por hash inválido';
  end if;

  -- Função: define uma vez e nunca sobrescreve
  if not public.definir_senha_inicial('Leandro', v_hash_teste) then
    raise exception 'primeira definição deveria gravar';
  end if;

  if public.definir_senha_inicial('Leandro', v_outro_hash) then
    raise exception 'segunda definição não deveria gravar';
  end if;

  if (select senha_hash from public.usuarios where nome = 'Leandro') <> v_hash_teste then
    raise exception 'senha foi sobrescrita';
  end if;

  if public.definir_senha_inicial('Ninguem', v_hash_teste) then
    raise exception 'usuário inexistente não deveria gravar';
  end if;

  if exists (select 1 from public.usuarios where nome <> 'Leandro' and senha_hash is not null) then
    raise exception 'outros usuários foram alterados';
  end if;

  raise notice 'autenticacao_test: todas as verificações passaram';
end;
$teste$;

rollback;

-- Fora da transação: confirma que nada do teste permaneceu
select
  (select count(*) from public.usuarios) as usuarios,
  (select count(*) from public.usuarios where senha_hash is not null) as com_senha,
  (select count(*) from public.sessoes) as sessoes;
