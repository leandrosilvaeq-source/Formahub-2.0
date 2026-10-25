-- listar_estoque passa a informar também o caminho da foto da compra de cada lote
-- (compra_itens.imagem_caminho, bucket privado compra-imagens), usado na tela Estoque.
-- Só a leitura muda: nenhuma tabela ou dado é alterado; a assinatura e a segurança continuam
-- as da migration 20261010120000_compras_estoque.sql.

create or replace function public.listar_estoque()
returns jsonb
language sql
stable
security definer
set search_path = ''
as $corpo$
  select jsonb_build_object(
    'filamentos', coalesce(
      (select jsonb_agg(jsonb_build_object(
                'id', f.id,
                'cor', f.cor,
                'material', f.material,
                'tipo', f.tipo,
                'marca', f.marca,
                'peso_disponivel_g', f.peso_disponivel_g::text,
                'data_compra', c.data_compra,
                'peso_rolo_g', ci.peso_rolo_g::text,
                'peso_comprado_g', (ci.quantidade * ci.peso_rolo_g)::text,
                'valor_unitario', ci.valor_unitario::text,
                'imagem_caminho', ci.imagem_caminho
              ) order by c.data_compra desc, f.id desc)
         from public.estoque_filamentos as f
         join public.compra_itens as ci on ci.id = f.compra_item_id
         join public.compras as c on c.id = ci.compra_id),
      '[]'::jsonb),
    'itens', coalesce(
      (select jsonb_agg(jsonb_build_object(
                'id', i.id,
                'categoria', i.categoria,
                'nome', i.nome,
                'quantidade_disponivel', i.quantidade_disponivel,
                'data_compra', c.data_compra,
                'quantidade_comprada', ci.quantidade,
                'valor_total', ci.valor_total::text,
                'imagem_caminho', ci.imagem_caminho
              ) order by c.data_compra desc, i.id desc)
         from public.estoque_itens as i
         join public.compra_itens as ci on ci.id = i.compra_item_id
         join public.compras as c on c.id = ci.compra_id),
      '[]'::jsonb)
  );
$corpo$;
