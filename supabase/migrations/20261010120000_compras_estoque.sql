-- Estoque: toda entrada de insumos passa a ser feita por uma compra (Registrar Compra).
--
-- Depende de 20261009120000_estoque.sql (publicada, ainda não aplicada no remoto) e não a
-- reescreve: esta migration altera o que ela criou.
--
-- 1. compras: data, local (plataforma ou nome do local) e chave_envio, que identifica a
--    submissão do formulário: reenviar a mesma compra (duplo clique, resposta perdida) não
--    a duplica.
-- 2. compra_itens: as linhas da compra (filamento, acessório ou embalagem), com os valores
--    digitados, sem arredondar nada calculado. Uma linha de filamento = rolos iguais (mesmas
--    características, peso e valor unitário).
-- 3. Lotes: cada lote de estoque_filamentos / estoque_itens nasce de um item de compra
--    (compra_item_id, obrigatório e único). Data e custo deixam de ficar no lote: a data é
--    da compra e o custo é calculado na leitura a partir do item (custo/kg = valor unitário
--    x 1000 / peso por rolo; custo unitário = valor total / quantidade).
-- 4. registrar_compra(): grava compra, itens e lotes numa única transação (tudo ou nada).
--    salvar_estoque_filamento() e salvar_estoque_item() são removidas: não há mais cadastro
--    direto. editar_estoque_filamento() e editar_estoque_item() só corrigem a descrição do
--    lote; o saldo não muda por edição, e um gatilho impede que qualquer UPDATE o aumente.
-- 5. Bucket privado compra-imagens para a foto opcional de cada item (mesmo padrão do
--    pedido-imagens): o banco guarda só o caminho.
--
-- Gravação sem estado parcial (mesmo fluxo dos pedidos):
--   1. reservar_id_compra() devolve o id da próxima compra (nada é gravado);
--   2. o servidor envia as fotos ao Storage usando esse id no caminho;
--   3. registrar_compra() grava tudo numa transação. Se o passo 2 ou 3 falhar, o servidor
--      remove as fotos enviadas nessa tentativa; o id reservado só vira um salto na sequência.
--
-- Sem baixas, exclusão de compra nem integração com Custos nesta etapa.
-- Erros: PT403 usuário inválido · PT404 lote não encontrado · PT422 saldo aumentado ou lote
-- trocado de compra. Valores inválidos são recusados pelos CHECK das tabelas.
-- Criada em paralelo a 20261007120000 e 20261008120000 (Custos: já aplicadas no remoto,
-- ausentes neste checkout); não depende delas.
-- Migration LOCAL: aplicar somente por pedido explícito (supabase db push).

-- ---------- Pré-condições ----------
-- Os lotes passam a exigir uma compra: a migration só roda com o estoque ainda vazio
-- (20261009120000 cria as tabelas vazias e nada é inserido nelas antes desta).
do $verifica$
begin
  if exists (select 1 from public.estoque_filamentos) or exists (select 1 from public.estoque_itens)
  then
    raise exception 'o estoque já tem lotes sem compra; revise-os antes de aplicar esta migration';
  end if;
  if exists (select 1 from storage.buckets where id = 'compra-imagens') then
    raise exception 'o bucket compra-imagens já existe; revise-o antes de aplicar esta migration';
  end if;
end;
$verifica$;

-- ---------- Fim do cadastro direto ----------
drop function public.salvar_estoque_filamento(
  bigint, text, text, text, text, numeric, date, numeric, bigint
);
drop function public.salvar_estoque_item(bigint, text, text, integer, date, numeric, bigint);

-- ---------- Compras ----------
create table public.compras (
  id          bigint      generated always as identity primary key,
  data_compra date        not null check (data_compra between '2000-01-01' and '2099-12-31'),
  local_tipo  text        not null
              check (local_tipo in ('mercado_livre', 'shopee', 'aliexpress', 'outro',
                                    'loja_fisica')),
  -- Nome do local: obrigatório em Outro e Loja Física; nas plataformas, sempre NULL.
  local_nome  text,
  chave_envio uuid        not null,
  criado_em   timestamptz not null default now(),
  criado_por  bigint      not null references public.usuarios (id) on delete restrict,
  constraint compras_chave_envio_unica unique (chave_envio),
  constraint compras_local_coerente check (
    (local_tipo in ('outro', 'loja_fisica')) = (local_nome is not null)
  ),
  constraint compras_local_nome_valido check (
    local_nome is null
    or (char_length(local_nome) between 1 and 60 and local_nome = btrim(local_nome))
  )
);

