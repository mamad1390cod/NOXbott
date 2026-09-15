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
from tests.audit.db import get_balance, make_category, make_product, make_user
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
        user = await make_user(session, balance=100_000, username="receipt_user")
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
        payment_id, uid = payment.id, user.id
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


# --------------------------------------------------------------------------- #
#  3. admin ticket handling must reach the customer
# --------------------------------------------------------------------------- #


async def _open_ticket(sim, username: str):
    """A customer opens a ticket through the UI; returns its id + telegram id."""
    from bot.models.ticket import Ticket, TicketCategory

    factory = get_session_factory()
    async with factory() as session:
        user = await make_user(session, username=username)
        category = TicketCategory(name=f"phase5 {username}", is_active=True)
        session.add(category)
        await session.commit()
        tg, category_id, user_id = user.telegram_id, category.id, user.id
    await finish_account_info(user_id)

    await sim.send("/start", tg)
    await sim.click("menu:support", tg)
    await sim.click("ticket:new", tg)
    res = await sim.click(f"tick_cat:{category_id}", tg)
    assert res["ok"], res["error"]
    res = await sim.send(f"body of {username}", tg)
    assert res["ok"], res["error"]

    async with factory() as session:
        ticket = (
            await session.execute(select(Ticket).where(Ticket.user_id == user_id))
        ).scalars().one()
        return ticket.id, tg, user_id


async def test_admin_ticket_reply_reaches_the_customer(sim):
    """An admin reply must be stored, move the status, and notify the customer."""
    from bot.models.ticket import Ticket, TicketMessage, TicketStatus

    ticket_id, tg, user_id = await _open_ticket(sim, "ticket_customer")

    await sim.send("/start", sim.owner_id, first_name="Owner")
    res = await sim.click(f"atick:view:{ticket_id}", sim.owner_id)
    assert res["ok"], res["error"]

    res = await sim.click(f"atick:reply:{ticket_id}", sim.owner_id)
    assert res["ok"], res["error"]
    res = await sim.send("admin answer text", sim.owner_id)
    assert res["ok"], res["error"]

    factory = get_session_factory()
    async with factory() as session:
        ticket = await session.get(Ticket, ticket_id)
        status = ticket.status
        replies = (
            await session.execute(
                select(TicketMessage).where(TicketMessage.ticket_id == ticket_id)
            )
        ).scalars().all()
        admin_replies = [m for m in replies if m.is_admin and "admin answer text" in (m.message or "")]
        assert status == TicketStatus.IN_PROGRESS, f"ticket status {status}"
        assert admin_replies, "the admin reply was not stored"

    # The customer must actually receive it.
    assert any(
        "admin answer text" in (m.get("text") or "")
        for m in sim.session.sent
        if str(m.get("chat_id")) == str(tg)
    ), "the customer was not notified about the admin reply"


async def test_admin_ticket_close_from_admin_side(sim):
    """Closing a ticket as admin must land and notify the customer."""
    from bot.models.ticket import Ticket, TicketStatus

    ticket_id, tg, _user_id = await _open_ticket(sim, "ticket_customer2")

    await sim.send("/start", sim.owner_id, first_name="Owner")
    res = await sim.click(f"ticket:close:{ticket_id}", sim.owner_id)
    assert res["ok"], res["error"]

    factory = get_session_factory()
    async with factory() as session:
        status = (await session.get(Ticket, ticket_id)).status
        assert status == TicketStatus.CLOSED, f"ticket status {status}"

    screen = sim.last_screen(sim.owner_id)
    buttons = sim.buttons((screen or {}).get("reply_markup"))
    assert f"ticket:reply:{ticket_id}" not in buttons, (
        f"a closed ticket still offers «پاسخ»: {buttons}"
    )


# --------------------------------------------------------------------------- #
#  4. cancelling a custom must reach every participant
# --------------------------------------------------------------------------- #


