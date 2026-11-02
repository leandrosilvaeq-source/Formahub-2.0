-- Teste transacional da migration de gestão de custos (20261019120000_custos_gestao.sql).
--
-- Seguro com dados reais: tudo roda dentro de BEGIN/ROLLBACK, e o teste NÃO usa as linhas nem as
-- sequences reais. Depois das conferências de estrutura (somente leitura), ele renomeia as quatro
-- tabelas de custos tocadas pela migration e cria clones vazios (LIKE ... INCLUDING ALL). As
-- funções, que usam os nomes das tabelas, passam a operar nos clones, com dados fictícios. O
-- ROLLBACK desfaz tudo; as sequences reais nunca são consumidas.
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
  v_tabela text;
  v_papel  text;
  v_funcao text;
begin
  foreach v_tabela in array array['custos_ajustes_insumo', 'custos_manutencao_gastos',
                                  'custos_manutencao_periodo'] loop
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

  foreach v_funcao in array array[
    'listar_custos()', 'salvar_custos_parametros(jsonb, bigint)',
    'salvar_ajuste_insumo(text, numeric, bigint)',
    'definir_periodo_manutencao(date, date, numeric, bigint)'] loop
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
                and p.proname in ('listar_custos', 'salvar_custos_parametros',
                                  'salvar_ajuste_insumo', 'definir_periodo_manutencao')
                and (not p.prosecdef or p.proconfig is null
                     or not (p.proconfig::text like '%search_path=%'))) then
    raise exception 'funções de custos devem ser security definer com search_path fixo';
  end if;

  -- Sem sobrecarga: uma só versão de cada função.
  if (select count(*) from pg_proc p join pg_namespace n on n.oid = p.pronamespace
       where n.nspname = 'public' and p.proname = 'salvar_custos_parametros') <> 1 then
    raise exception 'salvar_custos_parametros não pode ter sobrecarga';
  end if;

  -- Nenhuma coluna guarda o custo por hora derivado (só o ajuste manual, como parâmetro).
  if exists (select 1 from information_schema.columns
              where table_schema = 'public' and table_name like 'custos_%'
                and column_name ~ 'custo_hora|por_hora') then
    raise exception 'o custo por hora derivado não deve ser guardado';
  end if;
end;
$estrutura$;

-- ---------- Isola os dados reais: clones vazios ----------
alter table public.custos_parametros rename to custos_parametros_real;
alter table public.custos_ajustes_insumo rename to custos_ajustes_insumo_real;
alter table public.custos_manutencao_gastos rename to custos_manutencao_gastos_real;
alter table public.custos_manutencao_periodo rename to custos_manutencao_periodo_real;
alter table public.manutencoes rename to manutencoes_real;
create table public.custos_parametros (like public.custos_parametros_real including all);
create table public.custos_ajustes_insumo (like public.custos_ajustes_insumo_real including all);
create table public.custos_manutencao_gastos
  (like public.custos_manutencao_gastos_real including all);
create table public.custos_manutencao_periodo
  (like public.custos_manutencao_periodo_real including all);
create table public.manutencoes (like public.manutencoes_real including all);

do $teste$
declare
  v_leandro bigint;
  v_kassia  bigint;
  v_retorno jsonb;
  v_antes   jsonb;
