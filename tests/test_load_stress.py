"""High-concurrency backend smoke tests.

These tests exercise the real service/repository paths without contacting
Telegram.  They are intentionally separate from the default regression suite
because they create hundreds of independent database sessions.
"""

import asyncio
from collections import Counter
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.models import Base
from bot.models.order import Order, OrderStatus
from bot.models.payment import Payment, PaymentMethod, PaymentStatus
from bot.models.product import Product, ProductStatus
from bot.models.user import User
from bot.models.user_dashboard import Transaction, TransactionType
from bot.repositories.cart import CartRepository
from bot.repositories.order import OrderRepository
from bot.repositories.payment import PaymentRepository
from bot.repositories.product import ProductRepository
from bot.repositories.user import UserRepository
from bot.repositories.user_dashboard import TransactionRepository
from bot.services.order import OrderService
from bot.services.wallet_payment import WalletPaymentService


def _uow(session):
    return SimpleNamespace(
        session=session,
        users=UserRepository(session),
        carts=CartRepository(session),
        orders=OrderRepository(session),
        products=ProductRepository(session),
        payments=PaymentRepository(session),
        transactions=TransactionRepository(session),
        flush=session.flush,
    )


@pytest.mark.asyncio
async def test_500_concurrent_customer_checkouts_and_admin_reads(tmp_path):
    """500 customers pay while admin order/payment statistics are read."""
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'load.db'}",
        connect_args={"timeout": 60},
        pool_size=32,
        max_overflow=0,
        pool_timeout=300,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as setup:
        product = Product(
            title="load-product",
            status=ProductStatus.ACTIVE,
            price=10,
            stock=500,
            unlimited_stock=False,
        )
        setup.add(product)
        await setup.flush()
        users = [
            User(
                telegram_id=100000 + index,
                referral_code=f"LOAD{index:04d}",
                wallet_balance=10,
            )
            for index in range(500)
        ]
        setup.add_all(users)
        await setup.flush()
        user_ids = [user.id for user in users]
        product_id = product.id
        await setup.commit()

    async def checkout(user_id: str):
        async with factory() as session:
            uow = _uow(session)
            try:
                order_service = OrderService(uow)
                cart = await uow.carts.get_or_create(user_id)
                await uow.carts.add_item(
                    cart.id,
                    product_id=product_id,
                    quantity=1,
                )
                order = await order_service.create_order_from_cart(
                    user_id,
                    payment_method=PaymentMethod.BALANCE,
                )
                await WalletPaymentService(uow).pay_order_with_wallet(
                    user_id,
                    order.id,
                    10,
                )
                await order_service.approve_payment(order, users[0])
                await session.commit()
                return "ok"
            except Exception as error:
                await session.rollback()
                return f"{type(error).__name__}: {error}"

    async def admin_read():
        async with factory() as session:
            try:
                orders = OrderRepository(session)
                payments = PaymentRepository(session)
                revenue = await orders.get_revenue_stats()
                payment_stats = await payments.get_payment_stats()
                return revenue, payment_stats
            except Exception as error:
                return f"{type(error).__name__}: {error}"

    results = await asyncio.gather(
        *(checkout(user_id) for user_id in user_ids),
        *(admin_read() for _ in range(20)),
    )

    async with factory() as verify:
        user_balance = await verify.scalar(select(func.sum(User.wallet_balance)))
        remaining_stock = await verify.scalar(select(Product.stock).where(Product.id == product_id))
        orders = await verify.scalar(
            select(func.count()).select_from(Order).where(
                Order.status == OrderStatus.APPROVED
            )
        )
        payments = await verify.scalar(
            select(func.count()).select_from(Payment).where(
                Payment.status == PaymentStatus.APPROVED,
                Payment.method == PaymentMethod.BALANCE,
            )
        )
        transactions = await verify.scalar(
            select(func.count()).select_from(Transaction).where(
                Transaction.type == TransactionType.PURCHASE
            )
        )

    assert results[:500].count("ok") == 500, Counter(results[:500])
    assert all(not isinstance(result, str) or result != "OperationalError" for result in results)
    assert user_balance == 0
    assert remaining_stock == 0
    assert orders == payments == transactions == 500
    await engine.dispose()
