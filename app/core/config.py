"""Configuração centralizada do FormaHub 2.0.

As variáveis são lidas do ambiente (e do arquivo .env, se existir).
A aplicação sobe sem elas; só a integração com o Supabase as exige.
"""

import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv


class ConfigError(RuntimeError):
    """Configuração obrigatória ausente."""


@dataclass(frozen=True)
class Settings:
    supabase_url: str
    supabase_publishable_key: str

    @property
    def supabase_configurado(self) -> bool:
        return bool(self.supabase_url and self.supabase_publishable_key)


@lru_cache
def get_settings() -> Settings:
    load_dotenv()
    return Settings(
        supabase_url=os.getenv("SUPABASE_URL", "").strip(),
        supabase_publishable_key=os.getenv("SUPABASE_PUBLISHABLE_KEY", "").strip(),
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
