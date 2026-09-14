"""Regression tests pinning the defects root-caused by the audit.

Every test here guards one *fixed* defect so it cannot silently come back:

* aiogram filters written with Python ``or`` — the second condition was
  discarded, leaving ``/menu``, ``/panel``, ``/profile`` and ``action:noop``
  without a handler.
* the ``account`` router was never registered — the CODM/account purchase
  wizard and the only ``action:cancel`` handler were unreachable.
* callback payloads / service results used without validation — handlers
  crashed with ``ValueError``/``AttributeError`` instead of answering.
* the navigation middleware wiped FSM data on wizard callbacks, so multi-step
  flows lost the values collected in earlier steps.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from bot.database.session import get_session_factory
from bot.models.cart import CartItem
from bot.models.custom import Custom, CustomStatus, CustomType
from bot.models.user import User
from tests.audit.db import make_category, make_product, make_user
from tests.audit.flows import ShopDriver


def _answers(sim) -> list[str]:
    """Texts passed to answerCallbackQuery (alerts and plain answers)."""
    out = []
    for payload in sim.session.calls_named("AnswerCallbackQuery"):
        if payload.get("text"):
            out.append(payload["text"])
    return out


async def test_slash_commands_are_live(sim):
    """`/menu`, `/panel`, `/profile`, `/account` must each answer."""
    factory = get_session_factory()
    async with factory() as session:
        user = await make_user(session, username="cmds")
        await session.commit()
        tg_id = user.telegram_id

    for command in ("/menu", "/panel", "/profile", "/account"):
        res = await sim.send(command, tg_id)
        assert res["ok"], f"{command} raised {res['error']!r}"
        assert not res["unhandled"], f"{command} has no handler (dead command)"
        screen = sim.last_screen(tg_id)
        assert screen and screen["text"], f"{command} produced no screen"


async def test_noop_page_indicator_is_handled(sim):
    """`action:noop` (page counter buttons) must be answered, not dead."""
    factory = get_session_factory()
    async with factory() as session:
        user = await make_user(session, username="noop")
        await session.commit()
        tg_id = user.telegram_id

    res = await sim.click("action:noop", tg_id)
    assert res["ok"], res["error"]
    assert not res["unhandled"], "action:noop is a dead callback"


async def test_account_info_purchase_flow_reaches_cart(sim):
    """Account-type products must finish the CODM info wizard and add to cart."""
    factory = get_session_factory()
    async with factory() as session:
        cat = await make_category(session)
        user = await make_user(session, balance=0, username="codm")
        product = await make_product(
            session,
            price=30_000,
            stock=3,
            category_id=cat.id,
            requires_account_info=True,
        )
        await session.commit()
        ids = {"cat": cat.id, "user": user.id, "product": product.id, "tg": user.telegram_id}

    driver = ShopDriver(sim, ids["tg"])
    await driver.start()
    await driver.add_product_to_cart(ids["cat"], ids["product"])

    res = await sim.send("audit_codm", ids["tg"])
    assert res["ok"], res["error"]
    assert any("نام کاربری" in text for text in sim.session.texts()), (
        "account wizard prompt was never sent"
    )

    await sim.send("audit@example.com", ids["tg"])
    await sim.send("audit-password", ids["tg"])

    res = await sim.click("account:confirm", ids["tg"])
    assert res["ok"], res["error"]
    assert not res["unhandled"], "account:confirm has no live handler"

    async with factory() as session:
        item = (
            await session.execute(
                select(CartItem).where(CartItem.product_id == ids["product"])
            )
        ).scalar_one_or_none()

    assert item is not None, "account info flow did not add the product to the cart"
    assert item.account_data and "audit_codm" in item.account_data, (
        f"account data was not stored: {item.account_data!r}"
    )


async def test_cancel_button_aborts_the_flow(sim):
    """`action:cancel` must clear the FSM and stop collecting data."""
    factory = get_session_factory()
    async with factory() as session:
        cat = await make_category(session)
        user = await make_user(session, username="canceller")
        product = await make_product(
            session,
            price=10_000,
            stock=2,
            category_id=cat.id,
            requires_account_info=True,
        )
        await session.commit()
        ids = {"cat": cat.id, "user": user.id, "product": product.id, "tg": user.telegram_id}

    driver = ShopDriver(sim, ids["tg"])
    await driver.start()
    await driver.add_product_to_cart(ids["cat"], ids["product"])
    await sim.send("audit_codm", ids["tg"])  # flow now waits for the email

    res = await sim.click("action:cancel", ids["tg"])
    assert res["ok"], res["error"]
    assert not res["unhandled"], "action:cancel has no live handler"
    assert any("لغو" in text for text in _answers(sim)), (
        f"cancel was not acknowledged: {_answers(sim)!r}"
    )

    # A message after cancelling must not be collected by the wizard.
    res = await sim.send("not-an-email", ids["tg"])
    assert res["ok"], res["error"]

    async with factory() as session:
        stored = await session.get(User, ids["user"])
        assert stored.email is None, "wizard kept collecting data after cancel"
        item = (
            await session.execute(
                select(CartItem).where(CartItem.product_id == ids["product"])
            )
        ).scalar_one_or_none()
        assert item is None, "cancelled flow still added the product to the cart"


async def test_custom_start_message_flow_survives_navigation(sim):
    """Admin wizard steps must keep the data collected by earlier steps."""
    factory = get_session_factory()
    async with factory() as session:
        custom = Custom(
            title="Audit Custom",
            type=CustomType.FREE,
            status=CustomStatus.DRAFT,
            is_visible=True,
        )
        session.add(custom)
        await session.commit()
        custom_id = custom.id

    owner = sim.owner_id
    res = await sim.send("/start", owner, first_name="Owner")
    assert res["ok"], res["error"]

    res = await sim.click(f"acustom:set_start_msg:{custom_id}", owner)
    assert res["ok"], res["error"]

    res = await sim.send("AUDIT START TEXT", owner)
    assert res["ok"], res["error"]

    res = await sim.click(f"acustom:confirm_start_msg:{custom_id}", owner)
    assert res["ok"], res["error"]

    async with factory() as session:
        updated = await session.get(Custom, custom_id)

    assert updated.start_message == "AUDIT START TEXT", (
        "navigation middleware dropped the wizard data "
        f"(start_message={updated.start_message!r})"
    )


async def test_stale_payment_buttons_answer_without_crashing(sim):
    """Approve/reject on an unknown payment must alert, not raise."""
    res = await sim.send("/start", sim.owner_id, first_name="Owner")
    assert res["ok"], res["error"]

    missing = str(uuid.uuid4())
    for callback in (f"apay:approve:{missing}", f"apay:reject:{missing}"):
        res = await sim.click(callback, sim.owner_id)
        assert res["ok"], f"{callback} raised {res['error']!r}"
        assert any("یافت نشد" in text for text in _answers(sim)), (
            f"{callback} did not report the missing payment"
        )
