-- Janela Registrar Compra reformulada (20261112): lista única de itens, valor unitário em todas
-- as categorias, atributos novos, desconto e TikTok Shop.
--
-- Depende de 20261111120000_compras_novas_categorias.sql (categorias peca_manutencao e outro).
-- Nenhum dado existente é apagado ou recalculado:
-- 1. compras.desconto (R$, não negativo, padrão 0). Total da compra = itens − desconto + frete.
--    Como o frete, o desconto fica só na compra: NÃO entra no custo dos lotes (custo/kg do
--    filamento e custo unitário de acessórios e embalagens continuam como antes).
-- 2. Local: 'tiktok_shop' passa a ser aceito. 'outro' continua o mesmo código (a tela o mostra
--    como "Outros sites") e, como Loja Física, exige o nome.
-- 3. compra_itens:
--    - medidas (texto opcional): acessórios e embalagens;
--    - cor (opcional) também em acessórios e embalagens; marca (opcional) em peças e itens
--      gerais. No filamento, cor e marca continuam obrigatórias;
--    - valor_unitario também fora do filamento. Nessas categorias valor_total continua
--      obrigatório (é dele que sai o custo unitário do lote = valor_total / quantidade) e, quando
--      há valor_unitario, valor_total = quantidade × valor_unitario. Itens antigos ficam com
--      valor_unitario nulo e o valor_total que já tinham.
-- 4. registrar_compra e editar_compra ganham p_desconto (padrão 0: chamadas antigas continuam
--    válidas) e gravam medidas. O desconto não pode passar do subtotal dos itens. As versões
--    anteriores são removidas para não haver sobrecarga ambígua no PostgREST.
-- 5. listar_compras devolve desconto, valor_unitario e medidas, e o total já com o desconto.
--
-- Os lotes continuam nascendo só de filamento, acessório e embalagem; peças e itens gerais
-- ficam só na compra, sem manutenção realizada.
-- Migration LOCAL: aplicar somente por pedido explícito (supabase db push).

-- ---------- Compras: desconto e TikTok Shop ----------
alter table public.compras
  add column desconto numeric(12, 2) not null default 0
  constraint compras_desconto_nao_negativo check (desconto >= 0);

alter table public.compras drop constraint compras_local_tipo_check;
alter table public.compras
  add constraint compras_local_tipo_check
  check (local_tipo in ('tiktok_shop', 'mercado_livre', 'aliexpress', 'shopee', 'loja_fisica',
                        'outro'));

-- ---------- Itens: medidas, cor/marca opcionais e valor unitário ----------
alter table public.compra_itens add column medidas text;

alter table public.compra_itens drop constraint compra_itens_campos_da_categoria;
alter table public.compra_itens
  add constraint compra_itens_campos_da_categoria check (
    case categoria
      when 'filamento' then
        cor is not null and material is not null and tipo is not null and marca is not null
        and peso_rolo_g is not null and valor_unitario is not null
        and nome is null and valor_total is null and medidas is null
        and quantidade <= 9999
      else
        nome is not null and valor_total is not null
        and material is null and tipo is null and peso_rolo_g is null
        and (valor_unitario is null or valor_total = quantidade * valor_unitario)
        and (cor is null or categoria in ('acessorio', 'embalagem'))
        and (medidas is null or categoria in ('acessorio', 'embalagem'))
        and (marca is null or categoria in ('peca_manutencao', 'outro'))
    end
  );

alter table public.compra_itens drop constraint compra_itens_textos_validos;
alter table public.compra_itens
  add constraint compra_itens_textos_validos check (
    (cor is null or (char_length(cor) between 1 and 60 and cor = btrim(cor)))
    and (material is null or (char_length(material) between 1 and 60 and material = btrim(material)))
    and (marca is null or (char_length(marca) between 1 and 60 and marca = btrim(marca)))
    and (nome is null or (char_length(nome) between 1 and 60 and nome = btrim(nome)))
    and (medidas is null or (char_length(medidas) between 1 and 60 and medidas = btrim(medidas)))
  );

