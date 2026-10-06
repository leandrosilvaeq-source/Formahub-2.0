-- Teste transacional do Estoque com entrada por compra (migrations 20261009120000_estoque.sql
-- e 20261010120000_compras_estoque.sql).
--
-- Tudo roda dentro de BEGIN/ROLLBACK com dados fictícios; nenhuma linha real é lida para
-- alteração nem alterada. As compras passam pelas funções (como o servidor faz), então no
-- banco remoto as sequências das tabelas avançam (só saltos de id, sem linhas gravadas).
-- Qualquer falha interrompe com RAISE EXCEPTION.
-- Execução local (sem banco remoto): PostgreSQL ou PGlite com as migrations deste checkout.
-- Execução no remoto, só depois de aplicar as migrations e por pedido explícito:
--   supabase db query --linked -f supabase/tests/estoque_test.sql

begin;

-- Executa o comando e confere o código de erro esperado (SQLSTATE).
create function pg_temp.espera_erro(p_sql text, p_codigo text, p_caso text)
returns void
language plpgsql
as $f$
begin
  execute p_sql;
  raise exception 'deveria falhar com %: %', p_codigo, p_caso;
exception
  when others then
    if sqlstate <> p_codigo then
      raise exception 'caso "%": esperado %, veio % (%)', p_caso, p_codigo, sqlstate, sqlerrm;
    end if;
end;
$f$;

-- Itens de compra válidos, para montar os casos.
create function pg_temp.filamento(p_ordem int, p_extra jsonb default '{}')
returns jsonb
language sql
as $f$
  select jsonb_build_object(
    'ordem', p_ordem, 'categoria', 'filamento', 'quantidade', 3, 'cor', 'Preto',
    'material', 'PLA', 'tipo', 'silk', 'marca', 'Marca Teste', 'peso_rolo_g', '1000',
    'valor_unitario', '89.90', 'imagem_caminho', null) || p_extra;
$f$;

create function pg_temp.item(p_ordem int, p_categoria text, p_extra jsonb default '{}')
returns jsonb
language sql
as $f$
  select jsonb_build_object(
    'ordem', p_ordem, 'categoria', p_categoria, 'quantidade', 3, 'nome', 'Argola Teste',
    'valor_total', '10.00', 'imagem_caminho', null) || p_extra;
$f$;

-- Contagem de linhas das quatro tabelas, para conferir que uma recusa não gravou nada.
create function pg_temp.contagem()
returns text
language sql
as $f$
  select concat_ws('/',
    (select count(*) from public.compras), (select count(*) from public.compra_itens),
    (select count(*) from public.estoque_filamentos), (select count(*) from public.estoque_itens));
$f$;

do $teste$
declare
  v_leandro bigint;
  v_kassia  bigint;
  v_c1      bigint;
  v_c2      bigint;
  v_id      bigint;
  v_chave   uuid := '11111111-1111-4111-8111-111111111111';
  v_r       jsonb;
  v_linha   record;
  v_lista   jsonb;
  v_papel   text;
  v_tabela  text;
  v_funcao  record;
  v_antes   text;
  v_f1      bigint;
  v_a1      bigint;
  v_e1      bigint;
