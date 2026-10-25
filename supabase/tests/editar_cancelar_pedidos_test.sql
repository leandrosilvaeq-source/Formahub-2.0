-- Teste transacional da migration de editar e cancelar pedidos
-- (20261015120000_editar_cancelar_pedidos.sql).
--
-- Seguro com dados reais: tudo roda dentro de BEGIN/ROLLBACK; os pedidos de teste usam ids
-- NEGATIVOS inseridos com OVERRIDING SYSTEM VALUE. Usa somente dados fictícios.
-- Local:  node supabase/tests/executar_pglite.mjs supabase/tests/editar_cancelar_pedidos_test.sql
-- Remoto: supabase db query --linked -f supabase/tests/editar_cancelar_pedidos_test.sql

begin;

do $teste$
declare
  v_leandro bigint;
  v_papel   text;
  v_funcao  text;
  v_retorno jsonb;
  v_c1      constant text := 'pedidos/-1/itens/1/11111111-1111-4111-8111-111111111111.png';
  v_c2      constant text := 'pedidos/-1/itens/2/22222222-2222-4222-8222-222222222222.png';
  v_c3      constant text := 'pedidos/-1/itens/1/33333333-3333-4333-8333-333333333333.png';
  v_erro    text;
begin
  select id into v_leandro from public.usuarios where nome = 'Leandro';

  -- ---------- Funções protegidas ----------
  foreach v_funcao in array array[
    'public.editar_pedido(bigint, bigint, text, text, text, text, text, date, text, numeric, jsonb)',
    'public.cancelar_pedido(bigint, bigint)'
  ] loop
    if exists (select 1 from pg_proc where oid = v_funcao::regprocedure
                and (not prosecdef or not ('search_path=""' = any (proconfig)))) then
      raise exception '% deveria ser security definer com search_path vazio', v_funcao;
    end if;
    foreach v_papel in array array['anon', 'authenticated'] loop
      if has_function_privilege(v_papel, v_funcao, 'EXECUTE') then
        raise exception '% não deveria ser executável por %', v_funcao, v_papel;
      end if;
    end loop;
    if not has_function_privilege('service_role', v_funcao, 'EXECUTE') then
      raise exception '% deveria ser executável por service_role', v_funcao;
    end if;
  end loop;

  -- ---------- Pedidos de teste ----------
  insert into public.pedidos (
    id, cliente_nome, contato, forma_pagamento, status_pagamento, tipo_entrega, prazo_entrega,
    valor_total, criado_por
  ) overriding system value
  values
    (-1, 'Teste Editar', '(11) 98765-4321', 'pix', 'pendente', 'entrega', date '2026-10-20',
     30, v_leandro),
    (-2, 'Teste Entregue', '(11) 98765-4321', 'pix', 'pendente', 'entrega', date '2026-10-20',
     10, v_leandro),
    (-3, 'Teste Cancelar', '(11) 98765-4321', 'pix', 'pendente', 'entrega', date '2026-10-20',
     10, v_leandro);

  insert into public.pedido_itens (
    pedido_id, ordem, produto, quantidade, valor_unitario, subtotal, imagem_caminho
  ) values
    (-1, 1, 'Caneca', 2, 10, 20, v_c1),
    (-1, 2, 'Prato', 1, 10, 10, v_c2),
    (-2, 1, 'Copo', 1, 10, 10, null),
    (-3, 1, 'Copo', 1, 10, 10, null);

  update public.pedidos set etapa_producao = 'entregue' where id = -2;

  -- ---------- Edição: troca itens, renumera e mantém uma imagem ----------
  v_retorno := public.editar_pedido(
    -1, v_leandro, 'Cliente Editado', '(11) 91111-2222', 'dinheiro', 'pago', 'retirada',
    date '2026-10-25', 'obs', 45,
    jsonb_build_array(
      jsonb_build_object('ordem', 1, 'produto', 'Prato', 'quantidade', 1, 'valor_unitario', 10,
                         'subtotal', 10, 'imagem_caminho', v_c2),
      jsonb_build_object('ordem', 2, 'produto', 'Vaso', 'quantidade', 1, 'valor_unitario', 35,
                         'subtotal', 35, 'imagem_caminho', v_c3)
    )
  );
  if v_retorno -> 'imagens_removidas' is distinct from to_jsonb(array[v_c1]) then
    raise exception 'imagens_removidas deveria ser só a imagem que saiu: %', v_retorno;
  end if;
  if not exists (select 1 from public.pedidos where id = -1 and cliente_nome = 'Cliente Editado'
                  and forma_pagamento = 'dinheiro' and status_pagamento = 'pago'
                  and tipo_entrega = 'retirada' and valor_total = 45
                  and pagamento_atualizado_por = v_leandro) then
    raise exception 'dados do pedido não foram atualizados';
  end if;
  if (select count(*) from public.pedido_itens where pedido_id = -1) <> 2
     or not exists (select 1 from public.pedido_itens
                     where pedido_id = -1 and ordem = 1 and imagem_caminho = v_c2) then
    raise exception 'itens não foram substituídos / renumerados';
  end if;

  -- Total diferente da soma desfaz tudo.
  begin
    perform public.editar_pedido(
      -1, v_leandro, 'Outro Nome', '(11) 91111-2222', 'pix', 'pendente', 'entrega',
      date '2026-10-25', null, 999,
      jsonb_build_array(jsonb_build_object('ordem', 1, 'produto', 'X', 'quantidade', 1,
                        'valor_unitario', 1, 'subtotal', 1, 'imagem_caminho', null))
    );
    raise exception 'total incorreto deveria ser recusado';
  exception when check_violation then
    null;
  end;
  if exists (select 1 from public.pedidos where id = -1 and cliente_nome = 'Outro Nome') then
    raise exception 'edição recusada não deveria deixar nada gravado';
  end if;

  -- ---------- Finalizar de qualquer etapa (migration 20261016120000) ----------
  perform public.mover_etapa_producao(-1, 'fila_producao', 'entregue', v_leandro);
  if (select etapa_producao from public.pedidos where id = -1) <> 'entregue' then
    raise exception 'fila_producao -> entregue deveria ser permitido';
  end if;
  update public.pedidos set etapa_producao = 'fila_producao' where id = -1;

  -- ---------- Regras: entregue, inexistente, usuário inválido ----------
  foreach v_erro in array array['-2', '-99'] loop
    begin
      perform public.editar_pedido(
        v_erro::bigint, v_leandro, 'X', null, 'pix', 'pendente', 'entrega', date '2026-10-25',
        null, 1,
        jsonb_build_array(jsonb_build_object('ordem', 1, 'produto', 'X', 'quantidade', 1,
                          'valor_unitario', 1, 'subtotal', 1, 'imagem_caminho', null))
      );
      raise exception 'edição de % deveria ser recusada', v_erro;
    exception when sqlstate 'PT422' or sqlstate 'PT404' then
      null;
    end;
  end loop;
  begin
    perform public.cancelar_pedido(-2, v_leandro);
    raise exception 'pedido entregue não pode ser cancelado';
  exception when sqlstate 'PT422' then
    null;
  end;
  begin
    perform public.cancelar_pedido(-3, -12345);
    raise exception 'usuário inválido deveria ser recusado';
  exception when sqlstate 'PT403' then
    null;
  end;

  -- ---------- Cancelamento ----------
  v_retorno := public.cancelar_pedido(-3, v_leandro);
  if v_retorno ->> 'cancelado_por_nome' is distinct from 'Leandro' then
    raise exception 'cancelamento devolveu %', v_retorno;
  end if;
  if not exists (select 1 from public.listar_pedidos() where id = -3 and cancelado) then
    raise exception 'listar_pedidos deveria marcar o pedido como cancelado';
  end if;
  if exists (select 1 from public.listar_producao() where id = -3) then
    raise exception 'pedido cancelado não deveria aparecer na produção';
  end if;
  if (public.consultar_pedido(-3) ->> 'cancelado_em_local') is null then
    raise exception 'consultar_pedido deveria informar o cancelamento';
  end if;

  begin
    perform public.cancelar_pedido(-3, v_leandro);
    raise exception 'cancelar duas vezes deveria ser recusado';
  exception when sqlstate 'PT409' then
    null;
  end;

  -- Pedido cancelado não muda mais (nem por edição, nem por outra função).
  begin
    perform public.editar_pedido(
      -3, v_leandro, 'X', null, 'pix', 'pendente', 'entrega', date '2026-10-25', null, 1,
      jsonb_build_array(jsonb_build_object('ordem', 1, 'produto', 'X', 'quantidade', 1,
                        'valor_unitario', 1, 'subtotal', 1, 'imagem_caminho', null))
    );
    raise exception 'edição de pedido cancelado deveria ser recusada';
  exception when sqlstate 'PT422' then
    null;
  end;
  begin
    perform public.alterar_status_pagamento(-3, 'pendente', 'pago', v_leandro);
    raise exception 'pagamento de pedido cancelado deveria ser recusado';
  exception when sqlstate 'PT422' then
    null;
  end;
  begin
    perform public.mover_etapa_producao(-3, 'fila_producao', 'em_producao', v_leandro);
    raise exception 'etapa de pedido cancelado deveria ser recusada';
  exception when sqlstate 'PT422' then
    null;
  end;
end;
$teste$;

rollback;

select 'editar_cancelar_pedidos_test: ok' as resultado;
