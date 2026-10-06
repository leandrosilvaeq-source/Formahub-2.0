"""Estoque no Supabase (compras e lotes de filamentos, acessórios e embalagens), só no servidor.

Leitura e gravação passam pelas funções do banco listar_estoque, reservar_id_compra,
registrar_compra, editar_estoque_filamento e editar_estoque_item (migrations
20261009120000_estoque e 20261010120000_compras_estoque); as tabelas não têm acesso direto.
Todo lote nasce de um item de compra; a edição só corrige a descrição (o saldo não muda).
As fotos dos itens ficam no bucket privado compra-imagens e o banco guarda só o caminho.
Números vão e voltam como texto, para não passar por float.

Os testes usam um repositório em memória no lugar deste; nenhum teste acessa o Supabase.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from functools import lru_cache
from typing import Protocol

from app.core.config import ConfigError, get_supabase_admin_client

BUCKET_IMAGENS = "compra-imagens"

logger = logging.getLogger("formahub.estoque")


class FalhaNoEstoque(Exception):
    """O banco ou o Storage não respondeu (ou recusou) a leitura ou a gravação do estoque.

    incerta=True: a resposta não chegou, então não dá para saber se a operação aconteceu.
    """

    def __init__(self, incerta: bool = False):
        super().__init__()
        self.incerta = incerta


class LoteNaoEncontrado(Exception):
    """O lote a editar não existe (ou é de outra categoria)."""


def custo_por_kg(valor_unitario: Decimal, peso_rolo_g: Decimal) -> Decimal:
    """Custo/kg = valor unitário do rolo x 1000 / peso por rolo (sem arredondar)."""
    return valor_unitario * 1000 / peso_rolo_g


def custo_por_unidade(valor_total: Decimal, quantidade: int) -> Decimal:
    """Custo unitário = valor total / quantidade (sem arredondar)."""
    return valor_total / quantidade


@dataclass(frozen=True)
class Filamento:
    """Um lote de filamento: saldo atual e os valores da compra que o originou."""

    id: int
    cor: str
    material: str
    tipo: str  # código: solido, velvet, silk, bicolor ou tricolor
    marca: str
    peso_g: Decimal  # peso disponível
    data_compra: date
    peso_rolo_g: Decimal
    valor_unitario: Decimal  # por rolo

    @property
    def custo_kg(self) -> Decimal:
        return custo_por_kg(self.valor_unitario, self.peso_rolo_g)


@dataclass(frozen=True)
class ItemEstoque:
    """Um lote de acessório ou embalagem: saldo atual e os valores da compra."""

    id: int
    categoria: str  # acessorio ou embalagem
    nome: str
    quantidade: int  # disponível
    data_compra: date
    quantidade_comprada: int
    valor_total: Decimal

    @property
    def custo_unitario(self) -> Decimal:
        return custo_por_unidade(self.valor_total, self.quantidade_comprada)


@dataclass(frozen=True)
class Estoque:
    filamentos: list[Filamento]
    itens: list[ItemEstoque]  # acessórios e embalagens


@dataclass(frozen=True)
class ItemCompraParaGravar:
    """Uma linha da compra. Filamento usa cor..valor_unitario; os outros, nome e valor_total."""

    ordem: int
    categoria: str  # filamento, acessorio ou embalagem
    quantidade: int  # rolos ou unidades
    cor: str | None = None
    material: str | None = None
    tipo: str | None = None
    marca: str | None = None
    peso_rolo_g: Decimal | None = None
    valor_unitario: Decimal | None = None
    nome: str | None = None
    valor_total: Decimal | None = None
    imagem_caminho: str | None = None


@dataclass(frozen=True)
class CompraParaGravar:
    id: int  # reservado por reservar_id_compra
    chave_envio: uuid.UUID  # identifica a submissão: reenviar não duplica
    data_compra: date
    local_tipo: str
    local_nome: str | None
    usuario_id: int
    itens: tuple[ItemCompraParaGravar, ...]


class RepositorioEstoque(Protocol):
    def listar_estoque(self) -> Estoque:
        """Todos os lotes (FalhaNoEstoque se o banco não responder)."""

    def reservar_id_compra(self) -> int: ...

    def enviar_imagem(self, caminho: str, conteudo: bytes, tipo: str) -> None: ...

    def remover_imagens(self, caminhos: list[str]) -> None: ...

    def registrar_compra(self, compra: CompraParaGravar) -> bool:
        """Grava compra, itens e lotes numa única transação (ou nada).

        Devolve True se a chave_envio já estava gravada (nada novo foi gravado).
        """

    def editar_filamento(
        self, lote_id: int, cor: str, material: str, tipo: str, marca: str, usuario_id: int
    ) -> None:
        """Corrige a descrição do lote (LoteNaoEncontrado ou FalhaNoEstoque)."""

    def editar_item(self, lote_id: int, categoria: str, nome: str, usuario_id: int) -> None:
        """Corrige o nome do lote (LoteNaoEncontrado ou FalhaNoEstoque)."""


def _texto(valor: Decimal | None) -> str | None:
    return None if valor is None else str(valor)


class RepositorioEstoqueSupabase:
    def __init__(self, cliente):
        self._c = cliente

    def _executar(self, descricao: str, operacao):
        from postgrest.exceptions import APIError
        from storage3.exceptions import StorageApiError

        try:
            return operacao()
        except APIError as erro:  # o banco respondeu recusando
            if erro.code == "PT404":
                raise LoteNaoEncontrado from None
            logger.error("Supabase recusou %s: %s", descricao, erro.code)
            raise FalhaNoEstoque(incerta=False) from None
        except StorageApiError:
            logger.error("Storage recusou %s.", descricao)
            raise FalhaNoEstoque(incerta=False) from None
        except Exception as erro:  # rede, tempo esgotado...: a resposta não chegou
            # Registra só o tipo do erro: a mensagem pode conter dados sensíveis.
            logger.error("Falha ao executar %s no Supabase: %s", descricao, type(erro).__name__)
            raise FalhaNoEstoque(incerta=True) from None

    def _rpc(self, funcao: str, parametros: dict):
        return self._executar(funcao, lambda: self._c.rpc(funcao, parametros).execute().data)

    def listar_estoque(self) -> Estoque:
        dados = self._rpc("listar_estoque", {})
        try:
            return Estoque(
                filamentos=[
                    Filamento(
                        id=f["id"],
                        cor=f["cor"],
                        material=f["material"],
                        tipo=f["tipo"],
                        marca=f["marca"],
                        peso_g=Decimal(f["peso_disponivel_g"]),
                        data_compra=date.fromisoformat(f["data_compra"]),
                        peso_rolo_g=Decimal(f["peso_rolo_g"]),
                        valor_unitario=Decimal(f["valor_unitario"]),
                    )
                    for f in dados["filamentos"]
                ],
                itens=[
                    ItemEstoque(
                        id=i["id"],
                        categoria=i["categoria"],
                        nome=i["nome"],
                        quantidade=int(i["quantidade_disponivel"]),
                        data_compra=date.fromisoformat(i["data_compra"]),
                        quantidade_comprada=int(i["quantidade_comprada"]),
                        valor_total=Decimal(i["valor_total"]),
                    )
                    for i in dados["itens"]
                ],
            )
        except Exception as erro:
            # Resposta em formato inesperado (ex.: banco ainda sem a migration das compras).
            logger.error("Resposta inesperada de listar_estoque: %s", type(erro).__name__)
            raise FalhaNoEstoque from None

    def reservar_id_compra(self) -> int:
        return int(self._rpc("reservar_id_compra", {}))

    def enviar_imagem(self, caminho: str, conteudo: bytes, tipo: str) -> None:
        bucket = self._c.storage.from_(BUCKET_IMAGENS)
        opcoes = {"content-type": tipo, "upsert": "false"}
        self._executar("o envio da foto", lambda: bucket.upload(caminho, conteudo, opcoes))

    def remover_imagens(self, caminhos: list[str]) -> None:
        bucket = self._c.storage.from_(BUCKET_IMAGENS)
        self._executar("a remoção das fotos", lambda: bucket.remove(list(caminhos)))

    def registrar_compra(self, compra: CompraParaGravar) -> bool:
        parametros = {
            "p_id": compra.id,
            "p_chave_envio": str(compra.chave_envio),
            "p_data_compra": compra.data_compra.isoformat(),
            "p_local_tipo": compra.local_tipo,
            "p_local_nome": compra.local_nome,
            "p_itens": [
                {
                    "ordem": item.ordem,
                    "categoria": item.categoria,
                    "quantidade": item.quantidade,
                    "cor": item.cor,
                    "material": item.material,
                    "tipo": item.tipo,
                    "marca": item.marca,
                    "peso_rolo_g": _texto(item.peso_rolo_g),
                    "valor_unitario": _texto(item.valor_unitario),
                    "nome": item.nome,
                    "valor_total": _texto(item.valor_total),
                    "imagem_caminho": item.imagem_caminho,
                }
                for item in compra.itens
            ],
            "p_usuario_id": compra.usuario_id,
        }
        resposta = self._rpc("registrar_compra", parametros)
        try:
            return bool(resposta["duplicada"])
        except Exception:
            # A compra foi gravada, mas a resposta veio estranha: trata como incerta.
            logger.error("Resposta inesperada de registrar_compra.")
            raise FalhaNoEstoque(incerta=True) from None

    def editar_filamento(
        self, lote_id: int, cor: str, material: str, tipo: str, marca: str, usuario_id: int
    ) -> None:
        parametros = {
            "p_id": lote_id,
            "p_cor": cor,
            "p_material": material,
            "p_tipo": tipo,
            "p_marca": marca,
            "p_usuario_id": usuario_id,
        }
        self._rpc("editar_estoque_filamento", parametros)

    def editar_item(self, lote_id: int, categoria: str, nome: str, usuario_id: int) -> None:
        parametros = {
            "p_id": lote_id,
            "p_categoria": categoria,
            "p_nome": nome,
            "p_usuario_id": usuario_id,
        }
        self._rpc("editar_estoque_item", parametros)


@lru_cache
def _repositorio_supabase() -> RepositorioEstoque:
    return RepositorioEstoqueSupabase(get_supabase_admin_client())


def get_repositorio_estoque() -> RepositorioEstoque | None:
    """Repositório real; None se o acesso ao banco não estiver configurado."""
    try:
        return _repositorio_supabase()
    except ConfigError:
        return None
