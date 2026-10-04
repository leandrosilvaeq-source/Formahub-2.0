-- Teste transacional da migration que tira da Produção o pedido concluído
-- (20261006120000_producao_sem_concluidos.sql).
--
-- Seguro com dados reais: tudo roda dentro de BEGIN/ROLLBACK; os pedidos de teste usam ids
-- NEGATIVOS inseridos com OVERRIDING SYSTEM VALUE (não colidem com ids reais e não consomem
-- nem alteram as sequências); nenhum pedido real é lido para alteração nem alterado.
-- Qualquer falha interrompe com RAISE EXCEPTION. Usa somente dados fictícios.
-- Execução: supabase db query --linked -f supabase/tests/producao_sem_concluidos_test.sql

begin;

-- Ids de teste devolvidos por listar_producao, em ordem.
create function pg_temp.na_producao()
returns bigint[]
language sql
as $f$
  select coalesce(array_agg(l.id order by l.id desc), '{}')
    from public.listar_producao() as l where l.id < 0;
$f$;

-- Pedidos e itens de teste como estão gravados (para conferir que nada muda).
create function pg_temp.retrato()
returns jsonb
language sql
as $f$
  select jsonb_build_object(
    'pedidos', (select jsonb_agg(to_jsonb(p) order by p.id) from public.pedidos as p
                 where p.id < 0),
    'itens', (select jsonb_agg(to_jsonb(i) order by i.id) from public.pedido_itens as i
               where i.pedido_id < 0));
$f$;

do $teste$
declare
  v_funcao  constant text := 'public.listar_producao()';
  v_retorno constant text :=
    'TABLE(id bigint, etapa_producao text, cliente_nome text, forma_pagamento text, '
    'status_pagamento text, tipo_entrega text, prazo_entrega date, produtos text[], '
    'quantidade_total bigint, observacoes text, imagem_caminho text, imagem_produto text, '
    'comentario_producao text, comentario_producao_atualizado_em timestamp without time zone, '
    'comentario_producao_atualizado_por_nome text)';
  v_leandro bigint;
  v_papel   text;
  v_antes   jsonb;
  v_linha   jsonb;
  v_id      bigint;
