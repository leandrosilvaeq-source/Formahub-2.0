-- Consulta dos pedidos do FormaHub 2.0: listagem e detalhes, só leitura.
--
-- As tabelas continuam sem acesso direto (nem para service_role): o servidor lê pelas funções
-- abaixo, que devolvem apenas os campos das telas. Nada de senha, sessão ou autenticação:
-- de usuarios sai somente o nome de quem cadastrou.
-- Funções STABLE em SQL: o PostgreSQL recusa qualquer escrita dentro delas.
-- Valores em dinheiro saem como texto (exatos, sem passar por float no JSON).
-- Datas saem no horário de Brasília (o banco tem a base de fusos; o servidor Windows, não).
-- Migration LOCAL: aplicar somente por pedido explícito (supabase db push).

-- ---------- Listagem: do mais recente para o mais antigo ----------
create function public.listar_pedidos()
returns table (
  id               bigint,
  cliente_nome     text,
  contato          text,
  forma_pagamento  text,
  tipo_entrega     text,
  valor_total      text,
  quantidade_total bigint,
  criado_em_local  timestamp,
  criado_por_nome  text
)
language sql
stable
security definer
set search_path = ''
as $corpo$
  select
    p.id,
    p.cliente_nome,
    p.contato,
    p.forma_pagamento,
    p.tipo_entrega,
    p.valor_total::text,
    (select coalesce(sum(i.quantidade), 0) from public.pedido_itens as i where i.pedido_id = p.id),
    p.criado_em at time zone 'America/Sao_Paulo',
    u.nome
  from public.pedidos as p
  join public.usuarios as u on u.id = p.criado_por
  order by p.criado_em desc, p.id desc;
$corpo$;

-- ---------- Detalhes: um pedido com os itens na ordem original (NULL se não existir) ----------
-- imagem_caminho só serve para o servidor gerar a URL assinada; não vai para a tela.
create function public.consultar_pedido(p_id bigint)
returns jsonb
language sql
stable
security definer
set search_path = ''
as $corpo$
  select jsonb_build_object(
    'id', p.id,
    'cliente_nome', p.cliente_nome,
    'contato', p.contato,
    'forma_pagamento', p.forma_pagamento,
    'tipo_entrega', p.tipo_entrega,
    'observacoes', p.observacoes,
    'valor_total', p.valor_total::text,
    'criado_em_local', p.criado_em at time zone 'America/Sao_Paulo',
    'criado_por_nome', u.nome,
    'itens', (
      select coalesce(
        jsonb_agg(
          jsonb_build_object(
            'ordem', i.ordem,
            'produto', i.produto,
            'quantidade', i.quantidade,
            'valor_unitario', i.valor_unitario::text,
            'subtotal', i.subtotal::text,
            'imagem_caminho', i.imagem_caminho
          )
          order by i.ordem
        ),
        '[]'::jsonb
      )
      from public.pedido_itens as i
      where i.pedido_id = p.id
    )
  )
  from public.pedidos as p
  join public.usuarios as u on u.id = p.criado_por
  where p.id = p_id;
$corpo$;

revoke all on function public.listar_pedidos() from public, anon, authenticated;
revoke all on function public.consultar_pedido(bigint) from public, anon, authenticated;
grant execute on function public.listar_pedidos() to service_role;
grant execute on function public.consultar_pedido(bigint) to service_role;
