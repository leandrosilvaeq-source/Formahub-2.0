-- Teste transacional da migration do status do pagamento (20261002120000_status_pagamento.sql).
--
-- Seguro com dados reais: tudo roda dentro de BEGIN/ROLLBACK; os pedidos de teste usam ids
-- NEGATIVOS inseridos com OVERRIDING SYSTEM VALUE (não colidem com ids reais e não consomem
-- nem alteram as sequências); nenhum dado real é modificado.
-- criar_pedido só aceita ids reservados pela sequência; para não mexer nela, este teste
-- confere a assinatura e o código da função, e o status é exercitado pela restrição da tabela.
-- Qualquer falha interrompe com RAISE EXCEPTION. Usa somente dados fictícios.
-- Execução: supabase db query --linked -f supabase/tests/status_pagamento_test.sql

begin;

-- Executa p_sql e exige que falhe com o SQLSTATE (e a constraint, se informada) esperados.
create function pg_temp.deve_falhar(
  p_descricao text, p_sql text, p_estado text, p_restricao text default null
)
returns void
language plpgsql
as $f$
declare
  v_estado text;
  v_restricao text;
begin
  begin
    execute p_sql;
  exception when others then
    get stacked diagnostics v_estado = returned_sqlstate, v_restricao = constraint_name;
    if v_estado <> p_estado
       or (p_restricao is not null and v_restricao is distinct from p_restricao) then
      raise exception '%: esperado % %, veio % %',
        p_descricao, p_estado, coalesce(p_restricao, ''), v_estado, coalesce(v_restricao, '');
    end if;
    return;
  end;
  raise exception '%: deveria ter sido recusado', p_descricao;
end;
$f$;

do $teste$
declare
  v_criar constant text :=
    'public.criar_pedido(bigint, bigint, text, text, text, text, text, date, text, numeric, jsonb)';
  v_listar constant text := 'public.listar_pedidos()';
  v_consultar constant text := 'public.consultar_pedido(bigint)';
  v_usuario bigint;
  v_papel text;
  v_funcao text;
  v_linha jsonb;
  v_ordem bigint[];
