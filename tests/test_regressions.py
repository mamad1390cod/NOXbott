import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from bot.handlers import topup as topup_handlers
from bot.handlers.payments import _payment_is_owned_by_user
from bot.models import Base
from bot.models.order import Order, OrderDelivery, OrderStatus
from bot.models.order_event import OrderStatusEvent
from bot.models.payment import Payment, PaymentMethod, PaymentStatus
from bot.models.product import Product, ProductStatus
from bot.models.topup import TopUpPaymentMethod, TopUpRequest, TopUpStatus
from bot.models.user import User
from bot.models.user_dashboard import Transaction, TransactionType
from bot.repositories.cart import CartRepository
from bot.repositories.order import OrderRepository
from bot.repositories.payment import PaymentRepository
from bot.repositories.product import ProductRepository
from bot.repositories.topup import TopUpRequestRepository
from bot.repositories.user import UserRepository
from bot.repositories.user_dashboard import TransactionRepository
from bot.services.admin import AdminService
from bot.services.refund import AlreadyRefundedError, RefundService
from bot.services.wallet_payment import WalletPaymentService


@pytest_asyncio.fixture
async def session(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'regressions.db'}"
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        yield db
    await engine.dispose()


@pytest.mark.asyncio
async def test_stock_decrement_rejects_zero_and_insufficient_stock(session: AsyncSession):
    product = Product(
        title="limited",
        stock=2,
        status=ProductStatus.ACTIVE,
        unlimited_stock=False,
    )
    session.add(product)
    await session.flush()
    repository = ProductRepository(session)

    assert await repository.decrease_stock(product.id, 3) is False
    assert (await repository.get(product.id)).stock == 2
    assert await repository.decrease_stock(product.id, 2) is True
    assert (await repository.get(product.id)).stock == 0
    assert await repository.decrease_stock(product.id, 1) is False
    await session.rollback()


@pytest.mark.asyncio
async def test_payment_approval_is_single_transition(session: AsyncSession):
    user = User(telegram_id=1001, referral_code="TEST1001")
    admin = User(telegram_id=1002, referral_code="TEST1002")
    session.add_all([user, admin])
    await session.flush()
    payment = Payment(
        user_id=user.id,
        amount=100,
        method=PaymentMethod.CARD,
        status=PaymentStatus.PENDING,
    )
    session.add(payment)
    await session.flush()
    repository = PaymentRepository(session)

    assert await repository.approve_payment(payment.id, admin.id) is not None
    assert await repository.approve_payment(payment.id, admin.id) is None
    await session.rollback()


@pytest.mark.asyncio
async def test_cart_get_or_create_respects_unique_user_cart(session: AsyncSession):
    user = User(telegram_id=2001, referral_code="TEST2001")
    session.add(user)
    await session.flush()
    repository = CartRepository(session)

    first = await repository.get_or_create(user.id)
    second = await repository.get_or_create(user.id)

    assert first.id == second.id
    await session.rollback()


@pytest.mark.asyncio
async def test_payment_receipt_ownership_rejects_forged_order(session: AsyncSession):
    owner = User(telegram_id=3001, referral_code="TEST3001")
    other = User(telegram_id=3002, referral_code="TEST3002")
    session.add_all([owner, other])
    await session.flush()
    payment = Payment(
        user_id=owner.id,
        amount=100,
        method=PaymentMethod.CARD,
        status=PaymentStatus.PENDING,
        order_id="owner-order",
    )
    session.add(payment)
    await session.flush()

    assert _payment_is_owned_by_user(payment, other.id, "owner-order") is False
    assert _payment_is_owned_by_user(payment, owner.id, "owner-order") is True
    assert _payment_is_owned_by_user(payment, owner.id, "missing-order") is False
    await session.rollback()


@pytest.mark.asyncio
async def test_refund_is_single_transition(session: AsyncSession):
    owner = User(telegram_id=4001, referral_code="TEST4001")
    admin = User(telegram_id=4002, referral_code="TEST4002")
    session.add_all([owner, admin])
    await session.flush()
    order = Order(
        user_id=owner.id,
        order_number="NOX-TEST-REFUND",
        status=OrderStatus.APPROVED,
        total_amount=100,
        final_amount=100,
    )
    payment = Payment(
        user_id=owner.id,
        amount=100,
        method=PaymentMethod.CARD,
        status=PaymentStatus.APPROVED,
    )
    order.payments.append(payment)
    session.add(order)
    await session.flush()
    uow = SimpleNamespace(
        session=session,
        orders=OrderRepository(session),
        transactions=TransactionRepository(session),
        flush=session.flush,
    )
    service = RefundService(uow)

    await service.refund_order(order, admin, "test")
    with pytest.raises(AlreadyRefundedError):
        await service.refund_order(order, admin, "duplicate")
    await session.rollback()


