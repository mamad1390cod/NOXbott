"""Phase 5 — database backup and restore.

Backup/restore used a hardcoded ``Path("noxbot.db")`` (relative to the working
directory) while the application resolves its database from ``DATABASE_URL``.
Restoring also swapped the file underneath the running bot, which corrupts the
result (the engine's live connections keep writing the old image's pages) — the
upload is validated and staged instead, and ``main.py`` applies it at the next
start, before the engine opens the database.

The tests below pin that behaviour: the download must contain the live data, a
restore must never touch the live file while the bot runs, the startup applier
must adopt the staged file (keeping a rollback copy), and the destructive path
must not be reachable with payment permissions alone.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from sqlalchemy import select

from bot.database.session import get_session_factory
from bot.database.uow import UnitOfWork
from bot.models.user import User
from bot.services.rbac import RbacService
from bot.utils.backup import has_pending_restore, sqlite_file_path
from tests.audit.db import make_user

MARKER = "BACKUP-MARKER-XYZ-42"


async def _bootstrap_owner(sim) -> str:
    factory = get_session_factory()
    async with factory() as session:
        owner = (
            await session.execute(select(User).where(User.telegram_id == sim.owner_id))
        ).scalars().first()
        if owner is None:
            owner = await make_user(session, username="backup_owner")
            owner.telegram_id = sim.owner_id
            await session.commit()
        owner_id = owner.id
        marker = await make_user(session, username="backup_marker")
        marker.first_name = MARKER
        await session.commit()
    uow = UnitOfWork()
    async with uow:
        await RbacService(uow).seed_roles()
        await uow.commit()
    return owner_id


async def _make_admin(telegram_id: int, slug: str, owner_id: str) -> None:
    uow = UnitOfWork()
    async with uow:
        rbac = RbacService(uow)
        owner = await uow.users.get(owner_id)
        await rbac.create_admin(telegram_id, slug, added_by=owner)
        await uow.commit()


def _valid_backup(path: Path, marker: str) -> bytes:
    """A healthy, independent SQLite database with one marker row."""
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE restored_marker (value TEXT)")
    connection.execute("INSERT INTO restored_marker VALUES (?)", (marker,))
    connection.commit()
    connection.close()
    return path.read_bytes()


def _integrity(db_path: Path) -> str:
    connection = sqlite3.connect(db_path)
    try:
        return connection.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        connection.close()


async def test_backup_download_contains_the_live_database(sim):
    """The downloaded file must be a snapshot of the database in use."""
    await _bootstrap_owner(sim)
    db_path = sqlite_file_path()
    assert db_path.exists(), f"the live database is not where the app reads it: {db_path}"

    await sim.send("/start", sim.owner_id, first_name="Owner")
    res = await sim.click("abackup:download", sim.owner_id)
    assert res["ok"], res["error"]

    docs = [m for m in sim.session.sent if m.get("kind") == "SendDocument"]
    assert docs, "no backup file was sent (the handler could not find the database)"
    payload = docs[-1]["document_bytes"]
    assert payload, "the backup document was empty"
    assert payload[:16] == b"SQLite format 3\x00", "the sent file is not a SQLite database"
    assert MARKER.encode() in payload, (
        "the backup does not contain the live rows — it captured another file"
    )


async def test_restore_rejects_a_file_that_is_not_a_database(sim):
    """Garbage uploaded as ``.db`` must be refused and change nothing."""
    owner_id = await _bootstrap_owner(sim)
    db_path = sqlite_file_path()

    await sim.send("/start", sim.owner_id, first_name="Owner")
    res = await sim.click("abackup:upload", sim.owner_id)
    assert res["ok"], res["error"]
    res = await sim.send_document(
        sim.owner_id, file_name="evil.db", content=b"not a database at all"
    )
    assert res["ok"], res["error"]

    assert any(
        "دیتابیس سالم نیست" in (m.get("text") or "") for m in sim.session.sent_to(sim.owner_id)
    ), "the invalid upload was not refused with an explanation"
    assert not has_pending_restore(), "an invalid upload was staged for restore"
    assert _integrity(db_path) == "ok", "the live database was damaged by a bad upload"

    connection = sqlite3.connect(db_path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] > 0
    finally:
        connection.close()
    assert owner_id


async def test_restore_is_staged_and_leaves_the_live_database_alone(sim):
    """A valid upload is queued for the next start — never swapped underneath."""
    await _bootstrap_owner(sim)
    db_path = sqlite_file_path()

    uploaded = db_path.parent / "uploaded_for_test.db"
    content = _valid_backup(uploaded, "RESTORED-FROM-UPLOAD-XYZ")

    await sim.send("/start", sim.owner_id, first_name="Owner")
    res = await sim.click("abackup:upload", sim.owner_id)
    assert res["ok"], res["error"]
    res = await sim.send_document(sim.owner_id, file_name="restore.db", content=content)
    assert res["ok"], res["error"]
    uploaded.unlink()

    assert any(
        "ریستارت" in (m.get("text") or "") for m in sim.session.sent_to(sim.owner_id)
    ), "the staged restore was not confirmed with the restart requirement"

    from bot.utils.backup import PENDING_RESTORE

    assert has_pending_restore(), "the validated upload was not staged"
    assert PENDING_RESTORE.read_bytes() == content

    # the live database is untouched and still healthy
    assert _integrity(db_path) == "ok"
    connection = sqlite3.connect(db_path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] > 0
    finally:
        connection.close()


async def test_staged_restore_is_applied_at_startup(sim):
    """The startup applier adopts the staged file and keeps a rollback copy."""
    await _bootstrap_owner(sim)
    db_path = sqlite_file_path()

    from bot.utils.backup import PENDING_RESTORE, apply_pending_restore

    uploaded = db_path.parent / "staged_for_test.db"
    content = _valid_backup(uploaded, "APPLIED-AT-STARTUP-XYZ")
    PENDING_RESTORE.parent.mkdir(parents=True, exist_ok=True)
    PENDING_RESTORE.write_bytes(content)
    uploaded.unlink()
    assert has_pending_restore()

    assert apply_pending_restore() is True
    assert not has_pending_restore(), "the staged file was not consumed"

    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute("SELECT value FROM restored_marker").fetchall()
        assert rows == [("APPLIED-AT-STARTUP-XYZ",)], (
            "the staged backup was not adopted as the live database"
        )
    finally:
        connection.close()
    assert _integrity(db_path) == "ok", "the restored database is not a healthy SQLite file"

    rollbacks = sorted(db_path.parent.glob(f"{db_path.name}.pre_restore_*"))
    assert rollbacks, "no rollback copy of the replaced database was kept"
    assert not apply_pending_restore(), "a consumed restore was applied twice"


async def test_payment_permissions_cannot_touch_the_database(sim):
    """MANAGE_PAYMENTS alone (an operator) must not download or restore."""
    owner_id = await _bootstrap_owner(sim)
    factory = get_session_factory()
    async with factory() as session:
        person = await make_user(session, username="operator_admin")
        await session.commit()
        person_id, person_tg = person.id, person.telegram_id
    await _make_admin(person_tg, "operator", owner_id)

    await sim.send("/start", person_tg, first_name="Operator")

    before = sqlite_file_path().read_bytes()

    res = await sim.click("abackup:download", person_tg)
    assert res["ok"], res["error"]
    assert not [m for m in sim.session.sent if m.get("kind") == "SendDocument"], (
        "an operator downloaded the database"
    )
    download_blocked = any("abackup:download" in item for item in res["unhandled"]) or any(
        "دسترسی" in (p.get("text") or "") for p in sim.session.calls_named("AnswerCallbackQuery")
    )

    res = await sim.click("abackup:upload", person_tg)
    assert res["ok"], res["error"]
    upload_blocked = any("abackup:upload" in item for item in res["unhandled"]) or any(
        "دسترسی" in (p.get("text") or "") for p in sim.session.calls_named("AnswerCallbackQuery")
    )

    res = await sim.send_document(
        person_tg, file_name="x.db", content=b"SQLite format 3\x00" + b"\x00" * 64
    )
    assert res["ok"], res["error"]
    file_blocked = any("DOCUMENT" in item for item in res["unhandled"]) or any(
        "دسترسی" in (m.get("text") or "") for m in sim.session.sent_to(person_tg)
    )

    assert download_blocked, f"the operator reached the backup download: {res['unhandled']}"
    assert upload_blocked, "the operator reached the restore screen"
    assert file_blocked, "the operator's uploaded file was processed"
    assert not has_pending_restore(), "an operator staged a database restore"
    # the live database is healthy and still holds the app's own rows (the file
    # header legitimately changes as the bot writes, so compare content)
    db_path = sqlite_file_path()
    assert db_path.read_bytes()[:16] == b"SQLite format 3\x00"
    assert _integrity(db_path) == "ok", "the uploaded file replaced/damaged the database"
    connection = sqlite3.connect(db_path)
    try:
        names = [row[0] for row in connection.execute("SELECT first_name FROM users")]
    finally:
        connection.close()
    assert MARKER in names, "the operator's upload replaced the live data"
    assert before
    assert person_id


async def test_backup_permissions_allow_the_right_role(sim):
    """DEVELOPER (BACKUP_DATABASE + RESTORE_DATABASE) is the intended holder."""
    owner_id = await _bootstrap_owner(sim)
    factory = get_session_factory()
    async with factory() as session:
        dev = await make_user(session, username="developer_admin")
        await session.commit()
        dev_tg = dev.telegram_id
    await _make_admin(dev_tg, "developer", owner_id)

    await sim.send("/start", dev_tg, first_name="Developer")
    res = await sim.click("abackup:download", dev_tg)
    assert res["ok"], res["error"]
    docs = [m for m in sim.session.sent if m.get("kind") == "SendDocument"]
    assert docs, "a developer with BACKUP_DATABASE was refused the download"
    assert docs[-1]["document_bytes"][:16] == b"SQLite format 3\x00"

    res = await sim.click("abackup:upload", dev_tg)
    assert res["ok"], res["error"]
    assert any(
        "فایل بکاپ" in (m.get("text") or "") for m in sim.session.sent_to(dev_tg)
    ), "a developer with RESTORE_DATABASE was refused the restore screen"
