-- Produção: comentários próprios da produção e alternância do status do pagamento.
--
-- 1. pedidos ganha campos novos, todos opcionais (nenhum pedido existente é alterado):
--      comentario_producao, comentario_producao_atualizado_em, comentario_producao_atualizado_por
--      pagamento_atualizado_em, pagamento_atualizado_por
--    O campo observacoes (do cadastro do pedido) não muda.
-- 2. atualizar_comentario_producao(): grava o comentário (sem espaços nas pontas, vazio vira
--    NULL, no máximo 2.000 caracteres) com horário e responsável, conferindo o comentário
--    esperado (concorrência otimista; NULL é comparado corretamente).
-- 3. alterar_status_pagamento(): só pendente -> pago e pago -> pendente, conferindo o status
--    esperado, com horário e responsável. Não toca etapa, itens, prazo nem imagens.
-- 4. listar_producao passa a devolver o comentário da produção e a última atualização dele.
--    listar_pedidos e consultar_pedido não mudam: já leem status_pagamento da tabela.
-- Erros (as duas funções novas):
--   PT403 usuário inválido · PT404 pedido não encontrado
--   PT409 valor atual diferente do esperado · PT422 valor ou alteração não permitidos
-- Funções com SECURITY DEFINER, search_path vazio e execução só para service_role; nenhuma
-- permissão nova nas tabelas.
-- Migration LOCAL: aplicar somente por pedido explícito (supabase db push).

-- ---------- Colunas ----------
alter table public.pedidos
  add column comentario_producao text,
  add column comentario_producao_atualizado_em timestamptz,
  add column comentario_producao_atualizado_por bigint
    references public.usuarios (id) on delete restrict,
  add column pagamento_atualizado_em timestamptz,
  add column pagamento_atualizado_por bigint references public.usuarios (id) on delete restrict;

-- Comentário gravado sempre sem espaços nas pontas, nunca vazio, até 2.000 caracteres.
alter table public.pedidos
  add constraint pedidos_comentario_producao_check
  check (
    comentario_producao is null
    or (char_length(comentario_producao) between 1 and 2000
        and comentario_producao = btrim(comentario_producao, E' \t\n\r\f\x0B'))
  );

-- Horário e responsável andam juntos.
alter table public.pedidos
  add constraint pedidos_comentario_producao_auditoria_check
  check ((comentario_producao_atualizado_em is null)
         = (comentario_producao_atualizado_por is null));

alter table public.pedidos
  add constraint pedidos_pagamento_auditoria_check
  check ((pagamento_atualizado_em is null) = (pagamento_atualizado_por is null));

-- ---------- Comentário da produção ----------
create function public.atualizar_comentario_producao(
  p_pedido_id           bigint,
  p_comentario_esperado text,
  p_novo_comentario     text,
  p_usuario_id          bigint
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $corpo$
declare
  v_usuario text;
  v_atual   text;
  v_novo    text;
  v_quando  timestamptz;
begin
  select nome into v_usuario
    from public.usuarios
   where id = p_usuario_id and nome in ('Leandro', 'Kassia', 'Marise');
  if v_usuario is null then
    raise exception 'usuário inválido' using errcode = 'PT403';
  end if;

  -- Bloqueia a linha: duas gravações simultâneas ficam em fila, e a segunda vê o comentário
  -- já alterado (e recebe PT409).
  select comentario_producao into v_atual
    from public.pedidos
   where id = p_pedido_id
     for update;
  if not found then
    raise exception 'pedido não encontrado' using errcode = 'PT404';
  end if;

  -- Espaços, tabulações e quebras de linha nas pontas saem; texto vazio vira NULL.
  v_novo := nullif(btrim(p_novo_comentario, E' \t\n\r\f\x0B'), '');
  if char_length(v_novo) > 2000 then
    raise exception 'comentário maior que 2.000 caracteres' using errcode = 'PT422';
  end if;

  -- "is distinct from": NULL esperado só confere com comentário atual NULL.
  if v_atual is distinct from p_comentario_esperado then
    raise exception 'comentário do pedido foi alterado' using errcode = 'PT409';
  end if;

  v_quando := now();
  update public.pedidos
     set comentario_producao = v_novo,
         comentario_producao_atualizado_em = v_quando,
         comentario_producao_atualizado_por = p_usuario_id
   where id = p_pedido_id;

  return jsonb_build_object(
    'id', p_pedido_id,
    'comentario_producao', v_novo,
    'comentario_producao_atualizado_em', v_quando at time zone 'America/Sao_Paulo',
    'comentario_producao_atualizado_por_nome', v_usuario
  );
end;
$corpo$;

-- ---------- Status do pagamento ----------
create function public.alterar_status_pagamento(
  p_pedido_id       bigint,
  p_status_esperado text,
  p_novo_status     text,
  p_usuario_id      bigint
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

  select status_pagamento into v_atual
    from public.pedidos
   where id = p_pedido_id
     for update;
  if not found then
    raise exception 'pedido não encontrado' using errcode = 'PT404';
  end if;

  -- "is not true": valores NULL também são recusados.
  if ((p_status_esperado, p_novo_status) in (('pendente', 'pago'), ('pago', 'pendente')))
     is not true then
    raise exception 'alteração de pagamento não permitida' using errcode = 'PT422';
  end if;

  if v_atual is distinct from p_status_esperado then
    raise exception 'status do pagamento foi alterado' using errcode = 'PT409';
  end if;

  v_quando := now();
  update public.pedidos
     set status_pagamento = p_novo_status,
         pagamento_atualizado_em = v_quando,
         pagamento_atualizado_por = p_usuario_id
   where id = p_pedido_id;

  return jsonb_build_object(
    'id', p_pedido_id,
    'status_pagamento', p_novo_status,
    'pagamento_atualizado_em', v_quando at time zone 'America/Sao_Paulo',
    'pagamento_atualizado_por_nome', v_usuario
  );
end;
$corpo$;

-- ---------- listar_producao: inclui o comentário da produção ----------
drop function public.listar_producao();

create function public.listar_producao()
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
  order by
    array_position(
      array['fila_producao', 'em_producao', 'aguardando_entrega', 'entregue'], p.etapa_producao
    ),
    p.criado_em,
    p.id;
$corpo$;

-- ---------- Privilégios ----------
revoke all on function public.atualizar_comentario_producao(bigint, text, text, bigint)
  from public, anon, authenticated;
revoke all on function public.alterar_status_pagamento(bigint, text, text, bigint)
  from public, anon, authenticated;
revoke all on function public.listar_producao() from public, anon, authenticated;

grant execute on function public.atualizar_comentario_producao(bigint, text, text, bigint)
  to service_role;
grant execute on function public.alterar_status_pagamento(bigint, text, text, bigint)
  to service_role;
grant execute on function public.listar_producao() to service_role;
