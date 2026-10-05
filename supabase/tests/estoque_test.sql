-- Teste transacional da migration do Estoque (20261009120000_estoque.sql).
--
-- Tudo roda dentro de BEGIN/ROLLBACK com dados fictícios; nenhum lote real é lido para
-- alteração nem alterado. Os cadastros passam pelas funções (como o servidor faz), então no
-- banco remoto as sequências das tabelas avançam (só saltos de id, sem linhas gravadas).
-- Qualquer falha interrompe com RAISE EXCEPTION.
-- Execução local (sem banco remoto): PostgreSQL ou PGlite com as migrations deste checkout.
-- Execução no remoto, só depois de aplicar a migration e por pedido explícito:
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

do $teste$
declare
  v_leandro bigint;
  v_kassia  bigint;
  v_f1      bigint;
  v_f2      bigint;
  v_a1      bigint;
  v_a2      bigint;
  v_e1      bigint;
  v_linha   record;
  v_lista   jsonb;
  v_papel   text;
  v_tabela  text;
  v_funcao  record;
begin
  select id into v_leandro from public.usuarios where nome = 'Leandro';
  select id into v_kassia from public.usuarios where nome = 'Kassia';

  -- ---------- Estrutura e segurança ----------
  foreach v_tabela in array array['estoque_filamentos', 'estoque_itens'] loop
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

  -- Assinaturas exatas usadas pelo servidor (app/estoque_repositorio.py), retorno e segurança.
  for v_funcao in
    select * from (values
      ('listar_estoque', '', 'jsonb'),
      ('salvar_estoque_filamento',
       'p_id bigint, p_cor text, p_material text, p_tipo text, p_marca text, '
       'p_peso_disponivel_g numeric, p_data_compra date, p_custo_kg numeric, p_usuario_id bigint',
       'bigint'),
      ('salvar_estoque_item',
       'p_id bigint, p_categoria text, p_nome text, p_quantidade_disponivel integer, '
       'p_data_compra date, p_custo_unitario numeric, p_usuario_id bigint',
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

  -- ---------- Estoque vazio ----------
  if public.listar_estoque() <> '{"filamentos": [], "itens": []}'::jsonb then
    raise exception 'o estoque deveria começar vazio: %', public.listar_estoque();
  end if;

  -- ---------- Filamentos: cadastro, lotes distintos do mesmo insumo, edição ----------
  v_f1 := public.salvar_estoque_filamento(
    null, 'Preto', 'PLA', 'solido', 'Marca Teste', 750.5, '2026-08-01', 119.9, v_leandro);
  v_f2 := public.salvar_estoque_filamento(
    null, 'Preto', 'PLA', 'solido', 'Marca Teste', 1000, '2026-09-15', 135.9, v_leandro);
  if v_f1 is null or v_f2 is null or v_f1 = v_f2 then
    raise exception 'lotes do mesmo filamento deveriam ter ids distintos';
  end if;

  select * into v_linha from public.estoque_filamentos where id = v_f1;
  if (v_linha.peso_disponivel_g, v_linha.custo_kg, v_linha.data_compra, v_linha.tipo)
     is distinct from (750.50::numeric, 119.90::numeric, date '2026-08-01', 'solido') then
    raise exception 'filamento gravado com valores errados: %', row_to_json(v_linha);
  end if;
  if (v_linha.criado_por, v_linha.atualizado_por) is distinct from (v_leandro, v_leandro)
     or v_linha.criado_em is null or v_linha.atualizado_em is null then
    raise exception 'auditoria do cadastro errada: %', row_to_json(v_linha);
  end if;

  -- Edição por outra usuária: muda o lote e o responsável; quem cadastrou continua igual.
  if public.salvar_estoque_filamento(
       v_f1, 'Preto', 'PLA', 'velvet', 'Marca Teste', 0, '2026-08-02', 0, v_kassia) <> v_f1 then
    raise exception 'a edição deveria devolver o mesmo id';
  end if;
  select * into v_linha from public.estoque_filamentos where id = v_f1;
  if (v_linha.tipo, v_linha.peso_disponivel_g, v_linha.custo_kg, v_linha.data_compra)
     is distinct from ('velvet', 0::numeric, 0::numeric, date '2026-08-02')
     or (v_linha.criado_por, v_linha.atualizado_por) is distinct from (v_leandro, v_kassia) then
    raise exception 'edição do filamento errada: %', row_to_json(v_linha);
  end if;
  if (select peso_disponivel_g from public.estoque_filamentos where id = v_f2) <> 1000 then
    raise exception 'editar um lote alterou o outro';
  end if;
  if (select count(*) from public.estoque_filamentos) <> 2 then
    raise exception 'a edição não deveria criar lote';
  end if;

  -- Peso com mais de 2 casas é arredondado pelo numeric(12, 2) (a tela recusa antes).
  perform public.salvar_estoque_filamento(
    v_f2, 'Preto', 'PLA', 'solido', 'Marca Teste', 1.005, '2026-09-15', 1, v_leandro);
  if (select peso_disponivel_g from public.estoque_filamentos where id = v_f2) <> 1.01 then
    raise exception 'peso deveria ficar com 2 casas decimais';
  end if;

  -- ---------- Acessórios e embalagens ----------
  v_a1 := public.salvar_estoque_item(null, 'acessorio', 'Argola', 40, '2026-08-01', 0.35, v_leandro);
  v_a2 := public.salvar_estoque_item(null, 'acessorio', 'Argola', 1200, '2026-09-01', 0.3, v_leandro);
  v_e1 := public.salvar_estoque_item(null, 'embalagem', 'Caixa P', 30, '2026-09-02', 1.2, v_kassia);
  if v_a1 = v_a2 then
    raise exception 'lotes do mesmo acessório deveriam ter ids distintos';
  end if;

  perform public.salvar_estoque_item(v_e1, 'embalagem', 'Caixa P', 12, '2026-09-02', 1.25, v_leandro);
  select * into v_linha from public.estoque_itens where id = v_e1;
  if (v_linha.categoria, v_linha.quantidade_disponivel, v_linha.custo_unitario,
      v_linha.criado_por, v_linha.atualizado_por)
     is distinct from ('embalagem', 12, 1.25::numeric, v_kassia, v_leandro) then
    raise exception 'edição da embalagem errada: %', row_to_json(v_linha);
  end if;

  -- A categoria não muda: editar uma embalagem como acessório é "não encontrado".
  perform pg_temp.espera_erro(
    format('select public.salvar_estoque_item(%s, %L, %L, 1, %L, 1, %s)',
           v_e1, 'acessorio', 'Caixa P', '2026-09-02', v_leandro),
    'PT404', 'embalagem editada como acessório');
  if (select categoria from public.estoque_itens where id = v_e1) <> 'embalagem' then
    raise exception 'a categoria mudou';
  end if;

  -- ---------- listar_estoque: números como texto, compra mais recente primeiro ----------
  v_lista := public.listar_estoque();
  if jsonb_array_length(v_lista -> 'filamentos') <> 2 or jsonb_array_length(v_lista -> 'itens') <> 3
  then
    raise exception 'listar_estoque com quantidade errada: %', v_lista;
  end if;
  if v_lista -> 'filamentos' -> 0 ->> 'id' <> v_f2::text
     or jsonb_typeof(v_lista -> 'filamentos' -> 0 -> 'peso_disponivel_g') <> 'string'
     or v_lista -> 'filamentos' -> 0 ->> 'peso_disponivel_g' <> '1.01'
     or v_lista -> 'filamentos' -> 0 ->> 'data_compra' <> '2026-09-15'
     or jsonb_typeof(v_lista -> 'itens' -> 0 -> 'custo_unitario') <> 'string'
     or jsonb_typeof(v_lista -> 'itens' -> 0 -> 'quantidade_disponivel') <> 'number' then
    raise exception 'formato de listar_estoque inesperado: %', v_lista;
  end if;

  -- ---------- Recusas ----------
  perform pg_temp.espera_erro(
    'select public.salvar_estoque_filamento(null, ''Preto'', ''PLA'', ''solido'', ''M'', 1, '
    '''2026-09-01'', 1, -1)', 'PT403', 'usuário inexistente');
  perform pg_temp.espera_erro(
    'select public.salvar_estoque_item(null, ''acessorio'', ''A'', 1, ''2026-09-01'', 1, null)',
    'PT403', 'usuário nulo');
  perform pg_temp.espera_erro(
    format('select public.salvar_estoque_filamento(-1, %L, %L, %L, %L, 1, %L, 1, %s)',
           'Preto', 'PLA', 'solido', 'M', '2026-09-01', v_leandro),
    'PT404', 'filamento inexistente');
  perform pg_temp.espera_erro(
    format('select public.salvar_estoque_item(-1, %L, %L, 1, %L, 1, %s)',
           'embalagem', 'Caixa', '2026-09-01', v_leandro),
    'PT404', 'item inexistente');

  -- Valores inválidos (CHECK, NOT NULL, tipo e limites do numeric).
  for v_linha in
    select * from (values
      ('peso negativo', '''Preto'', ''PLA'', ''solido'', ''M'', -0.01, ''2026-09-01'', 1', '23514'),
      ('custo negativo', '''Preto'', ''PLA'', ''solido'', ''M'', 1, ''2026-09-01'', -1', '23514'),
      ('tipo fora da lista', '''Preto'', ''PLA'', ''Sólido'', ''M'', 1, ''2026-09-01'', 1', '23514'),
      ('cor vazia', ''''', ''PLA'', ''solido'', ''M'', 1, ''2026-09-01'', 1', '23514'),
      ('cor com espaços', ''' Preto'', ''PLA'', ''solido'', ''M'', 1, ''2026-09-01'', 1', '23514'),
      ('material longo', '''Preto'', repeat(''x'', 61), ''solido'', ''M'', 1, ''2026-09-01'', 1',
       '23514'),
      ('marca nula', '''Preto'', ''PLA'', ''solido'', null, 1, ''2026-09-01'', 1', '23502'),
      ('data nula', '''Preto'', ''PLA'', ''solido'', ''M'', 1, null, 1', '23502'),
      ('peso nulo', '''Preto'', ''PLA'', ''solido'', ''M'', null, ''2026-09-01'', 1', '23502'),
      ('peso grande demais', '''Preto'', ''PLA'', ''solido'', ''M'', 1e10, ''2026-09-01'', 1',
       '22003')
    ) as caso (nome, argumentos, codigo)
  loop
    perform pg_temp.espera_erro(
      format('select public.salvar_estoque_filamento(null, %s, %s)', v_linha.argumentos, v_leandro),
      v_linha.codigo, 'filamento: ' || v_linha.nome);
  end loop;

  for v_linha in
    select * from (values
      ('categoria inválida', '''parafuso'', ''A'', 1, ''2026-09-01'', 1', '23514'),
      ('quantidade negativa', '''acessorio'', ''A'', -1, ''2026-09-01'', 1', '23514'),
      ('custo negativo', '''embalagem'', ''A'', 1, ''2026-09-01'', -0.01', '23514'),
      ('nome vazio', '''embalagem'', '''', 1, ''2026-09-01'', 1', '23514'),
      ('nome nulo', '''embalagem'', null, 1, ''2026-09-01'', 1', '23502'),
      ('quantidade decimal', '''acessorio'', ''A'', 1.5, ''2026-09-01'', 1', '42883')
    ) as caso (nome, argumentos, codigo)
  loop
    perform pg_temp.espera_erro(
      format('select public.salvar_estoque_item(null, %s, %s)', v_linha.argumentos, v_leandro),
      v_linha.codigo, 'item: ' || v_linha.nome);
  end loop;

  -- Edição inválida não altera o lote.
  perform pg_temp.espera_erro(
    format('select public.salvar_estoque_item(%s, %L, %L, -5, %L, 1, %s)',
           v_a1, 'acessorio', 'Argola', '2026-08-01', v_leandro),
    '23514', 'edição com quantidade negativa');
  if (select quantidade_disponivel from public.estoque_itens where id = v_a1) <> 40 then
    raise exception 'edição recusada alterou o lote';
  end if;

  if (select count(*) from public.estoque_filamentos) <> 2
     or (select count(*) from public.estoque_itens) <> 3 then
    raise exception 'uma recusa gravou algo';
  end if;
end;
$teste$;

-- ---------- Permissões na prática ----------
-- service_role (o servidor) usa as funções, mas não lê as tabelas direto.
set local role service_role;
do $papel$
begin
  perform public.listar_estoque();
  perform pg_temp.espera_erro('select * from public.estoque_filamentos', '42501',
                              'service_role lendo a tabela');
  perform pg_temp.espera_erro('insert into public.estoque_itens (categoria) values (''x'')',
                              '42501', 'service_role inserindo direto');
end;
$papel$;
reset role;

-- anon e authenticated não executam nada.
set local role anon;
do $papel$
begin
  perform pg_temp.espera_erro('select public.listar_estoque()', '42501', 'anon listando');
  perform pg_temp.espera_erro(
    'select public.salvar_estoque_item(null, ''acessorio'', ''A'', 1, ''2026-09-01'', 1, 1)',
    '42501', 'anon salvando');
end;
$papel$;
reset role;

set local role authenticated;
do $papel$
begin
  perform pg_temp.espera_erro('select public.listar_estoque()', '42501', 'authenticated listando');
end;
$papel$;
reset role;

select 'estoque_test: ok' as resultado;

rollback;
