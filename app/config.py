from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./local.db"
    cmc_api_key: str = ""
    coingecko_api_key: str = ""

    telegram_bot_token: str = ""
    telegram_bot_username: str = ""
    telegram_chat_id: str = ""

    session_secret: str = "dev-insecure-secret-change-me"
    gap_reminder_interval_hours: int = 24

    projects_config_path: str = "config/projects.yaml"


settings = Settings()
