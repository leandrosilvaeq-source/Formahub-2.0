-- Listagem de pedidos: devolve também o valor total do pedido (nova coluna da tabela).
-- Igual à versão de 20261015120000_editar_cancelar_pedidos.sql, com valor_total no fim.

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
  valor_total      text  -- numeric como texto: sem arredondamento no JSON
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
    p.valor_total::text
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