begin
  select id into v_leandro from public.usuarios where nome = 'Leandro';
  select id into v_kassia from public.usuarios where nome = 'Kassia';
  if v_leandro is null or v_kassia is null then
    raise exception 'usuários de teste não encontrados';
  end if;

  -- ---------- Valores substituídos: só o que está no padrão antigo e nunca foi editado ----------
  -- Estado anterior (migrations 20261007/08): padrões antigos sem edição, exceto a tarifa, que
  -- alguém editou no app (atualizado_por preenchido).
  insert into public.custos_parametros (chave, valor, atualizado_por) values
    ('energia_tarifa_kwh', 1, v_leandro), ('energia_consumo_w', 120, null),
    ('perda_percentual', 5, null), ('mdo_hora', 0.5, null), ('manutencao_hora', null, null),
    ('depreciacao_valor_aquisicao', 6000, null), ('depreciacao_vida_util_anos', 5, null),
    ('depreciacao_valor_residual', 0, null), ('depreciacao_horas_dia', 12, null),
    ('depreciacao_dias_mes', 30, null);
  -- Mesmas instruções da migration (cópia), executadas nos clones.
  update public.custos_parametros set valor = 3, atualizado_em = now()
   where chave = 'perda_percentual' and valor = 5 and atualizado_por is null;
  update public.custos_parametros set valor = 1.0393, atualizado_em = now()
   where chave = 'energia_tarifa_kwh' and valor = 1 and atualizado_por is null;
  update public.custos_parametros set valor = 15, atualizado_em = now()
   where chave = 'mdo_hora' and valor = 0.5 and atualizado_por is null;
  update public.custos_parametros set valor = 5000, atualizado_em = now()
   where chave = 'depreciacao_valor_aquisicao' and valor = 6000 and atualizado_por is null;
  insert into public.custos_parametros (chave, valor, atualizado_em, provisorio) values
    ('energia_consumo_kwh_mes', 40, now(), false),
    ('horas_impressas_mes', 305, timestamptz '2026-11-01 00:00:00-03', false),
    ('energia_hora_manual', 0.14, now(), true),
    ('depreciacao_hora_manual', 0.27, now(), true),
    ('taxa_venda_fixa', 0, now(), false), ('taxa_venda_percentual', 0, now(), false),
    ('mei_mensal', 100, now(), false), ('retirada_desejada_mensal', 500, now(), false)
  on conflict (chave) do nothing;

  v_retorno := public.listar_custos();
  if (v_retorno #>> '{parametros,perda_percentual}')::numeric <> 3
     or (v_retorno #>> '{parametros,mdo_hora}')::numeric <> 15
     or (v_retorno #>> '{parametros,depreciacao_valor_aquisicao}')::numeric <> 5000
     or (v_retorno #>> '{parametros,energia_tarifa_kwh}')::numeric <> 1 then
    raise exception 'substituições erradas (a tarifa editada deve ficar como estava): %',
      v_retorno -> 'parametros';
  end if;
  if (v_retorno #>> '{parametros,energia_consumo_kwh_mes}')::numeric <> 40
     or (v_retorno #>> '{parametros,horas_impressas_mes}')::numeric <> 305
     or (v_retorno #>> '{parametros,energia_hora_manual}')::numeric <> 0.14
     or (v_retorno #>> '{parametros,depreciacao_hora_manual}')::numeric <> 0.27
     or (v_retorno #>> '{parametros,mei_mensal}')::numeric <> 100
     or (v_retorno #>> '{parametros,retirada_desejada_mensal}')::numeric <> 500
     or (v_retorno #>> '{parametros,taxa_venda_fixa}')::numeric <> 0
     or (v_retorno #>> '{parametros,taxa_venda_percentual}')::numeric <> 0 then
    raise exception 'valores iniciais inesperados: %', v_retorno -> 'parametros';
  end if;
  -- Os parâmetros antigos continuam gravados (nada é apagado), só sem uso na tela.
  if (v_retorno #>> '{parametros,energia_consumo_w}')::numeric <> 120
     or (v_retorno #>> '{parametros,depreciacao_horas_dia}')::numeric <> 12 then
    raise exception 'os parâmetros antigos não deveriam ser apagados';
  end if;
  if v_retorno -> 'provisorios' <> '["depreciacao_hora_manual", "energia_hora_manual"]'::jsonb then
    raise exception 'provisórios inesperados: %', v_retorno -> 'provisorios';
  end if;
  if v_retorno #>> '{datas,horas_impressas_mes}' <> '2026-11-01' then
    raise exception 'data-base das horas inesperada: %', v_retorno -> 'datas';
  end if;
  if jsonb_typeof(v_retorno #> '{parametros,mdo_hora}') <> 'string'
     or v_retorno -> 'ajustes' <> '{}'::jsonb or v_retorno -> 'manutencao_gastos' <> '[]'::jsonb
     or jsonb_typeof(v_retorno -> 'manutencao_periodo') <> 'null' then
    raise exception 'leitura inicial inesperada: %', v_retorno;
  end if;

  -- ---------- Valores únicos: chaves novas, faixas, nulos e provisório ----------
  v_retorno := public.salvar_custos_parametros(
    '{"horas_impressas_mes": "280", "energia_consumo_kwh_mes": "42.5",
      "taxa_venda_fixa": "1.50", "taxa_venda_percentual": "2.5", "mei_mensal": "105.90",
      "retirada_desejada_mensal": "800"}', v_leandro);
  if (v_retorno ->> 'horas_impressas_mes')::numeric <> 280
     or (v_retorno ->> 'energia_consumo_kwh_mes')::numeric <> 42.5
     or (v_retorno ->> 'taxa_venda_percentual')::numeric <> 2.5
     or (v_retorno ->> 'mei_mensal')::numeric <> 105.90 then
    raise exception 'valores novos não gravados: %', v_retorno;
  end if;
  -- O ajuste manual salvo pelo app deixa de ser provisório; o outro continua.
  perform public.salvar_custos_parametros('{"energia_hora_manual": "0.15"}', v_kassia);
  v_retorno := public.listar_custos();
  if v_retorno -> 'provisorios' <> '["depreciacao_hora_manual"]'::jsonb then
    raise exception 'só o ajuste ainda não revisto deveria ser provisório: %',
      v_retorno -> 'provisorios';
  end if;
  -- Voltar ao calculado: NULL (nunca zero), em branco ou nulo, só nos ajustes e na manutenção.
  v_retorno := public.salvar_custos_parametros(
    '{"energia_hora_manual": null, "depreciacao_hora_manual": "", "manutencao_hora": ""}',
    v_leandro);
  if v_retorno -> 'energia_hora_manual' is distinct from 'null'::jsonb
     or v_retorno -> 'depreciacao_hora_manual' is distinct from 'null'::jsonb
     or v_retorno -> 'manutencao_hora' is distinct from 'null'::jsonb then
    raise exception 'ajustes deveriam voltar a NULL: %', v_retorno;
  end if;
  if exists (select 1 from public.custos_parametros
              where chave in ('energia_hora_manual', 'depreciacao_hora_manual') and valor = 0) then
    raise exception 'voltar ao calculado não pode virar zero';
  end if;
  perform public.salvar_custos_parametros('{"energia_hora_manual": "0"}', v_leandro);  -- zero é valor

  perform pg_temp.deve_falhar('horas mensais zero',
    'select public.salvar_custos_parametros(''{"horas_impressas_mes": "0"}'', ' || v_leandro || ')',
    'PT422');
  perform pg_temp.deve_falhar('horas mensais acima de 744',
    'select public.salvar_custos_parametros(''{"horas_impressas_mes": "744.01"}'', '
      || v_leandro || ')', 'PT422');
  perform public.salvar_custos_parametros('{"horas_impressas_mes": "744"}', v_leandro);  -- limite
  perform pg_temp.deve_falhar('taxa percentual acima de 100',
    'select public.salvar_custos_parametros(''{"taxa_venda_percentual": "100.01"}'', '
      || v_leandro || ')', 'PT422');
  perform pg_temp.deve_falhar('mdo em branco',
    'select public.salvar_custos_parametros(''{"mdo_hora": null}'', ' || v_leandro || ')', 'PT422');
  perform pg_temp.deve_falhar('mei em branco',
    'select public.salvar_custos_parametros(''{"mei_mensal": ""}'', ' || v_leandro || ')',
    'PT422');
  perform pg_temp.deve_falhar('horas mensais em branco',
    'select public.salvar_custos_parametros(''{"horas_impressas_mes": null}'', '
      || v_leandro || ')', 'PT422');
  perform pg_temp.deve_falhar('valor negativo',
    'select public.salvar_custos_parametros(''{"retirada_desejada_mensal": "-1"}'', '
      || v_leandro || ')', 'PT422');
  perform pg_temp.deve_falhar('chave desconhecida',
    'select public.salvar_custos_parametros(''{"mei_mensal": "1", "outra": "1"}'', '
      || v_leandro || ')', 'PT404');
  perform pg_temp.deve_falhar('usuário inválido',
    'select public.salvar_custos_parametros(''{"mei_mensal": "1"}'', -999)', 'PT403');
  perform pg_temp.deve_falhar('residual maior que a aquisição',
    'select public.salvar_custos_parametros(''{"depreciacao_valor_residual": "5001"}'', '
      || v_leandro || ')', 'PT422');
  -- Tudo ou nada: um valor bom seguido de um ruim não grava nenhum.
  v_antes := public.listar_custos() -> 'parametros';
  perform pg_temp.deve_falhar('tudo ou nada',
    'select public.salvar_custos_parametros(''{"mei_mensal": "999", "taxa_venda_percentual": "101"}'', '
      || v_leandro || ')', 'PT422');
  if public.listar_custos() -> 'parametros' <> v_antes then
    raise exception 'uma gravação recusada alterou valores';
  end if;
  -- Constraints do próprio banco (além da função): NULL só onde permitido.
  perform pg_temp.deve_falhar('NULL direto na tabela',
    'update public.custos_parametros set valor = null where chave = ''mei_mensal''', '23514');
  perform pg_temp.deve_falhar('horas fora da faixa direto na tabela',
    'update public.custos_parametros set valor = 745 where chave = ''horas_impressas_mes''',
    '23514');

  -- ---------- Ajustes manuais de insumos ----------
  v_retorno := public.salvar_ajuste_insumo('filamento:petg', 90.5, v_leandro);
  if (v_retorno ->> 'filamento:petg')::numeric <> 90.5 then
    raise exception 'ajuste não gravado: %', v_retorno;
  end if;
  v_retorno := public.salvar_ajuste_insumo('filamento:petg', 91, v_kassia);  -- atualiza, não duplica
  perform public.salvar_ajuste_insumo('acessorio:Imã com espaço', 3.3333333333, v_leandro);
  perform public.salvar_ajuste_insumo('embalagem:Caixa P', 0, v_leandro);  -- zero é valor
  if (select count(*) from public.custos_ajustes_insumo) <> 3
     or (select valor from public.custos_ajustes_insumo where chave = 'filamento:petg') <> 91
     or (select atualizado_por from public.custos_ajustes_insumo
          where chave = 'filamento:petg') <> v_kassia then
    raise exception 'ajustes inesperados: %', public.listar_custos() -> 'ajustes';
  end if;
  if jsonb_typeof(public.listar_custos() #> '{ajustes,acessorio:Imã com espaço}') <> 'string' then
    raise exception 'ajustes devem sair como texto';
  end if;
  -- Voltar ao calculado apaga o ajuste (e só ele).
  v_retorno := public.salvar_ajuste_insumo('filamento:petg', null, v_leandro);
  if v_retorno ? 'filamento:petg' or (select count(*) from public.custos_ajustes_insumo) <> 2 then
    raise exception 'voltar ao calculado deveria apagar só o ajuste: %', v_retorno;
  end if;
  perform public.salvar_ajuste_insumo('filamento:petg', null, v_leandro);  -- idempotente
  perform pg_temp.deve_falhar('chave de outro tipo',
    'select public.salvar_ajuste_insumo(''outro:x'', 1, ' || v_leandro || ')', 'PT422');
  perform pg_temp.deve_falhar('chave sem nome',
    'select public.salvar_ajuste_insumo(''filamento:'', 1, ' || v_leandro || ')', 'PT422');
  perform pg_temp.deve_falhar('chave nula',
    'select public.salvar_ajuste_insumo(null, 1, ' || v_leandro || ')', 'PT422');
  perform pg_temp.deve_falhar('ajuste negativo',
    'select public.salvar_ajuste_insumo(''filamento:pla'', -1, ' || v_leandro || ')', 'PT422');
  perform pg_temp.deve_falhar('ajuste acima do limite',
    'select public.salvar_ajuste_insumo(''filamento:pla'', 1000000000, ' || v_leandro || ')',
    'PT422');
  perform pg_temp.deve_falhar('ajuste NaN',
    'select public.salvar_ajuste_insumo(''filamento:pla'', ''NaN''::numeric, ' || v_leandro || ')',
    'PT422');
  perform pg_temp.deve_falhar('ajuste com usuário inválido',
    'select public.salvar_ajuste_insumo(''filamento:pla'', 1, -999)', 'PT403');

  -- ---------- Manutenção: gastos reais ----------
  -- Os gastos passaram a ser as manutenções (migration 20261020120000, testada em
  -- manutencoes_test.sql): a função que gravava na tabela antiga deixou de existir.
  if to_regprocedure('public.registrar_gasto_manutencao(date, text, numeric, bigint)') is not null then
    raise exception 'registrar_gasto_manutencao deveria ter sido removida';
  end if;

  -- ---------- Manutenção: período de referência ----------
  v_retorno := public.definir_periodo_manutencao(date '2026-10-01', date '2026-10-31', 250, v_leandro);
  if v_retorno ->> 'inicio' <> '2026-10-01' or (v_retorno ->> 'horas')::numeric <> 250 then
    raise exception 'período inesperado: %', v_retorno;
  end if;
  perform public.definir_periodo_manutencao(date '2026-07-01', date '2026-09-30', 900.5, v_kassia);
  perform public.definir_periodo_manutencao(date '2026-10-05', date '2026-10-05', 1, v_kassia);  -- 1 dia
  if (select count(*) from public.custos_manutencao_periodo) <> 1 then
    raise exception 'só pode existir um período de referência';
  end if;
  v_retorno := public.listar_custos() -> 'manutencao_periodo';
  if v_retorno ->> 'inicio' <> '2026-10-05' or (v_retorno ->> 'horas')::numeric <> 1 then
    raise exception 'período não foi atualizado: %', v_retorno;
  end if;
  perform pg_temp.deve_falhar('fim antes do início',
    'select public.definir_periodo_manutencao(date ''2026-10-31'', date ''2026-10-01'', 1, '
      || v_leandro || ')', 'PT422');
  perform pg_temp.deve_falhar('horas zero no período',
    'select public.definir_periodo_manutencao(date ''2026-10-01'', date ''2026-10-31'', 0, '
      || v_leandro || ')', 'PT422');
  perform pg_temp.deve_falhar('período sem data',
    'select public.definir_periodo_manutencao(null, date ''2026-10-31'', 1, ' || v_leandro || ')',
    'PT422');
  perform pg_temp.deve_falhar('período com usuário inválido',
    'select public.definir_periodo_manutencao(date ''2026-10-01'', date ''2026-10-31'', 1, -999)',
    'PT403');
end;
$teste$;

rollback;

-- Depois do ROLLBACK as tabelas reais voltam ao lugar e os clones somem.
do $final$
begin
  if to_regclass('public.custos_parametros_real') is not null
     or to_regclass('public.custos_ajustes_insumo_real') is not null
     or to_regclass('public.custos_manutencao_gastos_real') is not null
     or to_regclass('public.custos_manutencao_periodo_real') is not null
     or to_regclass('public.manutencoes_real') is not null then
    raise exception 'custos_gestao_test: tabelas temporárias sobraram depois do ROLLBACK';
  end if;
end;
$final$;

select 'custos_gestao_test: ok' as resultado;
