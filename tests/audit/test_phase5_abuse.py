"""Phase 5 — the anti-abuse panel (whitelist/blacklist, counters, export)."""

from __future__ import annotations

import glob
import os
import tempfile

from sqlalchemy import select

from bot.database.session import get_session_factory
from bot.database.uow import UnitOfWork
from bot.models.log import AdminLog, LogAction
from bot.models.user import User
from bot.services.rbac import RbacService
from tests.audit.db import make_user


async def _bootstrap_owner(sim) -> str:
    factory = get_session_factory()
    async with factory() as session:
        owner = (
            await session.execute(select(User).where(User.telegram_id == sim.owner_id))
        ).scalars().first()
        if owner is None:
            owner = await make_user(session, username="abuse_owner")
            owner.telegram_id = sim.owner_id
            await session.commit()
        owner_id = owner.id
    uow = UnitOfWork()
    async with uow:
        await RbacService(uow).seed_roles()
        await uow.commit()
    return owner_id


async def _flagged_user(**flags) -> int:
    """A user with whitelist/blacklist/violation flags set."""
    factory = get_session_factory()
    async with factory() as session:
        user = await make_user(session, username="flagged_user")
        for key, value in flags.items():
            setattr(user, key, value)
        await session.commit()
        return user.telegram_id


async def _flags(telegram_id: int) -> dict:
    factory = get_session_factory()
    async with factory() as session:
        user = (
            await session.execute(select(User).where(User.telegram_id == telegram_id))
        ).scalars().one()
        return {
            "whitelisted": user.whitelisted,
            "blacklisted": user.blacklisted,
            "violation_count": user.violation_count,
        }


async def test_whitelist_removal_only_clears_the_whitelist(sim):
    """«حذف» in the whitelist must not silently un-blacklist the user."""
    await _bootstrap_owner(sim)
    target_tg = await _flagged_user(whitelisted=True, blacklisted=True)

    await sim.send("/start", sim.owner_id, first_name="Owner")
    res = await sim.click("abuse:wl_list", sim.owner_id)
    assert res["ok"], res["error"]
    screen = sim.last_screen(sim.owner_id)
    buttons = sim.buttons((screen or {}).get("reply_markup"))
    assert f"abuse:wl_del:{target_tg}" in buttons, f"no remove button offered: {buttons}"

    res = await sim.click(f"abuse:wl_del:{target_tg}", sim.owner_id)
    assert res["ok"], res["error"]

    flags = await _flags(target_tg)
    assert flags["whitelisted"] is False, "the user was not removed from the whitelist"
    assert flags["blacklisted"] is True, (
        "removing a user from the whitelist silently un-blacklisted them"
    )


async def test_clear_counters_requires_confirmation(sim):
    """Wiping every violation counter is destructive: confirm, then log it."""
    await _bootstrap_owner(sim)
    target_tg = await _flagged_user(violation_count=3)

    await sim.send("/start", sim.owner_id, first_name="Owner")
    res = await sim.click("abuse:clear_counters", sim.owner_id)
    assert res["ok"], res["error"]

    assert (await _flags(target_tg))["violation_count"] == 3, (
        "a single tap wiped the violation counters without confirmation"
    )
    screen = sim.last_screen(sim.owner_id)
    buttons = sim.buttons((screen or {}).get("reply_markup"))
    assert "abuse:clear_counters_confirm" in buttons, (
        f"no confirmation button was offered: {buttons}"
    )

    res = await sim.click("abuse:clear_counters_confirm", sim.owner_id)
    assert res["ok"], res["error"]
    assert (await _flags(target_tg))["violation_count"] == 0, "the confirmed reset did not run"

    factory = get_session_factory()
    async with factory() as session:
        logs = (
            await session.execute(
                select(AdminLog).where(AdminLog.action == LogAction.USER_EDIT)
            )
        ).scalars().all()
    assert any("شمارنده" in (log.description or "") for log in logs), (
        "the destructive reset was not audit-logged"
    )


async def test_abuse_export_sends_a_csv_and_cleans_up(sim):
    """The CSV export must not leak temp files (it used tempfile.mktemp)."""
    await _bootstrap_owner(sim)
    await _flagged_user(violation_count=2)

    before = len(glob.glob(os.path.join(tempfile.gettempdir(), "*.csv")))
    await sim.send("/start", sim.owner_id, first_name="Owner")
    res = await sim.click("abuse:export", sim.owner_id)
    assert res["ok"], res["error"]

    docs = [m for m in sim.session.sent if m.get("kind") == "SendDocument"]
    assert docs, "no CSV was sent"
    assert docs[-1]["document_bytes"], "the CSV was empty"
    after = len(glob.glob(os.path.join(tempfile.gettempdir(), "*.csv")))
    assert after <= before, f"the export leaked a temp file ({before} → {after})"
