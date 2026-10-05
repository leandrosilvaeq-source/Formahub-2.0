-- Estoque: lotes de compra de filamentos, acessórios e embalagens (cadastro e edição manual).
--
-- Cada linha é um lote de compra, com saldo disponível, data e custo próprios: o mesmo insumo
-- pode aparecer em várias compras. O estoque começa vazio (nenhum dado é inserido aqui).
--
-- 1. estoque_filamentos: cor, material, tipo, marca, peso disponível (g, com decimais),
--    data de compra e custo por kg.
-- 2. estoque_itens: acessórios e embalagens (categoria), com nome, quantidade disponível
--    (inteira), data de compra e custo por unidade.
-- 3. listar_estoque(): devolve os dois conjuntos (números como texto, para não passar por
--    float). salvar_estoque_filamento() e salvar_estoque_item(): p_id NULL cadastra; com id,
--    edita o lote, guardando horário e responsável.
-- Sem baixa automática, exclusão, Compras ou integração com Custos nesta etapa.
-- Erros: PT403 usuário inválido · PT404 lote não encontrado. Valores inválidos são recusados
-- pelos CHECK das tabelas.
-- RLS ligado sem policies, nenhum privilégio direto nas tabelas; funções com SECURITY DEFINER,
-- search_path vazio e execução só para service_role.
-- Criada em paralelo a 20261007120000 e 20261008120000 (já aplicadas no remoto, ausentes neste
-- checkout); não depende delas.
-- Migration LOCAL: aplicar somente por pedido explícito (supabase db push).

-- ---------- Filamentos ----------
create table public.estoque_filamentos (
  id                bigint        generated always as identity primary key,
  cor               text          not null
                    check (char_length(cor) between 1 and 60 and cor = btrim(cor)),
  material          text          not null
                    check (char_length(material) between 1 and 60 and material = btrim(material)),
  tipo              text          not null
                    check (tipo in ('solido', 'velvet', 'silk', 'bicolor', 'tricolor')),
  marca             text          not null
                    check (char_length(marca) between 1 and 60 and marca = btrim(marca)),
  peso_disponivel_g numeric(12, 2) not null check (peso_disponivel_g >= 0),
  data_compra       date          not null,
  custo_kg          numeric(12, 2) not null check (custo_kg >= 0),
  criado_em         timestamptz   not null default now(),
  criado_por        bigint        not null references public.usuarios (id) on delete restrict,
  atualizado_em     timestamptz   not null default now(),
  atualizado_por    bigint        not null references public.usuarios (id) on delete restrict
);

-- ---------- Acessórios e embalagens ----------
create table public.estoque_itens (
  id                    bigint        generated always as identity primary key,
  categoria             text          not null check (categoria in ('acessorio', 'embalagem')),
  nome                  text          not null
                        check (char_length(nome) between 1 and 60 and nome = btrim(nome)),
  quantidade_disponivel integer       not null check (quantidade_disponivel >= 0),
  data_compra           date          not null,
  custo_unitario        numeric(12, 2) not null check (custo_unitario >= 0),
  criado_em             timestamptz   not null default now(),
  criado_por            bigint        not null references public.usuarios (id) on delete restrict,
  atualizado_em         timestamptz   not null default now(),
  atualizado_por        bigint        not null references public.usuarios (id) on delete restrict
);

create index estoque_itens_categoria_idx on public.estoque_itens (categoria);

-- ---------- Segurança: RLS sem policies e nenhum privilégio direto ----------
alter table public.estoque_filamentos enable row level security;
alter table public.estoque_itens enable row level security;

revoke all on public.estoque_filamentos from anon, authenticated, service_role;
revoke all on public.estoque_itens from anon, authenticated, service_role;
revoke all on sequence public.estoque_filamentos_id_seq from anon, authenticated, service_role;
revoke all on sequence public.estoque_itens_id_seq from anon, authenticated, service_role;

-- ---------- Leitura ----------
create function public.listar_estoque()
returns jsonb
language sql
stable
security definer
set search_path = ''
as $corpo$
  select jsonb_build_object(
    'filamentos', coalesce(
      (select jsonb_agg(jsonb_build_object(
                'id', f.id,
                'cor', f.cor,
                'material', f.material,
                'tipo', f.tipo,
                'marca', f.marca,
                'peso_disponivel_g', f.peso_disponivel_g::text,
                'data_compra', f.data_compra,
                'custo_kg', f.custo_kg::text
              ) order by f.data_compra desc, f.id desc)
         from public.estoque_filamentos as f),
      '[]'::jsonb),
    'itens', coalesce(
      (select jsonb_agg(jsonb_build_object(
                'id', i.id,
                'categoria', i.categoria,
                'nome', i.nome,
                'quantidade_disponivel', i.quantidade_disponivel,
                'data_compra', i.data_compra,
                'custo_unitario', i.custo_unitario::text
              ) order by i.data_compra desc, i.id desc)
         from public.estoque_itens as i),
      '[]'::jsonb)
  );
