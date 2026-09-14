"""Admin service — statistics, dashboard, logs."""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from bot.database.uow import UnitOfWork
from bot.models.log import AdminLog, LogAction
from bot.models.order import Order, OrderStatus
from bot.models.topup import TopUpRequest, TopUpStatus
from bot.models.user import User, UserRole
from bot.services.base import BaseService


class AdminService(BaseService):
    """Admin service for dashboard and management."""

    def __init__(self, uow: UnitOfWork) -> None:
        super().__init__(uow)

    async def get_dashboard_stats(self) -> dict:
        """Collect all dashboard statistics."""
        from bot.services.user import UserService

        user_stats = await UserService(self.uow).get_stats()
        revenue_stats = await self.uow.orders.get_revenue_stats()
        payment_stats = await self.uow.payments.get_payment_stats()

        # Counts
        products_count = await self.uow.products.count_for_admin()
        configs_count = await self.uow.config_products.count_for_admin()
        customs_count = await self.uow.customs.count_for_admin()
        tickets_open = await self.uow.tickets.count_open_tickets()

        pending_orders = await self.uow.orders.count_for_admin(
            status=OrderStatus.PENDING
        )

        return {
            "users": user_stats,
            "revenue": revenue_stats,
            "payments": payment_stats,
            "products_count": products_count,
            "configs_count": configs_count,
            "customs_count": customs_count,
            "tickets_open": tickets_open,
            "pending_orders": pending_orders,
        }

    async def log_action(
        self,
        admin: User,
        action: LogAction,
        target_type: str | None = None,
        target_id: str | None = None,
        description: str | None = None,
        old_data: str | None = None,
        new_data: str | None = None,
        session_id: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> AdminLog:
        """Log an admin action with full audit context (before/after/session)."""
        return await self.uow.admin_logs.log_action(
            admin_id=admin.id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            description=description,
            old_data=old_data,
            new_data=new_data,
            ip_address=ip_address,
            user_agent=user_agent,
            session_id=session_id,
        )

    async def get_logs(
        self,
        *,
        offset: int = 0,
        limit: int = 50,
        action: LogAction | None = None,
    ) -> Sequence[AdminLog]:
        return await self.uow.admin_logs.get_logs(
            offset=offset, limit=limit, action=action
        )

    async def count_logs(self) -> int:
        return await self.uow.admin_logs.count_logs()

    async def get_admin_users(self) -> Sequence[User]:
        return await self.uow.users.get_admins()

    async def add_admin(self, telegram_id: int) -> User | None:
        """Add a new admin by telegram ID."""
        user = await self.uow.users.get_by_telegram_id(telegram_id)
        if not user:
            return None
        return await self.uow.users.update(user.id, role=UserRole.ADMIN)

    async def remove_admin(self, telegram_id: int) -> User | None:
        user = await self.uow.users.get_by_telegram_id(telegram_id)
        if not user:
            return None
        return await self.uow.users.update(user.id, role=UserRole.USER)

    async def get_total_users(self) -> int:
        return await self.uow.users.get_total_users()

    async def get_cleanup_counts(self) -> dict[str, int]:
        """Return only the records eligible for the admin cleanup operation."""
        return {
            "completed_orders": await self.uow.orders.count(
                status=OrderStatus.COMPLETED
            ),
            "approved_topups": await self.uow.topup_requests.count(
                status=TopUpStatus.APPROVED
            ),
        }

    async def delete_completed_orders(self) -> int:
        """Delete completed orders and their dependent records.

        Financial ledger transactions are intentionally retained.  This resets
        order history/report rows without changing user balances or accounting.
        """
        stmt = (
            select(Order)
            .where(Order.status == OrderStatus.COMPLETED)
            .options(
                selectinload(Order.items),
                selectinload(Order.payments),
                selectinload(Order.status_events),
                selectinload(Order.delivery),
            )
        )
        result = await self.uow.session.execute(stmt)
        orders = result.scalars().unique().all()
        for order in orders:
            await self.uow.session.delete(order)
        await self.uow.flush()
        return len(orders)

    async def delete_approved_topups(self) -> int:
        """Delete approved top-up requests while retaining wallet transactions."""
        stmt = (
            select(TopUpRequest)
            .where(TopUpRequest.status == TopUpStatus.APPROVED)
            .options(selectinload(TopUpRequest.receipts))
        )
        result = await self.uow.session.execute(stmt)
        requests = result.scalars().unique().all()
        for request in requests:
            await self.uow.session.delete(request)
        await self.uow.flush()
        return len(requests)
