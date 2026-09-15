"""Phase 5 — admin broadcast: the send screen must really send.

The service layer (BroadcastService.send / schedule_due / pause / cancel) and
the APScheduler tick in main.py were already in place; the UI stubs were not
wired to them, so the admin's «ارسال نهایی» reported a success that had not
happened and the schedule prompt dropped the admin's answer on the floor.
"""

from __future__ import annotations

from sqlalchemy import select

from bot.database.session import get_session_factory
from bot.models.broadcast import Broadcast, BroadcastStatus
from tests.audit.db import make_user
from tests.audit.flows import finish_account_info


async def _customers(count: int = 2):
    factory = get_session_factory()
    async with factory() as session:
        rows = []
        for i in range(count):
            user = await make_user(session, username=f"broadcast_target{i}")
            rows.append(user)
        await session.commit()
        tgs = [u.telegram_id for u in rows]
        ids = [u.id for u in rows]
    for uid in ids:
        await finish_account_info(uid)
    return tgs


async def _compose_broadcast(sim, text: str = "audit broadcast body") -> None:
    """Owner: compose a text broadcast for «همه»."""
    res = await sim.send("/start", sim.owner_id, first_name="Owner")
    assert res["ok"], res["error"]
    res = await sim.click("admin:broadcast", sim.owner_id)
    assert res["ok"], res["error"]
    res = await sim.click("abroad:compose", sim.owner_id)
    assert res["ok"], res["error"]
    res = await sim.click("abroad:type:text", sim.owner_id)
    assert res["ok"], res["error"]
    res = await sim.send(text, sim.owner_id)
    assert res["ok"], res["error"]
    res = await sim.click("abroad:aud:all", sim.owner_id)
    assert res["ok"], res["error"]


async def test_main_menu_send_actually_delivers(sim):
    """«🚀 ارسال» → «ارسال نهایی» must send, not just claim it did."""
    targets = await _customers(2)
    await _compose_broadcast(sim, "real broadcast body")

    res = await sim.click("abroad:done", sim.owner_id)
    assert res["ok"], res["error"]
    screen = sim.last_screen(sim.owner_id)
    buttons = sim.buttons((screen or {}).get("reply_markup"))
    assert "abroad:final_now" in buttons, (
        f"the settings screen must offer the real send action: {buttons}"
    )

    # the path the broadcast main menu itself opens: 🚀 ارسال → ارسال نهایی
    res = await sim.click("abroad:send", sim.owner_id)
    assert res["ok"], res["error"]
    screen = sim.last_screen(sim.owner_id)
    buttons = sim.buttons((screen or {}).get("reply_markup"))
    assert "abroad:send_now" not in buttons, (
        "the send screen still offers the no-op payload"
    )
    res = await sim.click("abroad:final_now", sim.owner_id)
    assert res["ok"], res["error"]

    # The customers must have received the message...
    delivered = [
        m for m in sim.session.sent
        if m.get("text") and "real broadcast body" in m["text"]
        and str(m.get("chat_id")) in {str(t) for t in targets}
    ]
    assert len(delivered) == len(targets), (
        f"the broadcast was not delivered to every target: {len(delivered)}/{len(targets)}"
    )

    # ...and the broadcast row must record it.
    factory = get_session_factory()
    async with factory() as session:
        rows = (await session.execute(select(Broadcast))).scalars().all()
        assert rows, "no broadcast row was created"
        broadcast = rows[-1]
        assert broadcast.status == BroadcastStatus.SENT, (
            f"broadcast status {broadcast.status}"
        )
        assert broadcast.sent_count == len(targets), (
            f"sent_count {broadcast.sent_count} != {len(targets)}"
        )


async def test_legacy_send_now_button_still_sends(sim):
    """Already-delivered messages keep the old button — it must work too."""
    targets = await _customers(1)
    await _compose_broadcast(sim, "legacy button body")

    res = await sim.click("abroad:send_now", sim.owner_id)
    assert res["ok"], res["error"]

    delivered = [
        m for m in sim.session.sent
        if m.get("text") and "legacy button body" in m["text"]
        and str(m.get("chat_id")) == str(targets[0])
    ]
    assert delivered, (
        "the old «ارسال نهایی» payload still pretends to send without sending"
    )


