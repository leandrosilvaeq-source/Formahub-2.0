-- Teste transacional da migration de manutenções (20261020120000_manutencoes.sql).
--
-- Seguro com dados reais: tudo roda dentro de BEGIN/ROLLBACK, e o teste NÃO usa as linhas nem as
-- sequences reais. Depois das conferências de estrutura (somente leitura), ele renomeia as duas
-- tabelas tocadas (manutencoes e custos_manutencao_gastos) e cria clones vazios (LIKE ...
-- INCLUDING ALL). As funções, que usam os nomes das tabelas, passam a operar nos clones, com
-- dados fictícios. O ROLLBACK desfaz tudo; as sequences reais nunca são consumidas.
-- Qualquer falha interrompe com RAISE EXCEPTION. Execução local: npm run test:sql

begin;

create function pg_temp.deve_falhar(p_descricao text, p_sql text, p_estado text)
returns void
language plpgsql
as $f$
declare
  v_estado text;
begin
  begin
    execute p_sql;
  exception when others then
    get stacked diagnostics v_estado = returned_sqlstate;
    if v_estado <> p_estado then
      raise exception '%: esperado %, veio %', p_descricao, p_estado, v_estado;
    end if;
    return;
  end;
  raise exception '%: deveria ter sido recusado', p_descricao;
end;
$f$;

-- ---------- Estrutura (somente leitura) ----------
do $estrutura$
declare
  v_papel  text;
  v_funcao text;
begin
  if not exists (select 1 from pg_class where oid = 'public.manutencoes'::regclass
                  and relrowsecurity) then
    raise exception 'manutencoes deveria ter RLS ligado';
  end if;
  foreach v_papel in array array['anon', 'authenticated', 'service_role'] loop
    if has_any_column_privilege(v_papel, 'public.manutencoes',
                                'SELECT, INSERT, UPDATE, REFERENCES')
       or has_table_privilege(v_papel, 'public.manutencoes', 'DELETE, TRUNCATE') then
      raise exception 'manutencoes não deveria ter acesso direto para %', v_papel;
    end if;
  end loop;
  if exists (select 1 from pg_policy where polrelid = 'public.manutencoes'::regclass) then
    raise exception 'manutencoes não deveria ter policies';
  end if;

  foreach v_funcao in array array[
    'listar_manutencoes()',
    'salvar_manutencao(bigint, date, text, integer, boolean, text, numeric, text, bigint)',
    'listar_custos()'] loop
    if not has_function_privilege('service_role', 'public.' || v_funcao, 'EXECUTE') then
      raise exception '% deveria ser executável pelo service_role', v_funcao;
    end if;
    foreach v_papel in array array['anon', 'authenticated', 'public'] loop
      if has_function_privilege(v_papel, 'public.' || v_funcao, 'EXECUTE') then
        raise exception '% não deveria ser executável por %', v_funcao, v_papel;
      end if;
    end loop;
  end loop;

  if exists (select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
              where n.nspname = 'public'
                and p.proname in ('listar_manutencoes', 'salvar_manutencao', 'listar_custos')
                and (not p.prosecdef or p.proconfig is null
                     or not (p.proconfig::text like '%search_path=%'))) then
    raise exception 'funções de manutenção devem ser security definer com search_path fixo';
  end if;

  -- Um só cadastro de manutenção: a função que gravava na tabela antiga foi removida.
  if to_regprocedure('public.registrar_gasto_manutencao(date, text, numeric, bigint)') is not null then
    raise exception 'registrar_gasto_manutencao deveria ter sido removida';
  end if;
  if (select count(*) from pg_proc p join pg_namespace n on n.oid = p.pronamespace
       where n.nspname = 'public' and p.proname = 'salvar_manutencao') <> 1 then
    raise exception 'salvar_manutencao não pode ter sobrecarga';
  end if;

  -- O custo é dinheiro exato (numeric) e o tempo parado, um inteiro de minutos.
  if (select data_type from information_schema.columns
       where table_schema = 'public' and table_name = 'manutencoes' and column_name = 'custo')
       <> 'numeric'
     or (select data_type from information_schema.columns
          where table_schema = 'public' and table_name = 'manutencoes'
            and column_name = 'minutos_parados') <> 'integer' then
    raise exception 'tipos de custo/minutos_parados inesperados';
  end if;
end;
$estrutura$;

-- ---------- Isola os dados reais: clones vazios ----------
alter table public.manutencoes rename to manutencoes_real;
alter table public.custos_manutencao_gastos rename to custos_manutencao_gastos_real;
create table public.manutencoes (like public.manutencoes_real including all);
create table public.custos_manutencao_gastos
  (like public.custos_manutencao_gastos_real including all);

