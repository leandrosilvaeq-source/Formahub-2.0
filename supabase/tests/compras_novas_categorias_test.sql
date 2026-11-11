-- Teste transacional das categorias Peças de manutenção e Outros
-- (migration 20261111120000_compras_novas_categorias.sql).
--
-- Tudo roda dentro de BEGIN/ROLLBACK com dados fictícios; nenhuma linha real é alterada.
-- Qualquer falha interrompe com RAISE EXCEPTION.
-- Execução local: node supabase/tests/executar_pglite.mjs supabase/tests/compras_novas_categorias_test.sql

begin;

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
  v_leandro    bigint;
  v_id         bigint;
  v_r          jsonb;
  v_itens      jsonb;
  v_filamentos bigint;
  v_lotes      bigint;
  v_manut      bigint;
  v_compra     jsonb;
begin
  select id into v_leandro from public.usuarios where nome = 'Leandro';
  select count(*) into v_filamentos from public.estoque_filamentos;
  select count(*) into v_lotes from public.estoque_itens;
  select count(*) into v_manut from public.manutencoes;

  -- Compra com as cinco categorias misturadas, com frete e foto na peça.
  v_id := public.reservar_id_compra();
  v_itens := jsonb_build_array(
    jsonb_build_object('ordem', 1, 'categoria', 'filamento', 'quantidade', 1, 'cor', 'Preto',
      'material', 'PLA', 'tipo', 'solido', 'marca', 'Marca', 'peso_rolo_g', '1000',
      'valor_unitario', '80.00'),
    jsonb_build_object('ordem', 2, 'categoria', 'acessorio', 'quantidade', 10,
      'nome', 'Argola', 'valor_total', '20.00'),
    jsonb_build_object('ordem', 3, 'categoria', 'embalagem', 'quantidade', 5,
      'nome', 'Caixa', 'valor_total', '10.00'),
    jsonb_build_object('ordem', 4, 'categoria', 'peca_manutencao', 'quantidade', 2,
      'nome', 'Bico 0,4', 'valor_total', '30.00', 'imagem_caminho',
      'compras/' || v_id || '/itens/4/44444444-4444-4444-8444-444444444444.png'),
    jsonb_build_object('ordem', 5, 'categoria', 'outro', 'quantidade', 1,
      'nome', 'Fita dupla face', 'valor_total', '7.50'));
  v_r := public.registrar_compra(v_id, gen_random_uuid(), '2026-11-10', 'shopee', null,
                                 v_itens, v_leandro, 12.5);
  if (v_r ->> 'duplicada')::boolean then
    raise exception 'compra nova marcada como duplicada';
  end if;

  if (select count(*) from public.compra_itens where compra_id = v_id) <> 5 then
    raise exception 'itens da compra não gravados';
  end if;
  -- Só filamento, acessório e embalagem viram lote; manutenção não é lançada.
  if (select count(*) from public.estoque_filamentos) <> v_filamentos + 1
     or (select count(*) from public.estoque_itens) <> v_lotes + 2
     or exists (select 1 from public.estoque_itens
                 where categoria not in ('acessorio', 'embalagem'))
     or (select count(*) from public.manutencoes) <> v_manut then
    raise exception 'categorias novas geraram estoque ou manutenção';
  end if;

  -- Total da compra: cada item uma vez + frete = 80 + 20 + 10 + 30 + 7,50 + 12,50 = 160.
  select c into v_compra
    from jsonb_array_elements(public.listar_compras() -> 'compras') as c
   where (c ->> 'id')::bigint = v_id;
  if (v_compra ->> 'total')::numeric <> 160 then
    raise exception 'total da compra errado: %', v_compra ->> 'total';
  end if;

  -- Edição mantém as categorias novas sem gerar lotes para elas.
  perform public.editar_compra(v_id, '2026-11-10', 'shopee', null, 0,
    jsonb_build_array(v_itens -> 3, v_itens -> 4), v_leandro);
  if (select count(*) from public.compra_itens where compra_id = v_id) <> 2
     or (select count(*) from public.estoque_filamentos) <> v_filamentos
     or (select count(*) from public.estoque_itens) <> v_lotes then
    raise exception 'edição com categorias novas mexeu no estoque';
  end if;

  -- Categoria desconhecida e campos de filamento numa peça continuam recusados.
  perform pg_temp.espera_erro(
    format('select public.registrar_compra(public.reservar_id_compra(), gen_random_uuid(), '
           '''2026-11-10'', ''shopee'', null, %L, %s)',
           jsonb_build_array(jsonb_build_object('ordem', 1, 'categoria', 'ferramenta',
             'quantidade', 1, 'nome', 'X', 'valor_total', '1.00')), v_leandro),
    '23514', 'categoria desconhecida');
  perform pg_temp.espera_erro(
    format('select public.registrar_compra(public.reservar_id_compra(), gen_random_uuid(), '
           '''2026-11-10'', ''shopee'', null, %L, %s)',
           jsonb_build_array(jsonb_build_object('ordem', 1, 'categoria', 'peca_manutencao',
             'quantidade', 1, 'nome', 'X', 'valor_total', '1.00', 'cor', 'Preto')), v_leandro),
    '23514', 'peça com campo de filamento');
end;
$teste$;

select 'compras_novas_categorias_test: ok' as resultado;

rollback;
