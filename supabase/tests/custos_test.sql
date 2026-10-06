-- Teste transacional da migration dos custos (20261007120000_custos.sql).
--
-- Seguro com dados reais: tudo roda dentro de BEGIN/ROLLBACK, e o teste NÃO usa as linhas nem as
-- sequences reais. Depois das conferências de estrutura (somente leitura), ele renomeia as três
-- tabelas de custos e cria clones vazios (LIKE ... INCLUDING ALL: mesmas colunas, constraints,
-- índices e coluna calculada; as identidades ganham sequences NOVAS). As funções, que usam os
-- nomes das tabelas, passam a operar nos clones, com dados fictícios. O ROLLBACK desfaz tudo,
-- inclusive as sequences dos clones; as sequences reais nunca são consumidas.
-- Qualquer falha interrompe com RAISE EXCEPTION. Execução:
--   supabase db query --linked -f supabase/tests/custos_test.sql

begin;

-- Executa p_sql e exige que falhe com o SQLSTATE esperado.
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
  v_tabela  text;
  v_funcao  text;
  v_papel   text;
  v_coluna  record;
begin
  foreach v_tabela in array array['custos_filamentos', 'custos_parametros', 'custos_itens'] loop
    if not exists (select 1 from pg_class where oid = ('public.' || v_tabela)::regclass
                    and relrowsecurity) then
      raise exception '% deveria ter RLS ligado', v_tabela;
    end if;
    foreach v_papel in array array['anon', 'authenticated', 'service_role'] loop
      if has_any_column_privilege(v_papel, 'public.' || v_tabela,
                                  'SELECT, INSERT, UPDATE, REFERENCES')
         or has_table_privilege(v_papel, 'public.' || v_tabela, 'DELETE, TRUNCATE') then
        raise exception '% não deveria ter acesso direto para %', v_tabela, v_papel;
      end if;
    end loop;
    if exists (select 1 from pg_policy where polrelid = ('public.' || v_tabela)::regclass) then
      raise exception '% não deveria ter policies', v_tabela;
    end if;
  end loop;

  foreach v_papel in array array['anon', 'authenticated', 'service_role'] loop
    foreach v_tabela in array array['custos_filamentos_id_seq', 'custos_itens_id_seq'] loop
      if has_sequence_privilege(v_papel, 'public.' || v_tabela, 'USAGE, SELECT, UPDATE') then
        raise exception 'a sequence % não deveria ter acesso para %', v_tabela, v_papel;
      end if;
    end loop;
  end loop;

  -- Valores sempre numeric (nada de float) e sem coluna de energia por hora.
  if exists (select 1 from information_schema.columns
              where table_schema = 'public'
                and table_name in ('custos_filamentos', 'custos_parametros', 'custos_itens')
                and data_type in ('real', 'double precision', 'money')) then
    raise exception 'os custos não deveriam usar float nem money';
  end if;
  for v_coluna in
    select table_name, column_name, data_type, is_generated, is_nullable
      from information_schema.columns
     where table_schema = 'public' and table_name like 'custos\_%'
  loop
    if (v_coluna.column_name in ('valor_kg', 'valor', 'valor_compra', 'custo_unitario')
        and v_coluna.data_type <> 'numeric')
       or (v_coluna.column_name = 'quantidade' and v_coluna.data_type <> 'integer') then
      raise exception '%.% com tipo inesperado %', v_coluna.table_name, v_coluna.column_name,
        v_coluna.data_type;
    end if;
    if v_coluna.column_name ~ 'energia_hora|custo_hora|consumo' then
      raise exception 'o custo da energia por hora não deve ser guardado (%)', v_coluna.column_name;
    end if;
  end loop;
  if (select is_generated from information_schema.columns
       where table_schema = 'public' and table_name = 'custos_itens'
         and column_name = 'custo_unitario') is distinct from 'ALWAYS' then
    raise exception 'custo_unitario deveria ser uma coluna calculada';
  end if;

  -- Auditoria ligada a usuarios com ON DELETE RESTRICT.
  if (select count(*) from pg_constraint c
       where c.contype = 'f' and c.confrelid = 'public.usuarios'::regclass
         and c.confdeltype = 'r'
         and c.conrelid in ('public.custos_filamentos'::regclass, 'public.custos_parametros'::regclass,
                            'public.custos_itens'::regclass)) <> 3 then
    raise exception 'atualizado_por deveria referenciar usuarios com ON DELETE RESTRICT';
  end if;

  -- Parâmetros: só a manutenção pode ficar sem valor; o parâmetro antigo não existe mais.
  if (select is_nullable from information_schema.columns
       where table_schema = 'public' and table_name = 'custos_parametros'
         and column_name = 'valor') <> 'YES' then
    raise exception 'custos_parametros.valor deveria aceitar NULL (manutenção "A definir")';
  end if;
  if exists (select 1 from public.custos_parametros where chave = 'manutencao_depreciacao_hora')
     or exists (select 1 from pg_constraint
                 where conrelid = 'public.custos_parametros'::regclass
                   and pg_get_constraintdef(oid) like '%manutencao_depreciacao_hora%') then
    raise exception 'o parâmetro antigo manutencao_depreciacao_hora não deveria mais existir';
  end if;
  if exists (select 1 from public.custos_parametros where valor is null and chave <> 'manutencao_hora') then
    raise exception 'só a manutenção pode estar sem valor';
  end if;
  if (select count(*) from pg_constraint
       where conrelid = 'public.custos_parametros'::regclass
         and conname in ('custos_parametros_valor_obrigatorio', 'custos_parametros_faixas',
                         'custos_parametros_chave_check')) <> 3 then
    raise exception 'faltam constraints dos parâmetros';
  end if;

  -- Funções: uma versão de cada, protegidas, só o service_role executa.
  if (select count(*) from pg_proc where pronamespace = 'public'::regnamespace
        and proname in ('listar_custos', 'salvar_custo_filamento', 'salvar_custos_parametros',
                        'salvar_custo_item')) <> 4 then
    raise exception 'cada função de custos deveria ter uma única versão';
  end if;
  foreach v_funcao in array array[
    'public.listar_custos()',
    'public.salvar_custo_filamento(bigint, numeric, bigint)',
    'public.salvar_custos_parametros(jsonb, bigint)',
    'public.salvar_custo_item(bigint, text, text, numeric, integer, bigint)'
  ] loop
    if to_regprocedure(v_funcao) is null then
      raise exception '% deveria existir', v_funcao;
    end if;
    if exists (select 1 from pg_proc where oid = v_funcao::regprocedure
                and (not prosecdef or not ('search_path=""' = any (proconfig)))) then
      raise exception '% deveria ser security definer com search_path vazio', v_funcao;
    end if;
    foreach v_papel in array array['anon', 'authenticated'] loop
      if has_function_privilege(v_papel, v_funcao, 'EXECUTE') then
        raise exception '% não deveria ser executável por %', v_funcao, v_papel;
      end if;
    end loop;
    if exists (select 1 from pg_proc p, aclexplode(p.proacl) a
                where p.oid = v_funcao::regprocedure and a.grantee = 0) then
      raise exception '% não deveria ser executável por PUBLIC', v_funcao;
    end if;
    if not has_function_privilege('service_role', v_funcao, 'EXECUTE') then
      raise exception '% deveria ser executável por service_role', v_funcao;
    end if;
  end loop;
