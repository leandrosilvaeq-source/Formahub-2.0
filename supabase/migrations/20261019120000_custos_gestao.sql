-- Custos: administração e gestão de custos (insumos, custos por hora, custos fixos e metas).
--
-- A tela deixa de ter cadastros próprios de filamentos, acessórios e embalagens: os insumos vêm
-- das compras do estoque (custo médio ponderado), e o que se guarda aqui são só AJUSTES MANUAIS
-- por cima desse valor. As compras e o estoque não são tocados. As tabelas custos_filamentos e
-- custos_itens (e as funções que as gravam) ficam como estão, sem uso na tela: nenhum dado é
-- apagado e nenhum pedido é alterado.
--
-- PARÂMETROS (custos_parametros, mesmas regras das migrations anteriores)
--   Chaves novas: energia_consumo_kwh_mes (40) · horas_impressas_mes (305, compartilhada por
--   energia e depreciação) · energia_hora_manual (0,14) · depreciacao_hora_manual (0,27) ·
--   taxa_venda_fixa (0) · taxa_venda_percentual (0) · mei_mensal (100) ·
--   retirada_desejada_mensal (500, meta gerencial: não entra no custo de produção).
--   Valor NULL = sem valor. É permitido só em manutencao_hora e nas chaves *_manual, onde NULL
--   significa "voltar ao calculado" (manutencao_hora, quando preenchida, é o ajuste manual da
--   manutenção; sem ela, a tela calcula pelos gastos reais). NULL nunca vira zero.
--   Coluna nova `provisorio`: marca o ajuste manual que entrou por esta migration; qualquer
--   gravação pelo app o desmarca.
--   A data-base das horas mensais é o atualizado_em de horas_impressas_mes (inicial: 2026-11-01).
--
-- VALORES SUBSTITUÍDOS (só os que ainda estão no padrão antigo E nunca foram editados no app,
--   isto é, atualizado_por nulo; edição manual é preservada):
--   perda 5% -> 3% · tarifa R$ 1,00/kWh -> R$ 1,0393/kWh · MDO R$ 0,50/h -> R$ 15,00/h ·
--   depreciação: aquisição R$ 6.000 -> R$ 5.000 (residual 0 e vida útil 5 anos seguem).
--   Os parâmetros antigos energia_consumo_w, depreciacao_horas_dia e depreciacao_dias_mes
--   continuam gravados, mas a tela não os usa mais (a base é horas_impressas_mes).
--
-- TABELAS NOVAS (RLS ligado, sem policies e sem privilégio direto; só funções SECURITY DEFINER)
--   custos_ajustes_insumo     ajuste manual do custo de um insumo ('filamento:petg',
--                             'acessorio:<nome>', 'embalagem:<nome>'); apagar a linha = voltar
--                             ao calculado. R$/kg para filamento e R$/un para os demais.
--   custos_manutencao_gastos  gastos reais de manutenção (data, descrição, valor).
--   custos_manutencao_periodo período de referência (início, fim) e as horas impressas nele:
--                             custo/h = gastos do período / horas do período.
--
-- Erros: PT403 usuário inválido · PT404 chave desconhecida · PT422 valor inválido.
-- Migration LOCAL: aplicar somente por pedido explícito (supabase db push).

-- ---------- Parâmetros: novas chaves, nulos e faixas ----------
alter table public.custos_parametros add column provisorio boolean not null default false;

alter table public.custos_parametros drop constraint custos_parametros_chave_check;
alter table public.custos_parametros add constraint custos_parametros_chave_check check (chave in (
  'energia_tarifa_kwh', 'energia_consumo_w', 'perda_percentual', 'mdo_hora', 'manutencao_hora',
  'depreciacao_valor_aquisicao', 'depreciacao_vida_util_anos', 'depreciacao_valor_residual',
  'depreciacao_horas_dia', 'depreciacao_dias_mes',
  'energia_consumo_kwh_mes', 'horas_impressas_mes', 'energia_hora_manual',
  'depreciacao_hora_manual', 'taxa_venda_fixa', 'taxa_venda_percentual', 'mei_mensal',
  'retirada_desejada_mensal'));

