"""Repositório de login em memória, para os testes. Nenhum teste acessa o Supabase."""

from dataclasses import replace
from datetime import datetime

from app.auth.repositorio import USUARIOS_FIXOS, SessaoAtiva, Usuario


class RepositorioMemoria:
    def __init__(self):
        self.usuarios = {nome: Usuario(id=i, nome=nome) for i, nome in enumerate(USUARIOS_FIXOS, 1)}
        self.sessoes: dict[str, dict] = {}  # token_hash -> dados da sessão

    def _nome_de(self, usuario_id: int) -> str:
        return next(n for n, u in self.usuarios.items() if u.id == usuario_id)

    def buscar_usuario(self, nome: str) -> Usuario | None:
        return self.usuarios.get(nome)

    def definir_senha_inicial(self, nome: str, senha_hash: str) -> bool:
        usuario = self.usuarios.get(nome)
        if usuario is None or usuario.senha_hash is not None:
            return False
        if not senha_hash.startswith("$argon2id$"):
            return False
        self.usuarios[nome] = replace(usuario, senha_hash=senha_hash, falhas_seguidas=0)
        return True

    def registrar_falhas(
        self, usuario_id: int, falhas_seguidas: int, bloqueado_ate: datetime | None
    ) -> None:
        nome = self._nome_de(usuario_id)
        self.usuarios[nome] = replace(
            self.usuarios[nome], falhas_seguidas=falhas_seguidas, bloqueado_ate=bloqueado_ate
        )

    def zerar_falhas(self, usuario_id: int) -> None:
        self.registrar_falhas(usuario_id, 0, None)

    def criar_sessao(self, token_hash: str, usuario_id: int, expira_em: datetime) -> None:
        self.sessoes[token_hash] = {
            "usuario_id": usuario_id,
            "expira_em": expira_em,
            "ultimo_uso": datetime.now(expira_em.tzinfo),
        }

    def buscar_sessao(self, token_hash: str) -> SessaoAtiva | None:
        dados = self.sessoes.get(token_hash)
        if dados is None:
            return None
        usuario = self.usuarios[self._nome_de(dados["usuario_id"])]
        return SessaoAtiva(
            usuario=Usuario(id=usuario.id, nome=usuario.nome),
            expira_em=dados["expira_em"],
            ultimo_uso=dados["ultimo_uso"],
        )

    def atualizar_ultimo_uso(self, token_hash: str, quando: datetime) -> None:
        if token_hash in self.sessoes:
            self.sessoes[token_hash]["ultimo_uso"] = quando

    def apagar_sessao(self, token_hash: str) -> None:
        self.sessoes.pop(token_hash, None)

    def apagar_sessoes_expiradas(self, agora: datetime) -> None:
        self.sessoes = {h: d for h, d in self.sessoes.items() if d["expira_em"] >= agora}