end;
$estrutura$;

-- ---------- Isola os dados reais: clones vazios com sequences novas ----------
alter table public.custos_filamentos rename to custos_filamentos_real;
alter table public.custos_parametros rename to custos_parametros_real;
alter table public.custos_itens rename to custos_itens_real;
create table public.custos_filamentos (like public.custos_filamentos_real including all);
create table public.custos_parametros (like public.custos_parametros_real including all);
create table public.custos_itens (like public.custos_itens_real including all);

-- Mesmas instruções de valores iniciais da migration (cópia), executadas nos clones.
create function pg_temp.valores_iniciais() returns void language plpgsql as $f$
begin
  insert into public.custos_filamentos (nome, abrange, valor_kg) values
    ('PLA', 'Cores sólidas, Velvet, Silk e pastel', 100),
    ('PLA Multicolor', 'Duocolor e tricolor', 125),
    ('PETG', null, 85)
  on conflict (lower(nome)) do nothing;
  insert into public.custos_parametros (chave, valor) values
    ('energia_tarifa_kwh', 1),
    ('energia_consumo_w', 120),
    ('perda_percentual', 5),
    ('mdo_hora', 0.5),
    ('manutencao_hora', null),
    ('depreciacao_valor_aquisicao', 6000),
    ('depreciacao_vida_util_anos', 5),
    ('depreciacao_valor_residual', 0),
    ('depreciacao_horas_dia', 12),
    ('depreciacao_dias_mes', 30)
  on conflict (chave) do nothing;
end;
$f$;

create function pg_temp.parametros() returns jsonb language sql as $f$
  select jsonb_object_agg(chave, valor::text) from public.custos_parametros;
$f$;

do $teste$
declare
  v_leandro bigint;
  v_kassia  bigint;
  v_retorno jsonb;
  v_antes   jsonb;
  v_item    bigint;
  v_item2   bigint;
  v_linha   record;
  v_texto   text;