alter table public.custos_parametros drop constraint custos_parametros_valor_obrigatorio;
alter table public.custos_parametros add constraint custos_parametros_valor_obrigatorio
  check (valor is not null
         or chave in ('manutencao_hora', 'energia_hora_manual', 'depreciacao_hora_manual'));

alter table public.custos_parametros drop constraint custos_parametros_faixas;
alter table public.custos_parametros add constraint custos_parametros_faixas check (
  case chave
    when 'depreciacao_vida_util_anos' then valor > 0
    when 'depreciacao_dias_mes' then valor between 1 and 31 and valor = trunc(valor)
    when 'depreciacao_horas_dia' then valor > 0 and valor <= 24
    when 'horas_impressas_mes' then valor > 0 and valor <= 744
    when 'taxa_venda_percentual' then valor <= 100
    else true
  end);

-- ---------- Valores substituídos (só o que está no padrão antigo e nunca foi editado) ----------
update public.custos_parametros set valor = 3, atualizado_em = now()
 where chave = 'perda_percentual' and valor = 5 and atualizado_por is null;
update public.custos_parametros set valor = 1.0393, atualizado_em = now()
 where chave = 'energia_tarifa_kwh' and valor = 1 and atualizado_por is null;
update public.custos_parametros set valor = 15, atualizado_em = now()
 where chave = 'mdo_hora' and valor = 0.5 and atualizado_por is null;
update public.custos_parametros set valor = 5000, atualizado_em = now()
 where chave = 'depreciacao_valor_aquisicao' and valor = 6000 and atualizado_por is null;

-- ---------- Valores iniciais (nunca sobrescrevem edições manuais) ----------
insert into public.custos_parametros (chave, valor, atualizado_em, provisorio) values
  ('energia_consumo_kwh_mes', 40, now(), false),
  ('horas_impressas_mes', 305, timestamptz '2026-11-01 00:00:00-03', false),
  ('energia_hora_manual', 0.14, now(), true),
  ('depreciacao_hora_manual', 0.27, now(), true),
  ('taxa_venda_fixa', 0, now(), false),
  ('taxa_venda_percentual', 0, now(), false),
  ('mei_mensal', 100, now(), false),
  ('retirada_desejada_mensal', 500, now(), false)
on conflict (chave) do nothing;

-- ---------- Ajustes manuais de insumos ----------
create table public.custos_ajustes_insumo (
  chave          text        primary key check (
                   chave ~ '^(filamento|acessorio|embalagem):[^[:cntrl:]]{1,120}$'),
  valor          numeric     not null check (valor >= 0 and valor < 1000000000),
  atualizado_em  timestamptz not null default now(),
  atualizado_por bigint      not null references public.usuarios (id) on delete restrict
);

-- ---------- Manutenção: gastos reais e período de referência ----------
create table public.custos_manutencao_gastos (
  id        bigint        generated always as identity primary key,
  data      date          not null,
  descricao text          not null check (
              descricao = btrim(descricao) and descricao <> '' and char_length(descricao) <= 200),
  valor     numeric(12, 2) not null check (valor > 0),
  criado_em timestamptz   not null default now(),
  criado_por bigint       not null references public.usuarios (id) on delete restrict
);

create table public.custos_manutencao_periodo (
  id             smallint    primary key check (id = 1),
  inicio         date        not null,
  fim            date        not null,
  horas          numeric     not null check (horas > 0 and horas < 1000000),
  atualizado_em  timestamptz not null default now(),
  atualizado_por bigint      not null references public.usuarios (id) on delete restrict,
  constraint custos_manutencao_periodo_ordem check (fim >= inicio)
);

alter table public.custos_ajustes_insumo enable row level security;
alter table public.custos_manutencao_gastos enable row level security;
alter table public.custos_manutencao_periodo enable row level security;

