-- Teste transacional de editar_compra (migration 20261107130000_editar_compra.sql).
--
-- Tudo roda dentro de BEGIN/ROLLBACK com dados fictícios; nenhuma linha real é alterada.
-- Qualquer falha interrompe com RAISE EXCEPTION.
-- Execução local: node supabase/tests/executar_pglite.mjs supabase/tests/editar_compra_test.sql
-- Execução no remoto, só depois de aplicar a migration e por pedido explícito:
--   supabase db query --linked -f supabase/tests/editar_compra_test.sql

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
  v_leandro bigint;
  v_id      bigint;
  v_r       jsonb;
  v_foto1   text;
  v_foto2   text;
  v_itens   jsonb;
  v_lotes   text;
begin
  select id into v_leandro from public.usuarios where nome = 'Leandro';

  -- Compra original: 1 filamento (com foto) e 1 acessório (com foto).
  v_id := public.reservar_id_compra();
  v_foto1 := 'compras/' || v_id || '/itens/1/11111111-1111-4111-8111-111111111111.png';
  v_foto2 := 'compras/' || v_id || '/itens/2/22222222-2222-4222-8222-222222222222.jpg';
  perform public.registrar_compra(
    v_id, gen_random_uuid(), '2026-09-01', 'shopee', null,
    jsonb_build_array(
      jsonb_build_object('ordem', 1, 'categoria', 'filamento', 'quantidade', 2, 'cor', 'Preto',
        'material', 'PLA', 'tipo', 'silk', 'marca', 'Marca', 'peso_rolo_g', '1000',
        'valor_unitario', '90.00', 'imagem_caminho', v_foto1),
      jsonb_build_object('ordem', 2, 'categoria', 'acessorio', 'quantidade', 10,
        'nome', 'Argola', 'valor_total', '20.00', 'imagem_caminho', v_foto2)),
    v_leandro, 5);

  -- Edição: remove o filamento, o acessório (com a foto mantida) passa a ser o item 1 e entra
  -- uma embalagem; muda data, local e frete.
  v_itens := jsonb_build_array(
    jsonb_build_object('ordem', 1, 'categoria', 'acessorio', 'quantidade', 12,
      'nome', 'Argola Dourada', 'valor_total', '24.00', 'imagem_caminho', v_foto2),
    jsonb_build_object('ordem', 2, 'categoria', 'embalagem', 'quantidade', 50,
      'nome', 'Saquinho', 'valor_total', '15.50', 'imagem_caminho', null));
  v_r := public.editar_compra(v_id, '2026-09-03', 'loja_fisica', 'Papelaria', 7.5, v_itens,
                              v_leandro);
  if v_r -> 'removidas' <> jsonb_build_array(v_foto1) then
    raise exception 'fotos removidas erradas: %', v_r;
  end if;

  if not exists (select 1 from public.compras
                  where id = v_id and data_compra = '2026-09-03' and local_tipo = 'loja_fisica'
                    and local_nome = 'Papelaria' and frete = 7.50) then
    raise exception 'dados da compra não foram atualizados';
  end if;
  if (select count(*) from public.compra_itens where compra_id = v_id) <> 2 then
    raise exception 'itens da compra não foram trocados';
  end if;
  select string_agg(i.categoria || ':' || i.nome || ':' || i.quantidade_disponivel, ',' order by ci.ordem)
    into v_lotes
    from public.estoque_itens as i
    join public.compra_itens as ci on ci.id = i.compra_item_id
   where ci.compra_id = v_id;
  if v_lotes <> 'acessorio:Argola Dourada:12,embalagem:Saquinho:50' then
    raise exception 'lotes errados: %', v_lotes;
  end if;
  if exists (select 1 from public.estoque_filamentos as f
               join public.compra_itens as ci on ci.id = f.compra_item_id
              where ci.compra_id = v_id) then
    raise exception 'lote do filamento removido continua no estoque';
  end if;

  -- Recusas: nada muda.
  perform pg_temp.espera_erro(
    format('select public.editar_compra(%s, ''2026-09-01'', ''shopee'', null, 0, %L, %s)',
           v_id + 1000, v_itens, v_leandro),
    'PT404', 'compra inexistente');
  perform pg_temp.espera_erro(
    format('select public.editar_compra(%s, ''2026-09-01'', ''shopee'', null, 0, ''[]'', %s)',
           v_id, v_leandro),
    '23514', 'sem itens');
  perform pg_temp.espera_erro(
    format('select public.editar_compra(%s, ''2026-09-01'', ''shopee'', null, 0, %L, -1)',
           v_id, v_itens),
    'PT403', 'usuário inválido');
  -- Foto de outra compra: recusada pelo CHECK do caminho.
  perform pg_temp.espera_erro(
    format('select public.editar_compra(%s, ''2026-09-01'', ''shopee'', null, 0, %L, %s)',
           v_id, jsonb_build_array(jsonb_build_object('ordem', 1, 'categoria', 'acessorio',
             'quantidade', 1, 'nome', 'X', 'valor_total', '1.00', 'imagem_caminho',
             'compras/' || (v_id + 1) || '/itens/1/33333333-3333-4333-8333-333333333333.png')),
           v_leandro),
    '23514', 'foto de outra compra');
  if (select data_compra from public.compras where id = v_id) <> '2026-09-03' then
    raise exception 'recusa alterou a compra';
  end if;

  -- Estoque já usado (saldo menor que o comprado): edição recusada.
  update public.estoque_itens set quantidade_disponivel = 1
   where compra_item_id in (select id from public.compra_itens where compra_id = v_id and ordem = 1);
  perform pg_temp.espera_erro(
    format('select public.editar_compra(%s, ''2026-09-01'', ''shopee'', null, 0, %L, %s)',
           v_id, v_itens, v_leandro),
    'PT409', 'estoque já usado');
end;
$teste$;

-- anon e authenticated não executam.
set local role anon;
do $papel$
begin
  perform pg_temp.espera_erro(
    'select public.editar_compra(1, ''2026-09-01'', ''shopee'', null, 0, ''[]'', 1)', '42501',
    'anon editando');
end;
$papel$;
reset role;

set local role authenticated;
do $papel$
begin
  perform pg_temp.espera_erro(
    'select public.editar_compra(1, ''2026-09-01'', ''shopee'', null, 0, ''[]'', 1)', '42501',
    'authenticated editando');
end;
$papel$;
reset role;

select 'editar_compra_test: ok' as resultado;

rollback;