begin
  select id into v_leandro from public.usuarios where nome = 'Leandro';
  select id into v_kassia from public.usuarios where nome = 'Kassia';
  if v_leandro is null or v_kassia is null then
    raise exception 'usuários de teste não encontrados';
  end if;

  -- ---------- Valores iniciais ----------
  if (select count(*) from public.custos_filamentos) <> 0
     or (select count(*) from public.custos_itens) <> 0 then
    raise exception 'o clone deveria começar vazio';
  end if;
  perform pg_temp.valores_iniciais();
  v_retorno := public.listar_custos();
  if v_retorno -> 'itens' <> '[]'::jsonb then
    raise exception 'acessórios e embalagens deveriam começar vazios (%)', v_retorno -> 'itens';
  end if;
  if (select array_agg(f ->> 'nome' order by (f ->> 'id')::bigint)
        from jsonb_array_elements(v_retorno -> 'filamentos') f)
       is distinct from array['PLA', 'PLA Multicolor', 'PETG']
     or (select array_agg((f ->> 'valor_kg')::numeric order by (f ->> 'id')::bigint)
           from jsonb_array_elements(v_retorno -> 'filamentos') f)
       is distinct from array[100, 125, 85]::numeric[] then
    raise exception 'filamentos iniciais inesperados: %', v_retorno -> 'filamentos';
  end if;
  if (v_retorno #>> '{parametros,energia_tarifa_kwh}')::numeric <> 1
     or (v_retorno #>> '{parametros,energia_consumo_w}')::numeric <> 120
     or (v_retorno #>> '{parametros,perda_percentual}')::numeric <> 5
     or (v_retorno #>> '{parametros,mdo_hora}')::numeric <> 0.5
     or (v_retorno #> '{parametros,manutencao_hora}') is distinct from 'null'::jsonb  -- A definir
     or (v_retorno #>> '{parametros,depreciacao_valor_aquisicao}')::numeric <> 6000
     or (v_retorno #>> '{parametros,depreciacao_vida_util_anos}')::numeric <> 5
     or (v_retorno #>> '{parametros,depreciacao_valor_residual}')::numeric <> 0
     or (v_retorno #>> '{parametros,depreciacao_horas_dia}')::numeric <> 12
     or (v_retorno #>> '{parametros,depreciacao_dias_mes}')::numeric <> 30
     or v_retorno #> '{parametros,manutencao_depreciacao_hora}' is not null then
    raise exception 'parâmetros iniciais inesperados: %', v_retorno -> 'parametros';
  end if;
  -- Numéricos saem como texto (nada de float no JSON).
  if jsonb_typeof(v_retorno #> '{parametros,mdo_hora}') <> 'string'
     or jsonb_typeof(v_retorno #> '{filamentos,0,valor_kg}') <> 'string' then
    raise exception 'os valores deveriam sair como texto';
  end if;

  -- ---------- Filamento ----------
  v_retorno := public.salvar_custo_filamento(
    (select id from public.custos_filamentos where nome = 'PLA'), 99.9999, v_kassia);
  if v_retorno ->> 'valor_kg' is distinct from '99.9999' or v_retorno ->> 'nome' <> 'PLA' then
    raise exception 'salvar_custo_filamento devolveu %', v_retorno;
  end if;
  if not exists (select 1 from public.custos_filamentos
                  where nome = 'PLA' and valor_kg = 99.9999 and atualizado_por = v_kassia
                    and abrange = 'Cores sólidas, Velvet, Silk e pastel')
     or (select valor_kg from public.custos_filamentos where nome = 'PETG') <> 85
     or (select atualizado_por from public.custos_filamentos where nome = 'PETG') is not null then
    raise exception 'só o PLA deveria ter mudado (valor e responsável)';
  end if;
  perform public.salvar_custo_filamento(
    (select id from public.custos_filamentos where nome = 'PETG'), 0, v_leandro);  -- zero vale
  perform pg_temp.deve_falhar('filamento: valor negativo',
    'select public.salvar_custo_filamento((select min(id) from public.custos_filamentos), -1, '
    || v_leandro || ')', 'PT422');
  perform pg_temp.deve_falhar('filamento: NaN',
    'select public.salvar_custo_filamento((select min(id) from public.custos_filamentos), '
    || '''NaN''::numeric, ' || v_leandro || ')', 'PT422');
  perform pg_temp.deve_falhar('filamento: nulo',
    'select public.salvar_custo_filamento((select min(id) from public.custos_filamentos), null, '
    || v_leandro || ')', 'PT422');
  perform pg_temp.deve_falhar('filamento: acima do limite',
    'select public.salvar_custo_filamento((select min(id) from public.custos_filamentos), '
    || '1000000000, ' || v_leandro || ')', 'PT422');
  perform pg_temp.deve_falhar('filamento: inexistente',
    'select public.salvar_custo_filamento(-999, 1, ' || v_leandro || ')', 'PT404');
  perform pg_temp.deve_falhar('filamento: usuário inexistente',
    'select public.salvar_custo_filamento((select min(id) from public.custos_filamentos), 1, -999)',
    'PT403');
  perform pg_temp.deve_falhar('filamento: usuário nulo',
    'select public.salvar_custo_filamento((select min(id) from public.custos_filamentos), 1, null)',
    'PT403');
  if (select valor_kg from public.custos_filamentos where nome = 'PLA') <> 99.9999 then
    raise exception 'tentativas recusadas não deveriam mudar o filamento';
  end if;

  -- ---------- Valores únicos ----------
  v_antes := pg_temp.parametros();
  v_retorno := public.salvar_custos_parametros(
    '{"energia_tarifa_kwh": "0.95", "energia_consumo_w": "150"}', v_leandro);
  if (v_retorno ->> 'energia_tarifa_kwh')::numeric <> 0.95
     or (v_retorno ->> 'energia_consumo_w')::numeric <> 150
     or v_retorno ->> 'mdo_hora' is distinct from v_antes ->> 'mdo_hora' then
    raise exception 'salvar_custos_parametros devolveu %', v_retorno;
  end if;
  if (select count(*) from public.custos_parametros where atualizado_por = v_leandro) <> 2 then
    raise exception 'só as duas chaves enviadas deveriam ter responsável';
  end if;
  -- Energia por hora NÃO é guardada: 0,95 x 150 / 1000 é calculado fora do banco.

  v_antes := pg_temp.parametros();
  perform public.salvar_custos_parametros('{"perda_percentual": "100"}', v_leandro);  -- limite
  perform public.salvar_custos_parametros('{"mdo_hora": "0", "manutencao_hora": "1.5"}',
    v_kassia);
  v_antes := pg_temp.parametros();
  perform pg_temp.deve_falhar('perdas acima de 100',
    'select public.salvar_custos_parametros(''{"perda_percentual": "100.01"}'', ' || v_leandro || ')',
    'PT422');
  perform pg_temp.deve_falhar('tudo ou nada: um valor inválido desfaz os válidos',
    'select public.salvar_custos_parametros(''{"mdo_hora": "9", "perda_percentual": "101"}'', '
    || v_leandro || ')', 'PT422');
  perform pg_temp.deve_falhar('chave desconhecida',
    'select public.salvar_custos_parametros(''{"mdo_hora": "9", "outra": "1"}'', '
    || v_leandro || ')', 'PT404');
  perform pg_temp.deve_falhar('valor não numérico',
    'select public.salvar_custos_parametros(''{"mdo_hora": "abc"}'', ' || v_leandro || ')', 'PT422');
  perform pg_temp.deve_falhar('valor negativo',
    'select public.salvar_custos_parametros(''{"mdo_hora": "-1"}'', ' || v_leandro || ')', 'PT422');
  perform pg_temp.deve_falhar('valor NaN',
    'select public.salvar_custos_parametros(''{"mdo_hora": "NaN"}'', ' || v_leandro || ')', 'PT422');
  perform pg_temp.deve_falhar('valor nulo no JSON',
    'select public.salvar_custos_parametros(''{"mdo_hora": null}'', ' || v_leandro || ')', 'PT422');
  perform pg_temp.deve_falhar('nenhum valor',
    'select public.salvar_custos_parametros(''{}'', ' || v_leandro || ')', 'PT422');
  perform pg_temp.deve_falhar('JSON que não é objeto',
    'select public.salvar_custos_parametros(''[1]'', ' || v_leandro || ')', 'PT422');
  perform pg_temp.deve_falhar('parâmetros: usuário inexistente',
    'select public.salvar_custos_parametros(''{"mdo_hora": "1"}'', -999)', 'PT403');
  if pg_temp.parametros() is distinct from v_antes then
    raise exception 'tentativas recusadas não deveriam mudar os parâmetros';
  end if;

  -- Os valores iniciais NÃO sobrescrevem edições manuais (mesma instrução da migration).
  perform pg_temp.valores_iniciais();
  if pg_temp.parametros() is distinct from v_antes
     or (select valor_kg from public.custos_filamentos where nome = 'PLA') <> 99.9999
     or (select count(*) from public.custos_filamentos) <> 3 then
    raise exception 'repetir os valores iniciais não pode desfazer edições manuais';
  end if;

  -- ---------- Manutenção: ausência de valor (NULL) e edição manual ----------
  -- Começa sem valor (NULL, "A definir") e NÃO é zero.
  perform pg_temp.valores_iniciais();
  update public.custos_parametros set valor = null, atualizado_por = null
   where chave = 'manutencao_hora';  -- volta ao estado inicial (só no clone)
  if (public.listar_custos() #> '{parametros,manutencao_hora}') is distinct from 'null'::jsonb then
    raise exception 'manutenção sem valor deveria sair como null no JSON (%)',
      public.listar_custos() #> '{parametros,manutencao_hora}';
  end if;

  v_retorno := public.salvar_custos_parametros('{"manutencao_hora": "0.35"}', v_kassia);
  if (v_retorno ->> 'manutencao_hora')::numeric <> 0.35
     or (select atualizado_por from public.custos_parametros where chave = 'manutencao_hora')
        is distinct from v_kassia then
    raise exception 'edição da manutenção devolveu %', v_retorno;
  end if;
  perform pg_temp.valores_iniciais();  -- reiniciar não volta a manutenção para "A definir"
  if (select valor from public.custos_parametros where chave = 'manutencao_hora') <> 0.35 then
    raise exception 'os valores iniciais não podem apagar a manutenção digitada';
  end if;
  -- Em branco (vazio, só espaços ou null no JSON) volta a "A definir".
  foreach v_texto in array array['{"manutencao_hora": ""}', '{"manutencao_hora": "   "}',
                                 '{"manutencao_hora": null}'] loop
    perform public.salvar_custos_parametros('{"manutencao_hora": "1"}', v_leandro);
    v_retorno := public.salvar_custos_parametros(v_texto::jsonb, v_leandro);
    if (select valor from public.custos_parametros where chave = 'manutencao_hora') is not null
       or v_retorno -> 'manutencao_hora' is distinct from 'null'::jsonb then
      raise exception 'em branco (%) deveria guardar NULL (%)', v_texto, v_retorno;
    end if;
  end loop;
  -- Zero digitado é um valor (não é "A definir").
  perform public.salvar_custos_parametros('{"manutencao_hora": "0"}', v_leandro);
  if (select valor from public.custos_parametros where chave = 'manutencao_hora') is distinct from 0 then
    raise exception 'zero deveria ser guardado como 0, não como NULL';
  end if;
  perform public.salvar_custos_parametros('{"manutencao_hora": ""}', v_leandro);
  perform pg_temp.deve_falhar('manutenção não numérica',
    'select public.salvar_custos_parametros(''{"manutencao_hora": "abc"}'', ' || v_leandro || ')',
    'PT422');
  perform pg_temp.deve_falhar('manutenção negativa',
    'select public.salvar_custos_parametros(''{"manutencao_hora": "-1"}'', ' || v_leandro || ')',
    'PT422');
  perform pg_temp.deve_falhar('manutenção NaN',
    'select public.salvar_custos_parametros(''{"manutencao_hora": "NaN"}'', ' || v_leandro || ')',
    'PT422');
  -- Só a manutenção aceita ficar em branco.
  perform pg_temp.deve_falhar('perdas em branco',
    'select public.salvar_custos_parametros(''{"perda_percentual": ""}'', ' || v_leandro || ')',
    'PT422');
  perform pg_temp.deve_falhar('MDO em branco',
    'select public.salvar_custos_parametros(''{"mdo_hora": "  "}'', ' || v_leandro || ')', 'PT422');
  perform pg_temp.deve_falhar('aquisição em branco',
    'select public.salvar_custos_parametros(''{"depreciacao_valor_aquisicao": ""}'', '
    || v_leandro || ')', 'PT422');
  if (select valor from public.custos_parametros where chave = 'manutencao_hora') is not null then
    raise exception 'tentativas recusadas não deveriam mudar a manutenção';
  end if;

  -- ---------- Depreciação: critérios, faixas e residual <= aquisição ----------
  -- O valor por hora NÃO é guardado: nenhuma chave dele existe (só os 5 critérios).
  if exists (select 1 from public.custos_parametros where chave ~ 'por_hora|depreciacao_hora$') then
    raise exception 'o valor por hora da depreciação não deveria ser guardado';
  end if;
  if (select count(*) from public.custos_parametros where chave like 'depreciacao\_%') <> 5 then
    raise exception 'deveria haver exatamente 5 critérios de depreciação';
  end if;
  v_retorno := public.salvar_custos_parametros(
    '{"depreciacao_valor_aquisicao": "7200", "depreciacao_vida_util_anos": "4",
      "depreciacao_valor_residual": "1200", "depreciacao_horas_dia": "10",
      "depreciacao_dias_mes": "25"}', v_kassia);
  if (v_retorno ->> 'depreciacao_valor_aquisicao')::numeric <> 7200
     or (v_retorno ->> 'depreciacao_vida_util_anos')::numeric <> 4
     or (v_retorno ->> 'depreciacao_valor_residual')::numeric <> 1200
     or (v_retorno ->> 'depreciacao_horas_dia')::numeric <> 10
     or (v_retorno ->> 'depreciacao_dias_mes')::numeric <> 25 then
    raise exception 'critérios da depreciação devolveram %', v_retorno;
  end if;
  if (select count(*) from public.custos_parametros
       where chave like 'depreciacao\_%' and atualizado_por = v_kassia) <> 5 then
    raise exception 'os cinco critérios deveriam ter o responsável';
  end if;
  v_antes := pg_temp.parametros();
  perform pg_temp.valores_iniciais();  -- reiniciar não volta os critérios aos iniciais
  if pg_temp.parametros() is distinct from v_antes then
    raise exception 'os valores iniciais não podem sobrescrever os critérios editados';
  end if;

  -- Limites aceitos.
  perform public.salvar_custos_parametros('{"depreciacao_horas_dia": "24"}', v_leandro);
  perform public.salvar_custos_parametros('{"depreciacao_horas_dia": "0.5"}', v_leandro);
  perform public.salvar_custos_parametros('{"depreciacao_dias_mes": "1"}', v_leandro);
  perform public.salvar_custos_parametros('{"depreciacao_dias_mes": "31"}', v_leandro);
  perform public.salvar_custos_parametros('{"depreciacao_vida_util_anos": "0.5"}', v_leandro);
  perform public.salvar_custos_parametros(  -- residual igual à aquisição
    '{"depreciacao_valor_aquisicao": "500", "depreciacao_valor_residual": "500"}', v_leandro);
  perform public.salvar_custos_parametros(  -- aquisição e residual juntos, em qualquer ordem
    '{"depreciacao_valor_aquisicao": "100", "depreciacao_valor_residual": "0"}', v_leandro);
  perform public.salvar_custos_parametros('{"depreciacao_valor_residual": "100"}', v_leandro);
  perform public.salvar_custos_parametros('{"depreciacao_valor_residual": "0"}', v_leandro);
  v_antes := pg_temp.parametros();

  foreach v_texto in array array[
    '{"depreciacao_vida_util_anos": "0"}',
    '{"depreciacao_vida_util_anos": "-1"}',
    '{"depreciacao_dias_mes": "0"}',
    '{"depreciacao_dias_mes": "32"}',
    '{"depreciacao_dias_mes": "1.5"}',
    '{"depreciacao_horas_dia": "0"}',
    '{"depreciacao_horas_dia": "24.01"}',
    '{"depreciacao_horas_dia": "-2"}',
    '{"depreciacao_valor_aquisicao": "-1"}',
    '{"depreciacao_valor_residual": "-1"}',
    '{"depreciacao_valor_residual": "100.01"}',                 -- acima da aquisição guardada (100)
    '{"depreciacao_valor_aquisicao": "-0.01"}',
    '{"depreciacao_valor_aquisicao": "1000000000"}'
  ] loop
    perform pg_temp.deve_falhar('critério inválido ' || v_texto,
      'select public.salvar_custos_parametros(''' || v_texto || '''::jsonb, ' || v_leandro || ')',
      'PT422');
  end loop;
  -- Residual maior que a aquisição, com os dois no mesmo envio, e aquisição abaixo do residual.
  perform public.salvar_custos_parametros('{"depreciacao_valor_residual": "50"}', v_leandro);
  perform pg_temp.deve_falhar('aquisição abaixo do residual guardado',
    'select public.salvar_custos_parametros(''{"depreciacao_valor_aquisicao": "49"}'', '
    || v_leandro || ')', 'PT422');
  perform pg_temp.deve_falhar('residual maior que a aquisição no mesmo envio',
    'select public.salvar_custos_parametros(''{"depreciacao_valor_aquisicao": "100", '
    || '"depreciacao_valor_residual": "200"}'', ' || v_leandro || ')', 'PT422');
  perform pg_temp.deve_falhar('tudo ou nada: um critério ruim desfaz o válido',
    'select public.salvar_custos_parametros(''{"depreciacao_horas_dia": "8", '
    || '"depreciacao_dias_mes": "40"}'', ' || v_leandro || ')', 'PT422');
  perform public.salvar_custos_parametros('{"depreciacao_valor_residual": "0"}', v_leandro);
  v_antes := pg_temp.parametros();
  if v_antes ->> 'depreciacao_valor_aquisicao' is distinct from '100'
     or (v_antes ->> 'depreciacao_horas_dia')::numeric <> 0.5 then
    raise exception 'tentativas recusadas não deveriam mudar os critérios (%)', v_antes;
  end if;
  -- Volta aos critérios iniciais (no clone) para seguir o teste.
  perform public.salvar_custos_parametros(
    '{"depreciacao_valor_aquisicao": "6000", "depreciacao_vida_util_anos": "5",
      "depreciacao_valor_residual": "0", "depreciacao_horas_dia": "12",
      "depreciacao_dias_mes": "30"}', v_leandro);

  -- ---------- Acessórios e embalagens ----------
  v_retorno := public.salvar_custo_item(null, 'acessorio', '  Chaveiro  ', 25.00, 50, v_kassia);
  v_item := (v_retorno ->> 'id')::bigint;
  if v_retorno ->> 'nome' <> 'Chaveiro' or v_retorno ->> 'tipo' <> 'acessorio'
     or (v_retorno ->> 'custo_unitario')::numeric <> 0.5 or (v_retorno ->> 'quantidade')::int <> 50
     or jsonb_typeof(v_retorno -> 'custo_unitario') <> 'string' then
    raise exception 'cadastro de acessório devolveu %', v_retorno;
  end if;
  if (select custo_unitario from public.custos_itens where id = v_item)
       is distinct from 25.00::numeric / 50 then
    raise exception 'custo unitário deveria ser valor / quantidade';
  end if;

  -- Sem arredondamento prematuro: 10 / 3 guarda todas as casas do numeric.
  v_retorno := public.salvar_custo_item(null, 'embalagem', 'Caixa P', 10, 3, v_leandro);
  v_item2 := (v_retorno ->> 'id')::bigint;
  if (v_retorno ->> 'custo_unitario')::numeric <> 10::numeric / 3
     or (v_retorno ->> 'custo_unitario')::numeric = round(10::numeric / 3, 2)
     or length(split_part(v_retorno ->> 'custo_unitario', '.', 2)) < 10 then
    raise exception 'o custo unitário não deveria ser arredondado (%)', v_retorno;
  end if;

  -- Edição: valor e quantidade; recalcula e não duplica.
  v_retorno := public.salvar_custo_item(v_item, 'acessorio', 'Chaveiro', 30.00, 40, v_leandro);
  if (v_retorno ->> 'custo_unitario')::numeric <> 0.75 or (v_retorno ->> 'id')::bigint <> v_item
     or (select count(*) from public.custos_itens) <> 2 then
    raise exception 'edição do item devolveu %', v_retorno;
  end if;
  if not exists (select 1 from public.custos_itens
                  where id = v_item and valor_compra = 30.00 and quantidade = 40
                    and atualizado_por = v_leandro and custo_unitario = 0.75) then
    raise exception 'edição deveria gravar valor, quantidade, custo unitário e responsável';
  end if;
  perform public.salvar_custo_item(v_item, 'acessorio', 'Chaveiro grande', 0, 40, v_leandro);  -- zero vale
  if (select custo_unitario from public.custos_itens where id = v_item) <> 0 then
    raise exception 'valor zero deveria dar custo unitário zero';
  end if;

  -- O mesmo nome vale em outro tipo; não vale repetido no mesmo tipo (sem diferenciar caixa).
  perform public.salvar_custo_item(null, 'acessorio', 'Caixa P', 5, 5, v_leandro);
  perform pg_temp.deve_falhar('nome repetido no mesmo tipo',
    'select public.salvar_custo_item(null, ''embalagem'', ''CAIXA p'', 1, 1, ' || v_leandro || ')',
    'PT409');
  perform pg_temp.deve_falhar('editar para o nome de outro item do mesmo tipo',
    'select public.salvar_custo_item(' || v_item || ', ''acessorio'', ''caixa p'', 1, 1, '
    || v_leandro || ')', 'PT409');
  -- Salvar de novo com o próprio nome não é conflito.
  perform public.salvar_custo_item(v_item2, 'embalagem', 'Caixa P', 12, 4, v_leandro);

  -- ---------- Valores inválidos e registros inexistentes ----------
  perform pg_temp.deve_falhar('quantidade zero',
    'select public.salvar_custo_item(null, ''acessorio'', ''X'', 1, 0, ' || v_leandro || ')', 'PT422');
  perform pg_temp.deve_falhar('quantidade negativa',
    'select public.salvar_custo_item(null, ''acessorio'', ''X'', 1, -2, ' || v_leandro || ')', 'PT422');
  perform pg_temp.deve_falhar('quantidade nula',
    'select public.salvar_custo_item(null, ''acessorio'', ''X'', 1, null, ' || v_leandro || ')', 'PT422');
  perform pg_temp.deve_falhar('valor de compra negativo',
    'select public.salvar_custo_item(null, ''acessorio'', ''X'', -1, 1, ' || v_leandro || ')', 'PT422');
  perform pg_temp.deve_falhar('valor de compra NaN',
    'select public.salvar_custo_item(null, ''acessorio'', ''X'', ''NaN''::numeric, 1, '
    || v_leandro || ')', 'PT422');
  perform pg_temp.deve_falhar('valor de compra nulo',
    'select public.salvar_custo_item(null, ''acessorio'', ''X'', null, 1, ' || v_leandro || ')', 'PT422');
  perform pg_temp.deve_falhar('valor de compra acima do limite',
    'select public.salvar_custo_item(null, ''acessorio'', ''X'', 1000000000, 1, '
    || v_leandro || ')', 'PT422');
  perform pg_temp.deve_falhar('nome vazio',
    'select public.salvar_custo_item(null, ''acessorio'', '''', 1, 1, ' || v_leandro || ')', 'PT422');
  perform pg_temp.deve_falhar('nome só com espaços',
    'select public.salvar_custo_item(null, ''acessorio'', E''  \t '', 1, 1, ' || v_leandro || ')',
    'PT422');
  perform pg_temp.deve_falhar('nome nulo',
    'select public.salvar_custo_item(null, ''acessorio'', null, 1, 1, ' || v_leandro || ')', 'PT422');
  perform pg_temp.deve_falhar('nome com mais de 120 caracteres',
    'select public.salvar_custo_item(null, ''acessorio'', ''' || repeat('x', 121) || ''', 1, 1, '
    || v_leandro || ')', 'PT422');
  perform public.salvar_custo_item(null, 'acessorio', repeat('y', 120), 1, 1, v_leandro);  -- 120 vale
  perform pg_temp.deve_falhar('tipo inválido',
    'select public.salvar_custo_item(null, ''produto'', ''X'', 1, 1, ' || v_leandro || ')', 'PT422');
  perform pg_temp.deve_falhar('tipo nulo',
    'select public.salvar_custo_item(null, null, ''X'', 1, 1, ' || v_leandro || ')', 'PT422');
  perform pg_temp.deve_falhar('item inexistente',
    'select public.salvar_custo_item(-999, ''acessorio'', ''X'', 1, 1, ' || v_leandro || ')', 'PT404');
  perform pg_temp.deve_falhar('item de outro tipo',
    'select public.salvar_custo_item(' || v_item || ', ''embalagem'', ''X'', 1, 1, '
    || v_leandro || ')', 'PT404');
  perform pg_temp.deve_falhar('item: usuário inexistente',
    'select public.salvar_custo_item(null, ''acessorio'', ''X'', 1, 1, -999)', 'PT403');
  perform pg_temp.deve_falhar('item: usuário nulo',
    'select public.salvar_custo_item(null, ''acessorio'', ''X'', 1, 1, null)', 'PT403');
  if (select count(*) from public.custos_itens) <> 4 then
    raise exception 'tentativas recusadas não deveriam criar itens';
  end if;

  -- ---------- Leitura: itens por nome, valores como texto ----------
  v_retorno := public.listar_custos();
  if (select array_agg(i ->> 'nome' order by n)
        from jsonb_array_elements(v_retorno -> 'itens') with ordinality as t(i, n))
       is distinct from array['Caixa P', 'Caixa P', 'Chaveiro grande', repeat('y', 120)]
     or jsonb_typeof(v_retorno #> '{itens,0,valor_compra}') <> 'string'
     or jsonb_typeof(v_retorno #> '{itens,0,custo_unitario}') <> 'string' then
    raise exception 'listar_custos devolveu itens fora de ordem ou com tipos errados: %',
      v_retorno -> 'itens';
  end if;

  -- ---------- Regras direto nas tabelas (clones têm as mesmas constraints) ----------
  -- Quantidade zero: a coluna calculada (valor / quantidade) falha antes da CHECK, com divisão
  -- por zero; de um jeito ou de outro, o registro é recusado.
  perform pg_temp.deve_falhar('quantidade zero direto',
    'insert into public.custos_itens (tipo, nome, valor_compra, quantidade, atualizado_por) '
    || 'values (''acessorio'', ''Z1'', 1, 0, ' || v_leandro || ')', '22012');
  perform pg_temp.deve_falhar('quantidade negativa direto',
    'insert into public.custos_itens (tipo, nome, valor_compra, quantidade, atualizado_por) '
    || 'values (''acessorio'', ''Z1'', 1, -1, ' || v_leandro || ')', '23514');
  perform pg_temp.deve_falhar('valor negativo direto',
    'insert into public.custos_itens (tipo, nome, valor_compra, quantidade, atualizado_por) '
    || 'values (''acessorio'', ''Z2'', -1, 1, ' || v_leandro || ')', '23514');
  perform pg_temp.deve_falhar('tipo inválido direto',
    'insert into public.custos_itens (tipo, nome, valor_compra, quantidade, atualizado_por) '
    || 'values (''outro'', ''Z3'', 1, 1, ' || v_leandro || ')', '23514');
  perform pg_temp.deve_falhar('nome com espaços nas pontas direto',
    'insert into public.custos_itens (tipo, nome, valor_compra, quantidade, atualizado_por) '
    || 'values (''acessorio'', '' Z4'', 1, 1, ' || v_leandro || ')', '23514');
  perform pg_temp.deve_falhar('custo unitário não é gravável',
    'update public.custos_itens set custo_unitario = 1', '428C9');
  perform pg_temp.deve_falhar('perda acima de 100 direto',
    'update public.custos_parametros set valor = 101 where chave = ''perda_percentual''', '23514');
  perform pg_temp.deve_falhar('parâmetro negativo direto',
    'update public.custos_parametros set valor = -1 where chave = ''mdo_hora''', '23514');
  perform pg_temp.deve_falhar('chave de parâmetro inválida direto',
    'insert into public.custos_parametros (chave, valor) values (''consumo_em_watts'', 1)', '23514');
  perform pg_temp.deve_falhar('filamento negativo direto',
    'update public.custos_filamentos set valor_kg = -1', '23514');
  perform pg_temp.deve_falhar('filamento repetido (sem diferenciar caixa)',
    'insert into public.custos_filamentos (nome, valor_kg) values (''pla'', 1)', '23505');
  perform pg_temp.deve_falhar('nome de item repetido direto',
    'insert into public.custos_itens (tipo, nome, valor_compra, quantidade, atualizado_por) '
    || 'values (''embalagem'', ''caixa p'', 1, 1, ' || v_leandro || ')', '23505');

  perform pg_temp.deve_falhar('vida útil zero direto',
    'update public.custos_parametros set valor = 0 where chave = ''depreciacao_vida_util_anos''',
    '23514');
  perform pg_temp.deve_falhar('dias por mês zero direto',
    'update public.custos_parametros set valor = 0 where chave = ''depreciacao_dias_mes''', '23514');
  perform pg_temp.deve_falhar('dias por mês 32 direto',
    'update public.custos_parametros set valor = 32 where chave = ''depreciacao_dias_mes''', '23514');
  perform pg_temp.deve_falhar('dias por mês fracionado direto',
    'update public.custos_parametros set valor = 1.5 where chave = ''depreciacao_dias_mes''', '23514');
  perform pg_temp.deve_falhar('horas por dia zero direto',
    'update public.custos_parametros set valor = 0 where chave = ''depreciacao_horas_dia''', '23514');
  perform pg_temp.deve_falhar('horas por dia acima de 24 direto',
    'update public.custos_parametros set valor = 24.5 where chave = ''depreciacao_horas_dia''',
    '23514');
  perform pg_temp.deve_falhar('MDO sem valor direto',
    'update public.custos_parametros set valor = null where chave = ''mdo_hora''', '23514');
  perform pg_temp.deve_falhar('aquisição sem valor direto',
    'update public.custos_parametros set valor = null where chave = ''depreciacao_valor_aquisicao''',
    '23514');
  perform pg_temp.deve_falhar('parâmetro antigo não é mais uma chave válida',
    'insert into public.custos_parametros (chave, valor) values (''manutencao_depreciacao_hora'', 0.88)',
    '23514');
  update public.custos_parametros set valor = null where chave = 'manutencao_hora';  -- permitido

  raise notice 'custos_test: todas as verificações passaram';
end;
$teste$;

rollback;

-- Fora da transação: as tabelas reais voltaram, os clones sumiram e nada foi gravado nelas.
select
  to_regclass('public.custos_filamentos_real') is null as sem_tabelas_temporarias,
  (select count(*) from public.custos_filamentos) as filamentos,
  (select count(*) from public.custos_parametros) as parametros,
  (select count(*) from public.custos_itens) as itens,
  (select last_value from pg_sequences
    where schemaname = 'public' and sequencename = 'custos_itens_id_seq') as sequencia_itens,
  (select last_value from pg_sequences
    where schemaname = 'public' and sequencename = 'custos_filamentos_id_seq') as sequencia_filamentos;