-- ---------- Funções auxiliares (só usadas pelas funções abaixo) ----------
-- Grava os itens de p_itens na compra p_id e cria um lote por item de filamento, acessório e
-- embalagem. Depois confere o desconto contra o subtotal dos itens.
create function public.gravar_itens_compra(
  p_id         bigint,
  p_itens      jsonb,
  p_usuario_id bigint,
  p_desconto   numeric
)
returns void
language plpgsql
security definer
set search_path = ''
as $corpo$
begin
  insert into public.compra_itens (
    compra_id, ordem, categoria, quantidade, cor, material, tipo, marca, peso_rolo_g,
    valor_unitario, nome, valor_total, medidas, imagem_caminho
  )
  select p_id, i.ordem, i.categoria, i.quantidade, i.cor, i.material, i.tipo, i.marca,
         i.peso_rolo_g, i.valor_unitario, i.nome, i.valor_total, i.medidas, i.imagem_caminho
    from jsonb_to_recordset(p_itens) as i(
      ordem integer, categoria text, quantidade integer, cor text, material text, tipo text,
      marca text, peso_rolo_g numeric, valor_unitario numeric, nome text, valor_total numeric,
      medidas text, imagem_caminho text
    );

  if p_desconto > (select sum(case when ci.categoria = 'filamento'
                                   then ci.quantidade * ci.valor_unitario
                                   else ci.valor_total end)
                     from public.compra_itens as ci
                    where ci.compra_id = p_id) then
    raise exception 'desconto maior que o subtotal dos itens' using errcode = '23514';
  end if;

  -- Um lote por item: peso recebido = rolos x peso por rolo; unidades = quantidade.
  insert into public.estoque_filamentos (
    compra_item_id, cor, material, tipo, marca, peso_disponivel_g, criado_por, atualizado_por
  )
  select ci.id, ci.cor, ci.material, ci.tipo, ci.marca, ci.quantidade * ci.peso_rolo_g,
         p_usuario_id, p_usuario_id
    from public.compra_itens as ci
   where ci.compra_id = p_id and ci.categoria = 'filamento'
   order by ci.ordem;

  insert into public.estoque_itens (
    compra_item_id, categoria, nome, quantidade_disponivel, criado_por, atualizado_por
  )
  select ci.id, ci.categoria, ci.nome, ci.quantidade, p_usuario_id, p_usuario_id
    from public.compra_itens as ci
   where ci.compra_id = p_id and ci.categoria in ('acessorio', 'embalagem')
   order by ci.ordem;
end;
$corpo$;

-- Recusa (23514/22023) valores e itens inválidos comuns a registrar_compra e editar_compra.
create function public.validar_entrada_compra(p_frete numeric, p_desconto numeric, p_itens jsonb)
returns void
language plpgsql
immutable
set search_path = ''
as $corpo$
begin
  if p_frete is null or p_frete < 0 or p_frete > 9999999.99 or p_frete <> round(p_frete, 2) then
    raise exception 'frete inválido' using errcode = '23514';
  end if;
  if p_desconto is null or p_desconto < 0 or p_desconto > 9999999.99
     or p_desconto <> round(p_desconto, 2) then
    raise exception 'desconto inválido' using errcode = '23514';
  end if;
  if p_itens is null or jsonb_typeof(p_itens) <> 'array' or jsonb_array_length(p_itens) = 0 then
    raise exception 'compra sem itens' using errcode = '23514';
  end if;
  if exists (select 1 from jsonb_array_elements(p_itens) as e where jsonb_typeof(e) <> 'object') then
    raise exception 'item de compra inválido' using errcode = '22023';
  end if;
end;
$corpo$;

-- ---------- registrar_compra com desconto ----------
drop function public.registrar_compra(bigint, uuid, date, text, text, jsonb, bigint, numeric);

