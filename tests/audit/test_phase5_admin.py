"""Phase 5 — admin flows: faithful edit semantics + admin screens.

Guards:
1. a screen that switches between text and media must still render: Telegram
   cannot change a message's type, so "edit" is not enough (the banner screens
   in products/configs/customs and the admin payment review all do this);
2. admin screens that re-render identical content must not abort their handler
   (UserContextMiddleware swallows "message is not modified" → the tap is never
   answered).
"""

from __future__ import annotations

from sqlalchemy import select

from bot.database.session import get_session_factory
from bot.models.payment import Payment, PaymentMethod, PaymentStatus
from bot.models.user import User
from tests.audit.db import make_category, make_product, make_user
from tests.audit.flows import ShopDriver, finish_account_info


# --------------------------------------------------------------------------- #
#  1. switching a message between text and media
# --------------------------------------------------------------------------- #


async def test_banner_reached_from_a_text_list_renders(sim):
    """A product with a banner must open from the (text) product list.

    ``edit_media`` on a text message fails with "there is no media in the
    message to edit"; the helper used to swallow that error, so the tap only
    answered and the list stayed on screen.
    """
    factory = get_session_factory()
    async with factory() as session:
        cat = await make_category(session)
        user = await make_user(session, balance=500_000, username="banner_user")
        product = await make_product(
            session,
            price=10_000,
            stock=3,
            category_id=cat.id,
            image_url="https://example.com/banner.jpg",
        )
        await session.commit()
        tg, cid, pid, uid = user.telegram_id, cat.id, product.id, user.id
    await finish_account_info(uid)

    driver = ShopDriver(sim, tg)
    await driver.start()
    await driver.click("menu:products")
    res = await driver.click(f"prod_cat:{cid}")
    assert res["ok"], res["error"]
    assert "لیست محصولات" in driver.screen_text()

    res = await driver.click(f"prod_sel:{pid}")
    assert res["ok"], res["error"]
    assert "قیمت" in driver.screen_text(), (
        f"the banner screen did not render: {driver.screen_text()[:80]!r}"
    )
    assert f"prod_add:{pid}" in driver.buttons()


async def test_text_list_rendered_again_after_a_banner(sim):
    """Going back from a banner screen to the text list must render too."""
    factory = get_session_factory()
    async with factory() as session:
        cat = await make_category(session)
        user = await make_user(session, balance=500_000, username="banner_back")
        product = await make_product(
            session,
            price=10_000,
            stock=3,
            category_id=cat.id,
            image_url="https://example.com/banner.jpg",
        )
        await session.commit()
        tg, cid, pid, uid = user.telegram_id, cat.id, product.id, user.id
    await finish_account_info(uid)

    driver = ShopDriver(sim, tg)
    await driver.start()
    await driver.click("menu:products")
    await driver.click(f"prod_cat:{cid}")
    await driver.click(f"prod_sel:{pid}")
    assert "قیمت" in driver.screen_text()

    res = await driver.click(f"prod_cat:{cid}")
    assert res["ok"], res["error"]
    assert "لیست محصولات" in driver.screen_text(), (
        f"the text list did not come back: {driver.screen_text()[:80]!r}"
    )
    assert f"prod_sel:{pid}" in driver.buttons()


# --------------------------------------------------------------------------- #
#  2. admin screens
# --------------------------------------------------------------------------- #


async def _admin_opens_backup(sim):
    await sim.send("/start", sim.owner_id, first_name="Owner")
    res = await sim.click("admin:panel", sim.owner_id)
    assert res["ok"], res["error"]
    return await sim.click("admin:backup", sim.owner_id)


async def test_backup_menu_rerender_survives(sim):
    """Tapping «💾 بکاپ دیتابیس» twice must not abort the handler.

    The identical re-render hits Telegram's "message is not modified", which the
    middleware swallows; with a raw edit the handler stopped before
    ``callback.answer()`` and the button span forever.
    """
    res = await _admin_opens_backup(sim)
    assert res["ok"], res["error"]
    assert "بکاپ" in (sim.last_screen(sim.owner_id) or {}).get("text", "")

    sim.session.fail_edit_not_modified = True
    try:
        res = await sim.click("admin:backup", sim.owner_id)
    finally:
        sim.session.fail_edit_not_modified = False

    assert res["ok"], res["error"]
    assert any(name == "AnswerCallbackQuery" for name, _ in res["calls"]), (
        "the tap was not answered when the screen did not change"
    )