revoke all on public.custos_ajustes_insumo from anon, authenticated, service_role;
revoke all on public.custos_manutencao_gastos from anon, authenticated, service_role;
revoke all on public.custos_manutencao_periodo from anon, authenticated, service_role;
revoke all on sequence public.custos_manutencao_gastos_id_seq from anon, authenticated, service_role;

-- ---------- Leitura ----------
-- Mantém filamentos e itens (cadastros antigos, só leitura) e acrescenta o que a tela nova usa.
-- Números e datas saem como TEXTO (sem float no caminho).
create or replace function public.listar_custos()
returns jsonb
language sql
stable
security definer
set search_path = ''
as $corpo$
  select jsonb_build_object(
    'filamentos', coalesce((
      select jsonb_agg(jsonb_build_object(
               'id', f.id, 'nome', f.nome, 'abrange', f.abrange, 'valor_kg', f.valor_kg::text)
             order by f.id)
        from public.custos_filamentos as f), '[]'::jsonb),
    'parametros', coalesce((
      select jsonb_object_agg(p.chave, p.valor::text) from public.custos_parametros as p),
      '{}'::jsonb),
    'itens', coalesce((
      select jsonb_agg(jsonb_build_object(
               'id', i.id, 'tipo', i.tipo, 'nome', i.nome,
               'valor_compra', i.valor_compra::text, 'quantidade', i.quantidade,
               'custo_unitario', i.custo_unitario::text)
             order by lower(i.nome), i.id)
        from public.custos_itens as i), '[]'::jsonb),
    'provisorios', coalesce((
      select jsonb_agg(p.chave order by p.chave)
        from public.custos_parametros as p where p.provisorio), '[]'::jsonb),
    'datas', coalesce((
      select jsonb_object_agg(p.chave, to_char(p.atualizado_em at time zone 'America/Sao_Paulo',
                                               'YYYY-MM-DD'))
        from public.custos_parametros as p), '{}'::jsonb),
    'ajustes', coalesce((
      select jsonb_object_agg(a.chave, a.valor::text) from public.custos_ajustes_insumo as a),
      '{}'::jsonb),
    'manutencao_gastos', coalesce((
      select jsonb_agg(jsonb_build_object(
               'id', g.id, 'data', to_char(g.data, 'YYYY-MM-DD'), 'descricao', g.descricao,
               'valor', g.valor::text)
             order by g.data desc, g.id desc)
        from public.custos_manutencao_gastos as g), '[]'::jsonb),
    'manutencao_periodo', (
      select jsonb_build_object(
               'inicio', to_char(m.inicio, 'YYYY-MM-DD'), 'fim', to_char(m.fim, 'YYYY-MM-DD'),
               'horas', m.horas::text)
        from public.custos_manutencao_periodo as m where m.id = 1)
  );
$corpo$;

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
         or (v_chave = 'depreciacao_horas_dia' and (v_valor <= 0 or v_valor > 24)) then
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

