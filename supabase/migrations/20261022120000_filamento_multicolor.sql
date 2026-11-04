-- Filamentos: Bicolor e Tricolor deixam de existir e passam a ser um único tipo, Multicolor.
--
-- Converte os lotes e os itens de compra já gravados e troca a lista de tipos aceitos nas duas
-- tabelas. A conversão não tem volta: depois dela não se sabe mais qual era Bicolor ou Tricolor
-- (a cor do lote continua a mesma). Saldos, custos e datas não mudam.
-- Migration LOCAL: aplicar somente por pedido explícito (supabase db push).

alter table public.estoque_filamentos drop constraint estoque_filamentos_tipo_check;
alter table public.compra_itens drop constraint compra_itens_tipo_check;

update public.estoque_filamentos set tipo = 'multicolor' where tipo in ('bicolor', 'tricolor');
update public.compra_itens set tipo = 'multicolor' where tipo in ('bicolor', 'tricolor');

alter table public.estoque_filamentos
  add constraint estoque_filamentos_tipo_check
  check (tipo in ('solido', 'velvet', 'silk', 'multicolor'));
alter table public.compra_itens
  add constraint compra_itens_tipo_check
  check (tipo in ('solido', 'velvet', 'silk', 'multicolor'));
