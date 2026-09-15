"""Phase 4 — customer-flow hardening (verified defects, one test per fix).

Guards:
1. re-rendering an identical screen (duplicate tap / back-to-back navigation)
   must not abort the handler: UserContextMiddleware swallows Telegram's
   "message is not modified", so a raw edit would skip the rest of the
   handler (e.g. never answering the tap);
2. the button offered after the first-purchase info wizard must be live;
3. the refund path must credit the customer's wallet (single implementation);
4. closing a ticket must refresh the screen and drop its stale action buttons;
5. wallet-ledger signs must come from the signed amounts themselves.
"""

from __future__ import annotations

from sqlalchemy import select

from bot.database.session import get_session_factory
from bot.services.order import OrderService
from tests.audit.db import (
    get_balance,
    make_category,
    make_config,
    make_custom,
    make_custom_category,
    make_product,
    make_user,
)
from tests.audit.flows import ShopDriver, finish_account_info


# --------------------------------------------------------------------------- #
#  1. duplicate-tap re-render must not raise "message is not modified"
# --------------------------------------------------------------------------- #


async def test_rerender_of_identical_screen_survives(sim):
    """Every back/menu button must tolerate Telegram's 'not modified' error."""
    factory = get_session_factory()
    async with factory() as session:
        cat = await make_category(session)
        user = await make_user(session, username="rerender")
        await make_product(
            session,
            price=10_000,
            stock=3,
            category_id=cat.id,
            image_url="https://example.com/banner.jpg",
        )
        custom_cat = await make_custom_category(session)
        await make_custom(session, category_id=custom_cat.id, banner_url="https://example.com/c.jpg")
        await session.commit()
        tg_id = user.telegram_id

    driver = ShopDriver(sim, tg_id)
    await driver.start()

    # From here on, every edit the handlers attempt is reported by Telegram as
    # "message is not modified" — exactly what a duplicate tap produces.
    sim.session.fail_edit_not_modified = True
    try:
        for callback in ("menu:home", "menu:products", "menu:configs", "menu:customs"):
            res = await sim.click(callback, tg_id)
            assert res["ok"], f"{callback} crashed on a re-render: {res['error']!r}"
            # The edit is a no-op, but the handler must still finish (answering
            # the tap); UserContextMiddleware swallows the BadRequest, so a raw
            # edit_text would silently abort the rest of the handler and leave
            # the button spinning.
            answered = [n for n, _ in res["calls"] if n == "AnswerCallbackQuery"]
            assert answered, f"{callback}: handler aborted before answering the tap"
    finally:
        sim.session.fail_edit_not_modified = False


async def test_rerender_of_identical_banner_screens_survives(sim):
    """Product/config/custom screens with a photo must survive a re-render."""
    factory = get_session_factory()
    async with factory() as session:
        cat = await make_category(session)
        cfg_cat = await make_category(session)
        user = await make_user(session, username="banner")
        product = await make_product(
            session,
            price=10_000,
            stock=3,
            category_id=cat.id,
            image_url="https://example.com/p.jpg",
        )
        config = await make_config(
            session,
            price=20_000,
            category_id=cfg_cat.id,
            image_url="https://example.com/cfg.jpg",
        )
        custom_cat = await make_custom_category(session)
        custom = await make_custom(
            session, category_id=custom_cat.id, banner_url="https://example.com/cu.jpg"
        )
        await session.commit()
        ids = {
            "tg": user.telegram_id,
            "product": product.id,
            "config": config.id,
            "custom": custom.id,
            "custom_cat": custom_cat.id,
        }

    driver = ShopDriver(sim, ids["tg"])
    await driver.start()

    sim.session.fail_edit_not_modified = True
    try:
        for callback in (
            f"prod_sel:{ids['product']}",
            f"config_sel:{ids['config']}",
            f"custom_sel:{ids['custom']}",
        ):
            res = await sim.click(callback, ids["tg"])
            assert res["ok"], f"{callback} crashed on a banner re-render: {res['error']!r}"
            answered = [n for n, _ in res["calls"] if n == "AnswerCallbackQuery"]
            assert answered, f"{callback}: handler aborted before answering the tap"
    finally:
        sim.session.fail_edit_not_modified = False


