-- Teste transacional da migration de pedidos (20260930120000_criar_pedidos.sql).
--
-- Tudo roda dentro de BEGIN/ROLLBACK: nenhum pedido ou item de teste permanece.
-- As sequências (que não voltam com ROLLBACK) são restauradas ao valor anterior ao teste;
-- por isso, rode-o com o sistema parado (sem ninguém salvando pedidos ao mesmo tempo).
-- Qualquer falha interrompe com RAISE EXCEPTION. Usa somente dados fictícios.
-- Não toca no Storage: só confere a configuração do bucket.
-- Execução: supabase db query --linked -f supabase/tests/pedidos_test.sql

begin;

-- ---------- Auxiliares (temporárias: somem com o ROLLBACK) ----------

-- Itens válidos de um pedido de teste (total 26.00; o item 2 tem imagem).
create function pg_temp.itens_teste(p_id bigint)
returns jsonb
language sql
as $f$
  select jsonb_build_array(
    jsonb_build_object('ordem', 1, 'produto', 'Produto teste A', 'quantidade', 2,
                       'valor_unitario', 10.50, 'subtotal', 21.00, 'imagem_caminho', null),
    jsonb_build_object('ordem', 2, 'produto', 'Produto teste B', 'quantidade', 1,
                       'valor_unitario', 5, 'subtotal', 5,
                       'imagem_caminho',
                       'pedidos/' || p_id || '/itens/2/00000000-0000-4000-8000-000000000000.png')
  );
$f$;

-- Os itens de teste com campos trocados em um item: p_campos = {"campo": valor, ...}.
create function pg_temp.itens_com(p_id bigint, p_indice int, p_campos jsonb)
returns jsonb
language sql
as $f$
  select jsonb_set(
    pg_temp.itens_teste(p_id),
    array[p_indice::text],
    (pg_temp.itens_teste(p_id) -> p_indice) || p_campos
  );
$f$;

-- SQL de uma chamada a criar_pedido.
create function pg_temp.chamada(
  p_id bigint,
  p_usuario bigint,
  p_total numeric,
  p_itens jsonb,
  p_cliente text default 'Cliente teste',
  p_pagamento text default 'pix',
  p_entrega text default 'retirada'
)
returns text
language sql
as $f$
  select format(
    'select public.criar_pedido(%s, %s, %L, null, %L, %L, null, %s, %L::jsonb)',
    p_id, p_usuario, p_cliente, p_pagamento, p_entrega, p_total, p_itens
  );
$f$;

-- Executa p_sql e exige que falhe com o SQLSTATE (e a constraint, se informada) esperados.
-- O que a instrução tentou gravar é desfeito (subtransação do bloco EXCEPTION).
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
  v_seq_pedidos constant regclass := pg_get_serial_sequence('public.pedidos', 'id')::regclass;
  v_seq_itens   constant regclass := pg_get_serial_sequence('public.pedido_itens', 'id')::regclass;
  v_ult_pedidos constant bigint := pg_sequence_last_value(v_seq_pedidos);
  v_ult_itens   constant bigint := pg_sequence_last_value(v_seq_itens);
  v_pedidos_antes constant bigint := (select count(*) from public.pedidos);
  v_itens_antes   constant bigint := (select count(*) from public.pedido_itens);
  v_criar constant text :=
    'public.criar_pedido(bigint, bigint, text, text, text, text, text, numeric, jsonb)';
  v_usuario bigint;
  v_papel text;
  v_caminho text;
  v_id bigint;
  v_outro bigint;
