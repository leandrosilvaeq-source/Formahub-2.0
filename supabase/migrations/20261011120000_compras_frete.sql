-- Frete da compra (20261011).
--
-- O frete fica na compra (coluna `frete`, em reais, não negativa, padrão 0) e NÃO é distribuído
-- nos custos dos lotes nesta etapa. Compras já gravadas ficam com frete 0.
-- registrar_compra ganha o parâmetro p_frete (padrão 0, para chamadas antigas continuarem
-- válidas). A função anterior, de 7 parâmetros, é removida para não haver sobrecarga ambígua
-- no PostgREST; os privilégios são refeitos: só o service_role executa.

alter table public.compras
  add column frete numeric(12, 2) not null default 0
  constraint compras_frete_nao_negativo check (frete >= 0);

drop function public.registrar_compra(bigint, uuid, date, text, text, jsonb, bigint);

create function public.registrar_compra(
  p_id          bigint,
  p_chave_envio uuid,
  p_data_compra date,
  p_local_tipo  text,
  p_local_nome  text,
  p_itens       jsonb,
  p_usuario_id  bigint,
  p_frete       numeric default 0
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

  if p_frete is null or p_frete < 0 or p_frete > 9999999.99 or p_frete <> round(p_frete, 2) then
    raise exception 'frete inválido' using errcode = '23514';
  end if;

  if p_itens is null or jsonb_typeof(p_itens) <> 'array' or jsonb_array_length(p_itens) = 0 then
    raise exception 'compra sem itens' using errcode = '23514';
  end if;
  if exists (select 1 from jsonb_array_elements(p_itens) as e where jsonb_typeof(e) <> 'object') then
    raise exception 'item de compra inválido' using errcode = '22023';
  end if;

  -- Mesma submissão de novo: devolve a compra já gravada (o ON CONFLICT espera a outra
  -- transação terminar, então dois envios simultâneos também gravam uma vez só).
  insert into public.compras (
    id, data_compra, local_tipo, local_nome, frete, chave_envio, criado_por
  )
  overriding system value
  values (p_id, p_data_compra, p_local_tipo, p_local_nome, p_frete, p_chave_envio, p_usuario_id)
  on conflict (chave_envio) do nothing
  returning id into v_id;
  if v_id is null then
    select id into v_id from public.compras where chave_envio = p_chave_envio;
    return jsonb_build_object('id', v_id, 'duplicada', true);
  end if;

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

  return jsonb_build_object('id', p_id, 'duplicada', false);
end;
$corpo$;

revoke all on function public.registrar_compra(
  bigint, uuid, date, text, text, jsonb, bigint, numeric
) from public, anon, authenticated;
grant execute on function public.registrar_compra(
  bigint, uuid, date, text, text, jsonb, bigint, numeric
) to service_role;
