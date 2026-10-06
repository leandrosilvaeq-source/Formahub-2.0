-- Custos: separa "Manutenção e Depreciação" em Manutenção e Depreciação.
--
-- Esta migration só mexe em custos_parametros (e na função que a grava). A 20261007120000 já
-- foi aplicada e não é alterada. Tarifa, consumo, perdas, MDO, filamentos, acessórios e
-- embalagens ficam exatamente como estão.
--
-- MANUTENÇÃO (R$/h): chave nova, SEM valor (NULL = "A definir"). NULL é ausência de valor, não
--   zero: zero é um valor digitado. O antigo "manutencao_depreciacao_hora" (inicial R$ 0,88/h,
--   que juntava duas coisas) é APAGADO e NÃO é transferido para nenhum dos campos novos.
-- DEPRECIAÇÃO: guardam-se só os critérios; o valor por hora é sempre calculado pelo app
--   (nunca um campo independente que possa contradizer o cálculo):
--     por hora = (aquisição - residual) / (vida útil em anos x 12 x dias por mês x horas por dia)
--   Critérios iniciais: aquisição R$ 6.000,00 · vida útil 5 anos · residual R$ 0,00 ·
--   12 horas por dia · 30 dias por mês (360 h/mês; R$ 100,00/mês; ~R$ 0,2778/h).
--
-- Faixas (também no banco): aquisição e residual >= 0 (residual <= aquisição, conferido pela
-- função, que enxerga os dois valores); vida útil > 0; dias por mês inteiro de 1 a 31; horas
-- por dia > 0 e <= 24. Só a manutenção pode ficar sem valor.
-- Valores iniciais entram com "on conflict do nothing": nunca sobrescrevem edição manual.
-- Mesmo padrão de segurança das demais funções (security definer, search_path vazio, só
-- service_role executa). Migration LOCAL: aplicar somente por pedido explícito (db push).

-- ---------- Parâmetro antigo sai; chaves novas entram ----------
delete from public.custos_parametros where chave = 'manutencao_depreciacao_hora';

alter table public.custos_parametros drop constraint custos_parametros_chave_check;
alter table public.custos_parametros add constraint custos_parametros_chave_check check (chave in (
  'energia_tarifa_kwh', 'energia_consumo_w', 'perda_percentual', 'mdo_hora', 'manutencao_hora',
  'depreciacao_valor_aquisicao', 'depreciacao_vida_util_anos', 'depreciacao_valor_residual',
  'depreciacao_horas_dia', 'depreciacao_dias_mes'));

-- Só a manutenção aceita ausência de valor.
alter table public.custos_parametros alter column valor drop not null;
alter table public.custos_parametros add constraint custos_parametros_valor_obrigatorio
  check (valor is not null or chave = 'manutencao_hora');

alter table public.custos_parametros add constraint custos_parametros_faixas check (
  case chave
    when 'depreciacao_vida_util_anos' then valor > 0
    when 'depreciacao_dias_mes' then valor between 1 and 31 and valor = trunc(valor)
    when 'depreciacao_horas_dia' then valor > 0 and valor <= 24
    else true
  end);

-- ---------- Valores iniciais (nunca sobrescrevem edições manuais) ----------
insert into public.custos_parametros (chave, valor) values
  ('manutencao_hora', null),
  ('depreciacao_valor_aquisicao', 6000),
  ('depreciacao_vida_util_anos', 5),
  ('depreciacao_valor_residual', 0),
  ('depreciacao_horas_dia', 12),
  ('depreciacao_dias_mes', 30)
on conflict (chave) do nothing;

-- ---------- Gravação de valores únicos ----------
-- Mesma assinatura da anterior (create or replace: nenhuma sobrecarga). Novidades: a manutenção
-- aceita nulo ou texto vazio (volta a "A definir"), faixas dos critérios da depreciação e
-- residual <= aquisição no estado final (tudo ou nada).
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
      -- Só a manutenção pode voltar a "A definir" (NULL); nos outros, vazio é inválido.
      if v_chave <> 'manutencao_hora' then
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
         or (v_chave = 'perda_percentual' and v_valor > 100)
         or (v_chave = 'depreciacao_vida_util_anos' and v_valor <= 0)
         or (v_chave = 'depreciacao_dias_mes' and (v_valor < 1 or v_valor > 31
                                                   or v_valor <> trunc(v_valor)))
         or (v_chave = 'depreciacao_horas_dia' and (v_valor <= 0 or v_valor > 24)) then
        raise exception 'valor inválido para %', v_chave using errcode = 'PT422';
      end if;
    end if;

    perform 1 from public.custos_parametros where chave = v_chave for update;
    update public.custos_parametros
       set valor = v_valor, atualizado_em = now(), atualizado_por = p_usuario_id
     where chave = v_chave;
  end loop;

  -- O residual nunca passa do valor de aquisição (vale para o estado final, com ou sem os dois
  -- valores no mesmo envio).
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

revoke all on function public.salvar_custos_parametros(jsonb, bigint)
  from public, anon, authenticated;
grant execute on function public.salvar_custos_parametros(jsonb, bigint) to service_role;
