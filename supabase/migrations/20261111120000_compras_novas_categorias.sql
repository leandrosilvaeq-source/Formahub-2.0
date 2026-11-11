-- Compras: duas categorias novas de item, "Peças de manutenção" (peca_manutencao) e "Outros"
-- (outro), com os mesmos campos de acessório e embalagem (quantidade, nome, valor total e foto
-- opcional).
--
-- Só o CHECK de compra_itens.categoria muda. Os itens das categorias novas NÃO geram lote de
-- estoque: registrar_compra() e editar_compra() já criam lotes só para 'filamento'
-- (estoque_filamentos) e para 'acessorio'/'embalagem' (estoque_itens), pelo filtro explícito de
-- categoria. Também não são lançados como manutenção realizada. listar_compras() já soma o
-- valor_total de qualquer categoria que não seja filamento, então o total da compra os inclui.
-- Nenhum dado existente é alterado.
-- Migration LOCAL: aplicar somente por pedido explícito (supabase db push).

alter table public.compra_itens drop constraint compra_itens_categoria_check;
alter table public.compra_itens
  add constraint compra_itens_categoria_check
  check (categoria in ('filamento', 'acessorio', 'embalagem', 'peca_manutencao', 'outro'));