-- ---------- Itens da compra ----------
-- imagem_caminho guarda só o caminho do arquivo no bucket compra-imagens, no padrão
--   compras/{compra_id}/itens/{ordem}/{uuid}.{extensao}   (extensao: png, jpg ou webp)
create table public.compra_itens (
  id             bigint        generated always as identity primary key,
  compra_id      bigint        not null references public.compras (id) on delete restrict,
  ordem          integer       not null check (ordem between 1 and 50),
  categoria      text          not null check (categoria in ('filamento', 'acessorio', 'embalagem')),
  -- Filamento: rolos; acessório e embalagem: unidades.
  quantidade     integer       not null check (quantidade between 1 and 999999),
  -- Só filamento:
  cor            text,
  material       text,
  tipo           text          check (tipo in ('solido', 'velvet', 'silk', 'bicolor', 'tricolor')),
  marca          text,
  peso_rolo_g    numeric(12, 2) check (peso_rolo_g > 0 and peso_rolo_g <= 99999.99),
  valor_unitario numeric(12, 2) check (valor_unitario >= 0),
  -- Só acessório e embalagem:
  nome           text,
  valor_total    numeric(12, 2) check (valor_total >= 0),
  imagem_caminho text,
  constraint compra_itens_ordem_unica unique (compra_id, ordem),
  constraint compra_itens_campos_da_categoria check (
    case categoria
      when 'filamento' then
        cor is not null and material is not null and tipo is not null and marca is not null
        and peso_rolo_g is not null and valor_unitario is not null
        and nome is null and valor_total is null
        and quantidade <= 9999
      else
        nome is not null and valor_total is not null
        and cor is null and material is null and tipo is null and marca is null
        and peso_rolo_g is null and valor_unitario is null
    end
  ),
  constraint compra_itens_textos_validos check (
    (cor is null or (char_length(cor) between 1 and 60 and cor = btrim(cor)))
    and (material is null or (char_length(material) between 1 and 60 and material = btrim(material)))
    and (marca is null or (char_length(marca) between 1 and 60 and marca = btrim(marca)))
    and (nome is null or (char_length(nome) between 1 and 60 and nome = btrim(nome)))
  ),
  constraint compra_itens_imagem_caminho_padrao check (
    imagem_caminho is null
    or imagem_caminho ~ ('^compras/' || compra_id::text || '/itens/' || ordem::text
                         || '/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
                         || '\.(png|jpg|webp)$')
  )
);

-- ---------- Lotes: ligados ao item de compra que os originou ----------
alter table public.estoque_filamentos
  drop column data_compra,
  drop column custo_kg,
  add column compra_item_id bigint not null
    constraint estoque_filamentos_compra_item_unico unique
    references public.compra_itens (id) on delete restrict;

alter table public.estoque_itens
  drop column data_compra,
  drop column custo_unitario,
  add column compra_item_id bigint not null
    constraint estoque_itens_compra_item_unico unique
    references public.compra_itens (id) on delete restrict;

-- Nenhum UPDATE aumenta o saldo nem troca a compra do lote (entrada só por compra; baixas,
-- quando existirem, só diminuem). tg_argv[0]: coluna do saldo da tabela.
create function public.estoque_saldo_nao_aumenta()
returns trigger
language plpgsql
set search_path = ''
as $corpo$
begin
  if new.compra_item_id is distinct from old.compra_item_id then
    raise exception 'o lote não pode mudar de compra' using errcode = 'PT422';
  end if;
  if (to_jsonb(new) ->> tg_argv[0])::numeric > (to_jsonb(old) ->> tg_argv[0])::numeric then
    raise exception 'o saldo só aumenta por uma compra' using errcode = 'PT422';
  end if;
  return new;
end;
$corpo$;

create trigger estoque_filamentos_saldo_nao_aumenta
  before update on public.estoque_filamentos
  for each row execute function public.estoque_saldo_nao_aumenta('peso_disponivel_g');

create trigger estoque_itens_saldo_nao_aumenta
  before update on public.estoque_itens
  for each row execute function public.estoque_saldo_nao_aumenta('quantidade_disponivel');

-- ---------- Segurança: RLS sem policies e nenhum privilégio direto ----------
alter table public.compras enable row level security;
alter table public.compra_itens enable row level security;

revoke all on public.compras from anon, authenticated, service_role;
revoke all on public.compra_itens from anon, authenticated, service_role;
revoke all on sequence public.compras_id_seq from anon, authenticated, service_role;
revoke all on sequence public.compra_itens_id_seq from anon, authenticated, service_role;

-- ---------- Reserva do id da compra ----------
create function public.reservar_id_compra()
returns bigint
language sql
security definer
set search_path = ''
as $corpo$
  select nextval(pg_get_serial_sequence('public.compras', 'id'));
$corpo$;

