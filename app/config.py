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

    # MarketUniverseProvider (app/criteria/market_universe.py): auto-tracks
    # the top N CMC coins by market cap, plus any coin listed on CMC that's
    # also live on Binance Spot (even outside the top N). Off by default —
    # the manual list keeps working either way. Needs no API key (uses
    # CMC's and CoinGecko's public endpoints) -- CMC_API_KEY above is only
    # for the per-project socials comparison in app/clients/cmc.py.
    market_universe_enabled: bool = False
    market_universe_top_n: int = 600
    market_universe_overrides_path: str = "config/cmc_cg_overrides.yaml"


settings = Settings()
