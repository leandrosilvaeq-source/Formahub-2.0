"""Repositório de estoque em memória, para os testes. Nada acessa o Supabase.

Imita as funções do banco: p_id None cadastra; com id, edita (LoteNaoEncontrado se não
existir ou for de outra categoria). Falhas podem ser simuladas com `falhar`.
"""

from dataclasses import replace

from app.estoque_repositorio import (
    Estoque,
    FalhaNoEstoque,
    Filamento,
    ItemEstoque,
    LoteNaoEncontrado,
)


class RepositorioEstoqueMemoria:
    def __init__(self):
        self.filamentos: dict[int, Filamento] = {}
        self.itens: dict[int, ItemEstoque] = {}
        self.gravacoes: list[tuple[str, int | None, int]] = []  # (tipo, id pedido, usuário)
        self.falhar = False
        self._proximo = {"filamento": 1, "item": 1}

    def _talvez_falhar(self):
        if self.falhar:
            raise FalhaNoEstoque

    def listar_estoque(self) -> Estoque:
        self._talvez_falhar()
        return Estoque(filamentos=list(self.filamentos.values()), itens=list(self.itens.values()))

    def _salvar(self, tabela: dict, tipo: str, lote, usuario_id: int, mesmo_lote) -> int:
        self._talvez_falhar()
        self.gravacoes.append((tipo, lote.id, usuario_id))
        if lote.id is None:
            novo_id = self._proximo[tipo]
            self._proximo[tipo] += 1
            tabela[novo_id] = replace(lote, id=novo_id)
            return novo_id
        atual = tabela.get(lote.id)
        if atual is None or not mesmo_lote(atual):
            raise LoteNaoEncontrado
        tabela[lote.id] = lote
        return lote.id

    def salvar_filamento(self, filamento: Filamento, usuario_id: int) -> int:
        return self._salvar(self.filamentos, "filamento", filamento, usuario_id, lambda _: True)

    def salvar_item(self, item: ItemEstoque, usuario_id: int) -> int:
        return self._salvar(
            self.itens, "item", item, usuario_id, lambda atual: atual.categoria == item.categoria
        )
