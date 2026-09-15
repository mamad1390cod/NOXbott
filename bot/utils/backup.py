"""Database backup, export and restore utilities."""

import csv
import io
import json
import logging
import os
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from aiogram import Bot

from bot.database.engine import get_engine
from bot.config import get_settings

logger = logging.getLogger(__name__)

BACKUP_DIR = Path(__file__).resolve().parent.parent.parent / "backups"
EXPORT_DIR = Path(__file__).resolve().parent.parent.parent / "exports"


SQLITE_MAGIC = b"SQLite format 3\x00"


def sqlite_file_path() -> Path:
    """Absolute path of the SQLite file the application is actually using.

    Resolved from the engine URL (single source of truth) — a hardcoded
    "noxbot.db" silently pointed at the wrong file whenever the bot was started
    from another directory or with a custom ``DATABASE_URL``.
    """
    url = str(get_engine().url)
    db_path = url.replace("sqlite+aiosqlite:///", "").replace("sqlite:///", "")
    if not os.path.isabs(db_path):
        db_path = str(Path.cwd() / db_path)
    return Path(db_path)


def snapshot_database(source: Path, destination: Path) -> None:
    """Write a *consistent* copy of a live SQLite database.

    ``shutil.copy2`` on a database that has open writers can capture a torn
    file; SQLite's own backup API copies a transactionally consistent snapshot.
    """
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as src_conn, \
            sqlite3.connect(destination) as dst_conn:
        src_conn.backup(dst_conn)


def validate_sqlite_database(path: Path) -> None:
    """Raise ``ValueError`` unless ``path`` is a readable, sane SQLite DB."""
    if not path.exists() or path.stat().st_size == 0:
        raise ValueError("فایل خالی است")
    with path.open("rb") as fh:
        header = fh.read(len(SQLITE_MAGIC))
    if header != SQLITE_MAGIC:
        raise ValueError("ساختار فایل SQLite نیست")
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            row = connection.execute("PRAGMA integrity_check").fetchone()
        finally:
            connection.close()
    except sqlite3.DatabaseError as exc:
        raise ValueError(f"دیتابیس قابل خواندن نیست ({exc})") from exc
    if not row or str(row[0]).lower() != "ok":
        raise ValueError("integrity_check ناموفق بود")


def _ensure_dirs() -> None:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)


async def create_backup() -> Path:
    """Create a timestamped, consistent copy of the live SQLite database."""
    _ensure_dirs()
    db_path = sqlite_file_path()
    if not db_path.exists():
        raise FileNotFoundError(f"Database file not found: {db_path}")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = BACKUP_DIR / f"noxbot_backup_{stamp}.db"
    snapshot_database(db_path, dest)
    logger.info("Backup created: %s", dest)
    return dest


PENDING_RESTORE = BACKUP_DIR / "pending_restore.db"
PENDING_INFO = BACKUP_DIR / "pending_restore.json"


