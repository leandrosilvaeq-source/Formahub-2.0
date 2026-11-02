-- Manutenção: registro das manutenções da máquina (tela /manutencao) como fonte dos gastos reais.
--
-- Cada linha de public.manutencoes é UMA manutenção: data, tipo (preventiva/corretiva), tempo de
-- máquina parada (TOTAL EM MINUTOS), troca de peças (sim/não + nome das peças), custo e comentários.
--   custo NULL = "A definir" (não é zero); zero é um custo conhecido. Custo >= 0, duas casas.
--   troca_pecas = true exige o nome da peça (várias peças cabem no mesmo campo); false não tem peça.
--   Auditoria: criado_em/criado_por e atualizado_em/atualizado_por (usuário da sessão).
--
-- CUSTOS: listar_custos() passa a ler os gastos de manutenção desta tabela (só os que têm custo
--   definido) e informa as datas das manutenções SEM custo ('manutencao_sem_custo'), para a tela
--   avisar que o custo/h é um subtotal conhecido. O custo/h segue sendo
--   gastos do período ÷ horas impressas do mesmo período; o tempo parado nunca é o divisor.
--
-- GASTOS ANTIGOS: os gastos de custos_manutencao_gastos (tela de Custos) são COPIADOS para cá,
--   uma única vez cada (gasto_legado_id é único): data, valor e a descrição antiga como
--   comentário. Tipo e tempo parado não existiam e ficam vazios (NULL, nunca inventados).
--   A tabela antiga continua intacta, sem uso. A função registrar_gasto_manutencao, que gravava
--   nela, é removida para não haver dois cadastros de manutenção.
--
-- Segurança como nas demais: RLS ligado, sem policies e sem privilégio direto; só funções
-- SECURITY DEFINER com search_path vazio, executáveis apenas pelo service_role.
-- Erros: PT403 usuário inválido · PT404 manutenção inexistente · PT422 dados inválidos.
-- Migration LOCAL: aplicar somente por pedido explícito (supabase db push).

create table public.manutencoes (
  id              bigint        generated always as identity primary key,
  data            date          not null check (data between date '2000-01-01' and date '2100-12-31'),
  tipo            text          check (tipo in ('preventiva', 'corretiva')),
  minutos_parados integer       check (minutos_parados between 0 and 525600),
  troca_pecas     boolean       not null default false,
  pecas           text          check (pecas = btrim(pecas) and pecas <> '' and char_length(pecas) <= 300),
  custo           numeric(12, 2) check (custo >= 0),
  comentarios     text          check (comentarios = btrim(comentarios) and comentarios <> ''
                                       and char_length(comentarios) <= 1000),
  gasto_legado_id bigint        unique references public.custos_manutencao_gastos (id)
                                  on delete restrict,
  criado_em       timestamptz   not null default now(),
  criado_por      bigint        not null references public.usuarios (id) on delete restrict,
  atualizado_em   timestamptz   not null default now(),
  atualizado_por  bigint        not null references public.usuarios (id) on delete restrict,
  constraint manutencoes_pecas_coerentes check (troca_pecas = (pecas is not null)),
  constraint manutencoes_completa check (
    gasto_legado_id is not null or (tipo is not null and minutos_parados is not null))
);

alter table public.manutencoes enable row level security;
revoke all on public.manutencoes from anon, authenticated, service_role;
revoke all on sequence public.manutencoes_id_seq from anon, authenticated, service_role;

-- ---------- Gastos antigos: copiados uma única vez ----------
insert into public.manutencoes
  (data, troca_pecas, comentarios, custo, gasto_legado_id,
   criado_em, criado_por, atualizado_em, atualizado_por)
select g.data, false, g.descricao, g.valor, g.id, g.criado_em, g.criado_por, g.criado_em, g.criado_por
  from public.custos_manutencao_gastos as g
on conflict (gasto_legado_id) do nothing;

drop function public.registrar_gasto_manutencao(date, text, numeric, bigint);

-- ---------- Leitura das manutenções (mais recentes primeiro) ----------
-- Números e datas saem como TEXTO (sem float no caminho).
create function public.listar_manutencoes()
returns jsonb
language sql
stable
security definer
set search_path = ''
as $corpo$
  select coalesce(jsonb_agg(jsonb_build_object(
           'id', m.id,
           'data', to_char(m.data, 'YYYY-MM-DD'),
           'tipo', m.tipo,
           'minutos_parados', m.minutos_parados,
           'troca_pecas', m.troca_pecas,
           'pecas', m.pecas,
           'custo', m.custo::text,
           'comentarios', m.comentarios,
           'legado', m.gasto_legado_id is not null,
           'criado_por', cu.nome,
           'criado_em', to_char(m.criado_em at time zone 'America/Sao_Paulo',
                                'YYYY-MM-DD"T"HH24:MI:SS'),
           'atualizado_por', au.nome,
           'atualizado_em', to_char(m.atualizado_em at time zone 'America/Sao_Paulo',
                                    'YYYY-MM-DD"T"HH24:MI:SS'))
         order by m.data desc, m.id desc), '[]'::jsonb)
    from public.manutencoes as m
    join public.usuarios as cu on cu.id = m.criado_por
    join public.usuarios as au on au.id = m.atualizado_por;
$corpo$;