-- ---------- Insumo: ajuste manual (p_valor nulo = voltar ao calculado) ----------
create function public.salvar_ajuste_insumo(
  p_chave      text,
  p_valor      numeric,
  p_usuario_id bigint
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $corpo$
begin
  if not exists (select 1 from public.usuarios
                  where id = p_usuario_id and nome in ('Leandro', 'Kassia', 'Marise')) then
    raise exception 'usuário inválido' using errcode = 'PT403';
  end if;
  if p_chave is null or p_chave !~ '^(filamento|acessorio|embalagem):[^[:cntrl:]]{1,120}$' then
    raise exception 'insumo inválido' using errcode = 'PT422';
  end if;

  if p_valor is null then
    delete from public.custos_ajustes_insumo where chave = p_chave;
  else
    if p_valor = 'NaN'::numeric or p_valor < 0 or p_valor >= 1000000000 then
      raise exception 'valor inválido' using errcode = 'PT422';
    end if;
    insert into public.custos_ajustes_insumo (chave, valor, atualizado_por)
    values (p_chave, p_valor, p_usuario_id)
    on conflict (chave) do update
      set valor = excluded.valor, atualizado_em = now(), atualizado_por = excluded.atualizado_por;
  end if;

  return (select coalesce(jsonb_object_agg(a.chave, a.valor::text), '{}'::jsonb)
            from public.custos_ajustes_insumo as a);
end;
$corpo$;

-- ---------- Manutenção: registra um gasto real ----------
create function public.registrar_gasto_manutencao(
  p_data       date,
  p_descricao  text,
  p_valor      numeric,
  p_usuario_id bigint
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $corpo$
declare
  v_descricao text := btrim(p_descricao, E' \t\n\r\f\x0B');
  v_linha     public.custos_manutencao_gastos;
begin
  if not exists (select 1 from public.usuarios
                  where id = p_usuario_id and nome in ('Leandro', 'Kassia', 'Marise')) then
    raise exception 'usuário inválido' using errcode = 'PT403';
  end if;
  if p_data is null or v_descricao is null or v_descricao = '' or char_length(v_descricao) > 200
     or p_valor is null or p_valor = 'NaN'::numeric or p_valor <= 0
     or p_valor >= 10000000000 or p_valor <> round(p_valor, 2) then
    raise exception 'gasto inválido' using errcode = 'PT422';
  end if;

  insert into public.custos_manutencao_gastos (data, descricao, valor, criado_por)
  values (p_data, v_descricao, p_valor, p_usuario_id)
  returning * into v_linha;

  return jsonb_build_object('id', v_linha.id, 'data', to_char(v_linha.data, 'YYYY-MM-DD'),
                            'descricao', v_linha.descricao, 'valor', v_linha.valor::text);
end;
$corpo$;

-- ---------- Manutenção: período de referência e horas impressas nele ----------
create function public.definir_periodo_manutencao(
  p_inicio     date,
  p_fim        date,
  p_horas      numeric,
  p_usuario_id bigint
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $corpo$
begin
  if not exists (select 1 from public.usuarios
                  where id = p_usuario_id and nome in ('Leandro', 'Kassia', 'Marise')) then
    raise exception 'usuário inválido' using errcode = 'PT403';
  end if;
  if p_inicio is null or p_fim is null or p_fim < p_inicio
     or p_horas is null or p_horas = 'NaN'::numeric or p_horas <= 0 or p_horas >= 1000000 then
    raise exception 'período inválido' using errcode = 'PT422';
  end if;

  insert into public.custos_manutencao_periodo (id, inicio, fim, horas, atualizado_por)
  values (1, p_inicio, p_fim, p_horas, p_usuario_id)
  on conflict (id) do update
    set inicio = excluded.inicio, fim = excluded.fim, horas = excluded.horas,
        atualizado_em = now(), atualizado_por = excluded.atualizado_por;

  return jsonb_build_object('inicio', to_char(p_inicio, 'YYYY-MM-DD'),
                            'fim', to_char(p_fim, 'YYYY-MM-DD'), 'horas', p_horas::text);
end;
$corpo$;

-- ---------- Privilégios: somente o service_role executa ----------
revoke all on function public.listar_custos() from public, anon, authenticated;
revoke all on function public.salvar_custos_parametros(jsonb, bigint)
  from public, anon, authenticated;
revoke all on function public.salvar_ajuste_insumo(text, numeric, bigint)
  from public, anon, authenticated;
revoke all on function public.registrar_gasto_manutencao(date, text, numeric, bigint)
  from public, anon, authenticated;
revoke all on function public.definir_periodo_manutencao(date, date, numeric, bigint)
  from public, anon, authenticated;

grant execute on function public.listar_custos() to service_role;
grant execute on function public.salvar_custos_parametros(jsonb, bigint) to service_role;
grant execute on function public.salvar_ajuste_insumo(text, numeric, bigint) to service_role;
grant execute on function public.registrar_gasto_manutencao(date, text, numeric, bigint)
  to service_role;
grant execute on function public.definir_periodo_manutencao(date, date, numeric, bigint)
  to service_role;