def stage_restore(backup_path: Path, *, uploaded_name: str | None, admin_telegram_id: int) -> Path:
    """Validate an uploaded backup and queue it for the next startup.

    Swapping the database file *while the bot runs* corrupts it: the engine's
    live connections keep writing the old image's pages over the replacement
    (verified: the file becomes "database disk image is malformed"). So the
    upload is validated and parked here, and ``apply_pending_restore()`` applies
    it at the next start, before the engine opens the database.
    """
    _ensure_dirs()
    validate_sqlite_database(backup_path)
    shutil.copy2(backup_path, PENDING_RESTORE)
    PENDING_INFO.write_text(
        json.dumps(
            {
                "uploaded_name": uploaded_name,
                "admin_telegram_id": admin_telegram_id,
                "staged_at": datetime.now().isoformat(timespec="seconds"),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    logger.warning("Backup staged for restore by %s: %s", admin_telegram_id, PENDING_RESTORE)
    return PENDING_RESTORE


def apply_pending_restore() -> bool:
    """Apply a staged restore. Must run before the engine opens the database."""
    if not PENDING_RESTORE.exists():
        return False
    validate_sqlite_database(PENDING_RESTORE)

    db_path = sqlite_file_path()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if db_path.exists():
        snapshot_database(db_path, Path(f"{db_path}.pre_restore_{stamp}"))
    shutil.copy2(PENDING_RESTORE, db_path)
    for sidecar in (Path(f"{db_path}-wal"), Path(f"{db_path}-shm")):
        sidecar.unlink(missing_ok=True)

    PENDING_RESTORE.unlink(missing_ok=True)
    PENDING_INFO.unlink(missing_ok=True)
    logger.warning("Pending backup applied at startup (previous copy: %s.pre_restore_%s)", db_path, stamp)
    return True


async def restore_backup(backup_path: Path) -> bool:
    """Restore database from a backup file (call only while the engine is
    stopped — see :func:`stage_restore`).

    The uploaded file is validated first, and the database it replaces is
    snapshotted to ``<db>.pre_restore_<stamp>`` so a bad restore can be undone.
    """
    _ensure_dirs()
    if not backup_path.exists():
        raise FileNotFoundError(f"Backup not found: {backup_path}")
    validate_sqlite_database(backup_path)

    db_path = sqlite_file_path()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if db_path.exists():
        snapshot_database(db_path, Path(f"{db_path}.pre_restore_{stamp}"))

    shutil.copy2(backup_path, db_path)
    # The write-ahead log / shared-memory files belong to the database we just
    # replaced. Leaving them next to the new file makes SQLite apply the old
    # WAL on top of it → "database disk image is malformed".
    for sidecar in (Path(f"{db_path}-wal"), Path(f"{db_path}-shm")):
        sidecar.unlink(missing_ok=True)
    logger.warning("Database restored from %s (previous copy: %s.pre_restore_%s)", backup_path, db_path, stamp)
    return True


def has_pending_restore() -> bool:
    return PENDING_RESTORE.exists()


async def export_tickets_csv(output_path: str | None = None) -> Path:
    """Export all tickets to a CSV file."""
    _ensure_dirs()
    from bot.database.uow import UnitOfWork
    from bot.models.ticket import Ticket

    uow = UnitOfWork()
    tickets = []
    async with uow:
        from sqlalchemy import select
        result = await uow.session.execute(select(Ticket))
        tickets = result.scalars().all()

    if output_path is None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = str(EXPORT_DIR / f"tickets_{stamp}.csv")

    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow([
            "Ticket ID", "User ID", "Category", "Subject", "Message",
            "Status", "Created At", "Closed At",
        ])
        for ticket in tickets:
            writer.writerow([
                ticket.id,
                ticket.user.telegram_id if ticket.user else "",
                ticket.ticket_category.name if ticket.ticket_category else "",
                ticket.subject,
                ticket.message,
                ticket.status.value,
                ticket.created_at.isoformat() if ticket.created_at else "",
                ticket.closed_at.isoformat() if ticket.closed_at else "",
            ])
    logger.info("Tickets exported to %s", output_path)
    return Path(output_path)


async def export_database_json() -> Path:
    """Export key tables to a JSON file for portability."""
    _ensure_dirs()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = EXPORT_DIR / f"database_export_{stamp}.json"

    data: dict = {}
    async with get_engine().begin() as conn:
        # Dump all rows from all tables using model metadata
        from bot.models.base import Base
        for table in Base.metadata.sorted_tables:
            rows = (await conn.execute(table.select())).mappings().all()
            data[table.name] = [dict(r) for r in rows]

    # Convert non-serializable values to strings
    def _default(obj):
        if hasattr(obj, "isoformat"):
            return obj.isoformat()
        try:
            from enum import Enum
            if isinstance(obj, Enum):
                return obj.value
        except Exception:
            pass
        return str(obj)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, default=_default, indent=2)
    logger.info("Full export to %s", output_path)
    return str(output_path)


def list_backups() -> list[Path]:
    """List available backup files."""
    _ensure_dirs()
    return sorted(BACKUP_DIR.glob("*.db"), key=lambda p: p.stat().st_mtime, reverse=True)


def list_exports() -> list[Path]:
    """List available export files."""
    _ensure_dirs()
    return sorted(EXPORT_DIR.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)