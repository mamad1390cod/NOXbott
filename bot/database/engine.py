"""Database engine and initialization.

This module manages the SQLAlchemy async engine lifecycle and provides
database initialization functionality with proper path validation.
"""

import logging
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from bot.config import get_settings
from bot.models.base import Base
from bot.utils.paths import ensure_database_directory

logger = logging.getLogger(__name__)

_engine: AsyncEngine | None = None


def get_engine() -> AsyncEngine:
    """Get or create database engine with proper path validation.
    
    This function ensures the database directory exists before creating
    the engine, preventing "unable to open database file" errors.
    """
    global _engine
    if _engine is None:
        settings = get_settings()
        
        # Ensure database directory exists before creating engine
        if "sqlite" in settings.database_url and ":memory:" not in settings.database_url:
            ensure_database_directory()
            logger.info(f"Database URL: {settings.database_url}")
        
        engine_options = {
            "echo": settings.log_level == "DEBUG",
            "pool_pre_ping": True,
        }
        if (
            "sqlite" in settings.database_url
            and ":memory:" not in settings.database_url
        ):
            # A NullPool opens one SQLite connection per concurrent request.
            # Under load that exhausts resources before SQLite can serialize
            # its writes.  A bounded pool keeps concurrency controlled while
            # SQLite's busy timeout handles normal write contention.
            engine_options.update(
                connect_args={"timeout": 60},
                pool_size=32,
                max_overflow=0,
                pool_timeout=120,
            )
        _engine = create_async_engine(settings.database_url, **engine_options)
    return _engine


async def init_db() -> None:
    """Initialize database tables."""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_ensure_compatibility_columns)


def _ensure_compatibility_columns(connection: Connection) -> None:
    """Add safe additive columns for databases initialized before migrations."""
    columns = {
        column["name"]
        for column in inspect(connection).get_columns("users")
    }
    if "mandatory_membership_verified" not in columns:
        connection.execute(
            text(
                "ALTER TABLE users ADD COLUMN "
                "mandatory_membership_verified BOOLEAN NOT NULL DEFAULT 0"
            )
        )

    delivery_columns = {
        column["name"]
        for column in inspect(connection).get_columns("order_deliveries")
    }
    if "media_type" not in delivery_columns:
        connection.execute(
            text(
                "ALTER TABLE order_deliveries ADD COLUMN "
                "media_type VARCHAR(20) NOT NULL DEFAULT 'document'"
            )
        )


async def close_db() -> None:
    """Close database engine."""
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None