create function public.registrar_compra(
  p_id          bigint,
  p_chave_envio uuid,
  p_data_compra date,
  p_local_tipo  text,
  p_local_nome  text,
  p_itens       jsonb,
  p_usuario_id  bigint,
  p_frete       numeric default 0,
  p_desconto    numeric default 0
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $corpo$
declare
  v_id bigint;
begin
  if not exists (select 1 from public.usuarios
                  where id = p_usuario_id and nome in ('Leandro', 'Kassia', 'Marise')) then
    raise exception 'usuário inválido' using errcode = 'PT403';
  end if;

  -- só aceita um id já reservado (evita colisão com ids futuros da sequência)
  if p_id is null or p_id < 1 or p_id > coalesce(
       pg_sequence_last_value(pg_get_serial_sequence('public.compras', 'id')::regclass), 0) then
    raise exception 'id de compra não reservado' using errcode = '22023';
  end if;

  perform public.validar_entrada_compra(p_frete, p_desconto, p_itens);

  -- Mesma submissão de novo: devolve a compra já gravada (o ON CONFLICT espera a outra
  -- transação terminar, então dois envios simultâneos também gravam uma vez só).
  insert into public.compras (
    id, data_compra, local_tipo, local_nome, frete, desconto, chave_envio, criado_por
  )
  overriding system value
  values (p_id, p_data_compra, p_local_tipo, p_local_nome, p_frete, p_desconto, p_chave_envio,
          p_usuario_id)
  on conflict (chave_envio) do nothing
  returning id into v_id;
  if v_id is null then
    select id into v_id from public.compras where chave_envio = p_chave_envio;
    return jsonb_build_object('id', v_id, 'duplicada', true);
  end if;

  perform public.gravar_itens_compra(p_id, p_itens, p_usuario_id, p_desconto);

  return jsonb_build_object('id', p_id, 'duplicada', false);
end;
$corpo$;

-- ---------- editar_compra com desconto ----------
drop function public.editar_compra(bigint, date, text, text, numeric, jsonb, bigint);

create function public.editar_compra(
  p_id          bigint,
  p_data_compra date,
  p_local_tipo  text,
  p_local_nome  text,
  p_frete       numeric,
  p_itens       jsonb,
  p_usuario_id  bigint,
  p_desconto    numeric default 0
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $corpo$
declare
  v_antigas text[];
begin
  if not exists (select 1 from public.usuarios
                  where id = p_usuario_id and nome in ('Leandro', 'Kassia', 'Marise')) then
    raise exception 'usuário inválido' using errcode = 'PT403';
  end if;

  perform public.validar_entrada_compra(p_frete, p_desconto, p_itens);

  -- Trava a compra: duas edições simultâneas da mesma compra acontecem uma depois da outra.
  perform 1 from public.compras where id = p_id for update;
  if not found then
    raise exception 'compra não encontrada' using errcode = 'PT404';
  end if;

  if exists (select 1
               from public.estoque_filamentos as f
               join public.compra_itens as ci on ci.id = f.compra_item_id
              where ci.compra_id = p_id and f.peso_disponivel_g < ci.quantidade * ci.peso_rolo_g)
     or exists (select 1
                  from public.estoque_itens as i
                  join public.compra_itens as ci on ci.id = i.compra_item_id
                 where ci.compra_id = p_id and i.quantidade_disponivel < ci.quantidade) then
    raise exception 'compra com estoque já usado' using errcode = 'PT409';
  end if;

  select coalesce(array_agg(ci.imagem_caminho) filter (where ci.imagem_caminho is not null), '{}')
    into v_antigas
    from public.compra_itens as ci
   where ci.compra_id = p_id;

  update public.compras
     set data_compra = p_data_compra,
         local_tipo = p_local_tipo,
         local_nome = p_local_nome,
         frete = p_frete,
         desconto = p_desconto
   where id = p_id;

  delete from public.estoque_filamentos
   where compra_item_id in (select id from public.compra_itens where compra_id = p_id);
  delete from public.estoque_itens
   where compra_item_id in (select id from public.compra_itens where compra_id = p_id);
  delete from public.compra_itens where compra_id = p_id;

  perform public.gravar_itens_compra(p_id, p_itens, p_usuario_id, p_desconto);

  return jsonb_build_object(
    'id', p_id,
    'removidas', coalesce(
      (select jsonb_agg(antiga order by antiga)
         from unnest(v_antigas) as antiga
        where not exists (select 1 from public.compra_itens as ci
                           where ci.compra_id = p_id and ci.imagem_caminho = antiga)),
      '[]'::jsonb));
end;
$corpo$;

-- ---------- listar_compras: desconto, valor unitário e medidas ----------
create or replace function public.listar_compras(p_inicio date default null, p_fim date default null)
returns jsonb
language sql
stable
security definer
set search_path = ''
as $corpo$
  with valores as (
    select ci.compra_id,
           sum(case when ci.categoria = 'filamento'
                    then ci.quantidade * ci.valor_unitario
                    else ci.valor_total end) as itens
      from public.compra_itens as ci
     group by ci.compra_id
  ),
  periodo as (
    select c.*, coalesce(v.itens, 0) - c.desconto + c.frete as total
      from public.compras as c
      left join valores as v on v.compra_id = c.id
     where (p_inicio is null or c.data_compra >= p_inicio)
       and (p_fim is null or c.data_compra < p_fim)
  )
  select jsonb_build_object(
    'meses', coalesce(
      (select jsonb_agg(m.mes order by m.mes desc)
         from (select distinct to_char(c.data_compra, 'YYYY-MM') as mes
                 from public.compras as c) as m),
      '[]'::jsonb),
    'compras', coalesce(
      (select jsonb_agg(jsonb_build_object(
                'id', p.id,
                'data_compra', p.data_compra,
                'local_tipo', p.local_tipo,
                'local_nome', p.local_nome,
                'frete', p.frete::text,
                'desconto', p.desconto::text,
                'total', p.total::text,
                'itens', coalesce(
                  (select jsonb_agg(jsonb_build_object(
                            'ordem', ci.ordem,
                            'categoria', ci.categoria,
                            'quantidade', ci.quantidade,
                            'cor', ci.cor,
                            'material', ci.material,
                            'tipo', ci.tipo,
                            'marca', ci.marca,
                            'peso_rolo_g', ci.peso_rolo_g::text,
                            'nome', ci.nome,
                            'medidas', ci.medidas,
                            'valor_unitario', ci.valor_unitario::text,
                            'valor', (case when ci.categoria = 'filamento'
                                           then ci.quantidade * ci.valor_unitario
                                           else ci.valor_total end)::text,
                            'imagem_caminho', ci.imagem_caminho
                          ) order by ci.ordem)
                     from public.compra_itens as ci
                    where ci.compra_id = p.id),
                  '[]'::jsonb)
              ) order by p.data_compra desc, p.id desc)
         from periodo as p),
      '[]'::jsonb)
  );
$corpo$;

-- ---------- Privilégios: só o service_role executa ----------
revoke all on function public.gravar_itens_compra(bigint, jsonb, bigint, numeric)
  from public, anon, authenticated, service_role;
revoke all on function public.validar_entrada_compra(numeric, numeric, jsonb)
  from public, anon, authenticated, service_role;
revoke all on function public.registrar_compra(
  bigint, uuid, date, text, text, jsonb, bigint, numeric, numeric
) from public, anon, authenticated;
grant execute on function public.registrar_compra(
  bigint, uuid, date, text, text, jsonb, bigint, numeric, numeric
) to service_role;
revoke all on function public.editar_compra(bigint, date, text, text, numeric, jsonb, bigint, numeric)
  from public, anon, authenticated;
grant execute on function public.editar_compra(bigint, date, text, text, numeric, jsonb, bigint, numeric)
  to service_role;
revoke all on function public.listar_compras(date, date) from public, anon, authenticated;
grant execute on function public.listar_compras(date, date) to service_role;
