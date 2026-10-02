-- =====================================================================================
-- OPERAÇÃO ÚNICA, MANUAL E NÃO AUTOMÁTICA. NÃO É MIGRATION.
-- =====================================================================================
-- Devolve o pedido real da Marise (id 1) de "entregue" para "em_producao". Ele foi movido
-- até Entregue durante os testes da Produção.
--
-- Fica fora de supabase/migrations de propósito: o db push não o aplica. Executar uma
-- única vez, por decisão explícita, depois de conferir os dados:
--   npx supabase db query --linked -f supabase/operacoes/20261004_corrigir_etapa_pedido_marise.sql
-- (ou colar no SQL Editor do Supabase). Funciona antes ou depois da migration do prazo.
--
-- Altera SOMENTE etapa_producao, etapa_atualizada_em e etapa_atualizada_por do pedido 1.
-- Prazo, pagamento, itens e os demais dados não são tocados.
--
-- Segurança: tudo numa transação. Qualquer conferência que não bata (pedido, cliente, etapa,
-- unidades, total, usuário ou número de linhas alteradas) interrompe com erro; depois de um
-- erro, o PostgreSQL desfaz a transação inteira (o COMMIT do final vira ROLLBACK).
-- Depois de executado com sucesso, uma segunda execução é recusada (a etapa não é mais
-- "entregue").

begin;

do $corrige$
declare
  v_leandro  bigint;
  v_pedido   record;
  v_unidades bigint;
  v_linhas   integer;
begin
  select id into v_leandro from public.usuarios where nome = 'Leandro';
  if v_leandro is null then
    raise exception 'correção recusada: usuário Leandro não encontrado';
  end if;

  -- Bloqueia a linha até o fim da transação: ninguém move o pedido no meio da correção.
  select id, cliente_nome, etapa_producao, valor_total into v_pedido
    from public.pedidos
   where id = 1
     for update;
  if not found then
    raise exception 'correção recusada: pedido 1 não encontrado';
  end if;
  if lower(btrim(v_pedido.cliente_nome)) is distinct from 'marise' then
    raise exception 'correção recusada: o pedido 1 não é da cliente Marise (%)', v_pedido.cliente_nome;
  end if;
  if v_pedido.etapa_producao is distinct from 'entregue' then
    raise exception 'correção recusada: etapa atual é %, esperada entregue', v_pedido.etapa_producao;
  end if;
  if v_pedido.valor_total is distinct from 132.00 then
    raise exception 'correção recusada: total é %, esperado 132.00', v_pedido.valor_total;
  end if;

  select coalesce(sum(quantidade), 0) into v_unidades
    from public.pedido_itens
   where pedido_id = 1;
  if v_unidades <> 11 then
    raise exception 'correção recusada: o pedido tem % unidade(s), esperadas 11', v_unidades;
  end if;

  update public.pedidos
     set etapa_producao       = 'em_producao',
         etapa_atualizada_em  = now(),
         etapa_atualizada_por = v_leandro
   where id = 1
     and lower(btrim(cliente_nome)) = 'marise'
     and etapa_producao = 'entregue'
     and valor_total = 132.00;

  get diagnostics v_linhas = row_count;
  if v_linhas <> 1 then
    raise exception 'correção recusada: % linha(s) alterada(s), esperada exatamente 1', v_linhas;
  end if;

  raise notice 'pedido 1 (Marise) voltou para em_producao; responsável: Leandro (id %)', v_leandro;
end;
$corrige$;

-- Conferência (ainda dentro da transação; só chega aqui se tudo acima passou).
select
  p.id,
  p.cliente_nome,
  p.etapa_producao,
  p.etapa_atualizada_em,
  u.nome as etapa_atualizada_por,
  p.status_pagamento,
  p.valor_total,
  (select sum(i.quantidade) from public.pedido_itens as i where i.pedido_id = p.id) as unidades
from public.pedidos as p
left join public.usuarios as u on u.id = p.etapa_atualizada_por
where p.id = 1;

commit;
