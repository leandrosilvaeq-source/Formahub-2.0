"""Acesso ao banco para login e sessões (Supabase, com a chave secreta, só no servidor).

Os testes usam um repositório em memória no lugar deste; nenhum teste acessa o Supabase.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from typing import Protocol

from app.core.config import ConfigError, get_supabase_admin_client

USUARIOS_FIXOS = ("Leandro", "Kassia", "Marise")

logger = logging.getLogger("formahub.auth")


class ServicoIndisponivel(Exception):
    """O banco de usuários não está configurado ou não respondeu."""


@dataclass(frozen=True)
class Usuario:
    id: int
    nome: str
    senha_hash: str | None = None
    falhas_seguidas: int = 0
    bloqueado_ate: datetime | None = None

    @property
    def tem_senha(self) -> bool:
        return self.senha_hash is not None


@dataclass(frozen=True)
class SessaoAtiva:
    usuario: Usuario
    expira_em: datetime
    ultimo_uso: datetime


class RepositorioAuth(Protocol):
    def buscar_usuario(self, nome: str) -> Usuario | None: ...

    def definir_senha_inicial(self, nome: str, senha_hash: str) -> bool:
        """Grava a senha só se ainda não existir uma. True se gravou."""

    def registrar_falhas(
        self, usuario_id: int, falhas_seguidas: int, bloqueado_ate: datetime | None
    ) -> None: ...

    def zerar_falhas(self, usuario_id: int) -> None: ...

    def criar_sessao(self, token_hash: str, usuario_id: int, expira_em: datetime) -> None: ...

    def buscar_sessao(self, token_hash: str) -> SessaoAtiva | None: ...

    def atualizar_ultimo_uso(self, token_hash: str, quando: datetime) -> None: ...

    def apagar_sessao(self, token_hash: str) -> None: ...

    def apagar_sessoes_expiradas(self, agora: datetime) -> None: ...


def _data(valor: str | None) -> datetime | None:
    return datetime.fromisoformat(valor) if valor else None


def _usuario(linha: dict) -> Usuario:
    return Usuario(
        id=linha["id"],
        nome=linha["nome"],
        senha_hash=linha.get("senha_hash"),
        falhas_seguidas=linha.get("falhas_seguidas") or 0,
        bloqueado_ate=_data(linha.get("bloqueado_ate")),
    )


class RepositorioSupabase:
    def __init__(self, cliente):
        self._c = cliente

    def _executar(self, consulta):
        try:
            return consulta.execute()
        except Exception as erro:  # rede, permissão, tabela ausente...
            # Registra só o tipo do erro: a resposta do banco pode conter dados sensíveis.
            logger.error("Falha no acesso ao banco de usuários: %s", type(erro).__name__)
            raise ServicoIndisponivel from None

    def buscar_usuario(self, nome: str) -> Usuario | None:
        consulta = (
            self._c.table("usuarios")
            .select("id,nome,senha_hash,falhas_seguidas,bloqueado_ate")
            .eq("nome", nome)
            .limit(1)
        )
        linhas = self._executar(consulta).data
        return _usuario(linhas[0]) if linhas else None

    def definir_senha_inicial(self, nome: str, senha_hash: str) -> bool:
        consulta = self._c.rpc(
            "definir_senha_inicial", {"p_nome": nome, "p_senha_hash": senha_hash}
        )
        return self._executar(consulta).data is True

    def registrar_falhas(
        self, usuario_id: int, falhas_seguidas: int, bloqueado_ate: datetime | None
    ) -> None:
        dados = {
            "falhas_seguidas": falhas_seguidas,
            "bloqueado_ate": bloqueado_ate.isoformat() if bloqueado_ate else None,
        }
        self._executar(self._c.table("usuarios").update(dados).eq("id", usuario_id))

    def zerar_falhas(self, usuario_id: int) -> None:
        self.registrar_falhas(usuario_id, 0, None)

    def criar_sessao(self, token_hash: str, usuario_id: int, expira_em: datetime) -> None:
        dados = {
            "token_hash": token_hash,
            "usuario_id": usuario_id,
            "expira_em": expira_em.isoformat(),
        }
        self._executar(self._c.table("sessoes").insert(dados))

    def buscar_sessao(self, token_hash: str) -> SessaoAtiva | None:
        consulta = (
            self._c.table("sessoes")
            .select("expira_em,ultimo_uso,usuarios(id,nome)")
            .eq("token_hash", token_hash)
            .limit(1)
        )
        linhas = self._executar(consulta).data
        if not linhas:
            return None
        linha = linhas[0]
        return SessaoAtiva(
            usuario=Usuario(id=linha["usuarios"]["id"], nome=linha["usuarios"]["nome"]),
            expira_em=_data(linha["expira_em"]),
            ultimo_uso=_data(linha["ultimo_uso"]),
        )

    def atualizar_ultimo_uso(self, token_hash: str, quando: datetime) -> None:
        consulta = (
            self._c.table("sessoes")
            .update({"ultimo_uso": quando.isoformat()})
            .eq("token_hash", token_hash)
        )
        self._executar(consulta)

    def apagar_sessao(self, token_hash: str) -> None:
        self._executar(self._c.table("sessoes").delete().eq("token_hash", token_hash))

    def apagar_sessoes_expiradas(self, agora: datetime) -> None:
        self._executar(self._c.table("sessoes").delete().lt("expira_em", agora.isoformat()))


@lru_cache
def get_repositorio() -> RepositorioAuth:
    try:
        return RepositorioSupabase(get_supabase_admin_client())
    except ConfigError:
        raise ServicoIndisponivel from None
