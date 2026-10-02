-- Teste transacional da migration do prazo de entrega e da movimentação da produção
-- (20261004120000_prazo_entrega.sql).
--
-- Seguro com dados reais: tudo roda dentro de BEGIN/ROLLBACK; os pedidos de teste usam ids
-- NEGATIVOS inseridos com OVERRIDING SYSTEM VALUE (não colidem com ids reais e não consomem
-- nem alteram as sequências); nenhum pedido real é alterado nem movimentado.
-- As regras da conferência da migration (banco novo, outro pedido sem prazo) só valem no
-- momento em que ela é aplicada e não são repetidas aqui.
-- Qualquer falha interrompe com RAISE EXCEPTION. Usa somente dados fictícios.
-- Execução: supabase db query --linked -f supabase/tests/prazo_entrega_test.sql

begin;

-- Executa p_sql e exige que falhe com o SQLSTATE esperado.
create function pg_temp.deve_falhar(p_descricao text, p_sql text, p_estado text)
returns void
language plpgsql
as $f$
declare
  v_estado text;
begin
  begin
    execute p_sql;
  exception when others then
    get stacked diagnostics v_estado = returned_sqlstate;
    if v_estado <> p_estado then
      raise exception '%: esperado %, veio %', p_descricao, p_estado, v_estado;
    end if;
    return;
  end;
  raise exception '%: deveria ter sido recusado', p_descricao;
end;
$f$;

-- SQL de uma chamada a mover_etapa_producao.
create function pg_temp.mover(p_id bigint, p_de text, p_para text, p_usuario bigint)
returns text
language sql
as $f$
  select format('select public.mover_etapa_producao(%s, %L, %L, %s)',
                p_id, p_de, p_para, coalesce(p_usuario::text, 'null'));
$f$;

do $teste$
declare
  v_criar     constant text :=
    'public.criar_pedido(bigint, bigint, text, text, text, text, text, date, text, numeric, jsonb)';
  v_mover     constant text := 'public.mover_etapa_producao(bigint, text, text, bigint)';
  v_etapas    constant text[] := array['fila_producao', 'em_producao', 'aguardando_entrega',
                                       'entregue'];
  v_permitidos constant text[] := array['fila_producao>em_producao', 'em_producao>fila_producao',
                                        'em_producao>aguardando_entrega',
                                        'aguardando_entrega>entregue'];
  v_leandro   bigint;
  v_kassia    bigint;
  v_funcao    text;
  v_papel     text;
  v_de        text;
  v_para      text;
  v_retorno   jsonb;
  v_linha     jsonb;