begin
  -- ---------- Coluna ----------
  if not exists (select 1 from information_schema.columns
                  where table_schema = 'public' and table_name = 'pedidos'
                    and column_name = 'status_pagamento' and is_nullable = 'NO'
                    and data_type = 'text') then
    raise exception 'pedidos.status_pagamento deveria existir, ser text e NOT NULL';
  end if;

  if exists (select 1 from public.pedidos
              where status_pagamento is null or status_pagamento not in ('pendente', 'pago')) then
    raise exception 'todo pedido existente deveria ter status pendente ou pago';
  end if;

  -- ---------- Funções: uma versão de cada, protegidas ----------
  if (select count(*) from pg_proc where pronamespace = 'public'::regnamespace
        and proname = 'criar_pedido') <> 1
     or (select count(*) from pg_proc where pronamespace = 'public'::regnamespace
           and proname = 'listar_pedidos') <> 1
     or (select count(*) from pg_proc where pronamespace = 'public'::regnamespace
           and proname = 'consultar_pedido') <> 1 then
    raise exception 'não deveria haver versões sobrecarregadas das funções de pedido';
  end if;

  if to_regprocedure(v_criar) is null then
    raise exception 'criar_pedido deveria ter a assinatura nova (com p_status_pagamento)';
  end if;

  if not exists (select 1 from pg_proc
                  where oid = v_criar::regprocedure and 'p_status_pagamento' = any (proargnames))
     or pg_get_functiondef(v_criar::regprocedure) !~ 'status_pagamento,'
     or pg_get_functiondef(v_criar::regprocedure) !~ 'p_status_pagamento,' then
    raise exception 'criar_pedido deveria gravar o status recebido';
  end if;

  foreach v_funcao in array array[v_criar, v_listar, v_consultar] loop
    if exists (select 1 from pg_proc where oid = v_funcao::regprocedure
                and (not prosecdef or not ('search_path=""' = any (proconfig)))) then
      raise exception '% deveria ser security definer com search_path vazio', v_funcao;
    end if;
    foreach v_papel in array array['anon', 'authenticated'] loop
      if has_function_privilege(v_papel, v_funcao, 'EXECUTE') then
        raise exception '% não deveria ser executável por %', v_funcao, v_papel;
      end if;
    end loop;
    if exists (select 1 from pg_proc p, aclexplode(p.proacl) a
                where p.oid = v_funcao::regprocedure and a.grantee = 0) then
      raise exception '% não deveria ser executável por PUBLIC', v_funcao;
    end if;
    if not has_function_privilege('service_role', v_funcao, 'EXECUTE') then
      raise exception '% deveria ser executável por service_role', v_funcao;
    end if;
  end loop;

  if exists (select 1 from pg_proc where oid in (v_listar::regprocedure, v_consultar::regprocedure)
                and provolatile <> 's') then
    raise exception 'funções de consulta deveriam ser STABLE (sem escrita)';
  end if;

  if has_table_privilege('service_role', 'public.pedidos', 'SELECT,INSERT,UPDATE,DELETE')
     or has_table_privilege('service_role', 'public.pedido_itens', 'SELECT,INSERT,UPDATE,DELETE') then
    raise exception 'service_role não deveria acessar as tabelas diretamente';
  end if;

  -- ---------- Restrição do status (ids negativos, datas no futuro) ----------
  select id into v_usuario from public.usuarios where nome = 'Marise';

  perform pg_temp.deve_falhar('status fora da lista',
    format('insert into public.pedidos (id, cliente_nome, forma_pagamento, status_pagamento,'
           ' tipo_entrega, prazo_entrega, valor_total, criado_por) overriding system value'
           ' values (-9, %L, %L, %L, %L, %L, 0, %s)', 'Teste', 'pix', 'quitado', 'retirada',
           '2026-10-05', v_usuario),
    '23514', 'pedidos_status_pagamento_check');

  perform pg_temp.deve_falhar('status vazio',
    format('insert into public.pedidos (id, cliente_nome, forma_pagamento, status_pagamento,'
           ' tipo_entrega, prazo_entrega, valor_total, criado_por) overriding system value'
           ' values (-9, %L, %L, null, %L, %L, 0, %s)', 'Teste', 'pix', 'retirada', '2026-10-05',
           v_usuario),
    '23502');

  insert into public.pedidos (id, cliente_nome, contato, forma_pagamento, status_pagamento,
                              tipo_entrega, prazo_entrega, observacoes, valor_total, criado_por,
                              criado_em)
  overriding system value
  values
    (-1, 'Teste maior quantidade', null, 'pix', 'pago', 'retirada', '2999-02-01', 'Obs. de teste',
     16, v_usuario, '2999-01-04 12:00:00+00'),
    (-2, 'Teste empate', null, 'dinheiro', 'pendente', 'entrega', '2999-02-01', null, 6, v_usuario,
     '2999-01-03 12:00:00+00'),
    (-3, 'Teste segundo com imagem', null, 'cartao', 'pendente', 'retirada', '2999-02-01', null,
     11, v_usuario, '2999-01-02 12:00:00+00'),
    (-4, 'Teste sem imagem', null, 'pix', 'pago', 'entrega', '2999-02-01', null, 5, v_usuario,
     '2999-01-01 12:00:00+00');

  insert into public.pedido_itens (id, pedido_id, ordem, produto, quantidade, valor_unitario,
                                   subtotal, imagem_caminho)
  overriding system value
  values
    (-11, -1, 1, 'A1', 2, 1, 2, 'pedidos/-1/itens/1/00000000-0000-4000-8000-000000000001.png'),
    (-12, -1, 2, 'A2', 5, 1, 5, 'pedidos/-1/itens/2/00000000-0000-4000-8000-000000000002.png'),
    (-13, -1, 3, 'A3', 9, 1, 9, null),
    (-21, -2, 1, 'B1', 3, 1, 3, 'pedidos/-2/itens/1/00000000-0000-4000-8000-000000000001.png'),
    (-22, -2, 2, 'B2', 3, 1, 3, 'pedidos/-2/itens/2/00000000-0000-4000-8000-000000000002.png'),
    (-31, -3, 1, 'C1', 10, 1, 10, null),
    (-32, -3, 2, 'C2', 1, 1, 1, 'pedidos/-3/itens/2/00000000-0000-4000-8000-000000000002.png'),
    (-41, -4, 1, 'D1', 5, 1, 5, null);

  -- ---------- listar_pedidos ----------
  select array_agg(id) into v_ordem from (select id from public.listar_pedidos() limit 4) as t;
  if v_ordem is distinct from array[-1, -2, -3, -4]::bigint[] then
    raise exception 'listagem deveria vir do mais recente para o mais antigo: %', v_ordem;
  end if;

  select to_jsonb(l) into v_linha from public.listar_pedidos() as l where l.id = -1;
  if (select array_agg(k order by k) from jsonb_object_keys(v_linha) as k)
     is distinct from array['cliente_nome', 'criado_por_nome', 'forma_pagamento', 'id',
                            'imagem_caminho', 'imagem_produto', 'observacoes', 'prazo_entrega',
                            'produtos', 'quantidade_total', 'status_pagamento'] then
    raise exception 'listagem deveria devolver só os campos do card: %', v_linha;
  end if;

  if v_linha -> 'produtos' <> '["A1", "A2", "A3"]'::jsonb
     or v_linha ->> 'quantidade_total' <> '16'
     or v_linha ->> 'status_pagamento' <> 'pago'
     or v_linha ->> 'observacoes' <> 'Obs. de teste'
     or v_linha ->> 'criado_por_nome' <> 'Marise'
     or v_linha ->> 'imagem_produto' <> 'A2'
     or v_linha ->> 'imagem_caminho'
        <> 'pedidos/-1/itens/2/00000000-0000-4000-8000-000000000002.png' then
    raise exception 'maior quantidade com imagem: card inesperado %', v_linha;
  end if;

  if (select imagem_produto from public.listar_pedidos() where id = -2) <> 'B1' then
    raise exception 'empate: deveria escolher o primeiro item com imagem';
  end if;

  if (select imagem_produto from public.listar_pedidos() where id = -3) <> 'C2' then
    raise exception 'primeiro item sem imagem: deveria escolher o segundo';
  end if;

  if (select imagem_caminho is not null or imagem_produto is not null
        from public.listar_pedidos() where id = -4) then
    raise exception 'pedido sem imagem não deveria ter foto de destaque';
  end if;

  if (select observacoes from public.listar_pedidos() where id = -2) is not null then
    raise exception 'observações vazias deveriam vir NULL';
  end if;

  -- ---------- consultar_pedido ----------
  if public.consultar_pedido(-1) ->> 'status_pagamento' <> 'pago'
     or public.consultar_pedido(-2) ->> 'status_pagamento' <> 'pendente' then
    raise exception 'detalhe deveria trazer o status do pagamento';
  end if;

  if public.consultar_pedido(-1)::text ~* '(senha|token|sessao|argon2)' then
    raise exception 'detalhe não deveria conter dados de autenticação';
  end if;

  raise notice 'status_pagamento_test: todas as verificações passaram';
end;
$teste$;

rollback;

-- Fora da transação: nenhum pedido de teste (id negativo) permaneceu.
select
  (select count(*) from public.pedidos where id < 0) as pedidos_de_teste,
  (select count(*) from public.pedido_itens where id < 0 or pedido_id < 0) as itens_de_teste,
  (select count(*) from public.pedidos) as pedidos,
  (select count(*) from public.pedido_itens) as itens;