@pytest.mark.asyncio
async def test_wallet_reference_is_unique(session: AsyncSession):
    user = User(telegram_id=5001, referral_code="TEST5001")
    session.add(user)
    await session.flush()
    session.add(
        Transaction(
            user_id=user.id,
            type=TransactionType.PURCHASE,
            amount=-10,
            balance_before=10,
            balance_after=0,
            ref_id="duplicate-reference",
        )
    )
    await session.flush()
    session.add(
        Order(
            user_id=user.id,
            order_number="NOX-TEST-CLEANUP-STATS-PENDING",
            status=OrderStatus.WAITING_PAYMENT,
            final_amount=500,
        )
    )
    await session.flush()
    session.add(
        Transaction(
            user_id=user.id,
            type=TransactionType.PURCHASE,
            amount=-10,
            balance_before=10,
            balance_after=0,
            ref_id="duplicate-reference",
        )
    )
    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()


@pytest.mark.asyncio
async def test_custom_topup_amount_survives_state_transition(monkeypatch):
    state = SimpleNamespace(
        clear=AsyncMock(),
        update_data=AsyncMock(),
    )
    message = SimpleNamespace(
        text="125,000",
        answer=AsyncMock(),
    )
    shown = {}

    async def show_method_selection(event, amount, edit):
        shown["amount"] = amount
        shown["edit"] = edit

    monkeypatch.setattr(
        topup_handlers,
        "_show_method_selection",
        show_method_selection,
    )

    await topup_handlers.do_custom_amount(message, state, None, None)

    state.clear.assert_awaited_once()
    state.update_data.assert_awaited_once_with(topup_amount=125000)
    assert shown == {"amount": 125000, "edit": False}


@pytest.mark.asyncio
async def test_cleanup_deletes_only_completed_orders_and_approved_topups(
    session: AsyncSession,
):
    user = User(telegram_id=6001, referral_code="TEST6001")
    session.add(user)
    await session.flush()
    completed = Order(
        user_id=user.id,
        order_number="NOX-TEST-CLEANUP-DONE",
        status=OrderStatus.COMPLETED,
        final_amount=100,
    )
    pending = Order(
        user_id=user.id,
        order_number="NOX-TEST-CLEANUP-PENDING",
        status=OrderStatus.PENDING,
        final_amount=100,
    )
    approved = TopUpRequest(
        user_id=user.id,
        amount=100,
        payment_method=TopUpPaymentMethod.CARD,
        status=TopUpStatus.APPROVED,
        tracking_code="TOPUP-CLEANUP-DONE",
    )
    waiting = TopUpRequest(
        user_id=user.id,
        amount=100,
        payment_method=TopUpPaymentMethod.CARD,
        status=TopUpStatus.WAITING_FOR_RECEIPT,
        tracking_code="TOPUP-CLEANUP-WAIT",
    )
    session.add_all([completed, pending, approved, waiting])
    await session.flush()

    uow = SimpleNamespace(
        session=session,
        orders=OrderRepository(session),
        topup_requests=TopUpRequestRepository(session),
        flush=session.flush,
    )
    service = AdminService(uow)

    assert await service.delete_completed_orders() == 1
    assert await service.delete_approved_topups() == 1
    assert await session.get(Order, pending.id) is not None
    assert await session.get(Order, completed.id) is None
    assert await session.get(TopUpRequest, waiting.id) is not None
    assert await session.get(TopUpRequest, approved.id) is None
    await session.rollback()


@pytest.mark.asyncio
async def test_cleanup_completed_orders_updates_dashboard_stats(session: AsyncSession):
    user = User(telegram_id=6002, referral_code="TEST6002")
    session.add(user)
    await session.flush()
    order = Order(
        user_id=user.id,
        order_number="NOX-TEST-CLEANUP-STATS",
        status=OrderStatus.COMPLETED,
        total_amount=250,
        final_amount=250,
        paid_at=datetime.now(timezone.utc),
    )
    order.payments.append(
        Payment(
            user_id=user.id,
            amount=250,
            method=PaymentMethod.BALANCE,
            status=PaymentStatus.APPROVED,
        )
    )
    session.add(order)
    await session.flush()
    session.add(
        OrderStatusEvent(
            order_id=order.id,
            to_status=OrderStatus.COMPLETED,
            is_system=True,
        )
    )
    order.delivery = OrderDelivery(
        order_id=order.id,
        delivery_type="config_text",
        config_text="test",
    )
    await session.flush()
    uow = SimpleNamespace(
        session=session,
        orders=OrderRepository(session),
        topup_requests=TopUpRequestRepository(session),
        flush=session.flush,
    )
    service = AdminService(uow)
    before = await service.get_cleanup_counts()
    stats_before = await OrderRepository(session).get_revenue_stats()
    assert before["completed_orders"] == 1
    assert stats_before["total_revenue"] == 250
    assert stats_before["total_orders"] == 1

    assert await service.delete_completed_orders() == 1
    await session.commit()
    stats_after = await OrderRepository(session).get_revenue_stats()
    assert stats_after["total_revenue"] == 0
    assert stats_after["today_revenue"] == 0
    assert stats_after["total_orders"] == 0


