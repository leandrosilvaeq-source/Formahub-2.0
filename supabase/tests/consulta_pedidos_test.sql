-- Teste transacional das funções de consulta (20261001120000_consultar_pedidos.sql),
-- no formato vigente depois de 20261002120000_status_pagamento.sql (status e card da
-- listagem; as regras da foto de destaque estão em status_pagamento_test.sql).
--
-- Seguro com dados reais: tudo roda dentro de BEGIN/ROLLBACK, os pedidos de teste usam ids
-- NEGATIVOS inseridos com OVERRIDING SYSTEM VALUE (não colidem com ids reais e não consomem
-- nem alteram as sequências) e nenhum dado real é modificado.
-- Qualquer falha interrompe com RAISE EXCEPTION. Usa somente dados fictícios.
-- Execução: supabase db query --linked -f supabase/tests/consulta_pedidos_test.sql

begin;

do $teste$
declare
  v_listar   constant text := 'public.listar_pedidos()';
  v_consultar constant text := 'public.consultar_pedido(bigint)';
  v_usuario  bigint;
  v_papel    text;
  v_linha    jsonb;
  v_pedido   jsonb;
  v_primeiros bigint[];
begin
  -- ---------- Estrutura e privilégios ----------
  if exists (select 1 from pg_proc
              where oid in (v_listar::regprocedure, v_consultar::regprocedure)
                and (not prosecdef
                     or not ('search_path=""' = any (proconfig))
                     or provolatile <> 's'
                     or prolang <> (select oid from pg_language where lanname = 'sql'))) then
    raise exception 'funções deveriam ser SQL, STABLE, security definer e com search_path vazio';
  end if;

  foreach v_papel in array array['anon', 'authenticated'] loop
    if has_function_privilege(v_papel, v_listar, 'EXECUTE')
       or has_function_privilege(v_papel, v_consultar, 'EXECUTE') then
      raise exception 'papel % não deveria executar as funções de consulta', v_papel;
    end if;
  end loop;

  if exists (select 1 from pg_proc p, aclexplode(p.proacl) a
              where p.oid in (v_listar::regprocedure, v_consultar::regprocedure)
                and a.grantee = 0) then
    raise exception 'PUBLIC não deveria executar as funções de consulta';
  end if;

  if not has_function_privilege('service_role', v_listar, 'EXECUTE')
     or not has_function_privilege('service_role', v_consultar, 'EXECUTE') then
    raise exception 'service_role deveria executar as funções de consulta';
  end if;

  -- a leitura não abriu acesso direto às tabelas
  if has_table_privilege('service_role', 'public.pedidos', 'SELECT,INSERT,UPDATE,DELETE')
     or has_table_privilege('service_role', 'public.pedido_itens', 'SELECT,INSERT,UPDATE,DELETE') then
    raise exception 'service_role não deveria acessar pedidos ou pedido_itens diretamente';
  end if;

  -- ---------- Dados fictícios (ids negativos, datas no futuro para virem primeiro) ----------
  select id into v_usuario from public.usuarios where nome = 'Kassia';

  insert into public.pedidos (id, cliente_nome, contato, forma_pagamento, status_pagamento,
                              tipo_entrega, observacoes, valor_total, criado_por, criado_em)
  overriding system value
  values
    (-1, 'Cliente teste antigo', null, 'pix', 'pendente', 'retirada', null, 5, v_usuario,
     '2999-01-01 12:00:00+00'),
    (-2, 'Cliente teste novo', '(00) 00000-0000', 'cartao', 'pago', 'entrega', 'Obs. de teste',
     137, v_usuario, '2999-01-02 12:00:00+00'),
    (-3, 'Cliente teste empate', null, 'dinheiro', 'pendente', 'retirada', null, 0, v_usuario,
     '2999-01-02 12:00:00+00');

  -- itens do pedido -2 inseridos fora de ordem, um deles com imagem
  insert into public.pedido_itens (id, pedido_id, ordem, produto, quantidade, valor_unitario,
                                   subtotal, imagem_caminho)
  overriding system value
  values
    (-21, -2, 2, 'Item teste B', 2, 2.50, 5.00,
     'pedidos/-2/itens/2/00000000-0000-4000-8000-000000000000.png'),
    (-22, -2, 1, 'Item teste A', 11, 12, 132, null),
    (-11, -1, 1, 'Item teste C', 1, 5, 5, null);

  -- ---------- listar_pedidos ----------
  select array_agg(id) into v_primeiros
    from (select id from public.listar_pedidos() limit 3) as t;
  if v_primeiros is distinct from array[-2, -3, -1]::bigint[] then
    raise exception 'ordem da listagem inesperada: % (esperado mais recente primeiro, empate por id)',
      v_primeiros;
  end if;

  select to_jsonb(l) into v_linha from public.listar_pedidos() as l where l.id = -2;
  if (select array_agg(k order by k) from jsonb_object_keys(v_linha) as k)
     is distinct from array['cliente_nome', 'criado_por_nome', 'forma_pagamento', 'id',
                            'imagem_caminho', 'imagem_produto', 'observacoes', 'produtos',
                            'quantidade_total', 'status_pagamento'] then
    raise exception 'listagem deveria devolver só os campos do card: %', v_linha;
  end if;

  if v_linha ->> 'quantidade_total' <> '13'
     or v_linha -> 'produtos' <> '["Item teste A", "Item teste B"]'::jsonb
     or v_linha ->> 'status_pagamento' <> 'pago'
     or v_linha ->> 'criado_por_nome' <> 'Kassia' then
    raise exception 'linha da listagem inesperada: %', v_linha;
  end if;

  if (select produtos from public.listar_pedidos() where id = -3) <> '{}'::text[] then
    raise exception 'pedido sem itens deveria listar nenhum produto';
  end if;

  if (select quantidade_total from public.listar_pedidos() where id = -3) <> 0 then
    raise exception 'pedido sem itens deveria somar 0 unidades';
  end if;

  -- ---------- consultar_pedido ----------
  v_pedido := public.consultar_pedido(-2);

  if (select array_agg(k order by k) from jsonb_object_keys(v_pedido) as k)
     is distinct from array['cliente_nome', 'contato', 'criado_em_local', 'criado_por_nome',
                            'forma_pagamento', 'id', 'itens', 'observacoes', 'status_pagamento',
                            'tipo_entrega', 'valor_total'] then
    raise exception 'detalhe deveria devolver só os campos da tela: %', v_pedido;
  end if;

  if (select array_agg(k order by k) from jsonb_object_keys(v_pedido -> 'itens' -> 0) as k)
     is distinct from array['imagem_caminho', 'ordem', 'produto', 'quantidade', 'subtotal',
                            'valor_unitario'] then
    raise exception 'item deveria devolver só os campos da tela: %', v_pedido -> 'itens' -> 0;
  end if;

  if v_pedido::text ~* '(senha|token|sessao|sessoes|argon2)' then
    raise exception 'detalhe não deveria conter dados de autenticação';
  end if;

  if (select array_agg(e ->> 'produto' order by n)
        from jsonb_array_elements(v_pedido -> 'itens') with ordinality as x(e, n))
     is distinct from array['Item teste A', 'Item teste B'] then
    raise exception 'itens deveriam vir na ordem original: %', v_pedido -> 'itens';
  end if;

  if v_pedido -> 'itens' -> 1 ->> 'valor_unitario' <> '2.50'
     or v_pedido -> 'itens' -> 1 ->> 'subtotal' <> '5.00'
     or v_pedido -> 'itens' -> 1 ->> 'imagem_caminho'
        <> 'pedidos/-2/itens/2/00000000-0000-4000-8000-000000000000.png'
     or v_pedido -> 'itens' -> 0 -> 'imagem_caminho' <> 'null'::jsonb
     or v_pedido ->> 'valor_total' <> '137'
     or v_pedido ->> 'observacoes' <> 'Obs. de teste'
     or v_pedido ->> 'criado_por_nome' <> 'Kassia'
     or v_pedido ->> 'status_pagamento' <> 'pago'
     or (v_pedido ->> 'criado_em_local')::timestamp <> timestamp '2999-01-02 09:00:00' then
    raise exception 'detalhe inesperado: %', v_pedido;
  end if;

  if public.consultar_pedido(-3) -> 'itens' <> '[]'::jsonb then
    raise exception 'pedido sem itens deveria devolver lista vazia';
  end if;

  if public.consultar_pedido(-999) is not null then
    raise exception 'pedido inexistente deveria devolver NULL';
  end if;

  raise notice 'consulta_pedidos_test: todas as verificações passaram';
end;
$teste$;

rollback;

-- Fora da transação: nenhum pedido de teste (id negativo) permaneceu.
select
  (select count(*) from public.pedidos where id < 0) as pedidos_de_teste,
  (select count(*) from public.pedido_itens where id < 0 or pedido_id < 0) as itens_de_teste,
  (select count(*) from public.pedidos) as pedidos,
  (select count(*) from public.pedido_itens) as itens;
