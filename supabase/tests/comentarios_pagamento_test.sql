-- Teste transacional da migration dos comentários da produção e da alternância do pagamento
-- (20261005120000_comentarios_pagamento_producao.sql).
--
-- Seguro com dados reais: tudo roda dentro de BEGIN/ROLLBACK; os pedidos de teste usam ids
-- NEGATIVOS inseridos com OVERRIDING SYSTEM VALUE (não colidem com ids reais e não consomem
-- nem alteram as sequências); nenhum pedido real é lido para alteração nem alterado.
-- Qualquer falha interrompe com RAISE EXCEPTION. Usa somente dados fictícios.
-- Execução: supabase db query --linked -f supabase/tests/comentarios_pagamento_test.sql

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

create function pg_temp.comentar(p_id bigint, p_esperado text, p_novo text, p_usuario bigint)
returns text
language sql
as $f$
  select format('select public.atualizar_comentario_producao(%s, %L, %L, %s)',
                p_id, p_esperado, p_novo, coalesce(p_usuario::text, 'null'));
$f$;

create function pg_temp.pagar(p_id bigint, p_esperado text, p_novo text, p_usuario bigint)
returns text
language sql
as $f$
  select format('select public.alterar_status_pagamento(%s, %L, %L, %s)',
                p_id, p_esperado, p_novo, coalesce(p_usuario::text, 'null'));
$f$;

-- Linha do pedido sem os campos que as funções podem alterar (para conferir que o resto
-- não muda).
create function pg_temp.resto(p_id bigint)
returns jsonb
language sql
as $f$
  select to_jsonb(p) - array['comentario_producao', 'comentario_producao_atualizado_em',
                              'comentario_producao_atualizado_por', 'status_pagamento',
                              'pagamento_atualizado_em', 'pagamento_atualizado_por']
    from public.pedidos as p where p.id = p_id;
$f$;

do $teste$
declare
  v_comentar constant text := 'public.atualizar_comentario_producao(bigint, text, text, bigint)';
  v_pagar    constant text := 'public.alterar_status_pagamento(bigint, text, text, bigint)';
  v_novas    constant text[] := array['comentario_producao', 'comentario_producao_atualizado_em',
                                      'comentario_producao_atualizado_por',
                                      'pagamento_atualizado_em', 'pagamento_atualizado_por'];
  v_leandro  bigint;
  v_kassia   bigint;
  v_funcao   text;
  v_papel    text;
  v_coluna   text;
  v_retorno  jsonb;
  v_linha    jsonb;
  v_resto    jsonb;
  v_outro    jsonb;
  v_itens    jsonb;
  v_etapa    text;
