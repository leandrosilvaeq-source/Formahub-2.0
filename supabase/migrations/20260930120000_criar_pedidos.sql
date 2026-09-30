-- Pedidos do FormaHub 2.0: pedido, itens e bucket privado das imagens de referência.
--
-- Somente o servidor (chave secreta / role service_role) grava pedidos, e só por funções:
-- as tabelas têm RLS ligado, nenhuma policy e nenhum privilégio direto para ninguém.
-- Migration LOCAL: aplicar somente por pedido explícito (supabase db push).
--
-- Gravação sem estado parcial:
--   1. reservar_id_pedido() devolve o id do próximo pedido (nada é gravado);
--   2. o servidor envia as imagens ao Storage usando esse id no caminho;
--   3. criar_pedido() grava o pedido e todos os itens numa única transação.
-- Se o passo 2 ou 3 falhar, o servidor remove as imagens enviadas; o id reservado só vira
-- um salto na sequência.

-- ---------- Pedidos ----------
create table public.pedidos (
  id              bigint        generated always as identity primary key,
  cliente_nome    text          not null check (btrim(cliente_nome) <> ''),
  contato         text          check (contato is null or btrim(contato) <> ''),
  forma_pagamento text          not null check (forma_pagamento in ('pix', 'dinheiro', 'cartao')),
  tipo_entrega    text          not null check (tipo_entrega in ('entrega', 'retirada')),
  observacoes     text,
  valor_total     numeric       not null check (valor_total >= 0),
  criado_por      bigint        not null references public.usuarios (id) on delete restrict,
  criado_em       timestamptz   not null default now(),
  atualizado_em   timestamptz   not null default now()
);

-- ---------- Itens ----------
-- imagem_caminho guarda só o caminho do arquivo no bucket pedido-imagens, no padrão
--   pedidos/{pedido_id}/itens/{ordem}/{uuid}.{extensao}   (extensao: png, jpg ou webp)
-- Nunca Base64 nem o conteúdo da imagem.
create table public.pedido_itens (
  id             bigint      generated always as identity primary key,
  pedido_id      bigint      not null references public.pedidos (id) on delete restrict,
  produto        text        not null check (btrim(produto) <> ''),
  quantidade     integer     not null check (quantidade > 0),
  valor_unitario numeric     not null check (valor_unitario >= 0),
  subtotal       numeric     not null check (subtotal >= 0),
  imagem_caminho text,
  ordem          integer     not null check (ordem > 0),
  criado_em      timestamptz not null default now(),
  -- a ordem não se repete dentro do pedido (o índice desta regra também serve à FK pedido_id)
  constraint pedido_itens_ordem_unica unique (pedido_id, ordem),
  constraint pedido_itens_subtotal_coerente check (subtotal = quantidade * valor_unitario),
  constraint pedido_itens_imagem_caminho_padrao check (
    imagem_caminho is null
    or imagem_caminho ~ ('^pedidos/' || pedido_id::text || '/itens/' || ordem::text
                         || '/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
                         || '\.(png|jpg|webp)$')
  )
);

-- ---------- Segurança: RLS sem policies e nenhum privilégio direto ----------
alter table public.pedidos enable row level security;
alter table public.pedido_itens enable row level security;

revoke all on public.pedidos from anon, authenticated, service_role;
revoke all on public.pedido_itens from anon, authenticated, service_role;
-- As sequences das identity também recebem os privilégios padrão do projeto.
revoke all on sequence public.pedidos_id_seq from anon, authenticated, service_role;
revoke all on sequence public.pedido_itens_id_seq from anon, authenticated, service_role;

-- ---------- Reserva do id do pedido ----------
create function public.reservar_id_pedido()
returns bigint
language sql
security definer
set search_path = ''
as $corpo$
  select nextval(pg_get_serial_sequence('public.pedidos', 'id'));
$corpo$;

-- ---------- Gravação atômica do pedido com os itens ----------
-- p_itens: array JSON de {ordem, produto, quantidade, valor_unitario, subtotal, imagem_caminho}.
-- Qualquer problema (item inválido, ordem repetida, total diferente da soma) desfaz tudo.
create function public.criar_pedido(
  p_id              bigint,
  p_criado_por      bigint,
  p_cliente_nome    text,
  p_contato         text,
  p_forma_pagamento text,
  p_tipo_entrega    text,
  p_observacoes     text,
  p_valor_total     numeric,
  p_itens           jsonb
)
returns bigint
language plpgsql
security definer
set search_path = ''
as $corpo$
declare
  v_soma numeric;
begin
  -- só aceita um id já reservado (evita colisão com ids futuros da sequência)
  if p_id is null or p_id < 1 or p_id > coalesce(
       pg_sequence_last_value(pg_get_serial_sequence('public.pedidos', 'id')::regclass), 0) then
    raise exception 'id de pedido não reservado' using errcode = '22023';
  end if;

  if p_itens is null or jsonb_typeof(p_itens) <> 'array' or jsonb_array_length(p_itens) = 0 then
    raise exception 'pedido sem itens' using errcode = '23514';
  end if;

  -- cada item é um objeto; campos ausentes viram NULL e são barrados pelas restrições da tabela
  if exists (select 1 from jsonb_array_elements(p_itens) as e where jsonb_typeof(e) <> 'object') then
    raise exception 'item de pedido inválido' using errcode = '22023';
  end if;

  insert into public.pedidos (
    id, cliente_nome, contato, forma_pagamento, tipo_entrega, observacoes, valor_total, criado_por
  )
  overriding system value
  values (
    p_id, p_cliente_nome, p_contato, p_forma_pagamento, p_tipo_entrega, p_observacoes,
    p_valor_total, p_criado_por
  );

  insert into public.pedido_itens (
    pedido_id, ordem, produto, quantidade, valor_unitario, subtotal, imagem_caminho
  )
  select p_id, i.ordem, i.produto, i.quantidade, i.valor_unitario, i.subtotal, i.imagem_caminho
    from jsonb_to_recordset(p_itens) as i(
      ordem integer, produto text, quantidade integer, valor_unitario numeric,
      subtotal numeric, imagem_caminho text
    );

  select sum(subtotal) into v_soma from public.pedido_itens where pedido_id = p_id;
  if v_soma is distinct from p_valor_total then
    raise exception 'valor_total diferente da soma dos itens' using errcode = '23514';
  end if;

  return p_id;
end;
$corpo$;

revoke all on function public.reservar_id_pedido() from public, anon, authenticated;
revoke all on function public.criar_pedido(bigint, bigint, text, text, text, text, text, numeric, jsonb)
  from public, anon, authenticated;
grant execute on function public.reservar_id_pedido() to service_role;
grant execute on function public.criar_pedido(bigint, bigint, text, text, text, text, text, numeric, jsonb)
  to service_role;

-- ---------- Storage: bucket privado das imagens de referência ----------
-- Privado, até 10 MB, só PNG, JPEG e WebP. Nenhuma policy: só o servidor (service_role, que
-- ignora RLS) envia e remove arquivos. Caminho: pedidos/{pedido_id}/itens/{ordem}/{uuid}.{extensao}
do $verifica$
begin
  if exists (select 1 from storage.buckets where id = 'pedido-imagens') then
    raise exception 'o bucket pedido-imagens já existe; revise-o antes de aplicar esta migration';
  end if;
end;
$verifica$;

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
  'pedido-imagens',
  'pedido-imagens',
  false,
  10485760,
  array['image/png', 'image/jpeg', 'image/webp']
);
