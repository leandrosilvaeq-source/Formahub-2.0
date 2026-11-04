-- Teste transacional da migration da Mão de Obra (20261023120000_mdo_mao_de_obra.sql).
--
-- Roda dentro de BEGIN/ROLLBACK, só no PostgreSQL local em memória do executor (npm run test:sql).
-- Confere os valores iniciais, as faixas das chaves novas e que a retirada continua sendo a mesma
-- chave. Qualquer falha interrompe com RAISE EXCEPTION.

begin;

do $mdo$
declare
  v_leandro bigint;
  v_estado  text;
  v_dados   jsonb;
  v_teste   record;
begin
  select id into v_leandro from public.usuarios where nome = 'Leandro';

  -- Valores iniciais
  if (select valor from public.custos_parametros where chave = 'mdo_horas_dia') <> 4
     or (select valor from public.custos_parametros where chave = 'mdo_dias_mes') <> 30
     or (select valor from public.custos_parametros where chave = 'mdo_revisoes_confirmadas') <> 0
     or (select valor from public.custos_parametros where chave = 'retirada_desejada_mensal') <> 500
     or (select valor from public.custos_parametros where chave = 'mdo_hora') <> 15 then
    raise exception 'mdo_test: valores iniciais incorretos';
  end if;

  -- Gravação válida (e a resposta traz a retirada e as chaves novas)
  v_dados := public.salvar_custos_parametros(
    '{"mdo_horas_dia": "5.5", "mdo_dias_mes": "22", "retirada_desejada_mensal": "800.00",
      "mdo_revisoes_confirmadas": "1"}'::jsonb, v_leandro);
  if (v_dados ->> 'mdo_horas_dia')::numeric <> 5.5 or (v_dados ->> 'mdo_dias_mes')::numeric <> 22
     or (v_dados ->> 'retirada_desejada_mensal')::numeric <> 800
     or (v_dados ->> 'mdo_revisoes_confirmadas')::numeric <> 1 then
    raise exception 'mdo_test: gravação válida não refletida';
  end if;

  -- Valores recusados (PT422), sem alterar nada
  for v_teste in
    select * from (values
      ('{"mdo_horas_dia": "0"}'), ('{"mdo_horas_dia": "24.01"}'), ('{"mdo_horas_dia": "-1"}'),
      ('{"mdo_dias_mes": "0"}'), ('{"mdo_dias_mes": "32"}'), ('{"mdo_dias_mes": "20.5"}'),
      ('{"mdo_revisoes_confirmadas": "1.5"}'), ('{"mdo_revisoes_confirmadas": "-1"}'),
      ('{"mdo_horas_dia": ""}'), ('{"mdo_dias_mes": null}')
    ) as t (corpo)
  loop
    begin
      perform public.salvar_custos_parametros(v_teste.corpo::jsonb, v_leandro);
      raise exception 'mdo_test: % deveria ser recusado', v_teste.corpo;
    exception when sqlstate 'PT422' then
      null;
    end;
  end loop;

  if (select valor from public.custos_parametros where chave = 'mdo_horas_dia') <> 5.5 then
    raise exception 'mdo_test: recusa alterou o valor salvo';
  end if;

  -- O banco também recusa direto na tabela (restrição de faixas)
  begin
    update public.custos_parametros set valor = 25 where chave = 'mdo_horas_dia';
    raise exception 'mdo_test: restrição de faixas não recusou 25 h/dia';
  exception when check_violation then
    null;
  end;
end;
$mdo$;

rollback;

select 'mdo_test: ok' as resultado;
