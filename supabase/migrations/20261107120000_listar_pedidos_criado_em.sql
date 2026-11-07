-- Listagem de pedidos: devolve também a data de criação (horário de Brasília), usada nos
-- indicadores da página inicial (faturamento, pedidos e ticket médio por mês).
-- Igual à versão de 20261017120000_listar_pedidos_valor_total.sql, com criado_em_local no fim.
-- Migration LOCAL: aplicar somente por pedido explícito (supabase db push).
drop function public.listar_pedidos();

create function public.listar_pedidos()
returns table (
  id               bigint,
  cliente_nome     text,
  forma_pagamento  text,
  status_pagamento text,
  prazo_entrega    date,
  criado_por_nome  text,
  produtos         text[],
  quantidade_total bigint,
  observacoes      text,
  imagem_caminho   text,
  imagem_produto   text,
  cancelado        boolean,
  valor_total      text,  -- numeric como texto: sem arredondamento no JSON
  criado_em_local  timestamp
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
    p.prazo_entrega,
    u.nome,
    coalesce(
      (select array_agg(i.produto order by i.ordem) from public.pedido_itens as i
        where i.pedido_id = p.id),
      '{}'::text[]
    ),
    (select coalesce(sum(i.quantidade), 0) from public.pedido_itens as i where i.pedido_id = p.id),
    p.observacoes,
    destaque.imagem_caminho,
    destaque.produto,
    p.cancelado_em is not null,
    p.valor_total::text,
    p.criado_em at time zone 'America/Sao_Paulo'
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

revoke all on function public.listar_pedidos() from public, anon, authenticated;
grant execute on function public.listar_pedidos() to service_role;
