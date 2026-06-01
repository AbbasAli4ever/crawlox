from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://crawlox:crawlox@postgres:5432/crawlox"
    redis_url: str = "redis://redis:6379/0"

    jwt_secret: str = "change-me-to-a-long-random-string"
    jwt_access_ttl_minutes: int = 15
    jwt_refresh_ttl_days: int = 7

    groq_api_key: str = ""
    gemini_api_key: str = ""

    billing_provider: str = "noop"
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""

    captcha_solver: str = "manual_only"
    twocaptcha_api_key: str = ""

    cors_allow_origins: str = "http://localhost:3000"
    log_level: str = "INFO"


settings = Settings()
