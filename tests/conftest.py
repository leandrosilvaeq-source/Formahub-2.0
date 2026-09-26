"""Configuração comum dos testes: repositório de login falso e usuário logado simulado.

Nenhum teste acessa o Supabase: o repositório real é sempre trocado pelo de memória.
Os testes de autenticação (marcados com `sem_login`) usam o login de verdade, sobre esse
repositório falso; os demais testes rodam como se o Leandro estivesse logado.
"""

import pytest
from argon2 import PasswordHasher
from fastapi import Request

from app.auth import senhas
from app.auth.repositorio import Usuario, get_repositorio
from app.auth.rotas import exigir_usuario
from app.main import app
from tests.fake_auth import RepositorioMemoria


@pytest.fixture(autouse=True)
def hash_leve(monkeypatch):
    """Custo mínimo do Argon2 só para acelerar os testes (o custo real é testado à parte)."""
    monkeypatch.setattr(
        senhas, "_hasher", PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
    )


@pytest.fixture
def repo():
    return RepositorioMemoria()


@pytest.fixture(autouse=True)
def autenticacao_de_teste(request, repo):
    app.dependency_overrides[get_repositorio] = lambda: repo
    if not request.node.get_closest_marker("sem_login"):

        def usuario_logado(request: Request) -> Usuario:
            usuario = Usuario(id=1, nome="Leandro")
            request.state.usuario = usuario
            request.state.csrf = "csrf-de-teste"
            return usuario

        app.dependency_overrides[exigir_usuario] = usuario_logado
    yield
    app.dependency_overrides.clear()