async def test_scheduling_persists_the_intent(sim):
    """The schedule prompt must store the broadcast for the running scheduler."""
    await _customers(1)
    await _compose_broadcast(sim, "scheduled body")

    res = await sim.click("abroad:schedule", sim.owner_id)
    assert res["ok"], res["error"]
    res = await sim.send("2030-01-31 18:30", sim.owner_id)
    assert res["ok"], res["error"]

    factory = get_session_factory()
    async with factory() as session:
        rows = (await session.execute(select(Broadcast))).scalars().all()
        assert rows, "the schedule answer was dropped (no broadcast row)"
        broadcast = rows[-1]
        assert broadcast.status == BroadcastStatus.PENDING, (
            f"a scheduled broadcast must wait as PENDING, got {broadcast.status}"
        )
        assert broadcast.scheduled_at is not None
        assert broadcast.scheduled_at.year == 2030, (
            f"scheduled_at not taken from the admin's answer: {broadcast.scheduled_at}"
        )


async def test_schedule_rejects_a_bad_answer_and_a_past_date(sim):
    """Bad input must be refused with a retry, not silently swallowed."""
    await _compose_broadcast(sim, "bad schedule body")

    res = await sim.click("abroad:schedule", sim.owner_id)
    assert res["ok"], res["error"]
    res = await sim.send("not a date", sim.owner_id)
    assert res["ok"], res["error"]
    factory = get_session_factory()
    async with factory() as session:
        assert not (await session.execute(select(Broadcast))).scalars().all(), (
            "an unparsable date still created a broadcast"
        )
    assert any("قالب زمان نامعتبر" in (m.get("text") or "") for m in sim.session.sent), (
        "the admin was not told the date was invalid"
    )

    res = await sim.send("2001-01-01 10:00", sim.owner_id)
    assert res["ok"], res["error"]
    async with factory() as session:
        assert not (await session.execute(select(Broadcast))).scalars().all(), (
            "a past date still created a broadcast"
        )


async def test_cancel_discards_the_draft(sim):
    """«لغو» must actually drop the composition."""
    await _customers(1)
    await _compose_broadcast(sim, "cancelled body")

    res = await sim.click("abroad:cancel", sim.owner_id)
    assert res["ok"], res["error"]
    assert any("لغو شد" in (m.get("text") or "") for m in sim.session.sent)

    # after cancelling, the draft is gone: sending now must not go out.
    res = await sim.click("abroad:final_now", sim.owner_id)
    assert res["ok"], res["error"]

    factory = get_session_factory()
    async with factory() as session:
        rows = (await session.execute(select(Broadcast))).scalars().all()
        assert not rows, "a cancelled draft was still sent"


async def test_broadcast_requires_the_permission(sim):
    """A user without SEND_BROADCAST must not reach the compose screen."""
    from bot.services.rbac import RbacService
    from bot.database.uow import UnitOfWork

    factory = get_session_factory()
    async with factory() as session:
        owner = await make_user(session, username="bc_owner")
        owner.telegram_id = sim.owner_id
        viewer = await make_user(session, username="bc_viewer")
        await session.commit()
        owner_id, viewer_tg, viewer_id = owner.id, viewer.telegram_id, viewer.id
    await finish_account_info(viewer_id)

    uow = UnitOfWork()
    async with uow:
        rbac = RbacService(uow)
        await rbac.seed_roles()
        owner = await uow.users.get(owner_id)
        await rbac.create_admin(viewer_tg, "viewer", added_by=owner)
        await uow.commit()

    await sim.send("/start", viewer_tg, first_name="Viewer")
    res = await sim.click("abroad:send_now", viewer_tg)
    assert res["ok"], res["error"]

    alerts = [p.get("text") for p in sim.session.calls_named("AnswerCallbackQuery")]
    assert any("ارسال همگانی" in (t or "") for t in alerts), (
        f"a viewer without SEND_BROADCAST was not refused: {alerts[-3:]}"
    )
    async with factory() as session:
        assert not (await session.execute(select(Broadcast))).scalars().all(), (
            "a viewer managed to send a broadcast"
        )
