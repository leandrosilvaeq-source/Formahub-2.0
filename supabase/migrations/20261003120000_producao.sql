-- Produção do FormaHub 2.0: etapa de produção do pedido e o quadro (Kanban).
--
-- Etapas, nesta ordem: fila_producao -> em_producao -> aguardando_entrega -> entregue.
-- O pedido só avança para a próxima etapa (sem pular, sem voltar, nada depois de entregue).
-- A etapa é independente do status do pagamento. Nenhum pedido é excluído.
--
-- 1. Adiciona etapa_producao (pedidos existentes e novos começam em fila_producao),
--    etapa_atualizada_em e etapa_atualizada_por (quem moveu por último).
-- 2. listar_producao(): dados dos cards do quadro (só leitura).
-- 3. avancar_etapa_producao(): move um pedido para a próxima etapa, com bloqueio de linha e
--    conferência da etapa esperada (concorrência otimista). Erros reconhecíveis pelo servidor
--    (o PostgREST devolve o código e o status HTTP correspondente):
--      PT404 pedido não encontrado · PT403 usuário inválido
--      PT409 etapa atual diferente da esperada (outro usuário já moveu)
--      PT422 movimento inválido (pular, voltar ou avançar depois de entregue)
-- criar_pedido não muda: a coluna tem padrão fila_producao.
-- Migration LOCAL: aplicar somente por pedido explícito (supabase db push).

-- ---------- Colunas ----------
alter table public.pedidos
  add column etapa_producao text,
  add column etapa_atualizada_em timestamptz,
  add column etapa_atualizada_por bigint references public.usuarios (id) on delete restrict;

update public.pedidos set etapa_producao = 'fila_producao' where etapa_producao is null;

alter table public.pedidos alter column etapa_producao set default 'fila_producao';
alter table public.pedidos alter column etapa_producao set not null;

alter table public.pedidos
  add constraint pedidos_etapa_producao_check
  check (etapa_producao in ('fila_producao', 'em_producao', 'aguardando_entrega', 'entregue'));

-- ---------- Quadro: um card por pedido ----------
-- Etapas na ordem do processo; dentro de cada etapa, do pedido mais antigo para o mais recente.
-- Foto de destaque: mesma regra da listagem (maior quantidade com imagem; empate, menor ordem).
create function public.listar_producao()
returns table (
  id               bigint,
  etapa_producao   text,
  cliente_nome     text,
  forma_pagamento  text,
  status_pagamento text,
  tipo_entrega     text,
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
    p.etapa_producao,
    p.cliente_nome,
    p.forma_pagamento,
    p.status_pagamento,
    p.tipo_entrega,
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
  order by
    array_position(
      array['fila_producao', 'em_producao', 'aguardando_entrega', 'entregue'], p.etapa_producao
    ),
    p.criado_em,
    p.id;
$corpo$;

-- ---------- Avanço de etapa ----------
create function public.avancar_etapa_producao(
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
  v_proxima text;
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

  v_proxima := case v_atual
    when 'fila_producao' then 'em_producao'
    when 'em_producao' then 'aguardando_entrega'
    when 'aguardando_entrega' then 'entregue'
  end;
  if v_proxima is null or p_nova_etapa is distinct from v_proxima then
    raise exception 'movimento de etapa inválido' using errcode = 'PT422';
  end if;

  v_quando := now();
  update public.pedidos
     set etapa_producao = v_proxima,
         etapa_atualizada_em = v_quando,
         etapa_atualizada_por = p_usuario_id
   where id = p_pedido_id;

  return jsonb_build_object(
    'id', p_pedido_id,
    'etapa_producao', v_proxima,
    'etapa_atualizada_em', v_quando at time zone 'America/Sao_Paulo',
    'etapa_atualizada_por_nome', v_usuario
  );
end;
$corpo$;

-- ---------- Privilégios ----------
revoke all on function public.listar_producao() from public, anon, authenticated;
revoke all on function public.avancar_etapa_producao(bigint, text, text, bigint)
  from public, anon, authenticated;
grant execute on function public.listar_producao() to service_role;
grant execute on function public.avancar_etapa_producao(bigint, text, text, bigint)
  to service_role;
