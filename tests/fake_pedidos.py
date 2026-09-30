"""Repositório de pedidos em memória (banco e Storage), para os testes. Nada acessa o Supabase.

Imita as garantias do banco real: criar_pedido grava pedido e itens juntos ou nada.
Falhas podem ser simuladas por etapa com `falhar_em`.
"""

from app.pedidos_repositorio import FalhaAoGravar, PedidoParaGravar


class RepositorioPedidosMemoria:
    def __init__(self):
        self.proximo_id = 1
        self.pedidos: dict[int, PedidoParaGravar] = {}
        self.arquivos: dict[str, tuple[bytes, str]] = {}  # caminho -> (conteúdo, tipo)
        self.removidos: list[str] = []
        self.falhar_em: dict[str, FalhaAoGravar] = {}  # etapa -> erro a levantar
        self.falhar_upload_numero: int | None = None  # falha só no n-ésimo envio (1, 2, ...)
        self._envios = 0

    def _talvez_falhar(self, etapa: str) -> None:
        if etapa in self.falhar_em:
            raise self.falhar_em[etapa]

    def reservar_id(self) -> int:
        self._talvez_falhar("reservar_id")
        pedido_id = self.proximo_id
        self.proximo_id += 1
        return pedido_id

    def enviar_imagem(self, caminho: str, conteudo: bytes, tipo: str) -> None:
        self._envios += 1
        if self._envios == self.falhar_upload_numero:
            raise FalhaAoGravar()
        self._talvez_falhar("enviar_imagem")
        if caminho in self.arquivos:
            raise FalhaAoGravar()  # upsert desligado
        self.arquivos[caminho] = (conteudo, tipo)

    def remover_imagens(self, caminhos: list[str]) -> None:
        self._talvez_falhar("remover_imagens")
        for caminho in caminhos:
            self.arquivos.pop(caminho, None)
            self.removidos.append(caminho)

    def criar_pedido(self, pedido: PedidoParaGravar) -> None:
        self._talvez_falhar("criar_pedido")
        ordens = [item.ordem for item in pedido.itens]
        if not pedido.itens or len(set(ordens)) != len(ordens):
            raise FalhaAoGravar()
        if sum(item.subtotal for item in pedido.itens) != pedido.valor_total:
            raise FalhaAoGravar()
        self.pedidos[pedido.id] = pedido