async def test_backup_upload_screen_tapped_twice_survives(sim):
    """Same guard for the upload-instructions screen (the second raw edit)."""
    await _admin_opens_backup(sim)
    res = await sim.click("abackup:upload", sim.owner_id)
    assert res["ok"], res["error"]
    assert "آپلود بکاپ" in (sim.last_screen(sim.owner_id) or {}).get("text", "")

    sim.session.fail_edit_not_modified = True
    try:
        res = await sim.click("abackup:upload", sim.owner_id)
    finally:
        sim.session.fail_edit_not_modified = False

    assert res["ok"], res["error"]
    assert any(name == "AnswerCallbackQuery" for name, _ in res["calls"]), (
        "the tap was not answered when the screen did not change"
    )


async def test_admin_payment_detail_with_receipt_renders(sim):
    """The receipt photo must show when the review screen is opened from the list.

    The payment list is a *text* message, so ``edit_media`` cannot be used on it
    ("there is no media in the message to edit") — the screen has to be replaced.
    """
    factory = get_session_factory()
    async with factory() as session:
        cat = await make_category(session)
        user = await make_user(session, balance=100_000, username="receipt_user")
        product = await make_product(session, price=20_000, stock=2, category_id=cat.id)
        await session.commit()
        # a card order whose receipt is waiting for review
        from bot.models.order import Order, OrderStatus

        order = Order(
            user_id=user.id,
            order_number="AUDIT-CARD-1",
            payment_method=PaymentMethod.CARD,
            status=OrderStatus.PAYMENT_UPLOADED,
            total_amount=20_000,
            final_amount=20_000,
        )
        session.add(order)
        await session.flush()
        payment = Payment(
            user_id=user.id,
            order_id=order.id,
            amount=20_000,
            method=PaymentMethod.CARD,
            status=PaymentStatus.PENDING,
            receipt_url="https://example.com/receipt.jpg",
        )
        session.add(payment)
        await session.commit()
        tg, payment_id, uid = user.telegram_id, payment.id, user.id
    await finish_account_info(uid)

    await sim.send("/start", sim.owner_id, first_name="Owner")
    res = await sim.click("admin:payments", sim.owner_id)
    assert res["ok"], res["error"]
    res = await sim.click("apay:all", sim.owner_id)
    assert res["ok"], res["error"]

    res = await sim.click(f"apay:view:{payment_id}", sim.owner_id)
    assert res["ok"], res["error"]

    screen = sim.last_screen(sim.owner_id)
    assert screen is not None
    assert "پرداخت" in (screen["text"] or ""), (
        f"the payment review screen did not render: {(screen['text'] or '')[:80]!r}"
    )
    buttons = sim.buttons(screen["reply_markup"])
    assert f"apay:approve:{payment_id}" in buttons, (
        f"the review actions are missing: {buttons}"
    )


async def test_admin_payment_detail_rerender_survives(sim):
    """Re-opening the same receipt screen must answer the tap, not abort."""
    factory = get_session_factory()
    async with factory() as session:
        user = await make_user(session, balance=100_000, username="receipt_user2")
        await session.commit()
        from bot.models.order import Order, OrderStatus

        order = Order(
            user_id=user.id,
            order_number="AUDIT-CARD-2",
            payment_method=PaymentMethod.CARD,
            status=OrderStatus.PAYMENT_UPLOADED,
            total_amount=20_000,
            final_amount=20_000,
        )
        session.add(order)
        await session.flush()
        payment = Payment(
            user_id=user.id,
            order_id=order.id,
            amount=20_000,
            method=PaymentMethod.CARD,
            status=PaymentStatus.PENDING,
            receipt_url="https://example.com/receipt2.jpg",
        )
        session.add(payment)
        await session.commit()
        payment_id, uid = payment.id, user.id
    await finish_account_info(uid)

    await sim.send("/start", sim.owner_id, first_name="Owner")
    await sim.click("admin:payments", sim.owner_id)
    await sim.click("apay:all", sim.owner_id)
    res = await sim.click(f"apay:view:{payment_id}", sim.owner_id)
    assert res["ok"], res["error"]
    screen = sim.last_screen(sim.owner_id)
    assert screen and screen["message_id"] is not None

    sim.session.fail_edit_not_modified = True
    try:
        res = await sim.click(
            f"apay:view:{payment_id}", sim.owner_id, message_id=screen["message_id"]
        )
    finally:
        sim.session.fail_edit_not_modified = False

    assert res["ok"], res["error"]
    assert any(name == "AnswerCallbackQuery" for name, _ in res["calls"]), (
        "the tap was not answered when the receipt screen did not change"
    )
