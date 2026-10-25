-- Editar e cancelar pedidos do FormaHub 2.0.
--
-- Cancelar = marcar o pedido como cancelado (cancelado_em / cancelado_por). Nada é apagado: o
-- pedido continua consultável, mas sai da Produção e da listagem padrão de pedidos.
-- Só se edita ou cancela um pedido que ainda não foi entregue (etapa diferente de 'entregue').
--
-- 1. pedidos.cancelado_em e cancelado_por.
-- 2. Trigger: pedido cancelado não aceita mais nenhuma alteração (nem etapa, nem pagamento,
--    nem comentário). Código PT422, o mesmo das alterações recusadas.
-- 3. A regra do caminho da imagem deixa de exigir que o número depois de /itens/ seja a ordem do
--    item: ao editar, os itens são renumerados e a imagem mantida continua no mesmo arquivo.
-- 4. listar_pedidos (ganha a coluna cancelado), listar_producao (sem cancelados) e
--    consultar_pedido (ganha etapa_producao e cancelado_em).
-- 5. editar_pedido(): regrava dados e itens numa única transação, com bloqueio de linha, e
--    devolve os caminhos de imagem que deixaram de ser usados (o servidor os remove do Storage).
-- 6. cancelar_pedido().
-- Códigos de erro (o PostgREST devolve o código e o status HTTP correspondente):
--   PT404 pedido não encontrado · PT403 usuário inválido
--   PT409 pedido já cancelado (só em cancelar_pedido)
--   PT422 pedido entregue ou cancelado: não pode ser alterado
-- Migration LOCAL: aplicar somente por pedido explícito (supabase db push).

-- ---------- Colunas ----------
alter table public.pedidos
  add column cancelado_em timestamptz,
  add column cancelado_por bigint references public.usuarios (id) on delete restrict;

alter table public.pedidos
  add constraint pedidos_cancelamento_auditoria_check
  check ((cancelado_em is null) = (cancelado_por is null));

-- ---------- Pedido cancelado não muda ----------
create function public.bloquear_pedido_cancelado()
returns trigger
language plpgsql
set search_path = ''
as $corpo$
begin
  if old.cancelado_em is not null then
    raise exception 'pedido cancelado não pode ser alterado' using errcode = 'PT422';
  end if;
  return new;
end;
$corpo$;

create trigger pedidos_bloquear_cancelado
  before update on public.pedidos
  for each row execute function public.bloquear_pedido_cancelado();

-- ---------- Caminho da imagem sem amarrar o número à ordem ----------
alter table public.pedido_itens drop constraint pedido_itens_imagem_caminho_padrao;

alter table public.pedido_itens
  add constraint pedido_itens_imagem_caminho_padrao check (
    imagem_caminho is null
    or imagem_caminho ~ ('^pedidos/' || pedido_id::text || '/itens/[0-9]+'
                         || '/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
                         || '\.(png|jpg|webp)$')
  );

-- ---------- Listagem de pedidos: inclui a marca de cancelado ----------
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
  cancelado        boolean
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
    p.cancelado_em is not null
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

-- ---------- Produção: pedido cancelado não aparece ----------
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
  where p.cancelado_em is null
    and (p.etapa_producao = 'entregue' and p.status_pagamento = 'pago') is not true
  order by
    array_position(
      array['fila_producao', 'em_producao', 'aguardando_entrega', 'entregue'], p.etapa_producao
    ),
    p.criado_em,
    p.id;
$corpo$;

-- ---------- Detalhe do pedido: etapa e cancelamento ----------
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
    'prazo_entrega', p.prazo_entrega,
    'observacoes', p.observacoes,
    'valor_total', p.valor_total::text,
    'criado_em_local', p.criado_em at time zone 'America/Sao_Paulo',
    'criado_por_nome', u.nome,
    'etapa_producao', p.etapa_producao,
    'cancelado_em_local', p.cancelado_em at time zone 'America/Sao_Paulo',
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

