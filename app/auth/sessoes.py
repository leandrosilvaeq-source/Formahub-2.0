"""Tokens de sessão e de CSRF, e o redirecionamento seguro pós-login."""

import hashlib
import hmac
import secrets
from datetime import UTC, datetime
from urllib.parse import urlsplit

COOKIE_SESSAO = "formahub_sessao"
COOKIE_CSRF = "formahub_csrf"


def agora() -> datetime:
    return datetime.now(UTC)


def novo_token() -> str:
    """Token de sessão aleatório (256 bits). Só o SHA-256 dele vai para o banco."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def csrf_da_sessao(token_sessao: str) -> str:
    """Token CSRF de quem já está logado: derivado do token de sessão (que o atacante não tem)."""
    return hashlib.sha256(b"formahub-csrf:" + token_sessao.encode()).hexdigest()


def novo_csrf() -> str:
    """Token CSRF de quem ainda não está logado (cookie + campo do formulário)."""
    return secrets.token_urlsafe(32)


def csrf_valido(esperado: str | None, recebido: str | None) -> bool:
    if not esperado or not recebido:
        return False
    return hmac.compare_digest(esperado.encode(), recebido.encode())


def caminho_interno_seguro(destino: str | None, padrao: str = "/") -> str:
    """Aceita só caminhos do próprio site ("/pedidos/novo"); qualquer outra coisa vira o padrão."""
    if not destino or len(destino) > 512:
        return padrao
    if not destino.startswith("/") or destino.startswith("//") or "\\" in destino:
        return padrao
    if any(ord(c) < 32 or ord(c) == 127 for c in destino):
        return padrao
    partes = urlsplit(destino)
    if partes.scheme or partes.netloc:
        return padrao
    if partes.path.startswith(("/entrar", "/sair")):
        return padrao
    return destino