-- ---------- Cria (p_id nulo) ou altera (p_id) uma manutenção ----------
create function public.salvar_manutencao(
  p_id           bigint,
  p_data         date,
  p_tipo         text,
  p_minutos      integer,
  p_troca_pecas  boolean,
  p_pecas        text,
  p_custo        numeric,
  p_comentarios  text,
  p_usuario_id   bigint
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $corpo$
declare
  v_pecas       text := nullif(btrim(p_pecas, E' \t\n\r\f\x0B'), '');
  v_comentarios text := nullif(btrim(p_comentarios, E' \t\n\r\f\x0B'), '');
  v_id          bigint;
begin
  if not exists (select 1 from public.usuarios
                  where id = p_usuario_id and nome in ('Leandro', 'Kassia', 'Marise')) then
    raise exception 'usuário inválido' using errcode = 'PT403';
  end if;

  if p_data is null or p_data not between date '2000-01-01' and date '2100-12-31'
     or p_tipo is null or p_tipo not in ('preventiva', 'corretiva')
     or p_minutos is null or p_minutos < 0 or p_minutos > 525600
     or p_troca_pecas is null
     or (p_troca_pecas and (v_pecas is null or char_length(v_pecas) > 300))
     or (p_custo is not null and (p_custo = 'NaN'::numeric or p_custo < 0
                                  or p_custo >= 10000000000 or p_custo <> round(p_custo, 2)))
     or (v_comentarios is not null and char_length(v_comentarios) > 1000) then
    raise exception 'manutenção inválida' using errcode = 'PT422';
  end if;
  if not p_troca_pecas then
    v_pecas := null;  -- sem troca, não há peça
  end if;

  if p_id is null then
    insert into public.manutencoes
      (data, tipo, minutos_parados, troca_pecas, pecas, custo, comentarios,
       criado_por, atualizado_por)
    values (p_data, p_tipo, p_minutos, p_troca_pecas, v_pecas, p_custo, v_comentarios,
            p_usuario_id, p_usuario_id)
    returning id into v_id;
  else
    update public.manutencoes
       set data = p_data, tipo = p_tipo, minutos_parados = p_minutos, troca_pecas = p_troca_pecas,
           pecas = v_pecas, custo = p_custo, comentarios = v_comentarios,
           atualizado_em = now(), atualizado_por = p_usuario_id
     where id = p_id
    returning id into v_id;
    if v_id is null then
      raise exception 'manutenção não encontrada' using errcode = 'PT404';
    end if;
  end if;

  return (select e from jsonb_array_elements(public.listar_manutencoes()) as e
           where (e ->> 'id')::bigint = v_id);
end;
$corpo$;

-- ---------- Custos: os gastos reais passam a vir das manutenções ----------
-- Igual à versão anterior, exceto 'manutencao_gastos' (manutenções com custo definido, zero
-- incluído) e o campo novo 'manutencao_sem_custo' (datas das manutenções com custo "A definir").
create or replace function public.listar_custos()
returns jsonb
language sql
stable
security definer
set search_path = ''
as $corpo$
  select jsonb_build_object(
    'filamentos', coalesce((
      select jsonb_agg(jsonb_build_object(
               'id', f.id, 'nome', f.nome, 'abrange', f.abrange, 'valor_kg', f.valor_kg::text)
             order by f.id)
        from public.custos_filamentos as f), '[]'::jsonb),
    'parametros', coalesce((
      select jsonb_object_agg(p.chave, p.valor::text) from public.custos_parametros as p),
      '{}'::jsonb),
    'itens', coalesce((
      select jsonb_agg(jsonb_build_object(
               'id', i.id, 'tipo', i.tipo, 'nome', i.nome,
               'valor_compra', i.valor_compra::text, 'quantidade', i.quantidade,
               'custo_unitario', i.custo_unitario::text)
             order by lower(i.nome), i.id)
        from public.custos_itens as i), '[]'::jsonb),
    'provisorios', coalesce((
      select jsonb_agg(p.chave order by p.chave)
        from public.custos_parametros as p where p.provisorio), '[]'::jsonb),
    'datas', coalesce((
      select jsonb_object_agg(p.chave, to_char(p.atualizado_em at time zone 'America/Sao_Paulo',
                                               'YYYY-MM-DD'))
        from public.custos_parametros as p), '{}'::jsonb),
    'ajustes', coalesce((
      select jsonb_object_agg(a.chave, a.valor::text) from public.custos_ajustes_insumo as a),
      '{}'::jsonb),
    'manutencao_gastos', coalesce((
      select jsonb_agg(jsonb_build_object(
               'id', m.id, 'data', to_char(m.data, 'YYYY-MM-DD'),
               'descricao', coalesce(
                 m.pecas,
                 case m.tipo when 'preventiva' then 'Manutenção preventiva'
                             when 'corretiva' then 'Manutenção corretiva'
                             else m.comentarios end,
                 'Manutenção'),
               'valor', m.custo::text)
             order by m.data desc, m.id desc)
        from public.manutencoes as m where m.custo is not null), '[]'::jsonb),
    'manutencao_sem_custo', coalesce((
      select jsonb_agg(to_char(m.data, 'YYYY-MM-DD') order by m.data, m.id)
        from public.manutencoes as m where m.custo is null), '[]'::jsonb),
    'manutencao_periodo', (
      select jsonb_build_object(
               'inicio', to_char(p.inicio, 'YYYY-MM-DD'), 'fim', to_char(p.fim, 'YYYY-MM-DD'),
               'horas', p.horas::text)
        from public.custos_manutencao_periodo as p where p.id = 1)
  );
$corpo$;

-- ---------- Privilégios: somente o service_role executa ----------
revoke all on function public.listar_manutencoes() from public, anon, authenticated;
revoke all on function public.salvar_manutencao(
  bigint, date, text, integer, boolean, text, numeric, text, bigint)
  from public, anon, authenticated;
revoke all on function public.listar_custos() from public, anon, authenticated;

grant execute on function public.listar_manutencoes() to service_role;
grant execute on function public.salvar_manutencao(
  bigint, date, text, integer, boolean, text, numeric, text, bigint) to service_role;
grant execute on function public.listar_custos() to service_role;
