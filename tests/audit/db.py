"""Database helpers for the audit suite (seeding / querying / integrity checks)."""

from __future__ import annotations

import itertools
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.session import get_session_factory
from bot.models.category import Category
from bot.models.config_shop import ConfigProduct
from bot.models.discount_code import DiscountCode, DiscountType
from bot.models.order import Order, OrderItem, OrderStatus
from bot.models.payment import Payment, PaymentStatus
from bot.models.product import Product, ProductStatus
from bot.models.user import User, UserRole
from bot.models.user_dashboard import Transaction

_seq = itertools.count(1)


def uid(prefix: str = "x") -> str:
    return f"{prefix}-{next(_seq):06d}"


def next_telegram_id() -> int:
    return 7_000_000_000 + next(_seq) * 13


# --------------------------------------------------------------------------- #
#  Seeding
# --------------------------------------------------------------------------- #


async def make_user(
    session: AsyncSession,
    *,
    telegram_id: int | None = None,
    balance: int = 0,
    is_banned: bool = False,
    role: UserRole = UserRole.USER,
    username: str | None = "audit_user",
) -> User:
    user = User(
        telegram_id=telegram_id or next_telegram_id(),
        username=username,
        first_name="Audit",
        last_name="User",
        role=role,
        is_banned=is_banned,
        wallet_balance=balance,
        referral_code=f"REF{next(_seq):08d}",
    )
    session.add(user)
    await session.flush()
    return user


async def make_category(session: AsyncSession, name: str | None = None) -> Category:
    cat = Category(name=name or f"cat{next(_seq)}", type="product", is_visible=True, is_active=True)
    session.add(cat)
    await session.flush()
    return cat


async def make_product(
    session: AsyncSession,
    *,
    price: int = 10_000,
    stock: int = 10,
    unlimited: bool = False,
    status: ProductStatus = ProductStatus.ACTIVE,
    visible: bool = True,
    category_id: str | None = None,
    title: str | None = None,
    requires_account_info: bool = False,
    ptype: str = "digital",
) -> Product:
    product = Product(
        title=title or f"Product {next(_seq)}",
        description="audit product",
        price=price,
        stock=stock,
        unlimited_stock=unlimited,
        status=status,
        is_visible=visible,
        category_id=category_id,
        type=ptype,
        requires_account_info=requires_account_info,
    )
    session.add(product)
    await session.flush()
    return product


async def make_config(
    session: AsyncSession,
    *,
    price: int = 20_000,
    stock: int = 5,
    unlimited: bool = False,
    visible: bool = True,
    category_id: str | None = None,
) -> ConfigProduct:
    cfg = ConfigProduct(
        title=f"Config {next(_seq)}",
        price=price,
        stock=stock,
        unlimited_stock=unlimited,
        is_visible=visible,
        category_id=category_id,
    )
    session.add(cfg)
    await session.flush()
    return cfg


async def make_coupon(
    session: AsyncSession,
    *,
    code: str | None = None,
    dtype: DiscountType = DiscountType.PERCENTAGE,
    value: int = 10,
    max_uses: int | None = None,
    expires_at: datetime | None = None,
    is_active: bool = True,
    max_eligible_amount: int | None = None,
) -> DiscountCode:
    coupon = DiscountCode(
        code=(code or f"CODE{next(_seq)}").upper(),
        discount_type=dtype,
        discount_value=value,
        max_uses=max_uses,
        expires_at=expires_at,
        is_active=is_active,
        max_eligible_amount=max_eligible_amount,
        usage_count=0,
    )
    session.add(coupon)
    await session.flush()
    return coupon


# --------------------------------------------------------------------------- #
#  Querying
# --------------------------------------------------------------------------- #


async def get_user(session: AsyncSession, user_id: str) -> User | None:
    return await session.get(User, user_id)


async def get_balance(session: AsyncSession, user_id: str) -> int:
    session.expire_all()
    user = await session.get(User, user_id)
    return user.wallet_balance if user else -1


async def get_product_stock(session: AsyncSession, product_id: str) -> int | None:
    session.expire_all()
    product = await session.get(Product, product_id)
    return None if product is None else product.stock


async def count_rows(session: AsyncSession, model: Any) -> int:
    return (await session.execute(select(func.count()).select_from(model))).scalar_one()


async def all_order_statuses(session: AsyncSession, user_id: str) -> list[OrderStatus]:
    rows = (
        await session.execute(
            select(Order.status).where(Order.user_id == user_id).order_by(Order.created_at)
        )
    ).scalars().all()
    return list(rows)


async def user_transactions(session: AsyncSession, user_id: str) -> list[Transaction]:
    rows = (
        await session.execute(
            select(Transaction).where(Transaction.user_id == user_id).order_by(Transaction.created_at)
        )
    ).scalars().all()
    return list(rows)


async def order_items_of(session: AsyncSession, order_id: str) -> list[OrderItem]:
    rows = (await session.execute(select(OrderItem).where(OrderItem.order_id == order_id))).scalars().all()
    return list(rows)


async def payments_of_order(session: AsyncSession, order_id: str) -> list[Payment]:
    rows = (await session.execute(select(Payment).where(Payment.order_id == order_id))).scalars().all()
    return list(rows)


async def integrity_report(session: AsyncSession) -> dict[str, list[Any]]:
    """Collect DB integrity violations that must always be empty."""
    problems: dict[str, list[Any]] = {}
    checks = {
        "negative_wallets": "SELECT id, telegram_id, wallet_balance FROM users WHERE wallet_balance < 0",
        "negative_stock": (
            "SELECT id, title, stock FROM products WHERE stock < 0 AND unlimited_stock = 0"
        ),
        "negative_config_stock": (
            "SELECT id, title, stock FROM config_products WHERE stock < 0 AND unlimited_stock = 0"
        ),
        "bad_order_amounts": (
            "SELECT id, total_amount, discount_amount, final_amount FROM orders "
            "WHERE final_amount != total_amount - discount_amount OR final_amount < 0"
        ),
        "orphan_cart_items": (
            "SELECT ci.id FROM cart_items ci LEFT JOIN carts c ON ci.cart_id = c.id WHERE c.id IS NULL"
        ),
        "orphan_order_items": (
            "SELECT oi.id FROM order_items oi LEFT JOIN orders o ON oi.order_id = o.id "
            "WHERE o.id IS NULL"
        ),
        "orphan_payments": (
            "SELECT p.id FROM payments p LEFT JOIN users u ON p.user_id = u.id WHERE u.id IS NULL"
        ),
        "duplicate_ledger_refs": (
            "SELECT ref_id, COUNT(*) c FROM transactions WHERE ref_id IS NOT NULL "
            "GROUP BY ref_id HAVING c > 1"
        ),
        "wrong_ledger_balance": (
            "SELECT id, balance_before, amount, balance_after FROM transactions "
            "WHERE balance_before + amount != balance_after"
        ),
    }
    for key, sql in checks.items():
        rows = (await session.execute(text(sql))).fetchall()
        if rows:
            problems[key] = [tuple(r) for r in rows]
    return problems


async def session_factory():
    return get_session_factory()
