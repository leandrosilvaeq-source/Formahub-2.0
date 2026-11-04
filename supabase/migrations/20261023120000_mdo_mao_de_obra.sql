-- Custos: Mão de Obra calculada pela retirada e revisão trimestral.
--
-- O card "Mão de Obra" mostra o valor manual (mdo_hora, preservado como está) e o calculado de
-- referência: retirada_desejada_mensal / (mdo_horas_dia x mdo_dias_mes), sem arredondar. A
-- retirada continua sendo a mesma chave do card "Retirada desejada" (não é somada como custo).
--
-- Chaves novas em custos_parametros (nunca sobrescrevem edições já feitas):
--   mdo_horas_dia             horas de trabalho por dia (inicial 4; maior que 0 e até 24)
--   mdo_dias_mes              dias de trabalho por mês (inicial 30; inteiro de 1 a 31)
--   mdo_revisoes_confirmadas  revisões trimestrais já confirmadas (inicial 0; inteiro >= 0).
--                             A revisão n vence em 01/11/2026 + 3n meses de calendário (a
--                             primeira, em 01/02/2027); o aviso fica ativo até confirmar.
--
-- Nenhuma tabela nova. salvar_custos_parametros ganha só as faixas das chaves novas.
-- Migration LOCAL: aplicar somente por pedido explícito (supabase db push).

alter table public.custos_parametros drop constraint custos_parametros_chave_check;
alter table public.custos_parametros add constraint custos_parametros_chave_check check (chave in (
  'energia_tarifa_kwh', 'energia_consumo_w', 'perda_percentual', 'mdo_hora', 'manutencao_hora',
  'depreciacao_valor_aquisicao', 'depreciacao_vida_util_anos', 'depreciacao_valor_residual',
  'depreciacao_horas_dia', 'depreciacao_dias_mes',
  'energia_consumo_kwh_mes', 'horas_impressas_mes', 'energia_hora_manual',
  'depreciacao_hora_manual', 'taxa_venda_fixa', 'taxa_venda_percentual', 'mei_mensal',
  'retirada_desejada_mensal',
  'mdo_horas_dia', 'mdo_dias_mes', 'mdo_revisoes_confirmadas'));

alter table public.custos_parametros drop constraint custos_parametros_faixas;
alter table public.custos_parametros add constraint custos_parametros_faixas check (
  case chave
    when 'depreciacao_vida_util_anos' then valor > 0
    when 'depreciacao_dias_mes' then valor between 1 and 31 and valor = trunc(valor)
    when 'depreciacao_horas_dia' then valor > 0 and valor <= 24
    when 'horas_impressas_mes' then valor > 0 and valor <= 744
    when 'taxa_venda_percentual' then valor <= 100
    when 'mdo_horas_dia' then valor > 0 and valor <= 24
    when 'mdo_dias_mes' then valor between 1 and 31 and valor = trunc(valor)
    when 'mdo_revisoes_confirmadas' then valor = trunc(valor)
    else true
  end);

insert into public.custos_parametros (chave, valor, atualizado_em, provisorio) values
  ('mdo_horas_dia', 4, now(), false),
  ('mdo_dias_mes', 30, now(), false),
  ('mdo_revisoes_confirmadas', 0, now(), false)
on conflict (chave) do nothing;

-- ---------- Valores únicos: altera um ou mais, de uma vez (tudo ou nada) ----------
-- Mesma assinatura da anterior. Novidades: chaves novas e suas faixas; NULL/vazio volta a
-- "sem valor" só em manutencao_hora e nas chaves *_manual; qualquer gravação desmarca o
-- `provisorio`.
create or replace function public.salvar_custos_parametros(
  p_valores    jsonb,
  p_usuario_id bigint
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $corpo$
declare
  v_chave      text;
  v_texto      text;
  v_valor      numeric;
  v_aquisicao  numeric;
  v_residual   numeric;
begin
  if not exists (select 1 from public.usuarios
                  where id = p_usuario_id and nome in ('Leandro', 'Kassia', 'Marise')) then
    raise exception 'usuário inválido' using errcode = 'PT403';
  end if;
  if p_valores is null or jsonb_typeof(p_valores) <> 'object' or p_valores = '{}'::jsonb then
    raise exception 'nenhum valor informado' using errcode = 'PT422';
  end if;

  for v_chave, v_texto in
    select key, value #>> '{}' from jsonb_each(p_valores) order by key
  loop
    if not exists (select 1 from public.custos_parametros where chave = v_chave) then
      raise exception 'chave desconhecida: %', v_chave using errcode = 'PT404';
    end if;

    if v_texto is null or btrim(v_texto) = '' then
      if v_chave not in ('manutencao_hora', 'energia_hora_manual', 'depreciacao_hora_manual') then
        raise exception 'valor inválido para %', v_chave using errcode = 'PT422';
      end if;
      v_valor := null;
    else
      begin
        v_valor := v_texto::numeric;
      exception when others then
        raise exception 'valor inválido para %', v_chave using errcode = 'PT422';
      end;
      if v_valor = 'NaN'::numeric or v_valor < 0 or v_valor >= 1000000000
         or (v_chave in ('perda_percentual', 'taxa_venda_percentual') and v_valor > 100)
         or (v_chave = 'depreciacao_vida_util_anos' and v_valor <= 0)
         or (v_chave = 'horas_impressas_mes' and (v_valor <= 0 or v_valor > 744))
         or (v_chave = 'depreciacao_dias_mes' and (v_valor < 1 or v_valor > 31
                                                   or v_valor <> trunc(v_valor)))
         or (v_chave = 'depreciacao_horas_dia' and (v_valor <= 0 or v_valor > 24))
         or (v_chave = 'mdo_horas_dia' and (v_valor <= 0 or v_valor > 24))
         or (v_chave = 'mdo_dias_mes' and (v_valor < 1 or v_valor > 31 or v_valor <> trunc(v_valor)))
         or (v_chave = 'mdo_revisoes_confirmadas' and v_valor <> trunc(v_valor)) then
        raise exception 'valor inválido para %', v_chave using errcode = 'PT422';
      end if;
    end if;

    perform 1 from public.custos_parametros where chave = v_chave for update;
    update public.custos_parametros
       set valor = v_valor, provisorio = false, atualizado_em = now(),
           atualizado_por = p_usuario_id
     where chave = v_chave;
  end loop;

  select valor into v_aquisicao from public.custos_parametros
   where chave = 'depreciacao_valor_aquisicao';
  select valor into v_residual from public.custos_parametros
   where chave = 'depreciacao_valor_residual';
  if v_residual > v_aquisicao then
    raise exception 'o valor residual não pode ser maior que o valor de aquisição'
      using errcode = 'PT422';
  end if;

  return (select jsonb_object_agg(p.chave, p.valor::text) from public.custos_parametros as p);
end;
$corpo$;
