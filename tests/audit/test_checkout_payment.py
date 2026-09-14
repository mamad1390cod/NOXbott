"""Phase 4/5/6 — full purchase flow through the real handlers.

Cart → Checkout → Wallet payment → Order APPROVED, plus duplicate clicks,
insufficient balance and stock accounting.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from bot.database.session import get_session_factory
from bot.models.order import Order, OrderStatus
from bot.models.payment import Payment, PaymentStatus
from bot.models.product import Product
from bot.models.user import User
from tests.audit.db import (
    get_balance,
    make_category,
    make_product,
    make_user,
    payments_of_order,
)
from tests.audit.flows import ShopDriver, finish_account_info


@pytest.fixture
async def shop():
    factory = get_session_factory()
    async with factory() as session:
        cat = await make_category(session)
        user = await make_user(session, balance=200_000, username="buyer")
        product = await make_product(session, price=50_000, stock=5, category_id=cat.id)
        await session.commit()
        ids = {"cat": cat.id, "user": user.id, "product": product.id, "tg": user.telegram_id}
    await finish_account_info(ids["user"])
    return ids


async def test_wallet_purchase_end_to_end(sim, shop):
    """Happy path: buy one product with wallet balance."""
    driver = ShopDriver(sim, shop["tg"])
    await driver.start()
    await driver.add_product_to_cart(shop["cat"], shop["product"])
    await driver.open_cart()
    res = await driver.checkout_wallet()
    assert res["ok"], res["error"]

    factory = get_session_factory()
    async with factory() as session:
        balance = await get_balance(session, shop["user"])
        product = await session.get(Product, shop["product"])
        orders = (await session.execute(select(Order).where(Order.user_id == shop["user"]))).scalars().all()

    assert balance == 150_000, f"wallet should be 200000-50000, got {balance}"
    assert len(orders) == 1
    order = orders[0]
    assert order.status == OrderStatus.APPROVED, f"order status {order.status}"
    assert order.final_amount == 50_000
    assert product.stock == 4, f"stock should be 4 after one purchase, got {product.stock}"

    # A single success screen + success alert
    assert any("پرداخت موفق" in t for t in sim.session.texts())


async def test_stock_is_decremented_exactly_once_after_purchase(sim, shop):
    """Guard against double stock accounting (cart reservation + approval)."""
    factory = get_session_factory()
    async with factory() as session:
        product = await session.get(Product, shop["product"])
        start_stock = product.stock

    driver = ShopDriver(sim, shop["tg"])
    await driver.start()
    await driver.add_product_to_cart(shop["cat"], shop["product"])

    async with factory() as session:
        after_add = (await session.get(Product, shop["product"])).stock

    await driver.open_cart()
    await driver.checkout_wallet()

    async with factory() as session:
        after_buy = (await session.get(Product, shop["product"])).stock

    assert after_buy == start_stock - 1, (
        f"stock accounting broken: start={start_stock} after_add={after_add} after_buy={after_buy}"
    )


async def test_insufficient_balance_blocks_purchase(sim):
    factory = get_session_factory()
    async with factory() as session:
        cat = await make_category(session)
        user = await make_user(session, balance=0, username="poor")
        product = await make_product(session, price=50_000, stock=3, category_id=cat.id)
        await session.commit()
        ids = {"cat": cat.id, "user": user.id, "product": product.id, "tg": user.telegram_id}
    await finish_account_info(ids["user"])

    driver = ShopDriver(sim, ids["tg"])
    await driver.start()
    await driver.add_product_to_cart(ids["cat"], ids["product"])
    await driver.open_cart()
    await driver.click("cart:checkout")
    assert "موجودی کیف پول کافی نیست" in driver.screen_text()

    # Force the confirm callback anyway (stale screen / forged click)
    res = await driver.click("checkout:confirm")
    assert res["ok"], res["error"]

    async with factory() as session:
        orders = (await session.execute(select(Order).where(Order.user_id == ids["user"]))).scalars().all()
        balance = await get_balance(session, ids["user"])
    assert not orders, "order must not be created for an underfunded wallet"
    assert balance == 0


async def test_double_confirm_click_pays_once(sim, shop):
    """Rapid double-click on «پرداخت از کیف پول» must charge only once."""
    driver = ShopDriver(sim, shop["tg"])
    await driver.start()
    await driver.add_product_to_cart(shop["cat"], shop["product"])
    await driver.open_cart()
    await driver.click("cart:checkout")

    first = await driver.click("checkout:confirm")
    second = await driver.click("checkout:confirm")
    assert first["ok"] and second["ok"], (first["error"], second["error"])

    factory = get_session_factory()
    async with factory() as session:
        balance = await get_balance(session, shop["user"])
        orders = (await session.execute(select(Order).where(Order.user_id == shop["user"]))).scalars().all()
        payments = (await session.execute(select(Payment).where(Payment.user_id == shop["user"]))).scalars().all()

    assert balance == 150_000, f"double charge detected: balance={balance}"
    assert len(orders) == 1, f"order duplicated: {len(orders)}"
    assert len(payments) == 1, f"payment duplicated: {len(payments)}"


async def test_cart_is_emptied_after_successful_purchase(sim, shop):
    driver = ShopDriver(sim, shop["tg"])
    await driver.start()
    await driver.add_product_to_cart(shop["cat"], shop["product"])
    await driver.open_cart()
    await driver.checkout_wallet()

    await driver.click("menu:cart")
    assert "سبد خرید خالی" in driver.screen_text() or "خالی" in driver.screen_text()


async def test_payment_amount_matches_order_total(sim, shop):
    driver = ShopDriver(sim, shop["tg"])
    await driver.start()
    await driver.add_product_to_cart(shop["cat"], shop["product"])
    await driver.open_cart()
    await driver.checkout_wallet()

    factory = get_session_factory()
    async with factory() as session:
        order = (await session.execute(select(Order).where(Order.user_id == shop["user"]))).scalars().first()
        payments = await payments_of_order(session, order.id)

    assert order.final_amount == 50_000
    assert payments, "payment record missing"
    assert payments[0].amount == order.final_amount


async def test_cancelled_cart_keeps_stock_consistent(sim, shop):
    """Adding then removing an item must return the reserved stock."""
    factory = get_session_factory()
    driver = ShopDriver(sim, shop["tg"])
    await driver.start()
    await driver.add_product_to_cart(shop["cat"], shop["product"])

    async with factory() as session:
        after_add = (await session.get(Product, shop["product"])).stock

    await driver.open_cart()
    item_buttons = [b for b in driver.buttons() if b.startswith("cart:view:")]
    if not item_buttons:
        await driver.click("menu:cart")
        item_buttons = [b for b in driver.buttons() if b.startswith("cart:view:")]
    item_id = item_buttons[0].split(":", 2)[2]
    await driver.click(f"cart:view:{item_id}")
    await driver.click(f"cart:del:{item_id}")

    async with factory() as session:
        after_remove = (await session.get(Product, shop["product"])).stock

    assert after_remove == after_add + 1, (
        f"removing a cart item must release its reserved stock "
        f"(after_add={after_add}, after_remove={after_remove})"
    )
