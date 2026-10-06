-- Custos da empresa: cadastro persistente, com edição manual.
--
-- Esta etapa só guarda os custos (nada de precificação e nada muda em Pedidos ou Produção).
--   custos_filamentos  Material (Filamento): R$/kg por tipo. Registros iniciais: PLA, PLA Multicolor
--                      e PETG. Só o valor é editável.
--   custos_parametros  Valores únicos, um por chave: tarifa de energia (R$/kWh), consumo da
--                      impressora (W), perdas (%), mão de obra (R$/h) e manutenção e
--                      depreciação (R$/h). O custo da energia por hora NÃO é guardado: é calculado
--                      (tarifa x consumo em W / 1000), para nunca haver valores contraditórios.
--   custos_itens       Acessórios e embalagens: nome, valor total de compra e quantidade comprada.
--                      O custo unitário é uma coluna calculada (valor / quantidade), sem
--                      arredondar. Começa vazia: nenhum item de exemplo.
--
-- Precisão: numeric sem limite de casas (nada de float), em todas as colunas de valor.
-- Os valores iniciais entram com "on conflict do nothing": nunca sobrescrevem uma edição manual.
--
-- Segurança (mesmo padrão dos pedidos): RLS ligado, nenhuma policy e nenhum privilégio direto
-- nas tabelas. Só o servidor (service_role) lê e grava, e somente por funções SECURITY DEFINER
-- com search_path vazio. O responsável pela alteração vem sempre da sessão (p_usuario_id).
-- Erros das funções: PT403 usuário inválido · PT404 registro não encontrado ·
-- PT409 nome já cadastrado · PT422 valor inválido.
-- Migration LOCAL: aplicar somente por pedido explícito (supabase db push).

-- ---------- Material (Filamento) ----------
create table public.custos_filamentos (
  id            bigint      generated always as identity primary key,
  nome          text        not null check (btrim(nome) <> ''),
  abrange       text,
  valor_kg      numeric     not null check (valor_kg >= 0),
  atualizado_em timestamptz not null default now(),
  atualizado_por bigint     references public.usuarios (id) on delete restrict
);

create unique index custos_filamentos_nome_unico on public.custos_filamentos (lower(nome));

-- ---------- Valores únicos ----------
create table public.custos_parametros (
  chave         text        primary key check (chave in (
                  'energia_tarifa_kwh', 'energia_consumo_w', 'perda_percentual',
                  'mdo_hora', 'manutencao_depreciacao_hora')),
  valor         numeric     not null check (valor >= 0),
  atualizado_em timestamptz not null default now(),
  atualizado_por bigint     references public.usuarios (id) on delete restrict,
  constraint custos_parametros_perda_ate_100 check (chave <> 'perda_percentual' or valor <= 100)
);

-- ---------- Acessórios e embalagens ----------
create table public.custos_itens (
  id             bigint      generated always as identity primary key,
  tipo           text        not null check (tipo in ('acessorio', 'embalagem')),
  nome           text        not null check (
                   nome = btrim(nome) and nome <> '' and char_length(nome) <= 120),
  valor_compra   numeric     not null check (valor_compra >= 0),
  quantidade     integer     not null check (quantidade > 0),
  custo_unitario numeric     generated always as (valor_compra / quantidade) stored,
  criado_em      timestamptz not null default now(),
  atualizado_em  timestamptz not null default now(),
  atualizado_por bigint      not null references public.usuarios (id) on delete restrict
);

create unique index custos_itens_nome_unico on public.custos_itens (tipo, lower(nome));

-- ---------- Segurança: RLS sem policies e nenhum privilégio direto ----------
alter table public.custos_filamentos enable row level security;
alter table public.custos_parametros enable row level security;
alter table public.custos_itens enable row level security;

revoke all on public.custos_filamentos from anon, authenticated, service_role;
revoke all on public.custos_parametros from anon, authenticated, service_role;
revoke all on public.custos_itens from anon, authenticated, service_role;
revoke all on sequence public.custos_filamentos_id_seq from anon, authenticated, service_role;
revoke all on sequence public.custos_itens_id_seq from anon, authenticated, service_role;

-- ---------- Valores iniciais (nunca sobrescrevem edições manuais) ----------
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
  ('manutencao_depreciacao_hora', 0.88)
on conflict (chave) do nothing;

-- ---------- Leitura ----------
-- Valores numéricos saem como TEXTO: o JSON do PostgREST viraria float no cliente, e custos
-- unitários como 10/3 perderiam precisão.
create function public.listar_custos()
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
        from public.custos_itens as i), '[]'::jsonb)
  );
$corpo$;

