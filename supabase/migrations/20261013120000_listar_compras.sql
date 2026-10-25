-- Tela Compras: leitura do histórico de compras (resumo + últimas compras com seus itens).
--
-- Só cria uma função de leitura sobre public.compras e public.compra_itens; nenhuma tabela,
-- coluna ou dado é alterado. Valor de cada item: filamento = quantidade x valor unitário;
-- acessório e embalagem = valor total. Total da compra = soma dos itens + frete.
--
-- O resumo (total gasto e número de compras) considera TODAS as compras; a lista traz só as
-- 20 mais recentes: data de compra decrescente e, em empate, a compra registrada por último
-- (maior id). Valores monetários vão como texto (sem float). As fotos vão só como caminho no
-- bucket privado compra-imagens: o servidor gera as URLs assinadas.
-- Migration LOCAL: aplicar somente por pedido explícito (supabase db push).

create function public.listar_compras()
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
  totais as (
    select c.id, coalesce(v.itens, 0) + c.frete as total
      from public.compras as c
      left join valores as v on v.compra_id = c.id
  ),
  recentes as (
    select c.*
      from public.compras as c
     order by c.data_compra desc, c.id desc
     limit 20
  )
  select jsonb_build_object(
    'total_gasto', (select coalesce(sum(total), 0)::text from totais),
    'quantidade', (select count(*) from public.compras),
    'compras', coalesce(
      (select jsonb_agg(jsonb_build_object(
                'id', r.id,
                'data_compra', r.data_compra,
                'local_tipo', r.local_tipo,
                'local_nome', r.local_nome,
                'frete', r.frete::text,
                'total', t.total::text,
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
                            'imagem_caminho', ci.imagem_caminho
                          ) order by ci.ordem)
                     from public.compra_itens as ci
                    where ci.compra_id = r.id),
                  '[]'::jsonb)
              ) order by r.data_compra desc, r.id desc)
         from recentes as r
         join totais as t on t.id = r.id),
      '[]'::jsonb)
  );
$corpo$;

revoke all on function public.listar_compras() from public, anon, authenticated;
grant execute on function public.listar_compras() to service_role;
