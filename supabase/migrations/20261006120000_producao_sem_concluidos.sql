-- Produção: pedido concluído sai do quadro.
--
-- Concluído = etapa_producao 'entregue' E status_pagamento 'pago'. O estado é derivado das
-- duas colunas: nenhuma coluna nova, nenhum pedido alterado ou apagado. Entregue com pagamento
-- pendente continua na coluna Entregue; pago em qualquer etapa anterior continua no quadro.
-- O pedido concluído continua em listar_pedidos e consultar_pedido (que não mudam).
--
-- Só listar_producao muda: mesma assinatura, mesmas colunas de retorno, SECURITY DEFINER e
-- search_path vazio. "create or replace" troca o corpo sem criar outra versão (sem
-- sobrecarga) e mantém as permissões, que são reafirmadas abaixo.
-- Migration LOCAL: aplicar somente por pedido explícito (supabase db push).

create or replace function public.listar_producao()
returns table (
  id                                      bigint,
  etapa_producao                          text,
  cliente_nome                            text,
  forma_pagamento                         text,
  status_pagamento                        text,
  tipo_entrega                            text,
  prazo_entrega                           date,
  produtos                                text[],
  quantidade_total                        bigint,
  observacoes                             text,
  imagem_caminho                          text,
  imagem_produto                          text,
  comentario_producao                     text,
  comentario_producao_atualizado_em       timestamp,
  comentario_producao_atualizado_por_nome text
)
language sql
stable
security definer
set search_path = ''
as $corpo$
  select
    p.id,
    p.etapa_producao,
    p.cliente_nome,
    p.forma_pagamento,
    p.status_pagamento,
    p.tipo_entrega,
    p.prazo_entrega,
    coalesce(
      (select array_agg(i.produto order by i.ordem) from public.pedido_itens as i
        where i.pedido_id = p.id),
      '{}'::text[]
    ),
    (select coalesce(sum(i.quantidade), 0) from public.pedido_itens as i where i.pedido_id = p.id),
    p.observacoes,
    destaque.imagem_caminho,
    destaque.produto,
    p.comentario_producao,
    p.comentario_producao_atualizado_em at time zone 'America/Sao_Paulo',
    autor.nome
  from public.pedidos as p
  left join public.usuarios as autor on autor.id = p.comentario_producao_atualizado_por
  left join lateral (
    select i.imagem_caminho, i.produto
      from public.pedido_itens as i
     where i.pedido_id = p.id and i.imagem_caminho is not null
     order by i.quantidade desc, i.ordem
     limit 1
  ) as destaque on true
  -- Concluído (entregue e pago) fica de fora. "is not true": um valor NULL nunca esconde o
  -- pedido.
  where (p.etapa_producao = 'entregue' and p.status_pagamento = 'pago') is not true
  order by
    array_position(
      array['fila_producao', 'em_producao', 'aguardando_entrega', 'entregue'], p.etapa_producao
    ),
    p.criado_em,
    p.id;
$corpo$;

-- ---------- Privilégios (os mesmos de antes) ----------
revoke all on function public.listar_producao() from public, anon, authenticated;
grant execute on function public.listar_producao() to service_role;
