"""Phase 5 — admin order lifecycle driven through the real handlers.

Semantics (not just "does it crash"): every transition must land in the
database, the money must move exactly once, and the customer must be told.
"""

from __future__ import annotations

from sqlalchemy import select

from bot.database.session import get_session_factory
from bot.models.order import Order, OrderStatus
from bot.models.payment import PaymentStatus
from tests.audit.db import (
    get_balance,
    make_category,
    make_product,
    make_user,
    payments_of_order,
)
from tests.audit.flows import ShopDriver, finish_account_info


async def _make_card_order(*, tg_admin: int | None = None):
    """A customer order waiting for payment review (the admin's inbox)."""
    factory = get_session_factory()
    async with factory() as session:
        cat = await make_category(session)
        admin = await make_user(session, balance=0, username="order_admin")
        user = await make_user(session, balance=0, username="order_customer")
        product = await make_product(session, price=30_000, stock=4, category_id=cat.id)
        await session.commit()
        ids = {
            "cat": cat.id,
            "admin": admin.id,
            "user": user.id,
            "product": product.id,
            "tg": user.telegram_id,
        }
    await finish_account_info(ids["user"])
    return ids


async def _customer_places_card_order(sim, ids):
    """Customer: add to cart → card checkout → send receipt."""
    driver = ShopDriver(sim, ids["tg"])
    await driver.start()
    await driver.add_product_to_cart(ids["cat"], ids["product"])
    await driver.open_cart()
    res = await driver.checkout_card(file_id="admin_flow_receipt")
    assert res["ok"], res["error"]

    factory = get_session_factory()
    async with factory() as session:
        order = (
            await session.execute(select(Order).where(Order.user_id == ids["user"]))
        ).scalars().one()
        payments = await payments_of_order(session, order.id)
        return order.id, payments[0].id


async def test_admin_approve_then_deliver_completes_the_lifecycle(sim):
    """APPROVED → PREPARING → DELIVERED via the admin buttons."""
    ids = await _make_card_order()
    order_id, _payment_id = await _customer_places_card_order(sim, ids)

    factory = get_session_factory()
    async with factory() as session:
        assert (await session.get(Order, order_id)).status == OrderStatus.PAYMENT_UPLOADED

    await sim.send("/start", sim.owner_id, first_name="Owner")
    res = await sim.click(f"aorder:view:{order_id}", sim.owner_id)
    assert res["ok"], res["error"]

    for callback, expected in (
        (f"aorder:approve:{order_id}", OrderStatus.APPROVED),
        (f"aorder:prepare:{order_id}", OrderStatus.PREPARING),
        (f"aorder:deliver:{order_id}", OrderStatus.DELIVERED),
    ):
        res = await sim.click(callback, sim.owner_id)
        assert res["ok"], f"{callback} crashed: {res['error']!r}"
        async with factory() as session:
            order = await session.get(Order, order_id)
            assert order.status == expected, (
                f"{callback}: expected {expected}, got {order.status}"
            )

    async with factory() as session:
        payments = await payments_of_order(session, order_id)
        assert payments[0].status == PaymentStatus.APPROVED, (
            f"the payment must be approved with the order: {payments[0].status}"
        )


async def test_admin_transitions_are_logged(sim):
    """Every admin status change must leave an audit-log row."""
    ids = await _make_card_order()
    order_id, _ = await _customer_places_card_order(sim, ids)

    await sim.send("/start", sim.owner_id, first_name="Owner")
    res = await sim.click(f"aorder:approve:{order_id}", sim.owner_id)
    assert res["ok"], res["error"]

    factory = get_session_factory()
    async with factory() as session:
        from bot.models.log import AdminLog

        logs = (
            await session.execute(select(AdminLog).where(AdminLog.target_id == order_id))
        ).scalars().all()
        assert logs, "the admin transition was not audit-logged"


async def test_admin_refund_credits_the_customer_wallet(sim):
    """The admin refund button must pay the customer back exactly once."""
    ids = await _make_card_order()
    order_id, payment_id = await _customer_places_card_order(sim, ids)

    await sim.send("/start", sim.owner_id, first_name="Owner")
    # approve the payment so the order is paid, then refund it
    res = await sim.click(f"apay:approve:{payment_id}", sim.owner_id)
    assert res["ok"], res["error"]

    factory = get_session_factory()
    async with factory() as session:
        order = await session.get(Order, order_id)
        assert order.status == OrderStatus.APPROVED
        balance_before = await get_balance(session, ids["user"])
        assert balance_before == 0, "a card purchase must not debit the wallet"

    res = await sim.click(f"aorder:refund:{order_id}", sim.owner_id)
    assert res["ok"], res["error"]

    async with factory() as session:
        # read the ORM state *before* get_balance()/expire_all()
        status_after = (await session.get(Order, order_id)).status
        balance_after = await get_balance(session, ids["user"])
        assert status_after == OrderStatus.REFUNDED, f"order status {status_after}"
        assert balance_after == 30_000, (
            f"the refund must credit the wallet exactly once, got {balance_after}"
        )

    # A second refund must not pay twice.
    res = await sim.click(f"aorder:refund:{order_id}", sim.owner_id)
    async with factory() as session:
        balance_again = await get_balance(session, ids["user"])
        assert balance_again == 30_000, (
            f"double refund paid twice: {balance_again}"
        )
