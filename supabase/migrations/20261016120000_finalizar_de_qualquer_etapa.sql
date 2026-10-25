-- Finalizar pedido de qualquer etapa (pela lista de pedidos).
--
-- Antes, o pedido só chegava a 'entregue' passando por aguardando_entrega. Agora mover_etapa_producao
-- também aceita fila_producao -> entregue e em_producao -> entregue. Os demais movimentos e
-- os códigos de erro não mudam (PT403, PT404, PT409, PT422). Pedido cancelado continua
-- bloqueado pelo trigger da migration 20261015120000.
-- Migration LOCAL: aplicar somente por pedido explícito (supabase db push).

create or replace function public.mover_etapa_producao(
  p_pedido_id      bigint,
  p_etapa_esperada text,
  p_nova_etapa     text,
  p_usuario_id     bigint
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $corpo$
declare
  v_usuario text;
  v_atual   text;
  v_quando  timestamptz;
begin
  select nome into v_usuario
    from public.usuarios
   where id = p_usuario_id and nome in ('Leandro', 'Kassia', 'Marise');
  if v_usuario is null then
    raise exception 'usuário inválido' using errcode = 'PT403';
  end if;

  select etapa_producao into v_atual
    from public.pedidos
   where id = p_pedido_id
     for update;
  if not found then
    raise exception 'pedido não encontrado' using errcode = 'PT404';
  end if;

  if v_atual is distinct from p_etapa_esperada then
    raise exception 'etapa do pedido foi alterada' using errcode = 'PT409';
  end if;

  if ((v_atual, p_nova_etapa) in (
        ('fila_producao', 'em_producao'),
        ('fila_producao', 'entregue'),
        ('em_producao', 'fila_producao'),
        ('em_producao', 'aguardando_entrega'),
        ('em_producao', 'entregue'),
        ('aguardando_entrega', 'entregue')
      )) is not true then
    raise exception 'movimento de etapa não permitido' using errcode = 'PT422';
  end if;

  v_quando := now();
  update public.pedidos
     set etapa_producao = p_nova_etapa,
         etapa_atualizada_em = v_quando,
         etapa_atualizada_por = p_usuario_id
   where id = p_pedido_id;

  return jsonb_build_object(
    'id', p_pedido_id,
    'etapa_producao', p_nova_etapa,
    'etapa_atualizada_em', v_quando at time zone 'America/Sao_Paulo',
    'etapa_atualizada_por_nome', v_usuario
  );
end;
$corpo$;

revoke all on function public.mover_etapa_producao(bigint, text, text, bigint)
  from public, anon, authenticated;
grant execute on function public.mover_etapa_producao(bigint, text, text, bigint)
  to service_role;