begin
  select id into v_leandro from public.usuarios where nome = 'Leandro';

  -- ---------- Função: uma só versão, mesma assinatura e retorno, protegida ----------
  if (select count(*) from pg_proc where pronamespace = 'public'::regnamespace
        and proname = 'listar_producao') <> 1 then
    raise exception 'listar_producao não deveria ter versões sobrecarregadas';
  end if;
  if (select count(*) from pg_proc where pronamespace = 'public'::regnamespace
        and proname in ('listar_producao', 'listar_pedidos', 'consultar_pedido',
                        'mover_etapa_producao', 'criar_pedido', 'atualizar_comentario_producao',
                        'alterar_status_pagamento')) <> 7 then
    raise exception 'não deveria haver versões sobrecarregadas das funções de pedido';
  end if;
  if to_regprocedure(v_funcao) is null then
    raise exception '% deveria existir sem parâmetros', v_funcao;
  end if;
  if pg_get_function_result(v_funcao::regprocedure) is distinct from v_retorno then
    raise exception 'retorno de listar_producao mudou: %',
      pg_get_function_result(v_funcao::regprocedure);
  end if;
  if exists (select 1 from pg_proc where oid = v_funcao::regprocedure
              and (not prosecdef or not ('search_path=""' = any (proconfig))
                   or provolatile <> 's')) then
    raise exception '% deveria ser stable, security definer e com search_path vazio', v_funcao;
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

  -- Nenhuma coluna nova de conclusão.
  if exists (select 1 from information_schema.columns
              where table_schema = 'public' and table_name = 'pedidos'
                and column_name ilike '%conclu%') then
    raise exception 'pedidos não deveria ter coluna de conclusão (o estado é derivado)';
  end if;

  -- ---------- Pedidos de teste: as 8 combinações de etapa e pagamento ----------
  --   -1 fila/pendente   -2 fila/pago   -3 em_producao/pendente   -4 em_producao/pago
  --   -5 ag. entrega/pendente   -6 ag. entrega/pago   -7 entregue/pendente   -8 entregue/pago
  insert into public.pedidos (id, cliente_nome, forma_pagamento, status_pagamento, tipo_entrega,
                              prazo_entrega, observacoes, valor_total, criado_por, criado_em,
                              etapa_producao)
  overriding system value
  values
    (-1, 'Teste fila pendente', 'pix', 'pendente', 'retirada', '2028-02-29', null, 1,
     v_leandro, '2999-01-01 12:00:00+00', 'fila_producao'),
    (-2, 'Teste fila pago', 'pix', 'pago', 'retirada', '2028-02-29', null, 1,
     v_leandro, '2999-01-01 12:01:00+00', 'fila_producao'),
    (-3, 'Teste produção pendente', 'dinheiro', 'pendente', 'entrega', '2028-02-29', null, 1,
     v_leandro, '2999-01-01 12:02:00+00', 'em_producao'),
    (-4, 'Teste produção pago', 'dinheiro', 'pago', 'entrega', '2028-02-29', null, 1,
     v_leandro, '2999-01-01 12:03:00+00', 'em_producao'),
    (-5, 'Teste ag. entrega pendente', 'cartao', 'pendente', 'retirada', '2028-02-29', null, 1,
     v_leandro, '2999-01-01 12:04:00+00', 'aguardando_entrega'),
    (-6, 'Teste ag. entrega pago', 'cartao', 'pago', 'retirada', '2028-02-29', null, 1,
     v_leandro, '2999-01-01 12:05:00+00', 'aguardando_entrega'),
    (-7, 'Teste entregue pendente', 'pix', 'pendente', 'entrega', '2028-02-29', null, 1,
     v_leandro, '2999-01-01 12:06:00+00', 'entregue'),
    (-8, 'Teste entregue pago', 'pix', 'pago', 'entrega', '2028-02-29', 'Concluído', 1,
     v_leandro, '2999-01-01 12:07:00+00', 'entregue');

  insert into public.pedido_itens (id, pedido_id, ordem, produto, quantidade, valor_unitario,
                                   subtotal, imagem_caminho)
  overriding system value
  select -10 * n, -n, 1, 'Produto ' || n, 1, 1, 1, null from generate_series(1, 8) as n;

  v_antes := pg_temp.retrato();

  -- ---------- Leitura: só entregue + pago fica de fora ----------
  if pg_temp.na_producao() is distinct from array[-1, -2, -3, -4, -5, -6, -7]::bigint[] then
    raise exception 'listar_producao devolveu % (esperado -1 a -7, sem o -8)',
      pg_temp.na_producao();
  end if;

  -- Entregue + pendente continua, na etapa entregue e com todos os campos.
  select to_jsonb(l) into v_linha from public.listar_producao() as l where l.id = -7;
  if v_linha ->> 'etapa_producao' is distinct from 'entregue'
     or v_linha ->> 'status_pagamento' is distinct from 'pendente'
     or v_linha -> 'produtos' is distinct from '["Produto 7"]'::jsonb
     or (v_linha ->> 'quantidade_total')::bigint is distinct from 1 then
    raise exception 'entregue + pendente deveria continuar no quadro (%)', v_linha;
  end if;

  -- O concluído continua em Pedidos e nos detalhes.
  if not exists (select 1 from public.listar_pedidos() where id = -8) then
    raise exception 'o pedido concluído deveria continuar em listar_pedidos';
  end if;
  if public.consultar_pedido(-8) ->> 'cliente_nome' is distinct from 'Teste entregue pago'
     or public.consultar_pedido(-8) ->> 'status_pagamento' is distinct from 'pago' then
    raise exception 'o pedido concluído deveria continuar em consultar_pedido';
  end if;
  foreach v_id in array array[-1, -2, -3, -4, -5, -6, -7]::bigint[] loop
    if not exists (select 1 from public.listar_pedidos() where id = v_id)
       or public.consultar_pedido(v_id) is null then
      raise exception 'o pedido % deveria continuar em Pedidos e nos detalhes', v_id;
    end if;
  end loop;

  -- Ler o quadro não altera nada.
  if pg_temp.retrato() is distinct from v_antes then
    raise exception 'listar_producao não deveria alterar pedidos nem itens';
  end if;

  -- ---------- O estado acompanha as funções de alteração ----------
  -- Pago -> pendente: o -8 volta para a coluna Entregue; pendente -> pago: sai de novo.
  perform public.alterar_status_pagamento(-8, 'pago', 'pendente', v_leandro);
  if not (-8 = any (pg_temp.na_producao())) then
    raise exception 'entregue que voltou a pendente deveria voltar ao quadro';
  end if;
  perform public.alterar_status_pagamento(-8, 'pendente', 'pago', v_leandro);
  if -8 = any (pg_temp.na_producao()) then
    raise exception 'entregue marcado como pago deveria sair do quadro';
  end if;

  -- Entregue + pendente marcado como pago sai do quadro.
  perform public.alterar_status_pagamento(-7, 'pendente', 'pago', v_leandro);
  if -7 = any (pg_temp.na_producao()) then
    raise exception 'entregue + pendente marcado como pago deveria sair do quadro';
  end if;

  -- Pago movido de Ag. Entrega para Entregue sai do quadro.
  perform public.mover_etapa_producao(-6, 'aguardando_entrega', 'entregue', v_leandro);
  if -6 = any (pg_temp.na_producao()) then
    raise exception 'pedido pago movido para entregue deveria sair do quadro';
  end if;

  -- Pendente movido para Entregue continua.
  perform public.mover_etapa_producao(-5, 'aguardando_entrega', 'entregue', v_leandro);
  if not (-5 = any (pg_temp.na_producao())) then
    raise exception 'pedido pendente movido para entregue deveria continuar no quadro';
  end if;

  if pg_temp.na_producao() is distinct from array[-1, -2, -3, -4, -5]::bigint[] then
    raise exception 'quadro final inesperado: %', pg_temp.na_producao();
  end if;

  -- Nenhum pedido apagado: os 8 continuam gravados, com os mesmos itens.
  if (select count(*) from public.pedidos where id < 0) <> 8
     or (select count(*) from public.pedido_itens where pedido_id < 0) <> 8 then
    raise exception 'nenhum pedido ou item de teste deveria ser apagado';
  end if;
  if pg_temp.retrato() -> 'itens' is distinct from v_antes -> 'itens' then
    raise exception 'os itens de teste não deveriam mudar';
  end if;

  raise notice 'producao_sem_concluidos_test: todas as verificações passaram';
end;
$teste$;

rollback;

-- Fora da transação: nenhum pedido de teste (id negativo) permaneceu.
select
  (select count(*) from public.pedidos where id < 0) as pedidos_de_teste,
  (select count(*) from public.pedido_itens where id < 0 or pedido_id < 0) as itens_de_teste,
  (select count(*) from public.pedidos) as pedidos,
  (select count(*) from public.pedido_itens) as itens;
