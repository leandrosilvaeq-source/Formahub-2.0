-- Custos: valor inicial do card PLA (Sólido, Velvet e Silk reunidos): R$ 100,00/kg.
--
-- Só cria o ajuste manual 'filamento:pla' se ele ainda não existir: um valor já salvo pela tela é
-- preservado. Os ajustes antigos por tipo (filamento:pla-solido, -velvet e -silk) continuam
-- gravados, sem uso na tela: nenhum dado é apagado e nenhum pedido é alterado.
-- Migration LOCAL: aplicar somente por pedido explícito (supabase db push).

insert into public.custos_ajustes_insumo (chave, valor, atualizado_por)
select 'filamento:pla', 100, u.id
  from public.usuarios as u
 where u.nome = 'Leandro'
on conflict (chave) do nothing;