begin
  select id into v_leandro from public.usuarios where nome = 'Leandro';
  select id into v_kassia from public.usuarios where nome = 'Kassia';

  -- ---------- Estrutura e segurança ----------
  foreach v_tabela in array array['estoque_filamentos', 'estoque_itens', 'compras', 'compra_itens']
  loop
    if not (select relrowsecurity from pg_class where oid = ('public.' || v_tabela)::regclass) then
      raise exception 'RLS desligado em %', v_tabela;
    end if;
    if exists (select 1 from pg_policies where schemaname = 'public' and tablename = v_tabela)
    then
      raise exception '% não deveria ter policies', v_tabela;
    end if;
    foreach v_papel in array array['anon', 'authenticated', 'service_role'] loop
      if has_table_privilege(v_papel, 'public.' || v_tabela,
                             'select, insert, update, delete, truncate, references, trigger')
      then
        raise exception '% tem privilégio direto em %', v_papel, v_tabela;
      end if;
      if has_sequence_privilege(v_papel, 'public.' || v_tabela || '_id_seq',
                                'usage, select, update') then
        raise exception '% tem privilégio na sequência de %', v_papel, v_tabela;
      end if;
    end loop;
  end loop;

  -- Sem cadastro direto: as funções antigas não existem mais.
  if exists (select 1 from pg_proc where pronamespace = 'public'::regnamespace
               and proname in ('salvar_estoque_filamento', 'salvar_estoque_item')) then
    raise exception 'o cadastro direto de lotes deveria ter sido removido';
  end if;

  -- Data e custo saem do lote (vêm da compra); o lote exige o item de compra.
  if exists (select 1 from information_schema.columns
              where table_schema = 'public'
                and table_name in ('estoque_filamentos', 'estoque_itens')
                and column_name in ('data_compra', 'custo_kg', 'custo_unitario')) then
    raise exception 'data e custo deveriam ter saído das tabelas de lotes';
  end if;
  if (select count(*) from information_schema.columns
       where table_schema = 'public' and table_name in ('estoque_filamentos', 'estoque_itens')
         and column_name = 'compra_item_id' and is_nullable = 'NO') <> 2 then
    raise exception 'compra_item_id deveria ser obrigatório nos dois tipos de lote';
  end if;

  -- Assinaturas exatas usadas pelo servidor (app/estoque_repositorio.py), retorno e segurança.
  for v_funcao in
    select * from (values
      ('listar_estoque', '', 'jsonb'),
      ('reservar_id_compra', '', 'bigint'),
      ('registrar_compra',
       'p_id bigint, p_chave_envio uuid, p_data_compra date, p_local_tipo text, '
       'p_local_nome text, p_itens jsonb, p_usuario_id bigint',
       'jsonb'),
      ('editar_estoque_filamento',
       'p_id bigint, p_cor text, p_material text, p_tipo text, p_marca text, p_usuario_id bigint',
       'bigint'),
      ('editar_estoque_item',
       'p_id bigint, p_categoria text, p_nome text, p_usuario_id bigint',
       'bigint')
    ) as esperado (nome, argumentos, retorno)
  loop
    select p.oid, pg_get_function_identity_arguments(p.oid) as argumentos,
           pg_get_function_result(p.oid) as retorno, p.prosecdef, p.proconfig
      into v_linha
      from pg_proc as p
     where p.pronamespace = 'public'::regnamespace and p.proname = v_funcao.nome;
    if (select count(*) from pg_proc
         where pronamespace = 'public'::regnamespace and proname = v_funcao.nome) <> 1 then
      raise exception '% deveria ter uma única versão', v_funcao.nome;
    end if;
    if v_linha.argumentos <> v_funcao.argumentos then
      raise exception '% com argumentos inesperados: %', v_funcao.nome, v_linha.argumentos;
    end if;
    if v_linha.retorno <> v_funcao.retorno then
      raise exception '% com retorno inesperado: %', v_funcao.nome, v_linha.retorno;
    end if;
    if not v_linha.prosecdef or v_linha.proconfig is distinct from array['search_path=""'] then
      raise exception '% sem SECURITY DEFINER ou search_path vazio', v_funcao.nome;
    end if;
    if has_function_privilege('anon', v_linha.oid, 'execute')
       or has_function_privilege('authenticated', v_linha.oid, 'execute')
       or not has_function_privilege('service_role', v_linha.oid, 'execute') then
      raise exception '% com permissão de execução errada', v_funcao.nome;
    end if;
  end loop;

  -- Bucket das fotos: privado, 10 MB, só PNG/JPEG/WebP.
  if not exists (select 1 from storage.buckets
                  where id = 'compra-imagens' and public = false and file_size_limit = 10485760
                    and allowed_mime_types = array['image/png', 'image/jpeg', 'image/webp']) then
    raise exception 'bucket compra-imagens ausente ou com configuração errada';
  end if;

  -- ---------- Estoque vazio ----------
  if public.listar_estoque() <> '{"filamentos": [], "itens": []}'::jsonb then
    raise exception 'o estoque deveria começar vazio: %', public.listar_estoque();
  end if;

  -- ---------- Compra com as três categorias e duas linhas de filamento ----------
  v_c1 := public.reservar_id_compra();
  v_r := public.registrar_compra(
    v_c1, v_chave, '2026-09-10', 'mercado_livre', null,
    jsonb_build_array(
      pg_temp.filamento(1, jsonb_build_object(
        'quantidade', 3, 'peso_rolo_g', '1000', 'valor_unitario', '89.90',
        'imagem_caminho', 'compras/' || v_c1 || '/itens/1/0f8fad5b-d9cb-469f-a165-70867728950e.png')),
      pg_temp.filamento(2, '{"quantidade": 2, "peso_rolo_g": "750.5", "valor_unitario": "100",
                             "cor": "Branco", "tipo": "solido"}'),
      pg_temp.item(3, 'acessorio', '{"quantidade": 40, "valor_total": "14.00"}'),
      pg_temp.item(4, 'embalagem', '{"quantidade": 3, "valor_total": "10.00", "nome": "Caixa P"}')
    ),
    v_leandro);
  if v_r is distinct from jsonb_build_object('id', v_c1, 'duplicada', false) then
    raise exception 'registrar_compra devolveu %', v_r;
  end if;
  if pg_temp.contagem() <> '1/4/2/2' then
    raise exception 'a compra deveria gravar 1 compra, 4 itens e 4 lotes: %', pg_temp.contagem();
  end if;

  select * into v_linha from public.compras where id = v_c1;
  if (v_linha.data_compra, v_linha.local_tipo, v_linha.local_nome, v_linha.chave_envio,
      v_linha.criado_por) is distinct from
     (date '2026-09-10', 'mercado_livre', null::text, v_chave, v_leandro) then
    raise exception 'compra gravada com valores errados: %', row_to_json(v_linha);
  end if;

  -- Cada lote aponta para o item que o originou; peso recebido = rolos x peso por rolo.
  select f.*, ci.ordem, ci.quantidade, ci.imagem_caminho into v_linha
    from public.estoque_filamentos as f join public.compra_itens as ci on ci.id = f.compra_item_id
   where ci.compra_id = v_c1 and ci.ordem = 1;
  v_f1 := v_linha.id;
  if (v_linha.peso_disponivel_g, v_linha.cor, v_linha.tipo, v_linha.criado_por,
      v_linha.atualizado_por) is distinct from (3000::numeric, 'Preto', 'silk', v_leandro, v_leandro)
     or v_linha.imagem_caminho not like 'compras/' || v_c1 || '/itens/1/%' then
    raise exception 'lote de filamento errado: %', row_to_json(v_linha);
  end if;
  if (select f.peso_disponivel_g from public.estoque_filamentos as f
        join public.compra_itens as ci on ci.id = f.compra_item_id
       where ci.compra_id = v_c1 and ci.ordem = 2) <> 1501.00 then
    raise exception 'peso recebido da segunda linha deveria ser 2 x 750,5';
  end if;
  select i.id into v_a1 from public.estoque_itens as i
    join public.compra_itens as ci on ci.id = i.compra_item_id
   where ci.compra_id = v_c1 and i.categoria = 'acessorio' and i.quantidade_disponivel = 40;
  select i.id into v_e1 from public.estoque_itens as i
    join public.compra_itens as ci on ci.id = i.compra_item_id
   where ci.compra_id = v_c1 and i.categoria = 'embalagem' and i.nome = 'Caixa P'
     and i.quantidade_disponivel = 3;
  if v_a1 is null or v_e1 is null then
    raise exception 'lotes de acessório e embalagem não encontrados';
  end if;

  -- ---------- Reenvio da mesma submissão: não duplica ----------
  v_id := public.reservar_id_compra();
  v_r := public.registrar_compra(
    v_id, v_chave, '2026-09-10', 'mercado_livre', null,
    jsonb_build_array(pg_temp.item(1, 'acessorio')), v_leandro);
  if v_r is distinct from jsonb_build_object('id', v_c1, 'duplicada', true) then
    raise exception 'o reenvio deveria devolver a compra já gravada: %', v_r;
  end if;
  if pg_temp.contagem() <> '1/4/2/2' then
    raise exception 'o reenvio gravou algo: %', pg_temp.contagem();
  end if;

  -- ---------- Outra compra do mesmo insumo: lote separado; local personalizado ----------
  v_c2 := public.reservar_id_compra();
  perform public.registrar_compra(
    v_c2, '22222222-2222-4222-8222-222222222222', '2026-09-20', 'loja_fisica', 'Papelaria Centro',
    jsonb_build_array(pg_temp.filamento(1, '{"quantidade": 1, "valor_unitario": "0"}'),
                      pg_temp.item(2, 'acessorio', '{"quantidade": 7, "valor_total": "1.00"}')),
    v_kassia);
  if pg_temp.contagem() <> '2/6/3/3' then
    raise exception 'a segunda compra deveria criar lotes novos: %', pg_temp.contagem();
  end if;
  if (select local_nome from public.compras where id = v_c2) <> 'Papelaria Centro' then
    raise exception 'nome do local personalizado não gravado';
  end if;

  -- ---------- listar_estoque: valores da compra como texto, compra mais recente primeiro ----
  v_lista := public.listar_estoque();
  if jsonb_array_length(v_lista -> 'filamentos') <> 3 or jsonb_array_length(v_lista -> 'itens') <> 3
  then
    raise exception 'listar_estoque com quantidade errada: %', v_lista;
  end if;
  if v_lista -> 'filamentos' -> 0 ->> 'data_compra' <> '2026-09-20'
     or v_lista -> 'filamentos' -> 0 ->> 'valor_unitario' <> '0.00'
     or v_lista -> 'filamentos' -> 1 ->> 'id' <> (v_f1 + 1)::text  -- mesma data: id maior antes
     or v_lista -> 'filamentos' -> 1 ->> 'peso_disponivel_g' <> '1501.00'
     or v_lista -> 'filamentos' -> 1 ->> 'peso_rolo_g' <> '750.50'
     or v_lista -> 'filamentos' -> 1 ->> 'valor_unitario' <> '100.00'
     or jsonb_typeof(v_lista -> 'filamentos' -> 1 -> 'peso_disponivel_g') <> 'string'
     or v_lista -> 'itens' -> 0 ->> 'quantidade_comprada' <> '7'
     or v_lista -> 'itens' -> 0 ->> 'valor_total' <> '1.00'
     or jsonb_typeof(v_lista -> 'itens' -> 0 -> 'quantidade_disponivel') <> 'number' then
    raise exception 'formato de listar_estoque inesperado: %', v_lista;
  end if;

  -- ---------- Edição: só a descrição; saldo e compra não mudam ----------
  if public.editar_estoque_filamento(v_f1, 'Preto Fosco', 'PLA', 'velvet', 'Marca Teste', v_kassia)
     <> v_f1 then
    raise exception 'a edição deveria devolver o mesmo id';
  end if;
  select * into v_linha from public.estoque_filamentos where id = v_f1;
  if (v_linha.cor, v_linha.tipo, v_linha.peso_disponivel_g, v_linha.criado_por,
      v_linha.atualizado_por) is distinct from ('Preto Fosco', 'velvet', 3000::numeric, v_leandro,
                                                v_kassia) then
    raise exception 'edição do filamento errada: %', row_to_json(v_linha);
  end if;

  perform public.editar_estoque_item(v_e1, 'embalagem', 'Caixa Pequena', v_leandro);
  if (select (nome, quantidade_disponivel) from public.estoque_itens where id = v_e1)
     is distinct from ('Caixa Pequena'::text, 3) then
    raise exception 'edição da embalagem errada';
  end if;
  perform pg_temp.espera_erro(
    format('select public.editar_estoque_item(%s, %L, %L, %s)', v_e1, 'acessorio', 'X', v_leandro),
    'PT404', 'embalagem editada como acessório');
  perform pg_temp.espera_erro(
    format('select public.editar_estoque_filamento(-1, %L, %L, %L, %L, %s)',
           'Preto', 'PLA', 'solido', 'M', v_leandro),
    'PT404', 'filamento inexistente');
  perform pg_temp.espera_erro(
    format('select public.editar_estoque_item(%s, %L, %L, -1)', v_a1, 'acessorio', 'X'),
    'PT403', 'edição por usuário inexistente');
  perform pg_temp.espera_erro(
    format('select public.editar_estoque_filamento(%s, %L, %L, %L, %L, %s)',
           v_f1, ' Preto', 'PLA', 'solido', 'M', v_leandro),
    '23514', 'edição com cor inválida');

  -- Nenhum UPDATE aumenta o saldo nem troca a compra (nem direto na tabela).
  perform pg_temp.espera_erro(
    format('update public.estoque_filamentos set peso_disponivel_g = 3000.01 where id = %s', v_f1),
    'PT422', 'aumento do peso por fora da compra');
  perform pg_temp.espera_erro(
    format('update public.estoque_itens set quantidade_disponivel = 41 where id = %s', v_a1),
    'PT422', 'aumento da quantidade por fora da compra');
  perform pg_temp.espera_erro(
    format('update public.estoque_itens set compra_item_id = (select compra_item_id from'
           ' public.estoque_itens where id = %s) where id = %s', v_e1, v_a1),
    'PT422', 'lote trocado de compra');
  -- Diminuir continua possível no banco (reservado às baixas, que não existem nesta etapa).
  update public.estoque_itens set quantidade_disponivel = 39 where id = v_a1;
  if (select quantidade_disponivel from public.estoque_itens where id = v_a1) <> 39 then
    raise exception 'diminuir o saldo deveria ser aceito pelo gatilho';
  end if;

  -- Lote sem compra é recusado mesmo inserindo direto.
  perform pg_temp.espera_erro(
    format('insert into public.estoque_itens (categoria, nome, quantidade_disponivel, criado_por,'
           ' atualizado_por) values (%L, %L, 5, %s, %s)', 'acessorio', 'X', v_leandro, v_leandro),
    '23502', 'lote sem item de compra');

  -- ---------- Recusas: nada é gravado (nem pela metade) ----------
  v_antes := pg_temp.contagem();
  v_id := public.reservar_id_compra();

  perform pg_temp.espera_erro(
    format('select public.registrar_compra(%s, gen_random_uuid(), %L, %L, null, %L, -1)',
           v_id, '2026-09-01', 'shopee', jsonb_build_array(pg_temp.item(1, 'acessorio'))),
    'PT403', 'usuário inexistente');
  perform pg_temp.espera_erro(
    format('select public.registrar_compra(%s, gen_random_uuid(), %L, %L, null, %L, %s)',
           v_id + 1000, '2026-09-01', 'shopee', jsonb_build_array(pg_temp.item(1, 'acessorio')),
           v_leandro),
    '22023', 'id não reservado');
  perform pg_temp.espera_erro(
    format('select public.registrar_compra(%s, gen_random_uuid(), %L, %L, null, %L, %s)',
           v_id, '2026-09-01', 'shopee', '[]', v_leandro),
    '23514', 'compra sem itens');
  perform pg_temp.espera_erro(
    format('select public.registrar_compra(%s, gen_random_uuid(), %L, %L, null, %L, %s)',
           v_id, '2026-09-01', 'shopee', '[1]', v_leandro),
    '22023', 'item que não é objeto');
  perform pg_temp.espera_erro(
    format('select public.registrar_compra(%s, null, %L, %L, null, %L, %s)',
           v_id, '2026-09-01', 'shopee', jsonb_build_array(pg_temp.item(1, 'acessorio')),
           v_leandro),
    '23502', 'sem chave de envio');

  -- Compra válida até o último item: o item inválido desfaz a compra e os lotes anteriores.
  for v_linha in
    select * from (values
      ('local fora da lista', 'amazon', null, pg_temp.item(1, 'acessorio'), '23514'),
      ('Outro sem nome', 'outro', null, pg_temp.item(1, 'acessorio'), '23514'),
      ('Loja Física sem nome', 'loja_fisica', null, pg_temp.item(1, 'acessorio'), '23514'),
      ('plataforma com nome', 'shopee', 'Loja X', pg_temp.item(1, 'acessorio'), '23514'),
      ('nome do local com espaços', 'outro', ' Loja', pg_temp.item(1, 'acessorio'), '23514'),
      ('categoria inválida', 'shopee', null, pg_temp.item(1, 'parafuso'), '23514'),
      ('quantidade zero', 'shopee', null, pg_temp.item(1, 'acessorio', '{"quantidade": 0}'),
       '23514'),
      ('valor negativo', 'shopee', null, pg_temp.item(1, 'embalagem', '{"valor_total": "-0.01"}'),
       '23514'),
      ('nome nulo', 'shopee', null, pg_temp.item(1, 'embalagem', '{"nome": null}'), '23514'),
      ('acessório com cor', 'shopee', null, pg_temp.item(1, 'acessorio', '{"cor": "Preto"}'),
       '23514'),
      ('peso por rolo zero', 'shopee', null, pg_temp.filamento(1, '{"peso_rolo_g": "0"}'),
       '23514'),
      ('valor unitário negativo', 'shopee', null,
       pg_temp.filamento(1, '{"valor_unitario": "-1"}'), '23514'),
      ('tipo fora da lista', 'shopee', null, pg_temp.filamento(1, '{"tipo": "Sólido"}'), '23514'),
      ('filamento sem marca', 'shopee', null, pg_temp.filamento(1, '{"marca": null}'), '23514'),
      ('filamento com nome', 'shopee', null, pg_temp.filamento(1, '{"nome": "X"}'), '23514'),
      ('rolos demais', 'shopee', null, pg_temp.filamento(1, '{"quantidade": 10000}'), '23514'),
      ('cor longa', 'shopee', null,
       pg_temp.filamento(1, jsonb_build_object('cor', repeat('x', 61))), '23514'),
      ('foto fora do padrão', 'shopee', null,
       pg_temp.item(1, 'acessorio', '{"imagem_caminho": "pedidos/1/itens/1/x.png"}'), '23514'),
      ('quantidade decimal', 'shopee', null, pg_temp.item(1, 'acessorio', '{"quantidade": 1.5}'),
       '22P02')
    ) as caso (nome, local_tipo, local_nome, ultimo, codigo)
  loop
    perform pg_temp.espera_erro(
      format('select public.registrar_compra(%s, gen_random_uuid(), %L, %L, %L, %L, %s)',
             v_id, '2026-09-01', v_linha.local_tipo, v_linha.local_nome,
             jsonb_build_array(pg_temp.filamento(1), pg_temp.item(2, 'embalagem'),
                               v_linha.ultimo || jsonb_build_object('ordem', 3)),
             v_leandro),
      v_linha.codigo, 'compra: ' || v_linha.nome);
  end loop;

  perform pg_temp.espera_erro(
    format('select public.registrar_compra(%s, gen_random_uuid(), %L, %L, null, %L, %s)',
           v_id, '2026-09-01', 'shopee',
           jsonb_build_array(pg_temp.filamento(1), pg_temp.item(1, 'embalagem')), v_leandro),
    '23505', 'ordem repetida');
  perform pg_temp.espera_erro(
    format('select public.registrar_compra(%s, gen_random_uuid(), %L, %L, null, %L, %s)',
           v_id, '1999-12-31', 'shopee', jsonb_build_array(pg_temp.item(1, 'embalagem')),
           v_leandro),
    '23514', 'data fora do intervalo');

  if pg_temp.contagem() <> v_antes then
    raise exception 'uma recusa gravou algo: % -> %', v_antes, pg_temp.contagem();
  end if;