-- ---------- Edição ----------
-- p_itens: array JSON de {ordem, produto, quantidade, valor_unitario, subtotal, imagem_caminho}
-- (todos os itens do pedido: os itens antigos são substituídos por estes).
-- Devolve {id, imagens_removidas}: caminhos dos itens antigos que não aparecem nos novos.
create function public.editar_pedido(
  p_pedido_id        bigint,
  p_usuario_id       bigint,
  p_cliente_nome     text,
  p_contato          text,
  p_forma_pagamento  text,
  p_status_pagamento text,
  p_tipo_entrega     text,
  p_prazo_entrega    date,
  p_observacoes      text,
  p_valor_total      numeric,
  p_itens            jsonb
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $corpo$
declare
  v_usuario   text;
  v_etapa     text;
  v_cancelado timestamptz;
  v_status    text;
  v_soma      numeric;
  v_removidas text[];
begin
  select nome into v_usuario
    from public.usuarios
   where id = p_usuario_id and nome in ('Leandro', 'Kassia', 'Marise');
  if v_usuario is null then
    raise exception 'usuário inválido' using errcode = 'PT403';
  end if;

  -- Bloqueia a linha: duas edições (ou uma edição e um cancelamento) ficam em fila.
  select etapa_producao, cancelado_em, status_pagamento into v_etapa, v_cancelado, v_status
    from public.pedidos
   where id = p_pedido_id
     for update;
  if not found then
    raise exception 'pedido não encontrado' using errcode = 'PT404';
  end if;
  if v_cancelado is not null or v_etapa = 'entregue' then
    raise exception 'pedido não pode ser editado' using errcode = 'PT422';
  end if;

  if p_itens is null or jsonb_typeof(p_itens) <> 'array' or jsonb_array_length(p_itens) = 0 then
    raise exception 'pedido sem itens' using errcode = '23514';
  end if;
  if exists (select 1 from jsonb_array_elements(p_itens) as e where jsonb_typeof(e) <> 'object') then
    raise exception 'item de pedido inválido' using errcode = '22023';
  end if;

  update public.pedidos
     set cliente_nome = p_cliente_nome,
         contato = p_contato,
         forma_pagamento = p_forma_pagamento,
         status_pagamento = p_status_pagamento,
         tipo_entrega = p_tipo_entrega,
         prazo_entrega = p_prazo_entrega,
         observacoes = p_observacoes,
         valor_total = p_valor_total,
         atualizado_em = now(),
         pagamento_atualizado_em = case when p_status_pagamento is distinct from v_status
                                        then now() else pagamento_atualizado_em end,
         pagamento_atualizado_por = case when p_status_pagamento is distinct from v_status
                                         then p_usuario_id else pagamento_atualizado_por end
   where id = p_pedido_id;

  select coalesce(array_agg(imagem_caminho), '{}'::text[]) into v_removidas
    from public.pedido_itens
   where pedido_id = p_pedido_id and imagem_caminho is not null;

  delete from public.pedido_itens where pedido_id = p_pedido_id;

  insert into public.pedido_itens (
    pedido_id, ordem, produto, quantidade, valor_unitario, subtotal, imagem_caminho
  )
  select p_pedido_id, i.ordem, i.produto, i.quantidade, i.valor_unitario, i.subtotal,
         i.imagem_caminho
    from jsonb_to_recordset(p_itens) as i(
      ordem integer, produto text, quantidade integer, valor_unitario numeric,
      subtotal numeric, imagem_caminho text
    );

  select sum(subtotal) into v_soma from public.pedido_itens where pedido_id = p_pedido_id;
  if v_soma is distinct from p_valor_total then
    raise exception 'valor_total diferente da soma dos itens' using errcode = '23514';
  end if;

  -- Só deixam o Storage os arquivos que nenhum item novo usa.
  select coalesce(array_agg(c), '{}'::text[]) into v_removidas
    from unnest(v_removidas) as c
   where c not in (
     select imagem_caminho from public.pedido_itens
      where pedido_id = p_pedido_id and imagem_caminho is not null
   );

  return jsonb_build_object('id', p_pedido_id, 'imagens_removidas', to_jsonb(v_removidas));
end;
$corpo$;

-- ---------- Cancelamento ----------
create function public.cancelar_pedido(
  p_pedido_id  bigint,
  p_usuario_id bigint
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $corpo$
declare
  v_usuario   text;
  v_etapa     text;
  v_cancelado timestamptz;
  v_quando    timestamptz;
begin
  select nome into v_usuario
    from public.usuarios
   where id = p_usuario_id and nome in ('Leandro', 'Kassia', 'Marise');
  if v_usuario is null then
    raise exception 'usuário inválido' using errcode = 'PT403';
  end if;

  select etapa_producao, cancelado_em into v_etapa, v_cancelado
    from public.pedidos
   where id = p_pedido_id
     for update;
  if not found then
    raise exception 'pedido não encontrado' using errcode = 'PT404';
  end if;
  if v_cancelado is not null then
    raise exception 'pedido já cancelado' using errcode = 'PT409';
  end if;
  if v_etapa = 'entregue' then
    raise exception 'pedido entregue não pode ser cancelado' using errcode = 'PT422';
  end if;

  v_quando := now();
  update public.pedidos
     set cancelado_em = v_quando,
         cancelado_por = p_usuario_id,
         atualizado_em = v_quando
   where id = p_pedido_id;

  return jsonb_build_object(
    'id', p_pedido_id,
    'cancelado_em', v_quando at time zone 'America/Sao_Paulo',
    'cancelado_por_nome', v_usuario
  );
end;
$corpo$;

revoke all on function public.bloquear_pedido_cancelado() from public, anon, authenticated;
revoke all on function public.listar_pedidos() from public, anon, authenticated;
revoke all on function public.listar_producao() from public, anon, authenticated;
revoke all on function public.consultar_pedido(bigint) from public, anon, authenticated;
revoke all on function public.editar_pedido(
  bigint, bigint, text, text, text, text, text, date, text, numeric, jsonb
) from public, anon, authenticated;
revoke all on function public.cancelar_pedido(bigint, bigint) from public, anon, authenticated;

grant execute on function public.listar_pedidos() to service_role;
grant execute on function public.listar_producao() to service_role;
grant execute on function public.consultar_pedido(bigint) to service_role;
grant execute on function public.editar_pedido(
  bigint, bigint, text, text, text, text, text, date, text, numeric, jsonb
) to service_role;
grant execute on function public.cancelar_pedido(bigint, bigint) to service_role;
