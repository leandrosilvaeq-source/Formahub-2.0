"""Configuração centralizada do FormaHub 2.0.

As variáveis são lidas do ambiente (e do arquivo .env, se existir).
A aplicação sobe sem elas; só a integração com o Supabase as exige.

SUPABASE_SECRET_KEY é usada somente pelo servidor (login e sessões). Nunca vai para o navegador
nem para o Git.
"""

import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv


class ConfigError(RuntimeError):
    """Configuração obrigatória ausente."""


SESSAO_DURACAO_PADRAO_HORAS = 12


@dataclass(frozen=True)
class Settings:
    supabase_url: str
    supabase_publishable_key: str
    supabase_secret_key: str = ""
    cookie_secure: bool = False
    sessao_duracao_horas: int = SESSAO_DURACAO_PADRAO_HORAS

    @property
    def supabase_configurado(self) -> bool:
        return bool(self.supabase_url and self.supabase_publishable_key)


def _duracao_em_horas(texto: str) -> int:
    try:
        horas = int(texto)
    except ValueError:
        return SESSAO_DURACAO_PADRAO_HORAS
    return horas if horas > 0 else SESSAO_DURACAO_PADRAO_HORAS


@lru_cache
def get_settings() -> Settings:
    load_dotenv()
    return Settings(
        supabase_url=os.getenv("SUPABASE_URL", "").strip(),
        supabase_publishable_key=os.getenv("SUPABASE_PUBLISHABLE_KEY", "").strip(),
        supabase_secret_key=os.getenv("SUPABASE_SECRET_KEY", "").strip(),
        cookie_secure=os.getenv("COOKIE_SECURE", "").strip().lower() in {"1", "true", "sim", "yes"},
        sessao_duracao_horas=_duracao_em_horas(os.getenv("SESSAO_DURACAO_HORAS", "").strip()),
    )


def get_supabase_client(settings: Settings | None = None):
    """Cria o cliente Supabase. Falha com mensagem clara se faltar configuração."""
    settings = settings or get_settings()
    faltando = [
        nome
        for nome, valor in (
            ("SUPABASE_URL", settings.supabase_url),
            ("SUPABASE_PUBLISHABLE_KEY", settings.supabase_publishable_key),
        )
        if not valor
    ]
    if faltando:
        raise ConfigError(
            "Supabase não configurado. Defina "
            + ", ".join(faltando)
            + " no arquivo .env (veja .env.example)."
        )

    from supabase import create_client

    return create_client(settings.supabase_url, settings.supabase_publishable_key)


def get_supabase_admin_client(settings: Settings | None = None):
    """Cliente Supabase com a chave secreta, só para o servidor (usuários e sessões)."""
    settings = settings or get_settings()
    faltando = [
        nome
        for nome, valor in (
            ("SUPABASE_URL", settings.supabase_url),
            ("SUPABASE_SECRET_KEY", settings.supabase_secret_key),
        )
        if not valor
    ]
    if faltando:
        raise ConfigError(
            "Acesso ao banco não configurado. Defina "
            + ", ".join(faltando)
            + " no arquivo .env (veja .env.example)."
        )

    from supabase import create_client

    return create_client(settings.supabase_url, settings.supabase_secret_key)