begin
  select id into v_leandro from public.usuarios where nome = 'Leandro';
  select id into v_kassia from public.usuarios where nome = 'Kassia';

  -- ---------- Coluna: DATE, obrigatória, sem valor padrão ----------
  if not exists (select 1 from information_schema.columns
                  where table_schema = 'public' and table_name = 'pedidos'
                    and column_name = 'prazo_entrega' and data_type = 'date'
                    and is_nullable = 'NO' and column_default is null) then
    raise exception 'prazo_entrega deveria ser DATE NOT NULL, sem valor padrão';
  end if;

  -- O pedido real da Marise (se existir neste banco) ficou com 05/10/2026.
  if exists (select 1 from public.pedidos
              where id = 1 and lower(btrim(cliente_nome)) = 'marise'
                and prazo_entrega <> date '2026-10-05') then
    raise exception 'o pedido 1 da Marise deveria ter prazo 2026-10-05';
  end if;

  -- ---------- Funções: uma versão de cada, protegidas; a antiga não existe mais ----------
  if exists (select 1 from pg_proc where pronamespace = 'public'::regnamespace
              and proname = 'avancar_etapa_producao') then
    raise exception 'avancar_etapa_producao deveria ter sido removida';
  end if;

  if (select count(*) from pg_proc where pronamespace = 'public'::regnamespace
        and proname in ('criar_pedido', 'listar_pedidos', 'consultar_pedido', 'listar_producao',
                        'mover_etapa_producao')) <> 5 then
    raise exception 'não deveria haver versões sobrecarregadas das funções de pedido';
  end if;

  foreach v_funcao in array array[v_criar, 'public.listar_pedidos()',
                                  'public.consultar_pedido(bigint)', 'public.listar_producao()',
                                  v_mover] loop
    if to_regprocedure(v_funcao) is null then
      raise exception '% deveria existir com esta assinatura', v_funcao;
    end if;
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

  if pg_get_functiondef(v_mover::regprocedure) !~* 'for update' then
    raise exception 'mover_etapa_producao deveria bloquear a linha (FOR UPDATE)';
  end if;

  -- ---------- Pedidos de teste (ids negativos) ----------
  perform pg_temp.deve_falhar('pedido sem prazo',
    format('insert into public.pedidos (id, cliente_nome, forma_pagamento, status_pagamento,'
           ' tipo_entrega, valor_total, criado_por) overriding system value'
           ' values (-9, %L, %L, %L, %L, 0, %s)', 'Teste', 'pix', 'pendente', 'retirada',
           v_leandro),
    '23502');

  insert into public.pedidos (id, cliente_nome, forma_pagamento, status_pagamento, tipo_entrega,
                              prazo_entrega, observacoes, valor_total, criado_por, criado_em)
  overriding system value
  values
    (-1, 'Teste prazo', 'pix', 'pendente', 'retirada', '2028-02-29', null, 4, v_leandro,
     '2999-01-01 12:00:00+00');

  insert into public.pedido_itens (id, pedido_id, ordem, produto, quantidade, valor_unitario,
                                   subtotal, imagem_caminho)
  overriding system value
  values
    (-11, -1, 1, 'A1', 2, 1, 2, 'pedidos/-1/itens/1/00000000-0000-4000-8000-000000000001.png'),
    (-12, -1, 2, 'A2', 2, 1, 2, 'pedidos/-1/itens/2/00000000-0000-4000-8000-000000000002.png');

  -- ---------- Leitura: o prazo sai como data em todas as funções ----------
  if (select prazo_entrega from public.listar_pedidos() where id = -1) <> date '2028-02-29'
     or public.consultar_pedido(-1) ->> 'prazo_entrega' <> '2028-02-29'
     or (select prazo_entrega from public.listar_producao() where id = -1)
        <> date '2028-02-29' then
    raise exception 'listar_pedidos, consultar_pedido e listar_producao deveriam devolver o prazo';
  end if;

  select to_jsonb(l) into v_linha from public.listar_producao() as l where l.id = -1;
  if v_linha ? 'criado_por_nome' then
    raise exception 'listar_producao não deveria mais devolver quem cadastrou';
  end if;
  if v_linha ->> 'imagem_produto' <> 'A1' then
    raise exception 'foto de destaque: empate deveria escolher o primeiro item (%)', v_linha;
  end if;

  -- ---------- Movimentos: os 4 permitidos e todos os outros recusados ----------
  foreach v_de in array v_etapas loop
    foreach v_para in array v_etapas loop
      update public.pedidos set etapa_producao = v_de where id = -1;
      if v_de || '>' || v_para = any (v_permitidos) then
        v_retorno := public.mover_etapa_producao(-1, v_de, v_para, v_kassia);
        if v_retorno ->> 'etapa_producao' <> v_para
           or v_retorno ->> 'etapa_atualizada_por_nome' <> 'Kassia' then
          raise exception 'movimento % -> % devolveu %', v_de, v_para, v_retorno;
        end if;
        if not exists (select 1 from public.pedidos
                        where id = -1 and etapa_producao = v_para
                          and etapa_atualizada_por = v_kassia
                          and etapa_atualizada_em is not null) then
          raise exception 'movimento % -> % deveria gravar etapa, momento e responsável',
            v_de, v_para;
        end if;
      else
        perform pg_temp.deve_falhar(format('movimento %s -> %s', v_de, v_para),
          pg_temp.mover(-1, v_de, v_para, v_leandro), 'PT422');
        if (select etapa_producao from public.pedidos where id = -1) <> v_de then
          raise exception 'movimento recusado % -> % não deveria mudar a etapa', v_de, v_para;
        end if;
      end if;
    end loop;
  end loop;

  update public.pedidos set etapa_producao = 'em_producao' where id = -1;
  perform pg_temp.deve_falhar('nova etapa vazia',
    pg_temp.mover(-1, 'em_producao', null, v_leandro), 'PT422');
  perform pg_temp.deve_falhar('nova etapa fora da lista',
    pg_temp.mover(-1, 'em_producao', 'pausado', v_leandro), 'PT422');
  perform pg_temp.deve_falhar('etapa esperada desatualizada',
    pg_temp.mover(-1, 'fila_producao', 'em_producao', v_leandro), 'PT409');
  perform pg_temp.deve_falhar('voltar com etapa esperada desatualizada',
    pg_temp.mover(-1, 'aguardando_entrega', 'em_producao', v_leandro), 'PT409');
  perform pg_temp.deve_falhar('usuário inexistente',
    pg_temp.mover(-1, 'em_producao', 'fila_producao', -999), 'PT403');
  perform pg_temp.deve_falhar('pedido inexistente',
    pg_temp.mover(-999, 'em_producao', 'fila_producao', v_leandro), 'PT404');

  if (select etapa_producao from public.pedidos where id = -1) <> 'em_producao' then
    raise exception 'tentativas recusadas não deveriam mudar a etapa';
  end if;

  -- o prazo e os demais dados não mudam com a movimentação
  if not exists (select 1 from public.pedidos
                  where id = -1 and prazo_entrega = date '2028-02-29' and valor_total = 4
                    and status_pagamento = 'pendente') then
    raise exception 'a movimentação não deveria alterar outros dados do pedido';
  end if;

  raise notice 'prazo_entrega_test: todas as verificações passaram';
end;
$teste$;

rollback;

-- Fora da transação: nenhum pedido de teste (id negativo) permaneceu.
select
  (select count(*) from public.pedidos where id < 0) as pedidos_de_teste,
  (select count(*) from public.pedido_itens where id < 0 or pedido_id < 0) as itens_de_teste,
  (select count(*) from public.pedidos) as pedidos,
  (select count(*) from public.pedido_itens) as itens;
