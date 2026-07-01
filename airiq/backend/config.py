"""
config.py — Centralised settings loaded from environment / .env file.

Uses pydantic-settings so every variable is type-checked and documented
in one place.  Import `settings` everywhere else instead of os.getenv().
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All configurable knobs for AirIQ.  Values are read from .env first,
    then from real environment variables (env overrides .env)."""

    # ── External API keys ────────────────────────────────────────────────
    cpcb_api_key: str = ""          # CPCB / AQI data source API key
    anthropic_api_key: str = ""     # Claude API key for AI agents

    # ── Redis ────────────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379"

    # ── Scheduler ────────────────────────────────────────────────────────
    scheduler_interval_minutes: int = 30   # how often city jobs run

    # ── App metadata ─────────────────────────────────────────────────────
    app_env: str = "development"    # "development" | "production"
    log_level: str = "INFO"

    model_config = SettingsConfigDict(
        env_file=".env",            # load from .env in the working directory
        env_file_encoding="utf-8",
        case_sensitive=False,       # REDIS_URL == redis_url
        extra="ignore",             # silently ignore unknown env vars
    )


# Single shared instance — import this everywhere.
settings = Settings()
