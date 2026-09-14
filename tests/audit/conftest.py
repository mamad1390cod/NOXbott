"""Audit test fixtures.

IMPORTANT: environment variables are set *before* any `bot.*` import so the
whole test-session talks to a throwaway SQLite file and never touches the
production database bundled with the repository.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

# --------------------------------------------------------------------------- #
#  Isolated environment (must happen before bot.config is imported)
# --------------------------------------------------------------------------- #

_AUDIT_TMP = Path(tempfile.mkdtemp(prefix="noxbot-audit-"))
_DB_PATH = _AUDIT_TMP / "audit.db"

os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_DB_PATH.as_posix()}"
os.environ["BOT_TOKEN"] = "123456:AUDIT_TOKEN_NOT_REAL"
os.environ["OWNER_ID"] = "999000001"
os.environ["OWNER_IDS"] = ""
os.environ["ADMIN_PASSWORD"] = "audit-pass"
os.environ["LOG_LEVEL"] = "WARNING"
os.environ["CARD_NUMBER"] = "6037991111222233"
os.environ["CARD_HOLDER"] = "AUDIT HOLDER"
os.environ["BANK_NAME"] = "AUDIT BANK"

import pytest  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[2]
import sys  # noqa: E402

sys.path.insert(0, str(PROJECT_ROOT))

from bot.database.engine import get_engine, init_db  # noqa: E402
from bot.models.base import Base  # noqa: E402


@pytest.fixture(scope="session")
def audit_env() -> dict[str, str]:
    return {"tmp": str(_AUDIT_TMP), "db": str(_DB_PATH)}


# --------------------------------------------------------------------------- #
#  Database lifecycle
# --------------------------------------------------------------------------- #


async def _reset_schema() -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)


async def _bootstrap() -> None:
    """Mirror main.py's boot sequence: seed settings + RBAC roles."""
    from bot.database.uow import UnitOfWork
    from bot.services.rbac import RbacService
    from bot.services.settings import SettingsService

    uow = UnitOfWork()
    async with uow:
        ss = SettingsService(uow)
        await ss.ensure_defaults()
        await ss.load_cache()
        await RbacService(uow).seed_roles()


@pytest.fixture(autouse=True)
async def clean_db():
    """Every test starts from a pristine schema (no leftover test data)."""
    if os.environ.get("DATABASE_URL") != f"sqlite+aiosqlite:///{_DB_PATH.as_posix()}":
        raise RuntimeError("audit tests must run against the isolated audit database")
    await init_db()
    await _reset_schema()
    await _bootstrap()
    yield
    await _reset_schema()


@pytest.fixture(scope="session")
def sim_factory():
    """Build a TelegramSim once per session (routers cannot be re-attached)."""
    from tests.audit.harness import TelegramSim

    _sim = TelegramSim(owner_id=999_000_001)
    return lambda: _sim


@pytest.fixture
def sim(sim_factory):
    s = sim_factory()
    s.session.clear()
    s.events.reset()
    return s


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001
    """Remove the throwaway database directory when the run is over."""
    shutil.rmtree(_AUDIT_TMP, ignore_errors=True)
