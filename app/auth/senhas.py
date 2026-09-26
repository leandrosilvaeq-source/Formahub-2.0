"""Senhas: Argon2id (argon2-cffi) e regras da senha nova. Nada aqui grava ou registra a senha."""

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

TAMANHO_MINIMO = 10
TAMANHO_MAXIMO = 128

# Argon2id com os parâmetros padrão da biblioteca (RFC 9106, perfil de baixo consumo de memória).
_hasher = PasswordHasher()


def gerar_hash(senha: str) -> str:
    return _hasher.hash(senha)


def conferir_senha(senha: str, hash_guardado: str) -> bool:
    try:
        return _hasher.verify(hash_guardado, senha)
    except (VerificationError, InvalidHashError):
        return False


def validar_nova_senha(nome: str, senha: str, confirmacao: str) -> str | None:
    """Devolve a mensagem de erro (em português) ou None se a senha nova é aceitável."""
    if not senha:
        return "Informe a senha."
    if len(senha) < TAMANHO_MINIMO:
        return f"A senha deve ter pelo menos {TAMANHO_MINIMO} caracteres."
    if len(senha) > TAMANHO_MAXIMO:
        return f"A senha deve ter no máximo {TAMANHO_MAXIMO} caracteres."
    if senha.casefold() == nome.casefold():
        return "A senha não pode ser igual ao nome."
    if senha != confirmacao:
        return "As senhas não conferem."
    return None