-- ---------- Filamento: altera o valor por kg ----------
create function public.salvar_custo_filamento(
  p_id         bigint,
  p_valor_kg   numeric,
  p_usuario_id bigint
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $corpo$
declare
  v_linha public.custos_filamentos;
begin
  if not exists (select 1 from public.usuarios
                  where id = p_usuario_id and nome in ('Leandro', 'Kassia', 'Marise')) then
    raise exception 'usuário inválido' using errcode = 'PT403';
  end if;
  if p_valor_kg is null or p_valor_kg < 0 or p_valor_kg = 'NaN'::numeric
     or p_valor_kg >= 1000000000 then
    raise exception 'valor por kg inválido' using errcode = 'PT422';
  end if;

  select * into v_linha from public.custos_filamentos where id = p_id for update;
  if not found then
    raise exception 'filamento não encontrado' using errcode = 'PT404';
  end if;

  update public.custos_filamentos
     set valor_kg = p_valor_kg, atualizado_em = now(), atualizado_por = p_usuario_id
   where id = p_id
  returning * into v_linha;

  return jsonb_build_object('id', v_linha.id, 'nome', v_linha.nome, 'abrange', v_linha.abrange,
                            'valor_kg', v_linha.valor_kg::text);
end;
$corpo$;

-- ---------- Valores únicos: altera um ou mais, de uma vez (tudo ou nada) ----------
-- p_valores: {"chave": "valor", ...} com valores em texto (ponto decimal).
create function public.salvar_custos_parametros(
  p_valores    jsonb,
  p_usuario_id bigint
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $corpo$
declare
  v_chave text;
  v_texto text;
  v_valor numeric;
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
    begin
      v_valor := v_texto::numeric;
    exception when others then
      raise exception 'valor inválido para %', v_chave using errcode = 'PT422';
    end;
    if v_valor is null or v_valor = 'NaN'::numeric or v_valor < 0 or v_valor >= 1000000000
       or (v_chave = 'perda_percentual' and v_valor > 100) then
      raise exception 'valor inválido para %', v_chave using errcode = 'PT422';
    end if;

    perform 1 from public.custos_parametros where chave = v_chave for update;
    update public.custos_parametros
       set valor = v_valor, atualizado_em = now(), atualizado_por = p_usuario_id
     where chave = v_chave;
  end loop;

  return (select jsonb_object_agg(p.chave, p.valor::text) from public.custos_parametros as p);
end;
$corpo$;

-- ---------- Acessório ou embalagem: cadastra (p_id nulo) ou edita ----------
create function public.salvar_custo_item(
  p_id           bigint,
  p_tipo         text,
  p_nome         text,
  p_valor_compra numeric,
  p_quantidade   integer,
  p_usuario_id   bigint
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $corpo$
declare
  v_nome  text := btrim(p_nome, E' \t\n\r\f\x0B');
  v_linha public.custos_itens;
begin
  if not exists (select 1 from public.usuarios
                  where id = p_usuario_id and nome in ('Leandro', 'Kassia', 'Marise')) then
    raise exception 'usuário inválido' using errcode = 'PT403';
  end if;
  if p_tipo is null or p_tipo not in ('acessorio', 'embalagem')
     or v_nome is null or v_nome = '' or char_length(v_nome) > 120
     or p_valor_compra is null or p_valor_compra < 0 or p_valor_compra = 'NaN'::numeric
     or p_valor_compra >= 1000000000
     or p_quantidade is null or p_quantidade <= 0 then
    raise exception 'dados do item inválidos' using errcode = 'PT422';
  end if;

  begin
    if p_id is null then
      insert into public.custos_itens (tipo, nome, valor_compra, quantidade, atualizado_por)
      values (p_tipo, v_nome, p_valor_compra, p_quantidade, p_usuario_id)
      returning * into v_linha;
    else
      perform 1 from public.custos_itens where id = p_id and tipo = p_tipo for update;
      if not found then
        raise exception 'item não encontrado' using errcode = 'PT404';
      end if;
      update public.custos_itens
         set nome = v_nome, valor_compra = p_valor_compra, quantidade = p_quantidade,
             atualizado_em = now(), atualizado_por = p_usuario_id
       where id = p_id
      returning * into v_linha;
    end if;
  exception when unique_violation then
    raise exception 'já existe um item com este nome' using errcode = 'PT409';
  end;

  return jsonb_build_object(
    'id', v_linha.id, 'tipo', v_linha.tipo, 'nome', v_linha.nome,
    'valor_compra', v_linha.valor_compra::text, 'quantidade', v_linha.quantidade,
    'custo_unitario', v_linha.custo_unitario::text);
end;
$corpo$;

-- ---------- Privilégios: somente o service_role executa ----------
revoke all on function public.listar_custos() from public, anon, authenticated;
revoke all on function public.salvar_custo_filamento(bigint, numeric, bigint)
  from public, anon, authenticated;
revoke all on function public.salvar_custos_parametros(jsonb, bigint)
  from public, anon, authenticated;
revoke all on function public.salvar_custo_item(bigint, text, text, numeric, integer, bigint)
  from public, anon, authenticated;

grant execute on function public.listar_custos() to service_role;
grant execute on function public.salvar_custo_filamento(bigint, numeric, bigint) to service_role;
grant execute on function public.salvar_custos_parametros(jsonb, bigint) to service_role;
grant execute on function public.salvar_custo_item(bigint, text, text, numeric, integer, bigint)
  to service_role;
