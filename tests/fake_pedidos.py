"""Repositório de pedidos em memória (banco e Storage), para os testes. Nada acessa o Supabase.

Imita as garantias do banco real: criar_pedido grava pedido e itens juntos ou nada.
Falhas podem ser simuladas por etapa com `falhar_em`.
As URLs assinadas são falsas (domínio de exemplo) e só existem para arquivos guardados.
"""

from datetime import datetime, timedelta

from app.pedidos_repositorio import (
    FalhaAoGravar,
    ItemConsultado,
    PedidoConsultado,
    PedidoParaGravar,
    PedidoResumo,
)

USUARIOS = {1: "Leandro", 2: "Kassia", 3: "Marise"}


class RepositorioPedidosMemoria:
    def __init__(self):
        self.proximo_id = 1
        self.pedidos: dict[int, PedidoParaGravar] = {}
        self.criado_em: dict[int, datetime] = {}  # horário de Brasília, como o banco devolve
        self.relogio = datetime(2026, 10, 1, 9, 0)  # avança 1 minuto a cada pedido
        self.arquivos: dict[str, tuple[bytes, str]] = {}  # caminho -> (conteúdo, tipo)
        self.removidos: list[str] = []
        self.assinaturas: list[list[str]] = []  # caminhos pedidos em cada assinar_imagens
        self.escritas = 0  # chamadas que gravariam algo (reserva, envio, remoção, criação)
        self.falhar_em: dict[str, Exception] = {}  # etapa -> erro a levantar
        self.falhar_upload_numero: int | None = None  # falha só no n-ésimo envio (1, 2, ...)
        self._envios = 0

    def _talvez_falhar(self, etapa: str) -> None:
        if etapa in self.falhar_em:
            raise self.falhar_em[etapa]

    def reservar_id(self) -> int:
        self.escritas += 1
        self._talvez_falhar("reservar_id")
        pedido_id = self.proximo_id
        self.proximo_id += 1
        return pedido_id

    def enviar_imagem(self, caminho: str, conteudo: bytes, tipo: str) -> None:
        self.escritas += 1
        self._envios += 1
        if self._envios == self.falhar_upload_numero:
            raise FalhaAoGravar()
        self._talvez_falhar("enviar_imagem")
        if caminho in self.arquivos:
            raise FalhaAoGravar()  # upsert desligado
        self.arquivos[caminho] = (conteudo, tipo)

    def remover_imagens(self, caminhos: list[str]) -> None:
        self.escritas += 1
        self._talvez_falhar("remover_imagens")
        for caminho in caminhos:
            self.arquivos.pop(caminho, None)
            self.removidos.append(caminho)

    def criar_pedido(self, pedido: PedidoParaGravar) -> None:
        self.escritas += 1
        self._talvez_falhar("criar_pedido")
        ordens = [item.ordem for item in pedido.itens]
        if not pedido.itens or len(set(ordens)) != len(ordens):
            raise FalhaAoGravar()
        if sum(item.subtotal for item in pedido.itens) != pedido.valor_total:
            raise FalhaAoGravar()
        if pedido.status_pagamento not in ("pendente", "pago"):
            raise FalhaAoGravar()  # CHECK do banco
        self.pedidos[pedido.id] = pedido
        self.criado_em[pedido.id] = self.relogio
        self.relogio += timedelta(minutes=1)

    # ---------- Leitura ----------

    def listar_pedidos(self) -> list[PedidoResumo]:
        self._talvez_falhar("listar_pedidos")
        recentes = sorted(
            self.pedidos.values(), key=lambda p: (self.criado_em[p.id], p.id), reverse=True
        )
        resumos = []
        for p in recentes:
            itens = sorted(p.itens, key=lambda i: i.ordem)
            com_imagem = [i for i in itens if i.imagem_caminho]
            # Mesma regra do banco: maior quantidade; no empate, o primeiro pela ordem.
            destaque = min(com_imagem, key=lambda i: (-i.quantidade, i.ordem), default=None)
            resumos.append(
                PedidoResumo(
                    id=p.id,
                    cliente_nome=p.cliente_nome,
                    forma_pagamento=p.forma_pagamento,
                    status_pagamento=p.status_pagamento,
                    criado_por_nome=USUARIOS[p.criado_por],
                    produtos=tuple(i.produto for i in itens),
                    quantidade_total=sum(i.quantidade for i in itens),
                    observacoes=p.observacoes,
                    imagem_caminho=destaque.imagem_caminho if destaque else None,
                    imagem_produto=destaque.produto if destaque else None,
                )
            )
        return resumos

    def consultar_pedido(self, pedido_id: int) -> PedidoConsultado | None:
        self._talvez_falhar("consultar_pedido")
        p = self.pedidos.get(pedido_id)
        if p is None:
            return None
        return PedidoConsultado(
            id=p.id,
            cliente_nome=p.cliente_nome,
            contato=p.contato,
            forma_pagamento=p.forma_pagamento,
            status_pagamento=p.status_pagamento,
            tipo_entrega=p.tipo_entrega,
            observacoes=p.observacoes,
            valor_total=p.valor_total,
            criado_em=self.criado_em[p.id],
            criado_por_nome=USUARIOS[p.criado_por],
            itens=tuple(
                ItemConsultado(
                    ordem=i.ordem,
                    produto=i.produto,
                    quantidade=i.quantidade,
                    valor_unitario=i.valor_unitario,
                    subtotal=i.subtotal,
                    imagem_caminho=i.imagem_caminho,
                )
                for i in sorted(p.itens, key=lambda i: i.ordem)
            ),
        )

    def assinar_imagens(self, caminhos: list[str]) -> dict[str, str]:
        self.assinaturas.append(list(caminhos))
        self._talvez_falhar("assinar_imagens")
        return {
            c: f"https://armazenamento.exemplo/assinada/{n}.img?token=falso"
            for n, c in enumerate(caminhos)
            if c in self.arquivos
        }
