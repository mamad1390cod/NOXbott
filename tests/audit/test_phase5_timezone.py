"""Phase 5 — the time convention: the database stores UTC, admins work in local time.

SQLite gives datetimes back *without* a timezone, and every expiry check compared
those naive UTC values against a naive ``datetime.now()`` — i.e. against local
time. On any server that is not running on UTC every discount code that had an
expiry date was therefore dead on arrival, and a scheduled broadcast went out
shifted by the server's offset.
"""

from __future__ import annotations

import os
import time as time_module
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from bot.database.session import get_session_factory
from bot.database.uow import UnitOfWork
from bot.models.broadcast import Broadcast
from bot.models.discount_code import DiscountCode, DiscountType
from bot.services.discount_code import DiscountCodeService
from bot.utils.clock import as_utc, format_local, parse_local, utc_now

# +03:30 as a POSIX TZ string: no tzdata files needed, but the C library still
# resolves it, so ``datetime.astimezone()`` and ``time.tzset()`` agree.
TEHRAN = "<+0330>-3:30"


@pytest.fixture
def tehran():
    """Run the test as if the bot were deployed on an Iranian server."""
    previous = os.environ.get("TZ")
    os.environ["TZ"] = TEHRAN
    time_module.tzset()
    try:
        yield timezone(timedelta(hours=3, minutes=30))
    finally:
        if previous is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous
        time_module.tzset()


async def _make_code(code: str, *, hours: int) -> str:
    uow = UnitOfWork()
    async with uow:
        created = await DiscountCodeService(uow).create_discount_code(
            code=code,
            discount_type=DiscountType.PERCENTAGE,
            discount_value=10,
            expires_at=utc_now() + timedelta(hours=hours),
        )
        await uow.commit()
        return created.id


async def _read_code(code: str) -> DiscountCode:
    factory = get_session_factory()
    async with factory() as session:
        return (
            await session.execute(select(DiscountCode).where(DiscountCode.code == code))
        ).scalars().one()


async def test_clock_helpers_treat_naive_values_as_utc(tehran):
    """A naive datetime from the database means UTC; humans see local time."""
    stored = datetime(2030, 1, 1, 16, 30)  # what SQLite hands back

    assert as_utc(stored) == datetime(2030, 1, 1, 16, 30, tzinfo=timezone.utc), (
        "a naive value must be interpreted as UTC, not shifted"
    )
    assert format_local(stored) == "2030-01-01 20:00", (
        f"local formatting is off: {format_local(stored)}"
    )
    assert parse_local("2030-01-01 20:00") == datetime(
        2030, 1, 1, 16, 30, tzinfo=timezone.utc
    ), "a typed local time must be stored as UTC"
    assert format_local(parse_local("2030-01-01 20:00")) == "2030-01-01 20:00"


async def test_a_code_expiring_in_two_hours_is_not_expired(tehran):
    """The regression that made every expiring code unusable off-UTC."""
    await _make_code("TZ2H", hours=2)
    stored = await _read_code("TZ2H")
    assert stored.is_expired is False, (
        "a code that expires in two hours is already expired: the expiry is being "
        "compared against local time instead of UTC"
    )

    uow = UnitOfWork()
    async with uow:
        ok, message, _, amount = await DiscountCodeService(uow).validate_and_get_discount(
            "TZ2H", 100_000
        )
    assert ok, f"a valid code was rejected at checkout: {message}"
    assert amount == 10_000