# --------------------------------------------------------------------------- #
#  2. the first-purchase info flow must offer a live cart button
# --------------------------------------------------------------------------- #


async def test_first_purchase_cart_button_is_live(sim):
    """`cart:view` had no handler; the wizard must emit a live callback."""
    factory = get_session_factory()
    async with factory() as session:
        cat = await make_category(session)
        user = await make_user(session, balance=0, username="newbie")
        product = await make_product(session, price=15_000, stock=5, category_id=cat.id)
        await session.commit()
        ids = {"cat": cat.id, "product": product.id, "tg": user.telegram_id}
        assert user.email is None  # forces the customer-info wizard

    driver = ShopDriver(sim, ids["tg"])
    await driver.start()
    await driver.add_product_to_cart(ids["cat"], ids["product"])

    await sim.send("buyer@example.com", ids["tg"])
    await sim.send("secret-pass", ids["tg"])
    await sim.send("Audit Buyer", ids["tg"])

    screen = sim.last_screen(ids["tg"])
    assert screen, "info wizard produced no confirmation screen"
    buttons = sim.buttons(screen["reply_markup"])
    assert buttons, f"no button on the confirmation screen: {screen}"
    assert "cart:view" not in buttons, f"dead callback still emitted: {buttons}"

    res = await sim.click(buttons[0], ids["tg"])
    assert res["ok"], res["error"]
    assert not res["unhandled"], f"button {buttons[0]!r} is dead"


# --------------------------------------------------------------------------- #
#  3. refunding an order must credit the wallet
# --------------------------------------------------------------------------- #


async def test_refund_credits_customer_wallet(sim):
    """`OrderService.refund_order` must use the wallet-crediting implementation."""
    factory = get_session_factory()
    async with factory() as session:
        cat = await make_category(session)
        user = await make_user(session, balance=200_000, username="refundee")
        product = await make_product(session, price=50_000, stock=5, category_id=cat.id)
        admin = await make_user(session, balance=0, username="refund_admin")
        await session.commit()
        ids = {
            "cat": cat.id,
            "user": user.id,
            "product": product.id,
            "admin": admin.id,
            "tg": user.telegram_id,
        }
    await finish_account_info(ids["user"])

    driver = ShopDriver(sim, ids["tg"])
    await driver.start()
    await driver.add_product_to_cart(ids["cat"], ids["product"])
    await driver.open_cart()
    res = await driver.checkout_wallet()
    assert res["ok"], res["error"]

    factory = get_session_factory()
    async with factory() as session:
        balance_after_purchase = await get_balance(session, ids["user"])
        assert balance_after_purchase == 150_000

        from bot.database.uow import UnitOfWork
        from bot.models.order import Order

        uow = UnitOfWork()
        async with uow:
            order = (
                await uow.session.execute(select(Order).where(Order.user_id == ids["user"]))
            ).scalars().first()
            admin_user = await uow.users.get(ids["admin"])
            assert order is not None and order.is_paid

            refunded = await OrderService(uow).refund_order(
                order, admin_user, reason="audit refund"
            )
            await uow.commit()
            assert refunded.status.value == "refunded"
            user_row = await uow.users.get(ids["user"])

    assert user_row.wallet_balance == 200_000, (
        "refund did not credit the wallet: "
        f"balance={user_row.wallet_balance} (expected 200000)"
    )


# --------------------------------------------------------------------------- #
#  4. closing a ticket must refresh the screen and drop stale actions
# --------------------------------------------------------------------------- #


