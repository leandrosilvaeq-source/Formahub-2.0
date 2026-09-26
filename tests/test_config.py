import pytest

from app.core.config import ConfigError, Settings, get_supabase_client


def test_supabase_sem_configuracao_falha_com_mensagem_clara():
    vazio = Settings(supabase_url="", supabase_publishable_key="")

    with pytest.raises(ConfigError, match="SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY"):
        get_supabase_client(vazio)


def test_supabase_informa_somente_variavel_faltante():
    parcial = Settings(supabase_url="https://exemplo.supabase.co", supabase_publishable_key="")

    with pytest.raises(ConfigError, match="SUPABASE_PUBLISHABLE_KEY") as erro:
        get_supabase_client(parcial)
    assert "SUPABASE_URL" not in str(erro.value)
