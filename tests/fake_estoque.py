"""Repositório de estoque em memória, para os testes. Nada acessa o Supabase.

Imita as funções do banco: registrar_compra grava a compra e um lote por item numa
"transação" (tudo ou nada); reenviar a mesma chave_envio não grava de novo. A edição só muda
a descrição do lote. Falhas podem ser simuladas com `falhar` (leitura e edição) e
`falhar_em` (passos da gravação da compra). editar_compra troca itens e lotes da compra
(ids novos) e devolve as fotos que deixaram de ser usadas.
"""

from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal

from app.estoque_repositorio import (
    CompraListada,
    CompraParaGravar,
    Compras,
    Estoque,
    FalhaNoEstoque,
    Filamento,
    ItemCompraListado,
    ItemEstoque,
    LoteNaoEncontrado,
)


@dataclass
class CompraGravada:
    compra: CompraParaGravar
    lotes: list[tuple[str, int]]  # (filamento ou item, id do lote), na ordem dos itens


def _valor(item) -> Decimal:
    return (
        item.quantidade * item.valor_unitario if item.categoria == "filamento" else item.valor_total
    )


def _listar(compra: CompraParaGravar) -> CompraListada:
    """A compra como o banco a devolve em listar_compras (total = itens − desconto + frete)."""
    itens = tuple(
        ItemCompraListado(
            ordem=i.ordem,
            categoria=i.categoria,
            quantidade=i.quantidade,
            cor=i.cor,
            material=i.material,
            tipo=i.tipo,
            marca=i.marca,
            peso_rolo_g=i.peso_rolo_g,
            nome=i.nome,
            valor=_valor(i),
            imagem_caminho=i.imagem_caminho,
            medidas=i.medidas,
            valor_unitario=i.valor_unitario,
        )
        for i in compra.itens
    )
    valor = sum((_valor(i) for i in compra.itens), Decimal(0))
    return CompraListada(
        compra.id,
        compra.data_compra,
        compra.local_tipo,
        compra.local_nome,
        compra.frete,
        valor - compra.desconto + compra.frete,
        itens,
        compra.desconto,
    )


def _conferir(compra: CompraParaGravar) -> None:
    """Os CHECK do banco: valor_total = quantidade x unitário e desconto até o subtotal."""
    for i in compra.itens:
        if i.categoria != "filamento" and i.valor_unitario is not None:
            assert i.valor_total == i.quantidade * i.valor_unitario, "valor_total incoerente"
        if i.categoria == "filamento":
            assert i.medidas is None, "medidas em filamento"
    assert 0 <= compra.desconto <= sum((_valor(i) for i in compra.itens), Decimal(0))


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
        # "registrar_depois" (grava e perde a resposta), "editar", "editar_incerta" e "remover".
        self.falhar_em: set[str] = set()
        self._ultimo_id_compra = 0
        self._proximo = {"filamento": 1, "item": 1}

    # ---------- Leitura ----------

    def listar_estoque(self) -> Estoque:
        if self.falhar:
            raise FalhaNoEstoque
        return Estoque(filamentos=list(self.filamentos.values()), itens=list(self.itens.values()))

    # ---------- Compra ----------

    def listar_compras(self, inicio: date | None = None, fim: date | None = None) -> Compras:
        if self.falhar:
            raise FalhaNoEstoque
        listadas = [_listar(g.compra) for g in self.compras.values()]
        meses = sorted({c.data_compra.strftime("%Y-%m") for c in listadas}, reverse=True)
        no_periodo = [
            c
            for c in listadas
            if (inicio is None or c.data_compra >= inicio) and (fim is None or c.data_compra < fim)
        ]
        ordenadas = sorted(no_periodo, key=lambda c: (c.data_compra, c.id), reverse=True)
        return Compras(meses=tuple(meses), compras=tuple(ordenadas))

    def assinar_imagens(self, caminhos: list[str]) -> dict[str, str]:
        if "assinar" in self.falhar_em:
            raise FalhaNoEstoque
        return {
            c: f"https://storage.exemplo/assinada/{c}?token=t"
            for c in caminhos
            if c in self.imagens
        }

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
        _conferir(compra)
        lotes = self._criar_lotes(compra)
        self.compras[compra.id] = CompraGravada(compra, lotes)
        if "registrar_depois" in self.falhar_em:
            raise FalhaNoEstoque(incerta=True)  # gravou, mas a resposta se perdeu
        return False

    def _criar_lotes(self, compra: CompraParaGravar) -> list[tuple[str, int]]:
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
                    peso_comprado_g=item.quantidade * item.peso_rolo_g,
                    imagem_caminho=item.imagem_caminho,
                )
                lotes.append(("filamento", lote_id))
            elif item.categoria in ("acessorio", "embalagem"):
                # Peças de manutenção e outros ficam só na compra (sem lote), como no banco.
                lote_id = self._novo("item")
                self.itens[lote_id] = ItemEstoque(
                    id=lote_id,
                    categoria=item.categoria,
                    nome=item.nome,
                    quantidade=item.quantidade,
                    data_compra=compra.data_compra,
                    quantidade_comprada=item.quantidade,
                    valor_total=item.valor_total,
                    imagem_caminho=item.imagem_caminho,
                )
                lotes.append(("item", lote_id))
        return lotes

    def editar_compra(self, compra: CompraParaGravar) -> list[str]:
        if "editar" in self.falhar_em:
            raise FalhaNoEstoque
        if "editar_incerta" in self.falhar_em:
            raise FalhaNoEstoque(incerta=True)
        antiga = self.compras.get(compra.id)
        if antiga is None:
            raise LoteNaoEncontrado
        assert compra.itens, "compra sem itens"
        _conferir(compra)
        for item in compra.itens:  # o CHECK do caminho: só fotos da pasta da própria compra
            assert item.imagem_caminho is None or item.imagem_caminho.startswith(
                f"compras/{compra.id}/itens/"
            )
        for tipo, lote_id in antiga.lotes:
            (self.filamentos if tipo == "filamento" else self.itens).pop(lote_id)
        lotes = self._criar_lotes(compra)
        mantida = replace(compra, chave_envio=antiga.compra.chave_envio)
        self.compras[compra.id] = CompraGravada(mantida, lotes)
        self.edicoes.append(("compra", compra.id, compra.usuario_id))
        novas = {i.imagem_caminho for i in compra.itens}
        return sorted(
            i.imagem_caminho
            for i in antiga.compra.itens
            if i.imagem_caminho and i.imagem_caminho not in novas
        )

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
        peso_comprado_g=None,
    ) -> int:
        lote_id = self._novo("filamento")
        self.filamentos[lote_id] = Filamento(
            lote_id,
            cor,
            material,
            tipo,
            marca,
            peso_g,
            data_compra,
            peso_rolo_g,
            valor_unitario,
            peso_g if peso_comprado_g is None else peso_comprado_g,
        )
        return lote_id

    def com_item(
        self,
        nome,
        categoria="acessorio",
        quantidade=10,
        data_compra=date(2026, 9, 1),
        valor_total=None,
        imagem_caminho=None,
        quantidade_comprada=None,
    ) -> int:
        lote_id = self._novo("item")
        valor = Decimal(quantidade) if valor_total is None else valor_total  # R$ 1,00/un
        self.itens[lote_id] = ItemEstoque(
            lote_id,
            categoria,
            nome,
            quantidade,
            data_compra,
            max(quantidade, 1) if quantidade_comprada is None else quantidade_comprada,
            valor,
            imagem_caminho,
        )
        return lote_id