async def test_ticket_close_refreshes_and_hides_actions(sim):
    """After «✅ تکمیل شد» the ticket must render as closed with no actions."""
    from bot.database.session import get_session_factory
    from bot.models.ticket import TicketCategory

    factory = get_session_factory()
    from tests.audit.db import make_user

    async with factory() as session:
        user = await make_user(session, username="ticketer")
        category = TicketCategory(name="audit ticket", is_active=True)
        session.add(category)
        await session.commit()
        tg_id, category_id = user.telegram_id, category.id

    await sim.send("/start", tg_id)
    await sim.click("menu:support", tg_id)
    await sim.click("ticket:new", tg_id)
    res = await sim.click(f"tick_cat:{category_id}", tg_id)
    assert res["ok"], res["error"]
    res = await sim.send("audit ticket body", tg_id)
    assert res["ok"], res["error"]

    async with factory() as session:
        from bot.models.ticket import Ticket

        ticket = (
            await session.execute(select(Ticket).where(Ticket.subject == "audit ticket"))
        ).scalars().first()
    assert ticket is not None, "ticket was not created through the UI"

    res = await sim.click("ticket:list", tg_id)
    assert res["ok"], res["error"]
    res = await sim.click(f"ticket:view:{ticket.id}", tg_id, message_id=777)
    assert res["ok"], res["error"]

    res = await sim.click(f"ticket:close:{ticket.id}", tg_id, message_id=777)
    assert res["ok"], res["error"]

    edits = [payload for name, payload in res["calls"] if name == "EditMessageText"]
    assert edits, "the ticket screen was not refreshed after closing it"

    # Read the *effective* message state: Telegram keeps the previous keyboard
    # when an edit omits reply_markup, so a text-only refresh would leave the
    # now-useless «پاسخ»/«تکمیل شد» buttons on screen.
    screen = sim.last_screen(tg_id)
    assert screen and screen["message_id"] == 777, screen
    assert "بسته" in (screen["text"] or ""), (
        f"closed ticket still renders as open: {(screen['text'] or '')[:80]!r}"
    )
    buttons = sim.buttons(screen["reply_markup"])
    assert f"ticket:close:{ticket.id}" not in buttons, (
        f"«تکمیل شد» still offered on a closed ticket: {buttons}"
    )
    assert f"ticket:reply:{ticket.id}" not in buttons, (
        f"«پاسخ» still offered on a closed ticket: {buttons}"
    )

    async with factory() as session:
        from bot.models.ticket import Ticket, TicketStatus

        assert (await session.get(Ticket, ticket.id)).status == TicketStatus.CLOSED


# --------------------------------------------------------------------------- #
#  5. wallet ledger must sign entries from the amounts themselves
# --------------------------------------------------------------------------- #


async def test_wallet_ledger_signs_credits_from_amount(sim):
    """A credit whose type is absent from the old hard-coded list must show «+»."""
    from bot.database.session import get_session_factory
    from bot.models.user_dashboard import TransactionType
    from tests.audit.db import make_user

    factory = get_session_factory()
    async with factory() as session:
        user = await make_user(session, username="ledger_user")
        user.wallet_balance = 500_000
        user.reward_points = 0
        await session.commit()
        tg_id, user_id = user.telegram_id, user.id

    # ADJUSTMENT is a legitimate credit type but was absent from the old list.
    async with factory() as session:
        from bot.models.user_dashboard import Transaction

        session.add_all(
            [
                Transaction(
                    user_id=user_id,
                    type=TransactionType.ADJUSTMENT,
                    amount=25_000,
                    balance_before=475_000,
                    balance_after=500_000,
                    note="audit adjustment credit",
                ),
                Transaction(
                    user_id=user_id,
                    type=TransactionType.SPEND,
                    amount=-10_000,
                    balance_before=500_000,
                    balance_after=490_000,
                    note="audit spend",
                ),
            ]
        )
        await session.commit()

    await sim.send("/start", tg_id)
    res = await sim.click("dash:wallet", tg_id, message_id=888)
    assert res["ok"], res["error"]

    screen = sim.last_screen(tg_id)
    assert screen and screen["message_id"] == 888, screen
    text = screen["text"] or ""
    # amounts render with Persian digits via format_price
    assert "+۲۵,۰۰۰ adjustment" in text, f"credit entry mis-signed: {text!r}"
    assert "-۱۰,۰۰۰ spend" in text, f"debit entry mis-signed: {text!r}"
