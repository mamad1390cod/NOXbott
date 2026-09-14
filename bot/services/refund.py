"""Refund service - handles wallet credit on order refund."""

import logging
from datetime import datetime, timezone

from sqlalchemy import select, update

from bot.database.uow import UnitOfWork
from bot.models.order import Order, OrderStatus
from bot.models.payment import PaymentStatus
from bot.models.user import User
from bot.models.user_dashboard import TransactionType

logger = logging.getLogger(__name__)


class RefundError(Exception):
    """Base refund error."""
    pass


class AlreadyRefundedError(RefundError):
    """Order already refunded."""
    pass


class NotPaidError(RefundError):
    """Order not paid."""
    pass


class RefundService:
    """Service for processing order refunds with wallet credit."""

    def __init__(self, uow: UnitOfWork):
        self.uow = uow

    async def refund_order(self, order: Order, admin: User, reason: str | None = None) -> Order:
        """
        Refund an order: credit wallet + change status + create transaction.
        
        Idempotent: if already refunded, raises AlreadyRefundedError.
        Atomic: all changes in one transaction.
        """
        if order.status == OrderStatus.REFUNDED:
            raise AlreadyRefundedError(
                f"سفارش {order.order_number} قبلاً بازپرداخت شده است."
            )

        # Check if order is paid
        if not order.is_paid:
            raise NotPaidError("فقط سفارش پرداخت‌شده قابل بازگشت وجه است.")

        result = await self.uow.session.execute(
            update(Order)
            .where(
                Order.id == order.id,
                Order.status.in_(
                    (
                        OrderStatus.APPROVED,
                        OrderStatus.PREPARING,
                        OrderStatus.DELIVERED,
                        OrderStatus.COMPLETED,
                    )
                ),
            )
            .values(
                status=OrderStatus.REFUNDED,
                refunded_at=datetime.now(timezone.utc),
                refund_reason=reason or "بازگشت وجه",
            )
        )
        if result.rowcount != 1:
            raise AlreadyRefundedError(f"سفارش {order.order_number} قبلاً بازپرداخت شده است.")
        order = await self.uow.orders.get_with_items(order.id)
        if order is None:
            raise RefundError("سفارش یافت نشد")
        
        # Lock user row for atomic balance update
        stmt = (
            select(User)
            .where(User.id == order.user_id)
            .with_for_update(skip_locked=False)
        )
        result = await self.uow.session.execute(stmt)
        user = result.scalar_one_or_none()
        
        if not user:
            raise RefundError(f"کاربر سفارش یافت نشد: {order.user_id}")
        
        # Calculate refund amount
        refund_amount = order.final_amount
        
        # Credit wallet
        balance_before = user.wallet_balance or 0
        balance_after = balance_before + refund_amount
        user.wallet_balance = balance_after
        
        # Create refund transaction
        txn = await self.uow.transactions.add(
            user_id=user.id,
            type_=TransactionType.REFUND,
            amount=refund_amount,
            balance_before=balance_before,
            balance_after=balance_after,
            ref_id=order.id,
            note=f"بازگشت وجه سفارش {order.order_number}",
            admin_id=admin.id,
        )
        
        # Update payment status
        if order.payments:
            for payment in order.payments:
                if payment.status == PaymentStatus.APPROVED:
                    payment.status = PaymentStatus.REJECTED
                    payment.notes = f"Refunded: {reason or 'بازگشت وجه'}"
        
        # Update order status
        # Restore stock
        await self.uow.orders.restore_items_stock(order)
        
        await self.uow.flush()
        
        logger.info(
            "refund_completed: order=%s user=%s amount=%d balance=%d->%d txn=%s admin=%s",
            order.order_number, user.telegram_id, refund_amount,
            balance_before, balance_after, txn.id, admin.telegram_id,
        )
        
        return order