do $teste$
declare
  v_leandro bigint;
  v_kassia  bigint;
  v_id      bigint;
  v_outro   bigint;
  v_zero    bigint;
  v_sem     bigint;
  v_retorno jsonb;
begin
  select id into v_leandro from public.usuarios where nome = 'Leandro';
  select id into v_kassia from public.usuarios where nome = 'Kassia';
  if v_leandro is null or v_kassia is null then
    raise exception 'usuários de teste não encontrados';
  end if;

  -- ---------- Banco vazio ----------
  if public.listar_manutencoes() <> '[]'::jsonb then
    raise exception 'sem manutenções, a lista deve ser vazia';
  end if;
  v_retorno := public.listar_custos();
  if v_retorno -> 'manutencao_gastos' <> '[]'::jsonb
     or v_retorno -> 'manutencao_sem_custo' <> '[]'::jsonb then
    raise exception 'sem manutenções, não há gastos nem custos a definir: %', v_retorno;
  end if;

  -- ---------- Cria: tempo acima de 24 h, várias peças, custo e comentário ----------
  v_retorno := public.salvar_manutencao(null, date '2026-10-10', 'corretiva', 1500, true,
                 '  Bico novo, correia GT2  ', 62.50, '  trocado na sexta  ', v_leandro);
  v_id := (v_retorno ->> 'id')::bigint;
  if v_retorno ->> 'data' <> '2026-10-10' or v_retorno ->> 'tipo' <> 'corretiva'
     or (v_retorno ->> 'minutos_parados')::int <> 1500       -- 1 dia, 1 hora e 0 minuto
     or (v_retorno -> 'troca_pecas') <> 'true'::jsonb
     or v_retorno ->> 'pecas' <> 'Bico novo, correia GT2'
     or v_retorno ->> 'custo' <> '62.50' or jsonb_typeof(v_retorno -> 'custo') <> 'string'
     or v_retorno ->> 'comentarios' <> 'trocado na sexta'
     or v_retorno ->> 'criado_por' <> 'Leandro' or v_retorno ->> 'atualizado_por' <> 'Leandro'
     or (v_retorno -> 'legado') <> 'false'::jsonb then
    raise exception 'manutenção inesperada: %', v_retorno;
  end if;

  -- Minutos preservados mesmo quando a tela resume em dd:hh (1 dia, 2 horas e 30 minutos).
  v_retorno := public.salvar_manutencao(null, date '2026-10-20', 'preventiva', 1590, false,
                 'ignorada', 0, null, v_kassia);
  v_zero := (v_retorno ->> 'id')::bigint;
  if (v_retorno ->> 'minutos_parados')::int <> 1590 or v_retorno ->> 'custo' <> '0.00'
     or (v_retorno -> 'troca_pecas') <> 'false'::jsonb
     or jsonb_typeof(v_retorno -> 'pecas') <> 'null'     -- sem troca, não há peça
     or jsonb_typeof(v_retorno -> 'comentarios') <> 'null' then
    raise exception 'manutenção sem peça / custo zero inesperada: %', v_retorno;
  end if;

  -- Custo "A definir" (nulo) é diferente de zero.
  v_retorno := public.salvar_manutencao(null, date '2026-10-15', 'preventiva', 0, false,
                 null, null, '   ', v_kassia);
  v_sem := (v_retorno ->> 'id')::bigint;
  if jsonb_typeof(v_retorno -> 'custo') <> 'null' or (v_retorno ->> 'minutos_parados')::int <> 0
     or jsonb_typeof(v_retorno -> 'comentarios') <> 'null' then
    raise exception 'manutenção com custo a definir inesperada: %', v_retorno;
  end if;

  -- Mesma data: a mais nova (maior id) vem primeiro.
  v_retorno := public.salvar_manutencao(null, date '2026-10-15', 'corretiva', 30, true,
                 'Ventoinha', 12, null, v_leandro);
  v_outro := (v_retorno ->> 'id')::bigint;
  if (select array_agg((m ->> 'id')::bigint order by ordem)
        from jsonb_array_elements(public.listar_manutencoes()) with ordinality as t(m, ordem))
     is distinct from array[v_zero, v_outro, v_sem, v_id] then
    raise exception 'ordem inesperada: %', public.listar_manutencoes();
  end if;

  -- ---------- Edita: a criação fica, a atualização muda ----------
  v_retorno := public.salvar_manutencao(v_id, date '2026-10-11', 'preventiva', 45, false,
                 'não vale', null, 'revisado', v_kassia);
  if (v_retorno ->> 'id')::bigint <> v_id or v_retorno ->> 'data' <> '2026-10-11'
     or v_retorno ->> 'tipo' <> 'preventiva' or (v_retorno ->> 'minutos_parados')::int <> 45
     or jsonb_typeof(v_retorno -> 'pecas') <> 'null' or jsonb_typeof(v_retorno -> 'custo') <> 'null'
     or v_retorno ->> 'criado_por' <> 'Leandro' or v_retorno ->> 'atualizado_por' <> 'Kassia' then
    raise exception 'edição inesperada: %', v_retorno;
  end if;
  if (select count(*) from public.manutencoes) <> 4 then
    raise exception 'editar não pode criar outra manutenção';
  end if;

  -- ---------- Custos: só o custo definido (zero incluído) vira gasto ----------
  -- v_id agora está sem custo; ficam o zero (v_zero) e a de R$ 12 (v_outro).
  v_retorno := public.listar_custos();
  if (select array_agg(g ->> 'valor' order by ordem)
        from jsonb_array_elements(v_retorno -> 'manutencao_gastos') with ordinality as t(g, ordem))
     is distinct from array['0.00', '12.00']
     or v_retorno #>> '{manutencao_gastos,1,descricao}' <> 'Ventoinha'          -- nome da peça
     or v_retorno #>> '{manutencao_gastos,0,descricao}' <> 'Manutenção preventiva'
     or v_retorno -> 'manutencao_sem_custo' <> '["2026-10-11", "2026-10-15"]'::jsonb then
    raise exception 'listar_custos não reflete as manutenções: %', v_retorno;
  end if;

  -- ---------- Recusas ----------
  perform pg_temp.deve_falhar('tipo inválido',
    'select public.salvar_manutencao(null, date ''2026-10-01'', ''outra'', 0, false, null, null, null, '
      || v_leandro || ')', 'PT422');
  perform pg_temp.deve_falhar('tipo vazio',
    'select public.salvar_manutencao(null, date ''2026-10-01'', null, 0, false, null, null, null, '
      || v_leandro || ')', 'PT422');
  perform pg_temp.deve_falhar('sem data',
    'select public.salvar_manutencao(null, null, ''corretiva'', 0, false, null, null, null, '
      || v_leandro || ')', 'PT422');
  perform pg_temp.deve_falhar('data fora da faixa',
    'select public.salvar_manutencao(null, date ''1999-12-31'', ''corretiva'', 0, false, null, null, null, '
      || v_leandro || ')', 'PT422');
  perform pg_temp.deve_falhar('minutos negativos',
    'select public.salvar_manutencao(null, date ''2026-10-01'', ''corretiva'', -1, false, null, null, null, '
      || v_leandro || ')', 'PT422');
  perform pg_temp.deve_falhar('minutos além de um ano',
    'select public.salvar_manutencao(null, date ''2026-10-01'', ''corretiva'', 525601, false, null, null, null, '
      || v_leandro || ')', 'PT422');
  perform pg_temp.deve_falhar('minutos nulos',
    'select public.salvar_manutencao(null, date ''2026-10-01'', ''corretiva'', null, false, null, null, null, '
      || v_leandro || ')', 'PT422');
  perform pg_temp.deve_falhar('troca de peças sem nome',
    'select public.salvar_manutencao(null, date ''2026-10-01'', ''corretiva'', 0, true, ''   '', null, null, '
      || v_leandro || ')', 'PT422');
  perform pg_temp.deve_falhar('troca de peças nula',
    'select public.salvar_manutencao(null, date ''2026-10-01'', ''corretiva'', 0, null, null, null, null, '
      || v_leandro || ')', 'PT422');
  perform pg_temp.deve_falhar('peça longa demais',
    'select public.salvar_manutencao(null, date ''2026-10-01'', ''corretiva'', 0, true, repeat(''x'', 301), null, null, '
      || v_leandro || ')', 'PT422');
  perform pg_temp.deve_falhar('custo negativo',
    'select public.salvar_manutencao(null, date ''2026-10-01'', ''corretiva'', 0, false, null, -0.01, null, '
      || v_leandro || ')', 'PT422');
  perform pg_temp.deve_falhar('custo com três casas',
    'select public.salvar_manutencao(null, date ''2026-10-01'', ''corretiva'', 0, false, null, 1.234, null, '
      || v_leandro || ')', 'PT422');
  perform pg_temp.deve_falhar('custo NaN',
    'select public.salvar_manutencao(null, date ''2026-10-01'', ''corretiva'', 0, false, null, ''NaN''::numeric, null, '
      || v_leandro || ')', 'PT422');
  perform pg_temp.deve_falhar('custo acima do limite',
    'select public.salvar_manutencao(null, date ''2026-10-01'', ''corretiva'', 0, false, null, 10000000000, null, '
      || v_leandro || ')', 'PT422');
  perform pg_temp.deve_falhar('comentário longo demais',
    'select public.salvar_manutencao(null, date ''2026-10-01'', ''corretiva'', 0, false, null, null, repeat(''x'', 1001), '
      || v_leandro || ')', 'PT422');
  perform pg_temp.deve_falhar('usuário inválido',
    'select public.salvar_manutencao(null, date ''2026-10-01'', ''corretiva'', 0, false, null, null, null, -999)',
    'PT403');
  perform pg_temp.deve_falhar('manutenção inexistente',
    'select public.salvar_manutencao(999999, date ''2026-10-01'', ''corretiva'', 0, false, null, null, null, '
      || v_leandro || ')', 'PT404');
  if (select count(*) from public.manutencoes) <> 4 then
    raise exception 'dados recusados não podem ser gravados';
  end if;

  -- Regras da própria tabela (mesmo fora da função).
  perform pg_temp.deve_falhar('peça sem troca',
    'insert into public.manutencoes (data, tipo, minutos_parados, troca_pecas, pecas, criado_por, atualizado_por)
       values (date ''2026-10-01'', ''corretiva'', 0, false, ''x'', ' || v_leandro || ', ' || v_leandro || ')',
    '23514');
  perform pg_temp.deve_falhar('troca sem peça',
    'insert into public.manutencoes (data, tipo, minutos_parados, troca_pecas, criado_por, atualizado_por)
       values (date ''2026-10-01'', ''corretiva'', 0, true, ' || v_leandro || ', ' || v_leandro || ')',
    '23514');
  perform pg_temp.deve_falhar('sem tipo nem tempo, fora dos gastos antigos',
    'insert into public.manutencoes (data, troca_pecas, criado_por, atualizado_por)
       values (date ''2026-10-01'', false, ' || v_leandro || ', ' || v_leandro || ')',
    '23514');

  -- ---------- Gastos antigos: copiados uma única vez, sem inventar tipo nem tempo ----------
  insert into public.custos_manutencao_gastos (data, descricao, valor, criado_por)
  values (date '2026-09-05', 'Lubrificante', 18.90, v_leandro),
         (date '2026-09-20', 'Correia', 100, v_kassia);
  -- Mesma instrução da migration (cópia), executada duas vezes nos clones.
  for i in 1..2 loop
    insert into public.manutencoes
      (data, troca_pecas, comentarios, custo, gasto_legado_id,
       criado_em, criado_por, atualizado_em, atualizado_por)
    select g.data, false, g.descricao, g.valor, g.id, g.criado_em, g.criado_por, g.criado_em, g.criado_por
      from public.custos_manutencao_gastos as g
    on conflict (gasto_legado_id) do nothing;
  end loop;
  if (select count(*) from public.manutencoes where gasto_legado_id is not null) <> 2 then
    raise exception 'cada gasto antigo deve ser copiado uma única vez';
  end if;
  select m into v_retorno
    from jsonb_array_elements(public.listar_manutencoes()) as m
   where m ->> 'comentarios' = 'Lubrificante';
  if v_retorno ->> 'custo' <> '18.90' or v_retorno ->> 'data' <> '2026-09-05'
     or jsonb_typeof(v_retorno -> 'tipo') <> 'null'
     or jsonb_typeof(v_retorno -> 'minutos_parados') <> 'null'
     or (v_retorno -> 'legado') <> 'true'::jsonb or v_retorno ->> 'criado_por' <> 'Leandro' then
    raise exception 'gasto antigo copiado de forma inesperada: %', v_retorno;
  end if;
  if (select count(*) from jsonb_array_elements(public.listar_custos() -> 'manutencao_gastos')) <> 4 then
    raise exception 'os gastos antigos devem entrar nos custos (2 novos com custo + 2 antigos)';
  end if;

  -- Completar um registro antigo (tipo e tempo) é uma edição normal.
  v_retorno := public.salvar_manutencao(
    (select (m ->> 'id')::bigint from jsonb_array_elements(public.listar_manutencoes()) as m
      where m ->> 'comentarios' = 'Correia'),
    date '2026-09-20', 'preventiva', 20, false, null, 100, 'Correia', v_kassia);
  if v_retorno ->> 'tipo' <> 'preventiva' or (v_retorno ->> 'minutos_parados')::int <> 20
     or (v_retorno -> 'legado') <> 'true'::jsonb then
    raise exception 'completar um gasto antigo falhou: %', v_retorno;
  end if;
end;
$teste$;

rollback;

-- Depois do ROLLBACK as tabelas reais voltam ao lugar e os clones somem.
do $final$
begin
  if to_regclass('public.manutencoes_real') is not null
     or to_regclass('public.custos_manutencao_gastos_real') is not null then
    raise exception 'manutencoes_test: tabelas temporárias sobraram depois do ROLLBACK';
  end if;
end;
$final$;

select 'manutencoes_test: ok' as resultado;
