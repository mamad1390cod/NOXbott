"""Phase 5 — the financial dashboard's filters.

TWO OPEN ITEMS (marked ``xfail(strict=True)``): the date *end* bound drops the
whole last day, and the product / category / admin / payment-status filters the
UI confirms are not applied to any query — the report is shown as filtered while
every number is unfiltered. Both live in ``FinanceRepository._paid_stmt`` and
are *financial reporting semantics*, i.e. reserved for an explicit go-ahead
before they are changed.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from bot.database.session import get_session_factory
from bot.database.uow import UnitOfWork
from bot.models.order import Order, OrderItem, OrderStatus
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
            owner = await make_user(session, username="finance_owner")
            owner.telegram_id = sim.owner_id
            await session.commit()
        owner_id = owner.id
    uow = UnitOfWork()
    async with uow:
        await RbacService(uow).seed_roles()
        await uow.commit()
    return owner_id


async def _paid_order(customer_id: str, *, paid_at: datetime, title: str, amount: int) -> str:
    factory = get_session_factory()
    async with factory() as session:
        order = Order(
            order_number=f"NOX-FIN-{title}",
            user_id=customer_id,
            status=OrderStatus.APPROVED,
            total_amount=amount,
            discount_amount=0,
            final_amount=amount,
            paid_at=paid_at,
            approved_at=paid_at,
            created_at=paid_at,
        )
        session.add(order)
        await session.flush()
        session.add(
            OrderItem(
                order_id=order.id,
                quantity=1,
                unit_price=amount,
                total_price=amount,
                product_title=title,
                product_type="digital",
            )
        )
        await session.commit()
        return order.id


async def _owner_turnover(sim) -> int:
    """Read «درآمد کل» off the freshly rendered dashboard."""
    res = await sim.click("fin:home", sim.owner_id)
    assert res["ok"], res["error"]
    screen = (sim.last_screen(sim.owner_id) or {}).get("text") or ""
    for line in screen.splitlines():
        if "درآمد کل" in line:
            digits = "".join(ch for ch in line if ch.isdigit())
            return int(digits or 0)
    raise AssertionError(f"the dashboard did not render a revenue line: {screen!r}")


async def test_dashboard_counts_a_paid_order(sim):
    """Sanity: a paid order shows up in «درآمد کل» (no filters active)."""
    owner_id = await _bootstrap_owner(sim)
    factory = get_session_factory()
    async with factory() as session:
        customer = await make_user(session, username="finance_customer")
        await session.commit()
        customer_id = customer.id
    await _paid_order(
        customer_id,
        paid_at=datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc),
        title="FIN-PRODUCT",
        amount=123_000,
    )
    await sim.send("/start", sim.owner_id, first_name="Owner")
    await sim.click("fin:clear", sim.owner_id)
    assert await _owner_turnover(sim) == 123_000
    assert owner_id


@pytest.mark.xfail(strict=True, reason="OPEN (D20): the «تا» date bound drops the last day — financial logic needs approval")
async def test_date_filter_includes_the_last_day(sim):
    """A range that ends on the order's day must count that order."""
    await _bootstrap_owner(sim)
    factory = get_session_factory()
    async with factory() as session:
        customer = await make_user(session, username="finance_range_customer")
        await session.commit()
        customer_id = customer.id
    await _paid_order(
        customer_id,
        paid_at=datetime(2026, 1, 31, 12, 0, tzinfo=timezone.utc),
        title="FIN-SAMEDAY",
        amount=77_000,
    )

    await sim.send("/start", sim.owner_id, first_name="Owner")
    res = await sim.click("fin:f_date", sim.owner_id)
    assert res["ok"], res["error"]
    res = await sim.send("2026-01-31", sim.owner_id)
    assert res["ok"], res["error"]
    res = await sim.send("2026-01-31", sim.owner_id)
    assert res["ok"], res["error"]

    assert await _owner_turnover(sim) == 77_000


@pytest.mark.xfail(strict=True, reason="OPEN (D20): the product filter is confirmed but never applied — financial logic needs approval")
async def test_product_filter_actually_filters(sim):
    """A filter that matches nothing must show no revenue."""
    await _bootstrap_owner(sim)
    factory = get_session_factory()
    async with factory() as session:
        customer = await make_user(session, username="finance_filter_customer")
        await session.commit()
        customer_id = customer.id
    await _paid_order(
        customer_id,
        paid_at=datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc),
        title="FIN-PRODUCT",
        amount=123_000,
    )

    await sim.send("/start", sim.owner_id, first_name="Owner")
    # the filter store is a module-level dict (per admin), so start clean
    res = await sim.click("fin:clear", sim.owner_id)
    assert res["ok"], res["error"]
    res = await sim.click("fin:f_product", sim.owner_id)
    assert res["ok"], res["error"]
    res = await sim.send("NO-SUCH-PRODUCT-XYZ", sim.owner_id)
    assert res["ok"], res["error"]

    assert any(
        "فیلتر محصول اعمال شد" in (m.get("text") or "") for m in sim.session.sent_to(sim.owner_id)
    ), "the filter flow did not confirm"
    assert await _owner_turnover(sim) == 0
