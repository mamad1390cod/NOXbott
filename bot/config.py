"""Configuration module for NOXbot Shop."""

import os
from pathlib import Path
from typing import Optional
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from bot.utils.paths import get_project_root, ensure_database_directory


class Settings(BaseSettings):
    """Application settings loaded from environment variables.

    Both uppercase (BOT_TOKEN/OWNER_ID) and lowercase (bot_token/owner_id) env
    keys are accepted so the config works with any .env layout.
    """

    # Use centralized path resolution
    _PROJECT_DIR = get_project_root()
    
    # Search for .env in multiple locations (in order of priority):
    # 1. Current Working Directory
    # 2. Project directory (where this file's parent.parent is)
    # 3. Parent of project directory
    _env_candidates = [
        Path.cwd() / ".env",
        _PROJECT_DIR / ".env",
        _PROJECT_DIR.parent / ".env",
    ]
    _ENV_FILE = next((p for p in _env_candidates if p.exists()), _PROJECT_DIR / ".env")

    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )

    # --- Telegram ---
    bot_token: str = Field(..., description="Bot token from @BotFather")
    owner_id: int = Field(..., description="Main admin Telegram ID")
    owner_ids: str = Field(
        default="",
        description="Additional owner Telegram IDs, comma-separated",
    )

    # Accept both names for the admin id.
    @property
    def admin_id(self) -> int:
        return self.owner_id

    @property
    def admin_ids(self) -> frozenset[int]:
        """Return the primary owner and any additional owner IDs."""
        values = {self.owner_id}
        for value in self.owner_ids.replace("(", "").replace(")", "").split(","):
            value = value.strip()
            if value.isdigit():
                values.add(int(value))
        return frozenset(values)

    admin_password: str = Field(description="Admin panel password (required in .env)")

    # --- Database ---
    # Absolute path so the DB is always stored in the project folder no matter
    # where the bot is launched from (a relative path would create a fresh DB
    # in the launch directory every time, making data appear to vanish).
    
    @classmethod
    def _get_default_database_url(cls) -> str:
        """Generate default database URL with proper path resolution."""
        db_path = ensure_database_directory()
        # Use forward slashes for SQLite URL (cross-platform compatible)
        return f"sqlite+aiosqlite:///{db_path.as_posix()}"

    database_url: str = Field(
        default_factory=lambda: Settings._get_default_database_url(),
        description="Async SQLAlchemy database URL",
    )

    # --- Runtime ---
    default_language: str = Field(default="fa", description="Default language (fa/en)")
    log_level: str = Field(default="INFO", description="Logging level")

    # --- Cache & Performance ---
    cache_default_ttl: int = Field(default=300, description="Default cache TTL in seconds")
    telegram_max_retries: int = Field(default=3, description="Max Telegram API retries")

    # --- Payment ---
    card_number: str = Field(default="", description="Payment card number")
    card_holder: str = Field(default="", description="Card holder name")
    bank_name: str = Field(default="", description="Bank name")

    # --- Support ---
    support_text: str = Field(
        default="برای پشتیبانی با ادمین تماس بگیرید.",
        description="Support contact text",
    )

    # --- Welcome ---
    welcome_message: str = Field(
        default="به فروشگاه گیمینگ NOXbot خوش آمدید!",
        description="Welcome message for new users",
    )

    # --- Pagination ---
    items_per_page: int = Field(default=10, description="Items per page for pagination")
    admin_items_per_page: int = Field(default=20, description="Admin items per page")

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, v: str) -> str:
        """Ensure database URL uses async driver and path is valid."""
        # Normalize async driver prefix
        if v.startswith("sqlite://"):
            v = v.replace("sqlite://", "sqlite+aiosqlite://", 1)
        if v.startswith("postgresql://"):
            v = v.replace("postgresql://", "postgresql+asyncpg://", 1)
        
        # For SQLite URLs, ensure the parent directory exists
        if v.startswith("sqlite+aiosqlite:///"):
            # Extract path from URL (remove sqlite+aiosqlite:///)
            db_path_str = v.replace("sqlite+aiosqlite:///", "")
            
            # Handle both Windows (I:/path) and Unix (/path) style paths
            # Convert forward slashes back to Path object
            db_path = Path(db_path_str.replace("/", "\\") if ":" in db_path_str and "\\" not in db_path_str else db_path_str)
            
            # Ensure parent directory exists
            if not db_path.is_absolute():
                # If relative path provided, make it absolute relative to project
                # (get_project_root() instead of the private attr: pydantic
                #  private attributes are not readable from the class object).
                db_path = get_project_root() / db_path
            
            db_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Return normalized URL with forward slashes (cross-platform)
            return f"sqlite+aiosqlite:///{db_path.as_posix()}"
        
        return v

    @property
    def is_production(self) -> bool:
        """Check if running in production mode."""
        return self.log_level.upper() != "DEBUG"


# Global settings instance
settings = Settings()


def get_settings() -> Settings:
    """Get settings instance (for dependency injection)."""
    return settings