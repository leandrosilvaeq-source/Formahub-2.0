-- Status do pagamento do pedido (pendente ou pago) e listagem em cards.
--
-- 1. Adiciona pedidos.status_pagamento; os pedidos já existentes ficam como 'pendente'.
-- 2. criar_pedido passa a exigir o status (a versão antiga é removida: nenhuma sobrecarga fica).
-- 3. listar_pedidos passa a devolver o que o card mostra: pagamento e status, produtos,
--    unidades, observações e uma única foto de destaque (escolhida no banco).
-- 4. consultar_pedido passa a devolver também o status.
-- As funções mantêm SECURITY DEFINER, search_path vazio e execução só para service_role.
-- Migration LOCAL: aplicar somente por pedido explícito (supabase db push).

-- ---------- Coluna ----------
alter table public.pedidos add column status_pagamento text;

update public.pedidos set status_pagamento = 'pendente' where status_pagamento is null;

alter table public.pedidos alter column status_pagamento set not null;

alter table public.pedidos
  add constraint pedidos_status_pagamento_check
  check (status_pagamento in ('pendente', 'pago'));

-- ---------- criar_pedido: mesma lógica, agora com o status ----------
drop function public.criar_pedido(bigint, bigint, text, text, text, text, text, numeric, jsonb);

create function public.criar_pedido(
  p_id               bigint,
  p_criado_por       bigint,
  p_cliente_nome     text,
  p_contato          text,
  p_forma_pagamento  text,
  p_status_pagamento text,
  p_tipo_entrega     text,
  p_observacoes      text,
  p_valor_total      numeric,
  p_itens            jsonb
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
    id, cliente_nome, contato, forma_pagamento, status_pagamento, tipo_entrega, observacoes,
    valor_total, criado_por
  )
  overriding system value
  values (
    p_id, p_cliente_nome, p_contato, p_forma_pagamento, p_status_pagamento, p_tipo_entrega,
    p_observacoes, p_valor_total, p_criado_por
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

-- ---------- listar_pedidos: o que o card mostra ----------
-- Foto de destaque: entre os itens com imagem, o de maior quantidade; no empate, o primeiro
-- pela ordem. Só o caminho dessa imagem sai daqui (e o nome do produto, para o texto
-- alternativo); o servidor gera no máximo uma URL assinada por pedido.
drop function public.listar_pedidos();

create function public.listar_pedidos()
returns table (
  id               bigint,
  cliente_nome     text,
  forma_pagamento  text,
  status_pagamento text,
  criado_por_nome  text,
  produtos         text[],
  quantidade_total bigint,
  observacoes      text,
  imagem_caminho   text,
  imagem_produto   text
)
language sql
stable
security definer
set search_path = ''
as $corpo$
  select
    p.id,
    p.cliente_nome,
    p.forma_pagamento,
    p.status_pagamento,
    u.nome,
    coalesce(
      (select array_agg(i.produto order by i.ordem) from public.pedido_itens as i
        where i.pedido_id = p.id),
      '{}'::text[]
    ),
    (select coalesce(sum(i.quantidade), 0) from public.pedido_itens as i where i.pedido_id = p.id),
    p.observacoes,
    destaque.imagem_caminho,
    destaque.produto
  from public.pedidos as p
  join public.usuarios as u on u.id = p.criado_por
  left join lateral (
    select i.imagem_caminho, i.produto
      from public.pedido_itens as i
     where i.pedido_id = p.id and i.imagem_caminho is not null
     order by i.quantidade desc, i.ordem
     limit 1
  ) as destaque on true
  order by p.criado_em desc, p.id desc;
$corpo$;

-- ---------- consultar_pedido: inclui o status ----------
create or replace function public.consultar_pedido(p_id bigint)
returns jsonb
language sql
stable
security definer
set search_path = ''
as $corpo$
  select jsonb_build_object(
    'id', p.id,
    'cliente_nome', p.cliente_nome,
    'contato', p.contato,
    'forma_pagamento', p.forma_pagamento,
    'status_pagamento', p.status_pagamento,
    'tipo_entrega', p.tipo_entrega,
    'observacoes', p.observacoes,
    'valor_total', p.valor_total::text,
    'criado_em_local', p.criado_em at time zone 'America/Sao_Paulo',
    'criado_por_nome', u.nome,
    'itens', (
      select coalesce(
        jsonb_agg(
          jsonb_build_object(
            'ordem', i.ordem,
            'produto', i.produto,
            'quantidade', i.quantidade,
            'valor_unitario', i.valor_unitario::text,
            'subtotal', i.subtotal::text,
            'imagem_caminho', i.imagem_caminho
          )
          order by i.ordem
        ),
        '[]'::jsonb
      )
      from public.pedido_itens as i
      where i.pedido_id = p.id
    )
  )
  from public.pedidos as p
  join public.usuarios as u on u.id = p.criado_por
  where p.id = p_id;
$corpo$;

-- ---------- Privilégios (as funções recriadas voltam com os padrões do projeto) ----------
revoke all on function public.criar_pedido(
  bigint, bigint, text, text, text, text, text, text, numeric, jsonb
) from public, anon, authenticated;
revoke all on function public.listar_pedidos() from public, anon, authenticated;
revoke all on function public.consultar_pedido(bigint) from public, anon, authenticated;

grant execute on function public.criar_pedido(
  bigint, bigint, text, text, text, text, text, text, numeric, jsonb
) to service_role;
grant execute on function public.listar_pedidos() to service_role;
grant execute on function public.consultar_pedido(bigint) to service_role;