begin
  select id into v_leandro from public.usuarios where nome = 'Leandro';
  select id into v_kassia from public.usuarios where nome = 'Kassia';

  -- ---------- Colunas: opcionais, sem valor padrão; observacoes continua igual ----------
  foreach v_coluna in array v_novas loop
    if not exists (select 1 from information_schema.columns
                    where table_schema = 'public' and table_name = 'pedidos'
                      and column_name = v_coluna and is_nullable = 'YES'
                      and column_default is null) then
      raise exception 'pedidos.% deveria existir, opcional e sem valor padrão', v_coluna;
    end if;
  end loop;
  if not exists (select 1 from information_schema.columns
                  where table_schema = 'public' and table_name = 'pedidos'
                    and column_name = 'observacoes' and data_type = 'text') then
    raise exception 'observacoes deveria continuar existindo';
  end if;

  -- Responsáveis ligados a usuarios com ON DELETE RESTRICT.
  foreach v_coluna in array array['comentario_producao_atualizado_por',
                                  'pagamento_atualizado_por'] loop
    if not exists (
      select 1 from pg_constraint c
       where c.conrelid = 'public.pedidos'::regclass and c.contype = 'f'
         and c.confrelid = 'public.usuarios'::regclass and c.confdeltype = 'r'
         and c.conkey = array[(select attnum from pg_attribute
                                where attrelid = 'public.pedidos'::regclass
                                  and attname = v_coluna)]) then
      raise exception '% deveria referenciar usuarios.id com ON DELETE RESTRICT', v_coluna;
    end if;
  end loop;

  -- ---------- Funções: uma versão de cada, protegidas ----------
  if (select count(*) from pg_proc where pronamespace = 'public'::regnamespace
        and proname in ('atualizar_comentario_producao', 'alterar_status_pagamento',
                        'listar_producao', 'listar_pedidos', 'consultar_pedido',
                        'mover_etapa_producao', 'criar_pedido')) <> 7 then
    raise exception 'não deveria haver versões sobrecarregadas das funções de pedido';
  end if;

  foreach v_funcao in array array[v_comentar, v_pagar, 'public.listar_producao()',
                                  'public.listar_pedidos()', 'public.consultar_pedido(bigint)'] loop
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

  foreach v_funcao in array array[v_comentar, v_pagar] loop
    if pg_get_functiondef(v_funcao::regprocedure) !~* 'for update' then
      raise exception '% deveria bloquear a linha (FOR UPDATE)', v_funcao;
    end if;
  end loop;

  -- Nenhuma permissão direta nas tabelas (nem por coluna): tudo passa pelas funções.
  foreach v_papel in array array['anon', 'authenticated', 'service_role'] loop
    if has_any_column_privilege(v_papel, 'public.pedidos', 'SELECT, INSERT, UPDATE, REFERENCES')
       or has_table_privilege(v_papel, 'public.pedidos', 'DELETE, TRUNCATE')
       or has_any_column_privilege(v_papel, 'public.pedido_itens',
                                   'SELECT, INSERT, UPDATE, REFERENCES') then
      raise exception 'pedidos/pedido_itens não deveriam ter acesso direto para %', v_papel;
    end if;
  end loop;

  -- ---------- Pedidos de teste (ids negativos) ----------
  insert into public.pedidos (id, cliente_nome, forma_pagamento, status_pagamento, tipo_entrega,
                              prazo_entrega, observacoes, valor_total, criado_por, criado_em)
  overriding system value
  values
    (-1, 'Teste comentário', 'pix', 'pendente', 'retirada', '2028-02-29', 'Observação original',
     4, v_leandro, '2999-01-01 12:00:00+00'),
    (-2, 'Teste outro', 'cartao', 'pago', 'entrega', '2028-03-01', null, 1, v_leandro,
     '2999-01-01 12:01:00+00');

  insert into public.pedido_itens (id, pedido_id, ordem, produto, quantidade, valor_unitario,
                                   subtotal, imagem_caminho)
  overriding system value
  values
    (-11, -1, 1, 'A1', 2, 1, 2, 'pedidos/-1/itens/1/00000000-0000-4000-8000-000000000001.png'),
    (-12, -1, 2, 'A2', 2, 1, 2, null),
    (-21, -2, 1, 'B1', 1, 1, 1, null);

  update public.pedidos set etapa_producao = 'em_producao' where id = -1;
  v_resto := pg_temp.resto(-1);
  v_outro := to_jsonb((select p from public.pedidos as p where p.id = -2));
  select jsonb_agg(to_jsonb(i) order by i.id) into v_itens
    from public.pedido_itens as i where i.pedido_id in (-1, -2);

  -- Leitura antes de qualquer comentário: campos vazios.
  select to_jsonb(l) into v_linha from public.listar_producao() as l where l.id = -1;
  if v_linha -> 'comentario_producao' is distinct from 'null'::jsonb
     or v_linha -> 'comentario_producao_atualizado_em' is distinct from 'null'::jsonb
     or v_linha -> 'comentario_producao_atualizado_por_nome' is distinct from 'null'::jsonb
     or v_linha ->> 'observacoes' is distinct from 'Observação original' then
    raise exception 'listar_producao sem comentário devolveu %', v_linha;
  end if;

  -- ---------- Comentário: novo, edição, remoção ----------
  v_retorno := public.atualizar_comentario_producao(-1, null, '  Pintar de azul  ', v_kassia);
  if v_retorno ->> 'comentario_producao' is distinct from 'Pintar de azul'
     or v_retorno ->> 'comentario_producao_atualizado_por_nome' is distinct from 'Kassia'
     or v_retorno ->> 'comentario_producao_atualizado_em' is null
     or (v_retorno ->> 'id')::bigint is distinct from -1 then
    raise exception 'comentário novo devolveu %', v_retorno;
  end if;
  if not exists (select 1 from public.pedidos
                  where id = -1 and comentario_producao = 'Pintar de azul'
                    and comentario_producao_atualizado_por = v_kassia
                    and comentario_producao_atualizado_em is not null
                    and observacoes = 'Observação original') then
    raise exception 'comentário novo deveria gravar texto, horário e responsável';
  end if;

  select to_jsonb(l) into v_linha from public.listar_producao() as l where l.id = -1;
  if v_linha ->> 'comentario_producao' is distinct from 'Pintar de azul'
     or v_linha ->> 'comentario_producao_atualizado_por_nome' is distinct from 'Kassia'
     or v_linha ->> 'comentario_producao_atualizado_em' is null then
    raise exception 'listar_producao deveria devolver o comentário e a auditoria (%)', v_linha;
  end if;

  v_retorno := public.atualizar_comentario_producao(
    -1, 'Pintar de azul', E'Linha 1\nLinha 2\n', v_leandro);
  if v_retorno ->> 'comentario_producao' is distinct from E'Linha 1\nLinha 2'
     or v_retorno ->> 'comentario_producao_atualizado_por_nome' is distinct from 'Leandro' then
    raise exception 'edição do comentário devolveu %', v_retorno;
  end if;

  v_retorno := public.atualizar_comentario_producao(-1, E'Linha 1\nLinha 2', E'  \n\t ', v_leandro);
  if v_retorno -> 'comentario_producao' is distinct from 'null'::jsonb then
    raise exception 'comentário só com espaços deveria virar NULL (%)', v_retorno;
  end if;
  if not exists (select 1 from public.pedidos
                  where id = -1 and comentario_producao is null
                    and comentario_producao_atualizado_por = v_leandro) then
    raise exception 'remoção do comentário deveria gravar NULL com o responsável';
  end if;

  -- Só espaços em branco saem das pontas (letras como "v" e "f" ficam).
  v_retorno := public.atualizar_comentario_producao(
    -1, null, E'\x0B\f vivo e firme \r\n', v_leandro);
  if v_retorno ->> 'comentario_producao' is distinct from 'vivo e firme' then
    raise exception 'só os espaços das pontas deveriam sair (%)', v_retorno;
  end if;
  v_retorno := public.atualizar_comentario_producao(-1, 'vivo e firme', 'De novo', v_leandro);
  v_retorno := public.atualizar_comentario_producao(-1, 'De novo', '', v_leandro);
  if v_retorno -> 'comentario_producao' is distinct from 'null'::jsonb then
    raise exception 'comentário vazio deveria virar NULL';
  end if;

  -- ---------- Comentário: limite de 2.000 caracteres ----------
  v_retorno := public.atualizar_comentario_producao(
    -1, null, '  ' || repeat('x', 2000) || '  ', v_leandro);
  if char_length(v_retorno ->> 'comentario_producao') is distinct from 2000 then
    raise exception '2.000 caracteres (sem os espaços das pontas) deveriam ser aceitos';
  end if;
  perform pg_temp.deve_falhar('comentário com 2.001 caracteres',
    pg_temp.comentar(-1, repeat('x', 2000), repeat('y', 2001), v_leandro), 'PT422');
  perform pg_temp.deve_falhar('comentário maior que o limite direto na tabela',
    format('update public.pedidos set comentario_producao = %L where id = -1', repeat('z', 2001)),
    '23514');
  perform pg_temp.deve_falhar('comentário vazio direto na tabela',
    'update public.pedidos set comentario_producao = '''' where id = -1', '23514');
  perform pg_temp.deve_falhar('comentário com espaços nas pontas direto na tabela',
    'update public.pedidos set comentario_producao = '' a '' where id = -1', '23514');
  perform pg_temp.deve_falhar('auditoria do comentário pela metade',
    'update public.pedidos set comentario_producao_atualizado_por = null where id = -1', '23514');
  if (select comentario_producao from public.pedidos where id = -1) is distinct from repeat('x', 2000) then
    raise exception 'tentativas recusadas não deveriam mudar o comentário';
  end if;

  -- ---------- Comentário: conflitos e valores inválidos ----------
  perform pg_temp.deve_falhar('comentário esperado desatualizado',
    pg_temp.comentar(-1, 'texto antigo', 'novo', v_leandro), 'PT409');
  perform pg_temp.deve_falhar('esperado NULL com comentário existente',
    pg_temp.comentar(-1, null, 'novo', v_leandro), 'PT409');
  v_retorno := public.atualizar_comentario_producao(-1, repeat('x', 2000), null, v_leandro);
  perform pg_temp.deve_falhar('esperado preenchido com comentário NULL',
    pg_temp.comentar(-1, 'algo', 'novo', v_leandro), 'PT409');
  perform pg_temp.deve_falhar('esperado vazio não é o mesmo que NULL',
    pg_temp.comentar(-1, '', 'novo', v_leandro), 'PT409');
  perform pg_temp.deve_falhar('comentário: usuário inexistente',
    pg_temp.comentar(-1, null, 'novo', -999), 'PT403');
  perform pg_temp.deve_falhar('comentário: usuário NULL',
    pg_temp.comentar(-1, null, 'novo', null), 'PT403');
  perform pg_temp.deve_falhar('comentário: pedido inexistente',
    pg_temp.comentar(-999, null, 'novo', v_leandro), 'PT404');
  if (select comentario_producao from public.pedidos where id = -1) is not null then
    raise exception 'tentativas recusadas não deveriam mudar o comentário';
  end if;

  -- ---------- Pagamento: pendente -> pago -> pendente ----------
  select etapa_producao into v_etapa from public.pedidos where id = -1;
  v_retorno := public.alterar_status_pagamento(-1, 'pendente', 'pago', v_kassia);
  if v_retorno ->> 'status_pagamento' is distinct from 'pago'
     or v_retorno ->> 'pagamento_atualizado_por_nome' is distinct from 'Kassia'
     or v_retorno ->> 'pagamento_atualizado_em' is null then
    raise exception 'pendente -> pago devolveu %', v_retorno;
  end if;
  if not exists (select 1 from public.pedidos
                  where id = -1 and status_pagamento = 'pago'
                    and pagamento_atualizado_por = v_kassia
                    and pagamento_atualizado_em is not null) then
    raise exception 'pendente -> pago deveria gravar status, horário e responsável';
  end if;
  if (select status_pagamento from public.listar_pedidos() where id = -1) is distinct from 'pago'
     or public.consultar_pedido(-1) ->> 'status_pagamento' is distinct from 'pago'
     or (select status_pagamento from public.listar_producao() where id = -1) is distinct from 'pago' then
    raise exception 'o novo status deveria aparecer em todas as leituras';
  end if;

  v_retorno := public.alterar_status_pagamento(-1, 'pago', 'pendente', v_leandro);
  if v_retorno ->> 'status_pagamento' is distinct from 'pendente'
     or v_retorno ->> 'pagamento_atualizado_por_nome' is distinct from 'Leandro' then
    raise exception 'pago -> pendente devolveu %', v_retorno;
  end if;

  -- ---------- Pagamento: conflitos e valores inválidos ----------
  perform pg_temp.deve_falhar('status esperado desatualizado',
    pg_temp.pagar(-1, 'pago', 'pendente', v_leandro), 'PT409');
  perform pg_temp.deve_falhar('pendente -> pendente',
    pg_temp.pagar(-1, 'pendente', 'pendente', v_leandro), 'PT422');
  perform pg_temp.deve_falhar('status novo fora da lista',
    pg_temp.pagar(-1, 'pendente', 'estornado', v_leandro), 'PT422');
  perform pg_temp.deve_falhar('status esperado fora da lista',
    pg_temp.pagar(-1, 'qualquer', 'pago', v_leandro), 'PT422');
  perform pg_temp.deve_falhar('status novo NULL',
    pg_temp.pagar(-1, 'pendente', null, v_leandro), 'PT422');
  perform pg_temp.deve_falhar('status esperado NULL',
    pg_temp.pagar(-1, null, 'pago', v_leandro), 'PT422');
  perform pg_temp.deve_falhar('pagamento: usuário inexistente',
    pg_temp.pagar(-1, 'pendente', 'pago', -999), 'PT403');
  perform pg_temp.deve_falhar('pagamento: pedido inexistente',
    pg_temp.pagar(-999, 'pendente', 'pago', v_leandro), 'PT404');
  perform pg_temp.deve_falhar('auditoria do pagamento pela metade',
    'update public.pedidos set pagamento_atualizado_em = null where id = -1', '23514');
  if (select status_pagamento from public.pedidos where id = -1) is distinct from 'pendente' then
    raise exception 'tentativas recusadas não deveriam mudar o pagamento';
  end if;

  -- ---------- Nada fora dos campos previstos ----------
  if pg_temp.resto(-1) is distinct from v_resto then
    raise exception 'as funções alteraram outros campos: % -> %', v_resto, pg_temp.resto(-1);
  end if;
  if (select etapa_producao from public.pedidos where id = -1) is distinct from v_etapa then
    raise exception 'a alteração do pagamento não deveria mudar a etapa';
  end if;
  if to_jsonb((select p from public.pedidos as p where p.id = -2)) is distinct from v_outro then
    raise exception 'o outro pedido de teste não deveria mudar';
  end if;
  if (select jsonb_agg(to_jsonb(i) order by i.id) from public.pedido_itens as i
       where i.pedido_id in (-1, -2)) is distinct from v_itens then
    raise exception 'os itens (e imagens) não deveriam mudar';
  end if;

  raise notice 'comentarios_pagamento_test: todas as verificações passaram';
end;
$teste$;

rollback;

-- Fora da transação: nenhum pedido de teste (id negativo) permaneceu.
select
  (select count(*) from public.pedidos where id < 0) as pedidos_de_teste,
  (select count(*) from public.pedido_itens where id < 0 or pedido_id < 0) as itens_de_teste,
  (select count(*) from public.pedidos) as pedidos,
  (select count(*) from public.pedido_itens) as itens;