begin
  begin
    -- ---------- Estrutura ----------
    if to_regclass('public.pedidos') is null or to_regclass('public.pedido_itens') is null then
      raise exception 'tabelas pedidos e pedido_itens deveriam existir';
    end if;

    if exists (select 1 from pg_class
                where oid in ('public.pedidos'::regclass, 'public.pedido_itens'::regclass)
                  and not relrowsecurity) then
      raise exception 'RLS deveria estar habilitado nas duas tabelas';
    end if;

    if exists (select 1 from pg_policies
                where schemaname = 'public' and tablename in ('pedidos', 'pedido_itens')) then
      raise exception 'não deveria haver policies em pedidos ou pedido_itens';
    end if;

    if exists (select 1 from pg_proc
                where oid in ('public.reservar_id_pedido()'::regprocedure, v_criar::regprocedure)
                  and (not prosecdef or not ('search_path=""' = any (proconfig)))) then
      raise exception 'funções deveriam ser security definer com search_path vazio';
    end if;

    -- ---------- Privilégios ----------
    foreach v_papel in array array['anon', 'authenticated', 'service_role'] loop
      if has_table_privilege(v_papel, 'public.pedidos',
                             'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
         or has_table_privilege(v_papel, 'public.pedido_itens',
                                'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
         or has_any_column_privilege(v_papel, 'public.pedidos', 'SELECT,INSERT,UPDATE,REFERENCES')
         or has_any_column_privilege(v_papel, 'public.pedido_itens', 'SELECT,INSERT,UPDATE,REFERENCES')
         or has_sequence_privilege(v_papel, v_seq_pedidos::text, 'USAGE,SELECT,UPDATE')
         or has_sequence_privilege(v_papel, v_seq_itens::text, 'USAGE,SELECT,UPDATE') then
        raise exception 'papel % não deveria ter acesso direto às tabelas ou sequências', v_papel;
      end if;
    end loop;

    foreach v_papel in array array['anon', 'authenticated'] loop
      if has_function_privilege(v_papel, 'public.reservar_id_pedido()', 'EXECUTE')
         or has_function_privilege(v_papel, v_criar, 'EXECUTE') then
        raise exception 'papel % não deveria executar as funções de pedido', v_papel;
      end if;
    end loop;

    if not has_function_privilege('service_role', 'public.reservar_id_pedido()', 'EXECUTE')
       or not has_function_privilege('service_role', v_criar, 'EXECUTE') then
      raise exception 'service_role deveria executar as funções de pedido';
    end if;

    -- ---------- Storage ----------
    if not exists (select 1 from storage.buckets
                    where id = 'pedido-imagens'
                      and public = false
                      and file_size_limit = 10485760
                      and allowed_mime_types = array['image/png', 'image/jpeg', 'image/webp']) then
      raise exception 'bucket pedido-imagens deveria ser privado, 10 MB, PNG/JPEG/WebP';
    end if;

    if exists (select 1 from pg_policies
                where schemaname = 'storage'
                  and roles && array['public', 'anon', 'authenticated']::name[]) then
      raise exception 'não deveria haver policy de Storage para public, anon ou authenticated';
    end if;

    -- ---------- Gravação válida ----------
    select id into v_usuario from public.usuarios where nome = 'Leandro';

    v_id := public.reservar_id_pedido();
    if v_id is null or v_id < 1 then
      raise exception 'reservar_id_pedido deveria devolver um id';
    end if;
    if exists (select 1 from public.pedidos where id = v_id) then
      raise exception 'reservar o id não deveria gravar nada';
    end if;

    if public.criar_pedido(v_id, v_usuario, 'Cliente teste', '(00) 00000-0000', 'pix', 'retirada',
                           null, 26.00, pg_temp.itens_teste(v_id)) <> v_id then
      raise exception 'criar_pedido deveria devolver o id';
    end if;

    if not exists (select 1 from public.pedidos
                    where id = v_id and criado_por = v_usuario and valor_total = 26
                      and forma_pagamento = 'pix' and tipo_entrega = 'retirada') then
      raise exception 'pedido de teste não foi gravado como esperado';
    end if;

    if (select array_agg(ordem || ':' || subtotal || ':' || coalesce(imagem_caminho, '-')
                         order by ordem)
          from public.pedido_itens where pedido_id = v_id)
       is distinct from array[
         '1:21.00:-',
         '2:5:pedidos/' || v_id || '/itens/2/00000000-0000-4000-8000-000000000000.png'] then
      raise exception 'itens de teste não foram gravados como esperado';
    end if;

    -- ---------- Rejeições (cada uma desfaz tudo o que a chamada tentou gravar) ----------
    v_outro := public.reservar_id_pedido();

    perform pg_temp.deve_falhar('total diferente da soma',
      pg_temp.chamada(v_outro, v_usuario, 99.00, pg_temp.itens_teste(v_outro)), '23514');

    perform pg_temp.deve_falhar('total negativo',
      pg_temp.chamada(v_outro, v_usuario, -1, pg_temp.itens_teste(v_outro)),
      '23514', 'pedidos_valor_total_check');

    perform pg_temp.deve_falhar('pedido sem itens',
      pg_temp.chamada(v_outro, v_usuario, 0, '[]'::jsonb), '23514');

    perform pg_temp.deve_falhar('itens que não são objetos',
      pg_temp.chamada(v_outro, v_usuario, 26.00, '[1, "texto", null]'::jsonb), '22023');

    perform pg_temp.deve_falhar('itens que não são um array',
      pg_temp.chamada(v_outro, v_usuario, 26.00, '{"ordem": 1}'::jsonb), '23514');

    perform pg_temp.deve_falhar('item sem campos obrigatórios',
      pg_temp.chamada(v_outro, v_usuario, 0, '[{"ordem": 1}]'::jsonb), '23502');

    perform pg_temp.deve_falhar('ordem repetida no mesmo pedido',
      pg_temp.chamada(v_outro, v_usuario, 26.00,
        pg_temp.itens_com(v_outro, 1, '{"ordem": 1, "imagem_caminho": null}')),
      '23505', 'pedido_itens_ordem_unica');

    perform pg_temp.deve_falhar('subtotal incoerente',
      pg_temp.chamada(v_outro, v_usuario, 25.00,
        pg_temp.itens_com(v_outro, 0, '{"subtotal": 20}')),
      '23514', 'pedido_itens_subtotal_coerente');

    perform pg_temp.deve_falhar('quantidade zero',
      pg_temp.chamada(v_outro, v_usuario, 5,
        pg_temp.itens_com(v_outro, 0, '{"quantidade": 0, "subtotal": 0}')),
      '23514', 'pedido_itens_quantidade_check');

    perform pg_temp.deve_falhar('valor unitário negativo',
      pg_temp.chamada(v_outro, v_usuario, 3,
        pg_temp.itens_com(v_outro, 0, '{"valor_unitario": -1, "subtotal": -2}')),
      '23514');

    perform pg_temp.deve_falhar('ordem zero',
      pg_temp.chamada(v_outro, v_usuario, 26.00, pg_temp.itens_com(v_outro, 0, '{"ordem": 0}')),
      '23514', 'pedido_itens_ordem_check');

    perform pg_temp.deve_falhar('produto em branco',
      pg_temp.chamada(v_outro, v_usuario, 26.00,
        pg_temp.itens_com(v_outro, 0, '{"produto": "  "}')),
      '23514', 'pedido_itens_produto_check');

    perform pg_temp.deve_falhar('forma de pagamento fora da lista',
      pg_temp.chamada(v_outro, v_usuario, 26.00, pg_temp.itens_teste(v_outro),
                      p_pagamento => 'boleto'),
      '23514', 'pedidos_forma_pagamento_check');

    perform pg_temp.deve_falhar('tipo de entrega fora da lista',
      pg_temp.chamada(v_outro, v_usuario, 26.00, pg_temp.itens_teste(v_outro),
                      p_entrega => 'correio'),
      '23514', 'pedidos_tipo_entrega_check');

    perform pg_temp.deve_falhar('cliente em branco',
      pg_temp.chamada(v_outro, v_usuario, 26.00, pg_temp.itens_teste(v_outro), p_cliente => '   '),
      '23514', 'pedidos_cliente_nome_check');

    perform pg_temp.deve_falhar('criador inexistente',
      pg_temp.chamada(v_outro, -1, 26.00, pg_temp.itens_teste(v_outro)),
      '23503', 'pedidos_criado_por_fkey');

    perform pg_temp.deve_falhar('id não reservado',
      pg_temp.chamada(v_outro + 1000000, v_usuario, 26.00, pg_temp.itens_teste(v_outro + 1000000)),
      '22023');

    -- caminhos fora do padrão: outro pedido, outra ordem, formato não aceito, conteúdo em Base64
    foreach v_caminho in array array[
      'pedidos/' || v_id || '/itens/2/00000000-0000-4000-8000-000000000000.png',
      'pedidos/' || v_outro || '/itens/1/00000000-0000-4000-8000-000000000000.png',
      'pedidos/' || v_outro || '/itens/2/00000000-0000-4000-8000-000000000000.gif',
      'data:image/png;base64,iVBORw0KGgo='
    ] loop
      perform pg_temp.deve_falhar('caminho de imagem fora do padrão: ' || v_caminho,
        pg_temp.chamada(v_outro, v_usuario, 26.00,
          pg_temp.itens_com(v_outro, 1, jsonb_build_object('imagem_caminho', v_caminho))),
        '23514', 'pedido_itens_imagem_caminho_padrao');
    end loop;

    if exists (select 1 from public.pedidos where id = v_outro)
       or exists (select 1 from public.pedido_itens where pedido_id = v_outro) then
      raise exception 'uma chamada recusada deixou pedido ou item gravado';
    end if;

    -- ---------- Integridade ----------
    perform pg_temp.deve_falhar('item sem pedido',
      format('insert into public.pedido_itens (pedido_id, produto, quantidade, valor_unitario,'
             ' subtotal, ordem) values (%s, %L, 1, 1, 1, 1)', v_outro + 1000000, 'Órfão'),
      '23503', 'pedido_itens_pedido_id_fkey');

    perform pg_temp.deve_falhar('apagar pedido com itens (RESTRICT)',
      format('delete from public.pedidos where id = %s', v_id),
      '23503', 'pedido_itens_pedido_id_fkey');

    perform pg_temp.deve_falhar('apagar usuário com pedidos (RESTRICT)',
      format('delete from public.usuarios where id = %s', v_usuario),
      '23503', 'pedidos_criado_por_fkey');

  exception when others then
    -- restaura as sequências antes de repassar o erro
    perform setval(v_seq_pedidos, coalesce(v_ult_pedidos, 1), v_ult_pedidos is not null);
    perform setval(v_seq_itens, coalesce(v_ult_itens, 1), v_ult_itens is not null);
    raise;
  end;

  perform setval(v_seq_pedidos, coalesce(v_ult_pedidos, 1), v_ult_pedidos is not null);
  perform setval(v_seq_itens, coalesce(v_ult_itens, 1), v_ult_itens is not null);

  if (select count(*) from public.pedidos) <> v_pedidos_antes + 1
     or (select count(*) from public.pedido_itens) <> v_itens_antes + 2 then
    raise exception 'contagem inesperada: só o pedido de teste (1 pedido, 2 itens) deveria existir';
  end if;

  raise notice 'pedidos_test: todas as verificações passaram';
end;
$teste$;

rollback;

-- Fora da transação: nada do teste permaneceu e as sequências voltaram ao valor anterior.
select
  (select count(*) from public.pedidos) as pedidos,
  (select count(*) from public.pedido_itens) as itens,
  pg_sequence_last_value(pg_get_serial_sequence('public.pedidos', 'id')::regclass)
    as ultimo_id_pedido,
  pg_sequence_last_value(pg_get_serial_sequence('public.pedido_itens', 'id')::regclass)
    as ultimo_id_item;