end;
$teste$;

-- ---------- Permissões na prática ----------
-- service_role (o servidor) usa as funções, mas não lê as tabelas direto.
set local role service_role;
do $papel$
begin
  perform public.listar_estoque();
  perform public.reservar_id_compra();
  perform pg_temp.espera_erro('select * from public.estoque_filamentos', '42501',
                              'service_role lendo a tabela');
  perform pg_temp.espera_erro('select * from public.compras', '42501',
                              'service_role lendo as compras');
  perform pg_temp.espera_erro('insert into public.compra_itens (categoria) values (''x'')',
                              '42501', 'service_role inserindo direto');
end;
$papel$;
reset role;

-- anon e authenticated não executam nada.
set local role anon;
do $papel$
begin
  perform pg_temp.espera_erro('select public.listar_estoque()', '42501', 'anon listando');
  perform pg_temp.espera_erro('select public.reservar_id_compra()', '42501', 'anon reservando');
  perform pg_temp.espera_erro(
    'select public.registrar_compra(1, gen_random_uuid(), ''2026-09-01'', ''shopee'', null,'
    ' ''[]'', 1)', '42501', 'anon registrando');
end;
$papel$;
reset role;

set local role authenticated;
do $papel$
begin
  perform pg_temp.espera_erro('select public.listar_estoque()', '42501', 'authenticated listando');
  perform pg_temp.espera_erro(
    'select public.editar_estoque_item(1, ''acessorio'', ''A'', 1)', '42501',
    'authenticated editando');
end;
$papel$;
reset role;

select 'estoque_test: ok' as resultado;

rollback;
