-- Tela Compras: editar uma compra já registrada (botão Editar da lista), sem histórico de edição.
--
-- 1. editar_compra(): numa única transação, corrige data, local e frete da compra e troca os
--    itens: os itens e os lotes antigos são apagados e os novos gravados como em
--    registrar_compra (um lote por item, saldo = quantidade comprada). Devolve os caminhos das
--    fotos que deixaram de ser usadas, para o servidor removê-las do Storage depois.
--    Os lotes ganham ids novos e a descrição volta a ser a da compra.
--    Proteção: se algum lote da compra já tiver saldo menor que o comprado (baixa de estoque,
--    que ainda não existe), a edição é recusada (PT409) para não recriar estoque já usado.
-- 2. Caminho da foto do item: continua obrigatoriamente dentro da pasta da compra
--    (compras/{compra_id}/itens/{n}/{uuid}.{ext}), mas {n} não precisa mais ser a ordem atual
--    do item: ao editar, uma foto mantida pode mudar de posição (linha removida antes dela).
--
-- Erros: PT403 usuário inválido · PT404 compra não encontrada · PT409 estoque já usado.
-- Valores inválidos são recusados pelos CHECK das tabelas (nada fica gravado pela metade).
-- Migration LOCAL: aplicar somente por pedido explícito (supabase db push).

alter table public.compra_itens drop constraint compra_itens_imagem_caminho_padrao;
alter table public.compra_itens
  add constraint compra_itens_imagem_caminho_padrao check (
    imagem_caminho is null
    or imagem_caminho ~ ('^compras/' || compra_id::text || '/itens/[0-9]{1,3}/'
                         || '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
                         || '\.(png|jpg|webp)$')
  );

-- p_itens: o mesmo formato de registrar_compra.
create function public.editar_compra(
  p_id          bigint,
  p_data_compra date,
  p_local_tipo  text,
  p_local_nome  text,
  p_frete       numeric,
  p_itens       jsonb,
  p_usuario_id  bigint
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

  if p_frete is null or p_frete < 0 or p_frete > 9999999.99 or p_frete <> round(p_frete, 2) then
    raise exception 'frete inválido' using errcode = '23514';
  end if;

  if p_itens is null or jsonb_typeof(p_itens) <> 'array' or jsonb_array_length(p_itens) = 0 then
    raise exception 'compra sem itens' using errcode = '23514';
  end if;
  if exists (select 1 from jsonb_array_elements(p_itens) as e where jsonb_typeof(e) <> 'object') then
    raise exception 'item de compra inválido' using errcode = '22023';
  end if;

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
         frete = p_frete
   where id = p_id;

  delete from public.estoque_filamentos
   where compra_item_id in (select id from public.compra_itens where compra_id = p_id);
  delete from public.estoque_itens
   where compra_item_id in (select id from public.compra_itens where compra_id = p_id);
  delete from public.compra_itens where compra_id = p_id;

  insert into public.compra_itens (
    compra_id, ordem, categoria, quantidade, cor, material, tipo, marca, peso_rolo_g,
    valor_unitario, nome, valor_total, imagem_caminho
  )
  select p_id, i.ordem, i.categoria, i.quantidade, i.cor, i.material, i.tipo, i.marca,
         i.peso_rolo_g, i.valor_unitario, i.nome, i.valor_total, i.imagem_caminho
    from jsonb_to_recordset(p_itens) as i(
      ordem integer, categoria text, quantidade integer, cor text, material text, tipo text,
      marca text, peso_rolo_g numeric, valor_unitario numeric, nome text, valor_total numeric,
      imagem_caminho text
    );

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

revoke all on function public.editar_compra(bigint, date, text, text, numeric, jsonb, bigint)
  from public, anon, authenticated;
grant execute on function public.editar_compra(bigint, date, text, text, numeric, jsonb, bigint)
  to service_role;
