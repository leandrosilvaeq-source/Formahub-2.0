-- Tela Compras: leitura filtrada por período (mês/ano pela data da compra), com o valor de cada item.
--
-- Substitui listar_compras() por listar_compras(p_inicio, p_fim). Só altera a função de leitura:
-- nenhuma tabela, coluna ou dado muda. Sem parâmetros (ou ambos nulos) devolve TODAS as compras;
-- com eles, as compras de p_inicio (inclusive) até p_fim (exclusive). Não há mais limite de 20:
-- o servidor calcula os indicadores sobre todas as compras do período e pagina a tabela.
--
-- Valor de cada item: filamento = quantidade x valor unitário; acessório e embalagem = valor
-- total. Total da compra = soma dos itens + frete. 'meses' lista os meses (aaaa-mm) que têm
-- compras, do mais recente ao mais antigo, para o filtro. Ordem: data de compra decrescente e,
-- em empate, a compra registrada por último (maior id). Valores monetários vão como texto (sem
-- float). As fotos vão só como caminho no bucket privado compra-imagens: o servidor gera as URLs
-- assinadas.
-- Migration LOCAL: aplicar somente por pedido explícito (supabase db push).

drop function public.listar_compras();

create function public.listar_compras(p_inicio date default null, p_fim date default null)
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
    select c.*, coalesce(v.itens, 0) + c.frete as total
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

revoke all on function public.listar_compras(date, date) from public, anon, authenticated;
grant execute on function public.listar_compras(date, date) to service_role;
