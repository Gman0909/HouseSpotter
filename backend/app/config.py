from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent  # backend/
PROJECT_DIR = BASE_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="HS_",
        env_file=str(PROJECT_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Server
    host: str = "0.0.0.0"
    port: int = 8410
    data_dir: Path = PROJECT_DIR / "data"

    # Auth (single user, created on first start)
    username: str = "admin"
    password: str = ""  # required on first run; ignored once the user exists
    session_secret: str = ""  # required
    session_days: int = 30

    # Anthropic
    anthropic_api_key: str = ""
    model_intake: str = "claude-opus-4-8"
    model_scoring: str = "claude-haiku-4-5-20251001"
    model_research: str = "claude-opus-4-8"

    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Email (SMTP)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_to: str = ""

    # External data APIs (free keys)
    ors_api_key: str = ""  # openrouteservice.org

    # Scraping
    scrape_enabled: bool = True
    playwright_fallback: bool = False

    @property
    def db_path(self) -> Path:
        return self.data_dir / "housespotter.db"

    @property
    def image_cache_dir(self) -> Path:
        return self.data_dir / "images"


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