$corpo$;

-- ---------- Cadastro e edição de filamento ----------
create function public.salvar_estoque_filamento(
  p_id                bigint,
  p_cor               text,
  p_material          text,
  p_tipo              text,
  p_marca             text,
  p_peso_disponivel_g numeric,
  p_data_compra       date,
  p_custo_kg          numeric,
  p_usuario_id        bigint
)
returns bigint
language plpgsql
security definer
set search_path = ''
as $corpo$
declare
  v_id bigint;
begin
  if not exists (select 1 from public.usuarios
                  where id = p_usuario_id and nome in ('Leandro', 'Kassia', 'Marise')) then
    raise exception 'usuário inválido' using errcode = 'PT403';
  end if;

  if p_id is null then
    insert into public.estoque_filamentos
      (cor, material, tipo, marca, peso_disponivel_g, data_compra, custo_kg,
       criado_por, atualizado_por)
    values
      (p_cor, p_material, p_tipo, p_marca, p_peso_disponivel_g, p_data_compra, p_custo_kg,
       p_usuario_id, p_usuario_id)
    returning id into v_id;
  else
    update public.estoque_filamentos
       set cor = p_cor,
           material = p_material,
           tipo = p_tipo,
           marca = p_marca,
           peso_disponivel_g = p_peso_disponivel_g,
           data_compra = p_data_compra,
           custo_kg = p_custo_kg,
           atualizado_em = now(),
           atualizado_por = p_usuario_id
     where id = p_id
    returning id into v_id;
    if v_id is null then
      raise exception 'lote não encontrado' using errcode = 'PT404';
    end if;
  end if;
  return v_id;
end;
$corpo$;

-- ---------- Cadastro e edição de acessório ou embalagem ----------
create function public.salvar_estoque_item(
  p_id                    bigint,
  p_categoria             text,
  p_nome                  text,
  p_quantidade_disponivel integer,
  p_data_compra           date,
  p_custo_unitario        numeric,
  p_usuario_id            bigint
)
returns bigint
language plpgsql
security definer
set search_path = ''
as $corpo$
declare
  v_id bigint;
begin
  if not exists (select 1 from public.usuarios
                  where id = p_usuario_id and nome in ('Leandro', 'Kassia', 'Marise')) then
    raise exception 'usuário inválido' using errcode = 'PT403';
  end if;

  if p_id is null then
    insert into public.estoque_itens
      (categoria, nome, quantidade_disponivel, data_compra, custo_unitario,
       criado_por, atualizado_por)
    values
      (p_categoria, p_nome, p_quantidade_disponivel, p_data_compra, p_custo_unitario,
       p_usuario_id, p_usuario_id)
    returning id into v_id;
  else
    -- A categoria não muda: editar um acessório pelo endereço de embalagens dá PT404.
    update public.estoque_itens
       set nome = p_nome,
           quantidade_disponivel = p_quantidade_disponivel,
           data_compra = p_data_compra,
           custo_unitario = p_custo_unitario,
           atualizado_em = now(),
           atualizado_por = p_usuario_id
     where id = p_id and categoria = p_categoria
    returning id into v_id;
    if v_id is null then
      raise exception 'lote não encontrado' using errcode = 'PT404';
    end if;
  end if;
  return v_id;
end;
$corpo$;

-- ---------- Privilégios ----------
revoke all on function public.listar_estoque() from public, anon, authenticated;
revoke all on function public.salvar_estoque_filamento(
  bigint, text, text, text, text, numeric, date, numeric, bigint
) from public, anon, authenticated;
revoke all on function public.salvar_estoque_item(
  bigint, text, text, integer, date, numeric, bigint
) from public, anon, authenticated;

grant execute on function public.listar_estoque() to service_role;
grant execute on function public.salvar_estoque_filamento(
  bigint, text, text, text, text, numeric, date, numeric, bigint
) to service_role;
grant execute on function public.salvar_estoque_item(
  bigint, text, text, integer, date, numeric, bigint
) to service_role;