async def test_the_admin_types_local_time_and_the_database_stores_utc(sim, tehran):
    """The whole point: what the admin reads is what the admin typed."""
    res = await sim.send("/start", sim.owner_id, first_name="Owner")
    assert res["ok"], res["error"]
    res = await sim.click("admin:discount:create", sim.owner_id)
    assert res["ok"], res["error"]
    res = await sim.send("TZLOCAL", sim.owner_id)
    assert res["ok"], res["error"]
    res = await sim.click("admin:discount:type:percentage", sim.owner_id)
    assert res["ok"], res["error"]
    res = await sim.send("10", sim.owner_id)
    assert res["ok"], res["error"]
    res = await sim.click("admin:discount:skip_max_eligible", sim.owner_id)
    assert res["ok"], res["error"]
    prompt = sim.last_screen(sim.owner_id)
    assert "به وقت محلی" in (prompt or {}).get("text", ""), (
        "the prompt does not tell the admin which timezone it wants"
    )
    res = await sim.send("2030-01-01 20:00", sim.owner_id)
    assert res["ok"], res["error"]
    res = await sim.click("admin:discount:skip_max_uses", sim.owner_id)
    assert res["ok"], res["error"]
    res = await sim.click("admin:discount:skip_description", sim.owner_id)
    assert res["ok"], res["error"]

    stored = await _read_code("TZLOCAL")
    assert stored.expires_at.replace(tzinfo=None) == datetime(2030, 1, 1, 16, 30), (
        f"20:00 local must be stored as 16:30 UTC, got {stored.expires_at}"
    )
    assert stored.is_expired is False

    res = await sim.click(f"admin:discount:view:{stored.id}", sim.owner_id)
    assert res["ok"], res["error"]
    screen = sim.last_screen(sim.owner_id)
    assert "2030-01-01 20:00" in (screen or {}).get("text", ""), (
        f"the detail screen must show the local time back: {(screen or {}).get('text')}"
    )


async def test_a_scheduled_broadcast_fires_at_the_local_time_typed(sim, tehran):
    """«⏰ زمانبندی» must mean the time the admin typed, in the admin's timezone."""
    res = await sim.send("/start", sim.owner_id, first_name="Owner")
    assert res["ok"], res["error"]
    res = await sim.click("admin:broadcast", sim.owner_id)
    assert res["ok"], res["error"]
    res = await sim.click("abroad:compose", sim.owner_id)
    assert res["ok"], res["error"]
    res = await sim.click("abroad:type:text", sim.owner_id)
    assert res["ok"], res["error"]
    res = await sim.send("tz broadcast body", sim.owner_id)
    assert res["ok"], res["error"]
    res = await sim.click("abroad:aud:all", sim.owner_id)
    assert res["ok"], res["error"]
    res = await sim.click("abroad:schedule", sim.owner_id)
    assert res["ok"], res["error"]
    res = await sim.send("2030-01-01 18:30", sim.owner_id)
    assert res["ok"], res["error"]

    factory = get_session_factory()
    async with factory() as session:
        rows = (await session.execute(select(Broadcast))).scalars().all()
        broadcast = rows[-1] if rows else None
        assert broadcast is not None, "the schedule answer was dropped"
        assert broadcast.scheduled_at.replace(tzinfo=None) == datetime(
            2030, 1, 1, 15, 0
        ), f"18:30 local must be stored as 15:00 UTC, got {broadcast.scheduled_at}"

    confirmation = sim.last_screen(sim.owner_id)
    assert "2030-01-01 18:30" in (confirmation or {}).get("text", ""), (
        f"the confirmation must show the local time: {(confirmation or {}).get('text')}"
    )


async def test_a_past_local_time_is_refused(sim, tehran):
    """«زمان واردشده گذشته است» must use the same frame as the input."""
    res = await sim.send("/start", sim.owner_id, first_name="Owner")
    assert res["ok"], res["error"]
    res = await sim.click("admin:broadcast", sim.owner_id)
    assert res["ok"], res["error"]
    res = await sim.click("abroad:compose", sim.owner_id)
    assert res["ok"], res["error"]
    res = await sim.click("abroad:type:text", sim.owner_id)
    assert res["ok"], res["error"]
    res = await sim.send("past schedule body", sim.owner_id)
    assert res["ok"], res["error"]
    res = await sim.click("abroad:aud:all", sim.owner_id)
    assert res["ok"], res["error"]
    res = await sim.click("abroad:schedule", sim.owner_id)
    assert res["ok"], res["error"]

    # One hour ago *on the wall clock of that server*: read as UTC (the old
    # behaviour) it looks like the future, so this only gets refused once the
    # input is interpreted in the admin's own timezone.
    local_wall = utc_now().astimezone().replace(tzinfo=None)
    past_local = local_wall - timedelta(hours=1)
    res = await sim.send(past_local.strftime("%Y-%m-%d %H:%M"), sim.owner_id)
    assert res["ok"], res["error"]
    screen = sim.last_screen(sim.owner_id)
    assert "گذشته است" in (screen or {}).get("text", ""), (
        f"a local time in the past was accepted: {(screen or {}).get('text')}"
    )
