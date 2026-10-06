"""Repositório de estoque em memória, para os testes. Nada acessa o Supabase.

Imita as funções do banco: registrar_compra grava a compra e um lote por item numa
"transação" (tudo ou nada); reenviar a mesma chave_envio não grava de novo. A edição só muda
a descrição do lote. Falhas podem ser simuladas com `falhar` (leitura e edição) e
`falhar_em` (passos da gravação da compra).
"""

from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal

from app.estoque_repositorio import (
    CompraParaGravar,
    Estoque,
    FalhaNoEstoque,
    Filamento,
    ItemEstoque,
    LoteNaoEncontrado,
)


@dataclass
class CompraGravada:
    compra: CompraParaGravar
    lotes: list[tuple[str, int]]  # (filamento ou item, id do lote), na ordem dos itens


class RepositorioEstoqueMemoria:
    def __init__(self):
        self.filamentos: dict[int, Filamento] = {}
        self.itens: dict[int, ItemEstoque] = {}
        self.compras: dict[int, CompraGravada] = {}
        self.imagens: dict[str, bytes] = {}  # caminho -> conteúdo (o "Storage")
        self.removidas: list[str] = []
        self.edicoes: list[tuple[str, int, int]] = []  # (tipo, id do lote, usuário)
        self.falhar = False
        # Passos que falham: "reservar", "enviar", "registrar", "registrar_incerta",
        # "registrar_depois" (grava e perde a resposta) e "remover".
        self.falhar_em: set[str] = set()
        self._ultimo_id_compra = 0
        self._proximo = {"filamento": 1, "item": 1}

    # ---------- Leitura ----------

    def listar_estoque(self) -> Estoque:
        if self.falhar:
            raise FalhaNoEstoque
        return Estoque(filamentos=list(self.filamentos.values()), itens=list(self.itens.values()))

    # ---------- Compra ----------

    def reservar_id_compra(self) -> int:
        if "reservar" in self.falhar_em:
            raise FalhaNoEstoque(incerta=True)
        self._ultimo_id_compra += 1
        return self._ultimo_id_compra

    def enviar_imagem(self, caminho: str, conteudo: bytes, tipo: str) -> None:
        if "enviar" in self.falhar_em:
            raise FalhaNoEstoque
        assert caminho not in self.imagens, "upsert desligado: o caminho não pode repetir"
        assert tipo in ("image/png", "image/jpeg", "image/webp")
        self.imagens[caminho] = conteudo

    def remover_imagens(self, caminhos: list[str]) -> None:
        if "remover" in self.falhar_em:
            raise FalhaNoEstoque
        for caminho in caminhos:
            self.imagens.pop(caminho, None)
            self.removidas.append(caminho)

    def registrar_compra(self, compra: CompraParaGravar) -> bool:
        if "registrar" in self.falhar_em:
            raise FalhaNoEstoque
        if "registrar_incerta" in self.falhar_em:
            raise FalhaNoEstoque(incerta=True)
        assert 1 <= compra.id <= self._ultimo_id_compra, "id de compra não reservado"
        if any(g.compra.chave_envio == compra.chave_envio for g in self.compras.values()):
            return True
        assert compra.itens, "compra sem itens"
        lotes = []
        for item in compra.itens:
            if item.categoria == "filamento":
                lote_id = self._novo("filamento")
                self.filamentos[lote_id] = Filamento(
                    id=lote_id,
                    cor=item.cor,
                    material=item.material,
                    tipo=item.tipo,
                    marca=item.marca,
                    peso_g=item.quantidade * item.peso_rolo_g,
                    data_compra=compra.data_compra,
                    peso_rolo_g=item.peso_rolo_g,
                    valor_unitario=item.valor_unitario,
                )
                lotes.append(("filamento", lote_id))
            else:
                lote_id = self._novo("item")
                self.itens[lote_id] = ItemEstoque(
                    id=lote_id,
                    categoria=item.categoria,
                    nome=item.nome,
                    quantidade=item.quantidade,
                    data_compra=compra.data_compra,
                    quantidade_comprada=item.quantidade,
                    valor_total=item.valor_total,
                )
                lotes.append(("item", lote_id))
        self.compras[compra.id] = CompraGravada(compra, lotes)
        if "registrar_depois" in self.falhar_em:
            raise FalhaNoEstoque(incerta=True)  # gravou, mas a resposta se perdeu
        return False

    def _novo(self, tipo: str) -> int:
        novo_id = self._proximo[tipo]
        self._proximo[tipo] += 1
        return novo_id

    # ---------- Edição ----------

    def editar_filamento(
        self, lote_id: int, cor: str, material: str, tipo: str, marca: str, usuario_id: int
    ) -> None:
        if self.falhar:
            raise FalhaNoEstoque
        if lote_id not in self.filamentos:
            raise LoteNaoEncontrado
        self.edicoes.append(("filamento", lote_id, usuario_id))
        self.filamentos[lote_id] = replace(
            self.filamentos[lote_id], cor=cor, material=material, tipo=tipo, marca=marca
        )

    def editar_item(self, lote_id: int, categoria: str, nome: str, usuario_id: int) -> None:
        if self.falhar:
            raise FalhaNoEstoque
        atual = self.itens.get(lote_id)
        if atual is None or atual.categoria != categoria:
            raise LoteNaoEncontrado
        self.edicoes.append(("item", lote_id, usuario_id))
        self.itens[lote_id] = replace(atual, nome=nome)

    # ---------- Atalhos dos testes (lotes fictícios já comprados) ----------

    def com_filamento(
        self,
        cor="Preto",
        material="PLA",
        tipo="solido",
        marca="Marca A",
        peso_g=Decimal("1000"),
        data_compra=date(2026, 9, 1),
        peso_rolo_g=Decimal("1000"),
        valor_unitario=Decimal("100.00"),
    ) -> int:
        lote_id = self._novo("filamento")
        self.filamentos[lote_id] = Filamento(
            lote_id, cor, material, tipo, marca, peso_g, data_compra, peso_rolo_g, valor_unitario
        )
        return lote_id

    def com_item(
        self,
        nome,
        categoria="acessorio",
        quantidade=10,
        data_compra=date(2026, 9, 1),
        valor_total=None,
    ) -> int:
        lote_id = self._novo("item")
        valor = Decimal(quantidade) if valor_total is None else valor_total  # R$ 1,00/un
        self.itens[lote_id] = ItemEstoque(
            lote_id, categoria, nome, quantidade, data_compra, max(quantidade, 1), valor
        )
        return lote_id
