-- Teste transacional da migration 20261112120000_compra_janela_itens.sql (desconto, TikTok Shop,
-- medidas, cor/marca opcionais e valor unitário em todas as categorias).
--
-- Tudo roda dentro de BEGIN/ROLLBACK com dados fictícios; nenhuma linha real é alterada.
-- Qualquer falha interrompe com RAISE EXCEPTION.
-- Execução local: node supabase/tests/executar_pglite.mjs supabase/tests/compra_janela_itens_test.sql

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

create function pg_temp.item(p_ordem int, p_categoria text, p_extra jsonb)
returns jsonb
language sql
as $f$
  select jsonb_build_object('ordem', p_ordem, 'categoria', p_categoria) || p_extra;
$f$;

do $teste$
declare
  v_leandro  bigint;
  v_id       bigint;
  v_itens    jsonb;
  v_compra   jsonb;
  v_lotes    bigint;
  v_manut    bigint;
  v_registro text;
begin
  select id into v_leandro from public.usuarios where nome = 'Leandro';
  select count(*) into v_lotes from public.estoque_itens;
  select count(*) into v_manut from public.manutencoes;

  -- Compra mista: as cinco categorias, com os atributos novos, desconto e frete.
  v_id := public.reservar_id_compra();
  v_itens := jsonb_build_array(
    pg_temp.item(1, 'filamento', '{"quantidade": 2, "cor": "Preto", "material": "PLA",
      "tipo": "silk", "marca": "Marca F", "peso_rolo_g": "1000", "valor_unitario": "80.00"}'),
    pg_temp.item(2, 'acessorio', '{"quantidade": 10, "nome": "Argola", "cor": "Dourada",
      "medidas": "2 cm", "valor_unitario": "1.50", "valor_total": "15.00"}'),
    pg_temp.item(3, 'embalagem', '{"quantidade": 4, "nome": "Caixa", "cor": "Kraft",
      "medidas": "10x10x5 cm", "valor_unitario": "2.25", "valor_total": "9.00"}'),
    pg_temp.item(4, 'peca_manutencao', '{"quantidade": 2, "nome": "Bico 0,4",
      "marca": "Bambu", "valor_unitario": "15.00", "valor_total": "30.00"}'),
    pg_temp.item(5, 'outro', '{"quantidade": 1, "nome": "Fita dupla face",
      "valor_unitario": "7.50", "valor_total": "7.50"}'));
  -- subtotal = 160 + 15 + 9 + 30 + 7,50 = 221,50; total = 221,50 − 21,50 + 12 = 212
  perform public.registrar_compra(v_id, gen_random_uuid(), '2026-11-10', 'tiktok_shop', null,
                                  v_itens, v_leandro, 12, 21.50);

  select c into v_compra
    from jsonb_array_elements(public.listar_compras() -> 'compras') as c
   where (c ->> 'id')::bigint = v_id;
  if (v_compra ->> 'total')::numeric <> 212 or (v_compra ->> 'desconto')::numeric <> 21.50
     or v_compra ->> 'local_tipo' <> 'tiktok_shop' then
    raise exception 'total/desconto/local errados: %', v_compra;
  end if;
  select string_agg(concat_ws('|', i ->> 'categoria', i ->> 'cor', i ->> 'medidas', i ->> 'marca',
                              i ->> 'valor_unitario', i ->> 'valor'), ';' order by (i ->> 'ordem')::int)
    into v_registro
    from jsonb_array_elements(v_compra -> 'itens') as i;
  if v_registro <> 'filamento|Preto|Marca F|80.00|160.00;acessorio|Dourada|2 cm|1.50|15.00;'
                   'embalagem|Kraft|10x10x5 cm|2.25|9.00;peca_manutencao|Bambu|15.00|30.00;'
                   'outro|7.50|7.50' then
    raise exception 'atributos não persistidos: %', v_registro;
  end if;

  -- Estoque: só acessório e embalagem viram lote de item; custo unitário vem de valor_total.
  if (select count(*) from public.estoque_itens) <> v_lotes + 2
     or (select count(*) from public.manutencoes) <> v_manut then
    raise exception 'peças/itens gerais geraram estoque ou manutenção';
  end if;

  -- Edição: troca desconto e mantém os atributos.
  perform public.editar_compra(v_id, '2026-11-10', 'outro', 'Site X', 0, v_itens, v_leandro, 0);
  select c into v_compra
    from jsonb_array_elements(public.listar_compras() -> 'compras') as c
   where (c ->> 'id')::bigint = v_id;
  if (v_compra ->> 'total')::numeric <> 221.50 or v_compra ->> 'local_nome' <> 'Site X'
     or v_compra -> 'itens' -> 1 ->> 'medidas' <> '2 cm' then
    raise exception 'edição não persistiu: %', v_compra;
  end if;

  -- Chamada antiga (sem p_desconto) continua válida e grava desconto 0.
  v_id := public.reservar_id_compra();
  perform public.registrar_compra(v_id, gen_random_uuid(), '2026-11-10', 'shopee', null,
    jsonb_build_array(pg_temp.item(1, 'acessorio',
      '{"quantidade": 3, "nome": "Pino", "valor_total": "10.00"}')), v_leandro, 0);
  if (select desconto from public.compras where id = v_id) <> 0 then
    raise exception 'desconto padrão diferente de zero';
  end if;

  -- Recusas: nada gravado.
  perform pg_temp.espera_erro(
    format('select public.registrar_compra(public.reservar_id_compra(), gen_random_uuid(), '
           '''2026-11-10'', ''shopee'', null, %L, %s, 0, 8)',
           jsonb_build_array(pg_temp.item(1, 'outro',
             '{"quantidade": 1, "nome": "X", "valor_unitario": "7.50", "valor_total": "7.50"}')),
           v_leandro),
    '23514', 'desconto maior que o subtotal');
  perform pg_temp.espera_erro(
    format('select public.registrar_compra(public.reservar_id_compra(), gen_random_uuid(), '
           '''2026-11-10'', ''shopee'', null, %L, %s, 0, -1)',
           jsonb_build_array(pg_temp.item(1, 'outro',
             '{"quantidade": 1, "nome": "X", "valor_total": "7.50"}')), v_leandro),
    '23514', 'desconto negativo');
  perform pg_temp.espera_erro(
    format('select public.registrar_compra(public.reservar_id_compra(), gen_random_uuid(), '
           '''2026-11-10'', ''shopee'', null, %L, %s)',
           jsonb_build_array(pg_temp.item(1, 'acessorio',
             '{"quantidade": 3, "nome": "X", "valor_unitario": "1.00", "valor_total": "5.00"}')),
           v_leandro),
    '23514', 'valor_total diferente de quantidade x unitário');
  perform pg_temp.espera_erro(
    format('select public.registrar_compra(public.reservar_id_compra(), gen_random_uuid(), '
           '''2026-11-10'', ''shopee'', null, %L, %s)',
           jsonb_build_array(pg_temp.item(1, 'peca_manutencao',
             '{"quantidade": 1, "nome": "X", "medidas": "2 cm", "valor_total": "1.00"}')),
           v_leandro),
    '23514', 'medidas em peça');
  perform pg_temp.espera_erro(
    format('select public.registrar_compra(public.reservar_id_compra(), gen_random_uuid(), '
           '''2026-11-10'', ''shopee'', null, %L, %s)',
           jsonb_build_array(pg_temp.item(1, 'acessorio',
             '{"quantidade": 1, "nome": "X", "marca": "M", "valor_total": "1.00"}')), v_leandro),
    '23514', 'marca em acessório');
  perform pg_temp.espera_erro(
    format('select public.registrar_compra(public.reservar_id_compra(), gen_random_uuid(), '
           '''2026-11-10'', ''outro'', null, %L, %s)',
           jsonb_build_array(pg_temp.item(1, 'outro',
             '{"quantidade": 1, "nome": "X", "valor_total": "1.00"}')), v_leandro),
    '23514', 'Outros sites sem nome');
  perform pg_temp.espera_erro(
    format('select public.editar_compra(%s, ''2026-11-10'', ''shopee'', null, 0, %L, %s, 1000)',
           v_id, jsonb_build_array(pg_temp.item(1, 'acessorio',
             '{"quantidade": 3, "nome": "Pino", "valor_total": "10.00"}')), v_leandro),
    '23514', 'edição com desconto maior que o subtotal');
end;
$teste$;

-- Auxiliares não são executáveis por ninguém além do dono.
set local role service_role;
do $papel$
begin
  perform pg_temp.espera_erro(
    'select public.validar_entrada_compra(0, 0, ''[{}]'')', '42501', 'service_role auxiliar');
end;
$papel$;
reset role;

select 'compra_janela_itens_test: ok' as resultado;

rollback;
