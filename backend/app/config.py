"""Configuração vinda do ambiente (variáveis MB_*).

O que muda em tempo de execução (ativos, risco, modo, IA...) fica no banco e é
editado pela tela de Configurações: ver ``app.runtime``.
"""

from __future__ import annotations

import os
import secrets
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PLACEHOLDER_SECRETS = {
    "",
    "changeme",
    "troque",
    "TROQUE_POR_UMA_CHAVE_ALEATORIA_COM_48_CARACTERES",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MB_", env_file=".env", extra="ignore")

    env: str = "development"
    secret_key: str = ""
    data_dir: str = "./data"
    database_url: str = ""
    cookie_secure: bool = False
    session_days: int = 30
    log_level: str = "INFO"
    # Dono do sistema criado na primeira inicialização (opcional; senão, código de setup nos logs).
    admin_email: str = ""
    admin_password: str = ""
    # Terminal MetaTrader 5 padrão (container mt5 da stack).
    mt5_bridge_url: str = "http://mt5:8001"
    mt5_bridge_token: str = ""
    mt5_timeout_seconds: float = 20.0
    # IA (a chave também pode ser cadastrada pela tela, criptografada no banco).
    anthropic_api_key: str = ""
    # Liga os agentes em segundo plano (os testes desligam).
    agents_enabled: bool = True
    # Busca notícias e calendário na internet (os testes desligam).
    network_enabled: bool = True
    timezone: str = "America/Sao_Paulo"
    # Origens extras aceitas em requisições que alteram dados (CSRF), separadas por vírgula.
    allowed_origins: str = ""

    @property
    def is_production(self) -> bool:
        return self.env.lower() == "production"

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{Path(self.data_dir).resolve() / 'metabot.db'}"


def ensure_secret_key(settings: Settings) -> str:
    """Devolve a chave de criptografia. Em produção ela é obrigatória.

    Em desenvolvimento, gera uma e guarda em ``data_dir/secret.key`` para que
    as chaves salvas continuem legíveis entre reinícios.
    """
    key = settings.secret_key.strip()
    if key and key not in PLACEHOLDER_SECRETS:
        if settings.is_production and len(key) < 32:
            raise RuntimeError("MB_SECRET_KEY precisa ter pelo menos 32 caracteres em produção.")
        return key
    if settings.is_production:
        raise RuntimeError(
            "Defina MB_SECRET_KEY (32+ caracteres aleatórios) antes de subir em produção. "
            "Gere com: openssl rand -base64 48"
        )
    path = Path(settings.data_dir) / "secret.key"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return path.read_text().strip()
    key = secrets.token_urlsafe(48)
    path.write_text(key)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return key


@lru_cache
def get_settings() -> Settings:
    return Settings()
