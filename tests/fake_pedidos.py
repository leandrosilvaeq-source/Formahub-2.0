"""Repositório de pedidos em memória (banco e Storage), para os testes. Nada acessa o Supabase.

Imita as garantias do banco real: criar_pedido grava pedido e itens juntos ou nada.
Falhas podem ser simuladas por etapa com `falhar_em`.
As URLs assinadas são falsas (domínio de exemplo) e só existem para arquivos guardados.
"""

from dataclasses import replace
from datetime import datetime, timedelta

from app.pedidos import ALTERNANCIA_PAGAMENTO, LIMITE_COMENTARIO, movimento_permitido
from app.pedidos_repositorio import (
    AlteracaoRecusada,
    CardProducao,
    ComentarioAtualizado,
    ConflitoDeEtapa,
    EtapaAtualizada,
    FalhaAoGravar,
    ItemConsultado,
    MovimentoInvalido,
    PagamentoAtualizado,
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
        # Produção: etapa de cada pedido e cada movimento feito (pedido, de, para, usuário).
        self.etapas: dict[int, str] = {}
        self.movimentos: list[tuple[int, str, str, int]] = []
        # Comentário da produção de cada pedido: (texto, quando, usuário que atualizou).
        self.comentarios: dict[int, tuple[str | None, datetime, int]] = {}
        # Cada alteração feita: (pedido, de, para, usuário).
        self.alteracoes_comentario: list[tuple[int, str | None, str | None, int]] = []
        self.alteracoes_pagamento: list[tuple[int, str, str, int]] = []
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
        if pedido.prazo_entrega is None:
            raise FalhaAoGravar()  # NOT NULL do banco
        self.pedidos[pedido.id] = pedido
        self.etapas[pedido.id] = "fila_producao"  # padrão da coluna no banco
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
                    prazo_entrega=p.prazo_entrega,
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
            prazo_entrega=p.prazo_entrega,
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

    # ---------- Produção ----------

    def _destaque(self, itens):
        com_imagem = [i for i in itens if i.imagem_caminho]
        return min(com_imagem, key=lambda i: (-i.quantidade, i.ordem), default=None)

    def listar_producao(self) -> list[CardProducao]:
        self._talvez_falhar("listar_producao")
        ordem_etapa = ["fila_producao", "em_producao", "aguardando_entrega", "entregue"]
        pedidos = sorted(
            self.pedidos.values(),
            key=lambda p: (ordem_etapa.index(self.etapas[p.id]), self.criado_em[p.id], p.id),
        )
        cards = []
        for p in pedidos:
            itens = sorted(p.itens, key=lambda i: i.ordem)
            destaque = self._destaque(itens)
            cards.append(
                CardProducao(
                    id=p.id,
                    etapa=self.etapas[p.id],
                    cliente_nome=p.cliente_nome,
                    forma_pagamento=p.forma_pagamento,
                    status_pagamento=p.status_pagamento,
                    tipo_entrega=p.tipo_entrega,
                    prazo_entrega=p.prazo_entrega,
                    produtos=tuple(i.produto for i in itens),
                    quantidade_total=sum(i.quantidade for i in itens),
                    observacoes=p.observacoes,
                    imagem_caminho=destaque.imagem_caminho if destaque else None,
                    imagem_produto=destaque.produto if destaque else None,
                    **self._comentario_do_card(p.id),
                )
            )
        return cards

    def _comentario_do_card(self, pedido_id: int) -> dict:
        if pedido_id not in self.comentarios:
            return {}
        texto, quando, usuario = self.comentarios[pedido_id]
        return {
            "comentario_producao": texto,
            "comentario_atualizado_em": quando,
            "comentario_atualizado_por_nome": USUARIOS[usuario],
        }

    def mover_etapa(
        self, pedido_id: int, etapa_esperada: str, nova_etapa: str, usuario_id: int
    ) -> EtapaAtualizada:
        """Mesmas regras da função mover_etapa_producao do banco."""
        self._talvez_falhar("mover_etapa")
        if pedido_id not in self.pedidos:
            raise MovimentoInvalido
        atual = self.etapas[pedido_id]
        if atual != etapa_esperada:
            raise ConflitoDeEtapa
        if not movimento_permitido(atual, nova_etapa):
            raise MovimentoInvalido
        self.etapas[pedido_id] = nova_etapa
        self.movimentos.append((pedido_id, atual, nova_etapa, usuario_id))
        return EtapaAtualizada(pedido_id, nova_etapa, USUARIOS[usuario_id])

    # ---------- Comentário e pagamento (mesmas regras das funções do banco) ----------

    def _conferir(self, pedido_id: int, usuario_id: int) -> None:
        if usuario_id not in USUARIOS:
            raise AlteracaoRecusada("usuario")
        if pedido_id not in self.pedidos:
            raise AlteracaoRecusada("nao_encontrado")

    def salvar_comentario_producao(
        self,
        pedido_id: int,
        comentario_esperado: str | None,
        novo_comentario: str | None,
        usuario_id: int,
    ) -> ComentarioAtualizado:
        self._talvez_falhar("salvar_comentario")
        self._conferir(pedido_id, usuario_id)
        novo = (novo_comentario or "").strip() or None
        if novo is not None and len(novo) > LIMITE_COMENTARIO:
            raise AlteracaoRecusada("invalido")
        atual = self.comentarios.get(pedido_id, (None,))[0]
        if atual != comentario_esperado:
            raise AlteracaoRecusada("conflito")
        quando = self.relogio
        self.comentarios[pedido_id] = (novo, quando, usuario_id)
        self.alteracoes_comentario.append((pedido_id, atual, novo, usuario_id))
        return ComentarioAtualizado(pedido_id, novo, quando, USUARIOS[usuario_id])

    def alterar_status_pagamento(
        self, pedido_id: int, status_esperado: str, novo_status: str, usuario_id: int
    ) -> PagamentoAtualizado:
        self._talvez_falhar("alterar_pagamento")
        self._conferir(pedido_id, usuario_id)
        if ALTERNANCIA_PAGAMENTO.get(status_esperado, ("",))[0] != novo_status:
            raise AlteracaoRecusada("invalido")
        pedido = self.pedidos[pedido_id]
        if pedido.status_pagamento != status_esperado:
            raise AlteracaoRecusada("conflito")
        self.pedidos[pedido_id] = replace(pedido, status_pagamento=novo_status)
        self.alteracoes_pagamento.append((pedido_id, status_esperado, novo_status, usuario_id))
        return PagamentoAtualizado(pedido_id, novo_status, self.relogio, USUARIOS[usuario_id])
