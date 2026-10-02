-- Teste transacional da migration de produção (20261003120000_producao.sql), no formato
-- vigente depois de 20261004120000_prazo_entrega.sql: a movimentação é feita por
-- mover_etapa_producao (que também permite voltar de em_producao para a fila; as regras
-- completas de movimento estão em prazo_entrega_test.sql).
--
-- Seguro com dados reais: tudo roda dentro de BEGIN/ROLLBACK; os pedidos de teste usam ids
-- NEGATIVOS inseridos com OVERRIDING SYSTEM VALUE (não colidem com ids reais e não consomem
-- nem alteram as sequências); nenhum pedido real é lido para alteração nem movimentado.
-- Qualquer falha interrompe com RAISE EXCEPTION. Usa somente dados fictícios.
-- Execução: supabase db query --linked -f supabase/tests/producao_test.sql

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
  v_mover constant text := 'public.mover_etapa_producao(bigint, text, text, bigint)';
  v_listar  constant text := 'public.listar_producao()';
  v_leandro bigint;
  v_kassia  bigint;
  v_papel   text;
  v_funcao  text;
  v_retorno jsonb;
  v_linha   jsonb;
  v_ordem   bigint[];
begin
  select id into v_leandro from public.usuarios where nome = 'Leandro';
  select id into v_kassia from public.usuarios where nome = 'Kassia';

  -- ---------- Colunas ----------
  if not exists (select 1 from information_schema.columns
                  where table_schema = 'public' and table_name = 'pedidos'
                    and column_name = 'etapa_producao' and is_nullable = 'NO'
                    and column_default like '%fila_producao%') then
    raise exception 'etapa_producao deveria ser NOT NULL com padrão fila_producao';
  end if;

  if not exists (select 1 from information_schema.columns
                  where table_schema = 'public' and table_name = 'pedidos'
                    and column_name = 'etapa_atualizada_em' and data_type like 'timestamp%')
     or not exists (select 1 from information_schema.columns
                     where table_schema = 'public' and table_name = 'pedidos'
                       and column_name = 'etapa_atualizada_por' and data_type = 'bigint') then
    raise exception 'etapa_atualizada_em e etapa_atualizada_por deveriam existir';
  end if;

  if not exists (select 1 from pg_constraint
                  where conrelid = 'public.pedidos'::regclass and contype = 'f'
                    and confrelid = 'public.usuarios'::regclass and confdeltype = 'r'
                    and conkey = array[(select attnum from pg_attribute
                                         where attrelid = 'public.pedidos'::regclass
                                           and attname = 'etapa_atualizada_por')]) then
    raise exception 'etapa_atualizada_por deveria referenciar usuarios com ON DELETE RESTRICT';
  end if;

  -- pedidos existentes (inclusive os reais) receberam uma etapa válida; nada é alterado aqui
  if exists (select 1 from public.pedidos
              where etapa_producao is null
                 or etapa_producao not in ('fila_producao', 'em_producao',
                                           'aguardando_entrega', 'entregue')) then
    raise exception 'todo pedido deveria ter uma etapa de produção válida';
  end if;

  -- ---------- Funções: uma versão de cada, protegidas ----------
  if (select count(*) from pg_proc where pronamespace = 'public'::regnamespace
        and proname in ('listar_producao', 'mover_etapa_producao')) <> 2
     or (select count(*) from pg_proc where pronamespace = 'public'::regnamespace
           and proname = 'criar_pedido') <> 1 then
    raise exception 'não deveria haver versões sobrecarregadas';
  end if;

  foreach v_funcao in array array[v_mover, v_listar] loop
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

  if (select provolatile from pg_proc where oid = v_listar::regprocedure) <> 's' then
    raise exception 'listar_producao deveria ser STABLE (sem escrita)';
  end if;

  if pg_get_functiondef(v_mover::regprocedure) !~* 'for update' then
    raise exception 'mover_etapa_producao deveria bloquear a linha (FOR UPDATE)';
  end if;

  foreach v_papel in array array['anon', 'authenticated', 'service_role'] loop
    if has_table_privilege(v_papel, 'public.pedidos', 'SELECT,INSERT,UPDATE,DELETE')
       or has_any_column_privilege(v_papel, 'public.pedidos', 'SELECT,UPDATE') then
      raise exception 'papel % não deveria acessar pedidos diretamente', v_papel;
    end if;
  end loop;

  -- ---------- Pedidos de teste (ids negativos; a etapa vem do padrão) ----------
  insert into public.pedidos (id, cliente_nome, forma_pagamento, status_pagamento,
                              tipo_entrega, prazo_entrega, observacoes, valor_total, criado_por,
                              criado_em)
  overriding system value
  values
    (-1, 'Teste A', 'pix', 'pendente', 'retirada', '2999-02-01', 'Obs. A', 3, v_leandro,
     '2999-01-02 12:00:00+00'),
    (-2, 'Teste B', 'cartao', 'pago', 'entrega', '2999-02-01', null, 1, v_leandro,
     '2999-01-01 12:00:00+00'),
    (-3, 'Teste C', 'dinheiro', 'pendente', 'retirada', '2999-02-01', null, 4, v_leandro,
     '2999-01-03 12:00:00+00');

  insert into public.pedido_itens (id, pedido_id, ordem, produto, quantidade, valor_unitario,
                                   subtotal, imagem_caminho)
  overriding system value
  values
    (-11, -1, 1, 'A1', 1, 1, 1, null),
    (-12, -1, 2, 'A2', 2, 1, 2, 'pedidos/-1/itens/2/00000000-0000-4000-8000-000000000002.png'),
    (-21, -2, 1, 'B1', 1, 1, 1, null),
    (-31, -3, 1, 'C1', 2, 1, 2, 'pedidos/-3/itens/1/00000000-0000-4000-8000-000000000001.png'),
    (-32, -3, 2, 'C2', 2, 1, 2, 'pedidos/-3/itens/2/00000000-0000-4000-8000-000000000002.png');

  if exists (select 1 from public.pedidos where id in (-1, -2, -3)
              and (etapa_producao <> 'fila_producao' or etapa_atualizada_em is not null
                   or etapa_atualizada_por is not null)) then
    raise exception 'pedido novo deveria entrar em fila_producao, sem movimentação registrada';
  end if;

  perform pg_temp.deve_falhar('etapa fora da lista',
    'update public.pedidos set etapa_producao = ''pausado'' where id = -1', '23514');

  -- ---------- listar_producao ----------
  select array_agg(id) into v_ordem
    from (select id from public.listar_producao() where id < 0) as t;
  if v_ordem is distinct from array[-2, -1, -3]::bigint[] then
    raise exception 'dentro da etapa, do mais antigo para o mais recente: %', v_ordem;
  end if;

  select to_jsonb(l) into v_linha from public.listar_producao() as l where l.id = -1;
  if (select array_agg(k order by k) from jsonb_object_keys(v_linha) as k)
     is distinct from array['cliente_nome', 'etapa_producao', 'forma_pagamento', 'id',
                            'imagem_caminho', 'imagem_produto', 'observacoes', 'prazo_entrega',
                            'produtos', 'quantidade_total', 'status_pagamento',
                            'tipo_entrega'] then
    raise exception 'listar_producao deveria devolver só os campos do card: %', v_linha;
  end if;
  if v_linha -> 'produtos' <> '["A1", "A2"]'::jsonb or v_linha ->> 'quantidade_total' <> '3'
     or v_linha ->> 'imagem_produto' <> 'A2' or v_linha ->> 'tipo_entrega' <> 'retirada'
     or v_linha ->> 'etapa_producao' <> 'fila_producao' then
    raise exception 'card inesperado: %', v_linha;
  end if;
  if (select imagem_produto from public.listar_producao() where id = -3) <> 'C1'
     or (select imagem_caminho from public.listar_producao() where id = -2) is not null then
    raise exception 'foto de destaque deveria seguir a regra (empate: menor ordem; sem imagem: nada)';
  end if;
  if v_linha::text ~* '(senha|token|sessao|argon2)' then
    raise exception 'listar_producao não deveria conter dados de autenticação';
  end if;

  -- ---------- Avanço sequencial ----------
  v_retorno := public.mover_etapa_producao(-1, 'fila_producao', 'em_producao', v_kassia);
  if v_retorno ->> 'etapa_producao' <> 'em_producao'
     or v_retorno ->> 'etapa_atualizada_por_nome' <> 'Kassia'
     or (v_retorno ->> 'id')::bigint <> -1 then
    raise exception 'retorno do avanço inesperado: %', v_retorno;
  end if;
  if not exists (select 1 from public.pedidos where id = -1 and etapa_producao = 'em_producao'
                   and etapa_atualizada_por = v_kassia and etapa_atualizada_em is not null) then
    raise exception 'o avanço deveria gravar etapa, momento e responsável';
  end if;

  perform public.mover_etapa_producao(-1, 'em_producao', 'aguardando_entrega', v_leandro);
  perform public.mover_etapa_producao(-1, 'aguardando_entrega', 'entregue', v_leandro);
  if (select etapa_producao from public.pedidos where id = -1) <> 'entregue' then
    raise exception 'o pedido deveria chegar em entregue';
  end if;

  -- a ordem da listagem segue as etapas: -1 (entregue) vem por último
  select array_agg(id) into v_ordem
    from (select id from public.listar_producao() where id < 0) as t;
  if v_ordem is distinct from array[-2, -3, -1]::bigint[] then
    raise exception 'listagem deveria seguir a ordem das etapas: %', v_ordem;
  end if;

  -- ---------- Bloqueios ----------
  perform pg_temp.deve_falhar('avançar depois de entregue',
    pg_temp.mover(-1, 'entregue', 'entregue', v_leandro), 'PT422');
  perform pg_temp.deve_falhar('voltar depois de entregue',
    pg_temp.mover(-1, 'entregue', 'aguardando_entrega', v_leandro), 'PT422');
  perform pg_temp.deve_falhar('pular etapa',
    pg_temp.mover(-2, 'fila_producao', 'aguardando_entrega', v_leandro), 'PT422');
  perform pg_temp.deve_falhar('pular até entregue',
    pg_temp.mover(-2, 'fila_producao', 'entregue', v_leandro), 'PT422');
  perform pg_temp.deve_falhar('ficar na mesma etapa',
    pg_temp.mover(-2, 'fila_producao', 'fila_producao', v_leandro), 'PT422');

  perform public.mover_etapa_producao(-2, 'fila_producao', 'em_producao', v_leandro);
  perform pg_temp.deve_falhar('pular de em_producao para entregue',
    pg_temp.mover(-2, 'em_producao', 'entregue', v_leandro), 'PT422');

  perform pg_temp.deve_falhar('etapa esperada desatualizada (outro usuário já moveu)',
    pg_temp.mover(-2, 'fila_producao', 'em_producao', v_leandro), 'PT409');
  perform pg_temp.deve_falhar('etapa esperada vazia',
    pg_temp.mover(-3, null, 'em_producao', v_leandro), 'PT409');

  perform pg_temp.deve_falhar('usuário inexistente',
    pg_temp.mover(-3, 'fila_producao', 'em_producao', -999), 'PT403');
  perform pg_temp.deve_falhar('usuário vazio',
    pg_temp.mover(-3, 'fila_producao', 'em_producao', null), 'PT403');
  perform pg_temp.deve_falhar('pedido inexistente',
    pg_temp.mover(-999, 'fila_producao', 'em_producao', v_leandro), 'PT404');

  if (select etapa_producao from public.pedidos where id = -2) <> 'em_producao'
     or (select etapa_producao from public.pedidos where id = -3) <> 'fila_producao' then
    raise exception 'tentativas recusadas não deveriam mudar a etapa';
  end if;

  raise notice 'producao_test: todas as verificações passaram';
end;
$teste$;

rollback;

-- Fora da transação: nenhum pedido de teste (id negativo) permaneceu.
select
  (select count(*) from public.pedidos where id < 0) as pedidos_de_teste,
  (select count(*) from public.pedido_itens where id < 0 or pedido_id < 0) as itens_de_teste,
  (select count(*) from public.pedidos) as pedidos,
  (select count(*) from public.pedido_itens) as itens;