@pytest.mark.asyncio
async def test_wallet_payment_high_concurrency_preserves_balance_and_ledger(
    tmp_path,
):
    """Concurrent independent connections must never overdraw the wallet."""
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'wallet_stress.db'}",
        connect_args={"timeout": 30},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as setup:
        user = User(telegram_id=7001, referral_code="TEST7001", wallet_balance=100)
        setup.add(user)
        await setup.flush()
        order_ids = []
        for index in range(30):
            order = Order(
                user_id=user.id,
                order_number=f"NOX-STRESS-{index:03d}",
                status=OrderStatus.WAITING_PAYMENT,
                final_amount=10,
            )
            setup.add(order)
            await setup.flush()
            order_ids.append(order.id)
        user_id = user.id
        await setup.commit()

    async def pay(order_id: str):
        async with factory() as db:
            uow = SimpleNamespace(
                session=db,
                users=UserRepository(db),
                transactions=TransactionRepository(db),
                flush=db.flush,
            )
            try:
                service = WalletPaymentService(uow)
                await service.pay_order_with_wallet(user_id, order_id, 10)
                await db.commit()
                return "success"
            except Exception as error:
                await db.rollback()
                return type(error).__name__

    results = await asyncio.gather(*(pay(order_id) for order_id in order_ids))
    async with factory() as verify:
        balance = await verify.scalar(select(User.wallet_balance).where(User.id == user_id))
        transaction_count = await verify.scalar(
            select(func.count()).select_from(Transaction).where(
                Transaction.user_id == user_id,
                Transaction.type == TransactionType.PURCHASE,
            )
        )
        balances = (
            await verify.scalars(
                select(Transaction.balance_after)
                .where(
                    Transaction.user_id == user_id,
                    Transaction.type == TransactionType.PURCHASE,
                )
                .order_by(Transaction.balance_after)
            )
        ).all()
        payment_count = await verify.scalar(
            select(func.count()).select_from(Payment).where(
                Payment.user_id == user_id,
                Payment.status == PaymentStatus.APPROVED,
                Payment.method == PaymentMethod.BALANCE,
            )
        )
        assert balance == 0
        assert transaction_count == 10
        assert results.count("success") == 10
        assert balances == list(range(0, 100, 10))
        assert payment_count == 10
    await engine.dispose()


@pytest.mark.asyncio
async def test_same_order_concurrent_wallet_callbacks_charge_once(tmp_path):
    """Retries/callback duplicates for one order must be idempotent."""
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'wallet_duplicate.db'}",
        connect_args={"timeout": 30},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as setup:
        user = User(telegram_id=7002, referral_code="TEST7002", wallet_balance=100)
        setup.add(user)
        await setup.flush()
        order = Order(
            user_id=user.id,
            order_number="NOX-STRESS-DUP",
            status=OrderStatus.WAITING_PAYMENT,
            final_amount=10,
        )
        setup.add(order)
        await setup.flush()
        user_id, order_id = user.id, order.id
        await setup.commit()

    async def pay_once():
        async with factory() as db:
            uow = SimpleNamespace(
                session=db,
                users=UserRepository(db),
                transactions=TransactionRepository(db),
                flush=db.flush,
            )
            try:
                payment = await WalletPaymentService(uow).pay_order_with_wallet(
                    user_id, order_id, 10
                )
                await db.commit()
                return payment
            except Exception:
                await db.rollback()
                return None

    results = await asyncio.gather(*(pay_once() for _ in range(12)))
    async with factory() as verify:
        balance = await verify.scalar(select(User.wallet_balance).where(User.id == user_id))
        transaction_count = await verify.scalar(
            select(func.count()).select_from(Transaction).where(
                Transaction.ref_id == order_id
            )
        )
        payment_count = await verify.scalar(
            select(func.count()).select_from(Payment).where(Payment.order_id == order_id)
        )
        assert sum(result is not None for result in results) == 1
        assert balance == 90
        assert transaction_count == 1
        assert payment_count == 1
    await engine.dispose()
