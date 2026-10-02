-- Prazo de entrega do pedido e movimentação da produção nos dois sentidos permitidos.
--
-- 1. Adiciona pedidos.prazo_entrega (DATE, obrigatório, sem valor padrão).
--    O único pedido existente hoje (id 1, cliente Marise) recebe 05/10/2026. Se houver
--    qualquer outro pedido, a migration para antes de alterar o banco: o prazo de cada pedido
--    real precisa ser informado por alguém, nunca inventado. Em banco novo (sem pedidos),
--    nada é preenchido.
-- 2. criar_pedido passa a exigir o prazo; listar_pedidos, consultar_pedido e
--    listar_producao passam a devolvê-lo (listar_producao deixa de devolver quem cadastrou,
--    que saiu do card). As versões antigas são removidas: nenhuma sobrecarga fica.
-- 3. mover_etapa_producao() substitui avancar_etapa_producao(). Movimentos permitidos:
--      fila_producao      -> em_producao
--      em_producao        -> fila_producao       (voltar para a fila)
--      em_producao        -> aguardando_entrega
--      aguardando_entrega -> entregue
--    Qualquer outro (pular, outro retrocesso, sair de entregue) continua recusado.
--    Erros: PT404 pedido não encontrado · PT403 usuário inválido
--           PT409 etapa atual diferente da esperada · PT422 movimento não permitido
-- As funções mantêm SECURITY DEFINER, search_path vazio e execução só para service_role.
-- Migration LOCAL: aplicar somente por pedido explícito (supabase db push).

-- ---------- Conferência antes de qualquer alteração ----------
do $confere$
declare
  v_outros bigint;
begin
  select count(*) into v_outros
    from public.pedidos
   where not (id = 1 and lower(btrim(cliente_nome)) = 'marise');
  if v_outros > 0 then
    raise exception 'prazo_entrega: há % pedido(s) sem prazo além do pedido 1 da Marise. Nada foi alterado.', v_outros
      using hint = 'Informe o prazo de cada pedido numa migration revisada antes de aplicar esta.';
  end if;
end;
$confere$;

-- ---------- Coluna ----------
alter table public.pedidos add column prazo_entrega date;

-- Só o pedido da Marise, e só se ainda estiver sem prazo; nenhum outro campo é tocado.
update public.pedidos
   set prazo_entrega = date '2026-10-05'
 where id = 1
   and lower(btrim(cliente_nome)) = 'marise'
   and prazo_entrega is null;

do $confere_preenchimento$
begin
  if exists (select 1 from public.pedidos where prazo_entrega is null) then
    raise exception 'prazo_entrega: ainda há pedido sem prazo; a migration foi interrompida.';
  end if;
end;
$confere_preenchimento$;

alter table public.pedidos alter column prazo_entrega set not null;

-- ---------- criar_pedido: mesma lógica, agora com o prazo ----------
drop function public.criar_pedido(bigint, bigint, text, text, text, text, text, text, numeric, jsonb);

create function public.criar_pedido(
  p_id               bigint,
  p_criado_por       bigint,
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

  -- prazo ausente é barrado pelo NOT NULL da coluna (23502)
  insert into public.pedidos (
    id, cliente_nome, contato, forma_pagamento, status_pagamento, tipo_entrega, prazo_entrega,
    observacoes, valor_total, criado_por
  )
  overriding system value
  values (
    p_id, p_cliente_nome, p_contato, p_forma_pagamento, p_status_pagamento, p_tipo_entrega,
    p_prazo_entrega, p_observacoes, p_valor_total, p_criado_por
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

-- ---------- listar_pedidos: inclui o prazo (o card da listagem não muda) ----------
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

-- ---------- consultar_pedido: inclui o prazo ----------
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

-- ---------- listar_producao: prazo no card; "cadastrado por" saiu ----------
-- Foto de destaque: mesma regra da listagem (maior quantidade com imagem; empate, menor ordem).
drop function public.listar_producao();

create function public.listar_producao()
returns table (
  id               bigint,
  etapa_producao   text,
  cliente_nome     text,
  forma_pagamento  text,
  status_pagamento text,
  tipo_entrega     text,
  prazo_entrega    date,
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
    destaque.produto
  from public.pedidos as p
  left join lateral (
    select i.imagem_caminho, i.produto
      from public.pedido_itens as i
     where i.pedido_id = p.id and i.imagem_caminho is not null
     order by i.quantidade desc, i.ordem
     limit 1
  ) as destaque on true
  order by
    array_position(
      array['fila_producao', 'em_producao', 'aguardando_entrega', 'entregue'], p.etapa_producao
    ),
    p.criado_em,
    p.id;
$corpo$;

-- ---------- Movimentação de etapa (substitui avancar_etapa_producao) ----------
drop function public.avancar_etapa_producao(bigint, text, text, bigint);

create function public.mover_etapa_producao(
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

  -- Bloqueia a linha: duas movimentações simultâneas do mesmo pedido ficam em fila, e a
  -- segunda vê a etapa já alterada (e recebe PT409).
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

  -- "is not true": nova etapa NULL também é recusada
  if ((v_atual, p_nova_etapa) in (
        ('fila_producao', 'em_producao'),
        ('em_producao', 'fila_producao'),
        ('em_producao', 'aguardando_entrega'),
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

-- ---------- Privilégios (as funções recriadas voltam com os padrões do projeto) ----------
revoke all on function public.criar_pedido(
  bigint, bigint, text, text, text, text, text, date, text, numeric, jsonb
) from public, anon, authenticated;
revoke all on function public.listar_pedidos() from public, anon, authenticated;
revoke all on function public.consultar_pedido(bigint) from public, anon, authenticated;
revoke all on function public.listar_producao() from public, anon, authenticated;
revoke all on function public.mover_etapa_producao(bigint, text, text, bigint)
  from public, anon, authenticated;

grant execute on function public.criar_pedido(
  bigint, bigint, text, text, text, text, text, date, text, numeric, jsonb
) to service_role;
grant execute on function public.listar_pedidos() to service_role;
grant execute on function public.consultar_pedido(bigint) to service_role;
grant execute on function public.listar_producao() to service_role;
grant execute on function public.mover_etapa_producao(bigint, text, text, bigint)
  to service_role;