-- ---------- Gravação atômica da compra, dos itens e dos lotes ----------
-- p_itens: array JSON de {ordem, categoria, quantidade, cor, material, tipo, marca,
-- peso_rolo_g, valor_unitario, nome, valor_total, imagem_caminho} (números como texto).
-- Devolve {"id": <compra>, "duplicada": false}; se a chave_envio já foi gravada, não grava
-- nada e devolve {"id": <compra já gravada>, "duplicada": true}.
-- Qualquer problema (item inválido, ordem repetida) desfaz tudo.
create function public.registrar_compra(
  p_id          bigint,
  p_chave_envio uuid,
  p_data_compra date,
  p_local_tipo  text,
  p_local_nome  text,
  p_itens       jsonb,
  p_usuario_id  bigint
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

  if p_itens is null or jsonb_typeof(p_itens) <> 'array' or jsonb_array_length(p_itens) = 0 then
    raise exception 'compra sem itens' using errcode = '23514';
  end if;
  if exists (select 1 from jsonb_array_elements(p_itens) as e where jsonb_typeof(e) <> 'object') then
    raise exception 'item de compra inválido' using errcode = '22023';
  end if;

  -- Mesma submissão de novo: devolve a compra já gravada (o ON CONFLICT espera a outra
  -- transação terminar, então dois envios simultâneos também gravam uma vez só).
  insert into public.compras (id, data_compra, local_tipo, local_nome, chave_envio, criado_por)
  overriding system value
  values (p_id, p_data_compra, p_local_tipo, p_local_nome, p_chave_envio, p_usuario_id)
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

-- ---------- Leitura: lotes com data, local e valores da compra ----------
-- Os valores de origem vão como texto (sem float); o custo é calculado pelo servidor.
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
                'valor_unitario', ci.valor_unitario::text
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
                'valor_total', ci.valor_total::text
              ) order by c.data_compra desc, i.id desc)
         from public.estoque_itens as i
         join public.compra_itens as ci on ci.id = i.compra_item_id
         join public.compras as c on c.id = ci.compra_id),
      '[]'::jsonb)
  );
$corpo$;

-- ---------- Edição: só a descrição do lote (o saldo não muda) ----------
create function public.editar_estoque_filamento(
  p_id         bigint,
  p_cor        text,
  p_material   text,
  p_tipo       text,
  p_marca      text,
  p_usuario_id bigint
)
returns bigint
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
  update public.estoque_filamentos
     set cor = p_cor,
         material = p_material,
         tipo = p_tipo,
         marca = p_marca,
         atualizado_em = now(),
         atualizado_por = p_usuario_id
   where id = p_id
  returning id into v_id;
  if v_id is null then
    raise exception 'lote não encontrado' using errcode = 'PT404';
  end if;
  return v_id;
end;
$corpo$;

create function public.editar_estoque_item(
  p_id         bigint,
  p_categoria  text,
  p_nome       text,
  p_usuario_id bigint
)
returns bigint
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
  -- A categoria não muda: editar um acessório pelo endereço de embalagens dá PT404.
  update public.estoque_itens
     set nome = p_nome,
         atualizado_em = now(),
         atualizado_por = p_usuario_id
   where id = p_id and categoria = p_categoria
  returning id into v_id;
  if v_id is null then
    raise exception 'lote não encontrado' using errcode = 'PT404';
  end if;
  return v_id;
end;
$corpo$;

-- ---------- Privilégios ----------
revoke all on function public.estoque_saldo_nao_aumenta() from public, anon, authenticated,
  service_role;
revoke all on function public.reservar_id_compra() from public, anon, authenticated;
revoke all on function public.registrar_compra(bigint, uuid, date, text, text, jsonb, bigint)
  from public, anon, authenticated;
revoke all on function public.editar_estoque_filamento(bigint, text, text, text, text, bigint)
  from public, anon, authenticated;
revoke all on function public.editar_estoque_item(bigint, text, text, bigint)
  from public, anon, authenticated;

grant execute on function public.reservar_id_compra() to service_role;
grant execute on function public.registrar_compra(bigint, uuid, date, text, text, jsonb, bigint)
  to service_role;
grant execute on function public.editar_estoque_filamento(bigint, text, text, text, text, bigint)
  to service_role;
grant execute on function public.editar_estoque_item(bigint, text, text, bigint)
  to service_role;

-- ---------- Storage: bucket privado das fotos dos itens de compra ----------
-- Privado, até 10 MB, só PNG, JPEG e WebP. Nenhuma policy: só o servidor (service_role, que
-- ignora RLS) envia e remove arquivos.
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
  'compra-imagens',
  'compra-imagens',
  false,
  10485760,
  array['image/png', 'image/jpeg', 'image/webp']
);
