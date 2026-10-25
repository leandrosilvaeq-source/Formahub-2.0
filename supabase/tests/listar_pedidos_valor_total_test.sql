-- Teste transacional da migration 20261017120000_listar_pedidos_valor_total.sql.
--
-- Seguro com dados reais: tudo roda dentro de BEGIN/ROLLBACK; os pedidos de teste usam ids
-- NEGATIVOS inseridos com OVERRIDING SYSTEM VALUE. Usa somente dados fictícios.
-- Local:  node supabase/tests/executar_pglite.mjs supabase/tests/listar_pedidos_valor_total_test.sql
-- Remoto: supabase db query --linked -f supabase/tests/listar_pedidos_valor_total_test.sql

begin;

do $teste$
declare
  v_leandro bigint;
  v_papel   text;
  v_funcao  constant text := 'public.listar_pedidos()';
  v_linha   jsonb;
begin
  select id into v_leandro from public.usuarios where nome = 'Leandro';

  -- ---------- Função protegida ----------
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

  -- ---------- Pedido de teste ----------
  insert into public.pedidos (
    id, cliente_nome, contato, forma_pagamento, status_pagamento, tipo_entrega, prazo_entrega,
    valor_total, criado_por
  ) overriding system value
  values
    (-1, 'Teste Total', '(11) 98765-4321', 'pix', 'pendente', 'entrega', date '2026-10-20',
     1234.5, v_leandro);

  insert into public.pedido_itens (
    pedido_id, ordem, produto, quantidade, valor_unitario, subtotal, imagem_caminho
  ) values
    (-1, 1, 'Caneca', 3, 400, 1200, null),
    (-1, 2, 'Prato', 1, 34.5, 34.5, null);

  -- ---------- Listagem ----------
  select to_jsonb(l) into v_linha from public.listar_pedidos() as l where l.id = -1;
  if (select array_agg(k order by k) from jsonb_object_keys(v_linha) as k)
     is distinct from array['cancelado', 'cliente_nome', 'criado_por_nome', 'forma_pagamento',
                            'id', 'imagem_caminho', 'imagem_produto', 'observacoes',
                            'prazo_entrega', 'produtos', 'quantidade_total', 'status_pagamento',
                            'valor_total'] then
    raise exception 'campos da listagem inesperados: %', v_linha;
  end if;

  if (v_linha ->> 'valor_total')::numeric <> 1234.5
     or v_linha ->> 'quantidade_total' <> '4'
     or v_linha ->> 'prazo_entrega' <> '2026-10-20'
     or (v_linha ->> 'cancelado')::boolean then
    raise exception 'linha da listagem inesperada: %', v_linha;
  end if;
end;
$teste$;

rollback;

select 'listar_pedidos_valor_total_test: ok' as resultado;