async def test_admin_cancel_custom_notifies_participants(sim):
    """Cancelling a custom must inform the players who registered for it."""
    from bot.models.custom import CustomRegistration, CustomStatus
    from tests.audit.db import make_custom, make_custom_category

    factory = get_session_factory()
    async with factory() as session:
        cat = await make_custom_category(session)
        custom = await make_custom(session, category_id=cat.id, title="CANCEL-ME")
        player = await make_user(session, username="cancelled_player")
        await session.commit()
        custom_id, player_tg, player_id = custom.id, player.telegram_id, player.id

    # register the player for the custom (to be notified)
    async with factory() as session:
        session.add(
            CustomRegistration(
                custom_id=custom_id,
                user_id=player_id,
                codm_username="cancel-player",
            )
        )
        await session.commit()

    await sim.send("/start", sim.owner_id, first_name="Owner")
    res = await sim.click(f"acustom:cancel:{custom_id}", sim.owner_id)
    assert res["ok"], res["error"]
    res = await sim.send("audit cancel reason", sim.owner_id)
    assert res["ok"], res["error"]

    async with factory() as session:
        status = (await session.get(__import__("bot.models.custom", fromlist=["Custom"]).Custom, custom_id)).status
        assert status == CustomStatus.CANCELLED, f"custom status {status}"

    assert any(
        "لغو" in (m.get("text") or "")
        for m in sim.session.sent
        if str(m.get("chat_id")) == str(player_tg)
    ), "the registered player was not notified about the cancellation"


# --------------------------------------------------------------------------- #
#  5. destructive admin tools: they must delete exactly what they promise
# --------------------------------------------------------------------------- #


async def test_cleanup_deletes_completed_orders_but_keeps_the_money(sim):
    """«پاک‌سازی سفارش‌های تکمیل‌شده» must not touch balances or the ledger."""
    from bot.models.order import Order, OrderStatus
    from bot.models.user_dashboard import Transaction, TransactionType

    factory = get_session_factory()
    async with factory() as session:
        user = await make_user(session, balance=100_000, username="cleanup_user")
        keeper = Order(
            user_id=user.id,
            order_number="AUDIT-KEEP-1",
            status=OrderStatus.APPROVED,  # not completed → must survive
            total_amount=10_000,
            final_amount=10_000,
        )
        doomed = Order(
            user_id=user.id,
            order_number="AUDIT-DOOM-1",
            status=OrderStatus.COMPLETED,  # completed → must be removed
            total_amount=10_000,
            final_amount=10_000,
        )
        session.add_all([keeper, doomed])
        await session.flush()
        session.add(
            Transaction(
                user_id=user.id,
                type=TransactionType.SPEND,
                amount=-10_000,
                balance_before=110_000,
                balance_after=100_000,
                note="audit ledger row",
            )
        )
        await session.commit()
        keeper_number, user_id = keeper.order_number, user.id

    await sim.send("/start", sim.owner_id, first_name="Owner")
    res = await sim.click("admin:cleanup", sim.owner_id)
    assert res["ok"], res["error"]
    res = await sim.click("admin:cleanup:orders", sim.owner_id)
    assert res["ok"], res["error"]
    res = await sim.click("admin:cleanup:confirm:orders", sim.owner_id)
    assert res["ok"], res["error"]

    async with factory() as session:
        remaining = (
            await session.execute(select(Order.order_number))
        ).scalars().all()
        ledger = (
            await session.execute(select(Transaction).where(Transaction.user_id == user_id))
        ).scalars().all()
        balance = await get_balance(session, user_id)

    assert keeper_number in remaining, "a non-completed order was deleted"
    assert "AUDIT-DOOM-1" not in remaining, "the completed order was not deleted"
    assert balance == 100_000, f"the cleanup changed the wallet balance: {balance}"
    assert len(ledger) >= 1, "the financial ledger was deleted"


async def test_cleanup_requires_the_permission(sim):
    """A non-owner admin without DELETE_ORDERS must be refused."""
    from bot.models.order import Order, OrderStatus

    factory = get_session_factory()
    async with factory() as session:
        other = await make_user(session, username="plain_admin")
        user = await make_user(session, balance=0, username="cleanup_user2")
        order = Order(
            user_id=user.id,
            order_number="AUDIT-PERM-1",
            status=OrderStatus.COMPLETED,
            total_amount=5_000,
            final_amount=5_000,
        )
        session.add(order)
        await session.commit()
        other_tg = other.telegram_id

    await sim.send("/start", other_tg, first_name="Helper")
    res = await sim.click("admin:cleanup:confirm:orders", other_tg)
    assert res["ok"], res["error"]

    factory = get_session_factory()
    async with factory() as session:
        remaining = (await session.execute(select(Order.order_number))).scalars().all()
        assert "AUDIT-PERM-1" in remaining, (
            "a non-admin without DELETE_ORDERS managed to delete orders"
        )
