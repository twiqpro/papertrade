from functools import lru_cache
from os import getenv

from dotenv import load_dotenv

load_dotenv()


class Settings:
    def __init__(self) -> None:
        self.app_name = "NIFTY Options Paper Trader"
        self.environment = getenv("ENVIRONMENT", "development")
        self.broker = getenv("BROKER", "dhan")
        self.paper_trading_only = getenv("PAPER_TRADING_ONLY", "true").lower() != "false"
        self.dhan_client_id = getenv("DHAN_CLIENT_ID", "")
        self.dhan_access_token = getenv("DHAN_ACCESS_TOKEN", "")
        self.database_url = getenv("DATABASE_URL", "sqlite:///./twiq.db")
        # Optional: set password here instead of embedding it in DATABASE_URL (avoids @/# encoding issues)
        self.database_password = getenv("DATABASE_PASSWORD", "")
        self.api_key = getenv("API_KEY", "")
        self.tick_interval_seconds = float(getenv("TICK_INTERVAL_SECONDS", "2.5"))
        self.cors_origins = [
            origin.strip()
            for origin in getenv(
                "CORS_ORIGINS",
                "http://127.0.0.1:4173,http://localhost:4173,http://127.0.0.1:4174,http://localhost:4174,http://127.0.0.1:5173,http://localhost:5173",
            ).split(",")
            if origin.strip()
        ]


@lru_cache
def get_settings() -> Settings:
    return Settings()
