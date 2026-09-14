"""Discount code repository."""

from datetime import datetime
from typing import Sequence

from sqlalchemy import Select, func, select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models.discount_code import DiscountCode, DiscountType
from bot.repositories.base import BaseRepository


class DiscountCodeRepository(BaseRepository[DiscountCode]):
    """Discount code repository with specialized queries."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, DiscountCode)

    async def get_by_code(self, code: str) -> DiscountCode | None:
        """Get discount code by code string (case-insensitive)."""
        stmt = select(DiscountCode).where(
            func.upper(DiscountCode.code) == code.upper()
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def exists_by_code(self, code: str, exclude_id: str | None = None) -> bool:
        """Check if discount code exists (case-insensitive)."""
        stmt = select(func.count(DiscountCode.id)).where(
            func.upper(DiscountCode.code) == code.upper()
        )
        if exclude_id:
            stmt = stmt.where(DiscountCode.id != exclude_id)
        result = await self.session.execute(stmt)
        count = result.scalar_one()
        return count > 0

    async def get_active_codes(
        self,
        *,
        offset: int = 0,
        limit: int = 50,
    ) -> Sequence[DiscountCode]:
        """Get active discount codes."""
        stmt = (
            select(DiscountCode)
            .where(DiscountCode.is_active == True)
            .order_by(DiscountCode.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_all_codes(
        self,
        *,
        offset: int = 0,
        limit: int = 50,
        active_only: bool = False,
    ) -> Sequence[DiscountCode]:
        """Get all discount codes."""
        stmt = select(DiscountCode).order_by(DiscountCode.created_at.desc())
        if active_only:
            stmt = stmt.where(DiscountCode.is_active == True)
        stmt = stmt.offset(offset).limit(limit)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def count_all(self, active_only: bool = False) -> int:
        """Count all discount codes."""
        stmt = select(func.count(DiscountCode.id))
        if active_only:
            stmt = stmt.where(DiscountCode.is_active == True)
        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def increment_usage(self, code_id: str) -> DiscountCode | None:
        """Increment usage count for a discount code atomically (Bug #15 fixed)."""
        from sqlalchemy import update
        
        # Bug #15 - Atomic SQL UPDATE instead of read-modify-write
        await self.session.execute(
            update(DiscountCode)
            .where(DiscountCode.id == code_id)
            .values(usage_count=DiscountCode.usage_count + 1)
        )
        await self.session.flush()
        
        # Reload to get updated value
        code = await self.session.get(DiscountCode, code_id)
        if code:
            await self.session.refresh(code)
        return code

    async def validate_code(self, code: str, cart_total: int) -> tuple[bool, str, DiscountCode | None]:
        """Validate a discount code for use (Bug #17 - with row locking).
        
        Args:
            code: The discount code to validate
            cart_total: The cart total amount
            
        Returns:
            Tuple of (is_valid, error_message, discount_code_object)
        """
        # Bug #17 - Lock discount code row during validation to prevent race conditions
        stmt = select(DiscountCode).where(
            func.upper(DiscountCode.code) == code.upper()
        ).with_for_update()
        result = await self.session.execute(stmt)
        discount = result.scalar_one_or_none()
        
        if not discount:
            return False, "کد تخفیف یافت نشد", None
        
        if not discount.is_active:
            return False, "کد تخفیف غیرفعال است", None
        
        if discount.is_expired:
            return False, "کد تخفیف منقضی شده است", None
        
        if discount.is_exhausted:
            return False, "ظرفیت استفاده از این کد تخفیف تمام شده است", None
        
        # Check max eligible amount for percentage discounts
        if discount.discount_type == DiscountType.PERCENTAGE:
            if discount.max_eligible_amount is not None and cart_total > discount.max_eligible_amount:
                return False, (
                    f"مبلغ سبد خرید شما ({cart_total:,} تومان) بیشتر از حداکثر مبلغ مجاز "
                    f"({discount.max_eligible_amount:,} تومان) برای این کد تخفیف است"
                ), None
        
        return True, "", discount

    async def deactivate_code(self, code_id: str) -> DiscountCode | None:
        """Deactivate a discount code."""
        code = await self.session.get(DiscountCode, code_id)
        if code:
            code.is_active = False
            await self.session.flush()
            await self.session.refresh(code)
        return code

    async def activate_code(self, code_id: str) -> DiscountCode | None:
        """Activate a discount code."""
        code = await self.session.get(DiscountCode, code_id)
        if code:
            code.is_active = True
            await self.session.flush()
            await self.session.refresh(code)
        return code

    async def update_code(
        self,
        code_id: str,
        *,
        code: str | None = None,
        discount_type: DiscountType | None = None,
        discount_value: int | None = None,
        max_eligible_amount: int | None = None,
        expires_at: datetime | None = None,
        max_uses: int | None = None,
        is_active: bool | None = None,
        description: str | None = None,
    ) -> DiscountCode | None:
        """Update a discount code."""
        discount_code = await self.session.get(DiscountCode, code_id)
        if not discount_code:
            return None
        
        if code is not None:
            discount_code.code = code
        if discount_type is not None:
            discount_code.discount_type = discount_type
        if discount_value is not None:
            discount_code.discount_value = discount_value
        if max_eligible_amount is not None:
            discount_code.max_eligible_amount = max_eligible_amount
        if expires_at is not None:
            discount_code.expires_at = expires_at
        if max_uses is not None:
            discount_code.max_uses = max_uses
        if is_active is not None:
            discount_code.is_active = is_active
        if description is not None:
            discount_code.description = description
        
        await self.session.flush()
        await self.session.refresh(discount_code)
        return discount_code

    async def create_code(
        self,
        code: str,
        discount_type: DiscountType,
        discount_value: int,
        *,
        max_eligible_amount: int | None = None,
        expires_at: datetime | None = None,
        max_uses: int | None = None,
        is_active: bool = True,
        description: str | None = None,
    ) -> DiscountCode:
        """Create a new discount code."""
        discount_code = DiscountCode(
            code=code.upper(),  # Store in uppercase for consistency
            discount_type=discount_type,
            discount_value=discount_value,
            max_eligible_amount=max_eligible_amount,
            expires_at=expires_at,
            max_uses=max_uses,
            usage_count=0,
            is_active=is_active,
            description=description,
        )
        self.session.add(discount_code)
        await self.session.flush()
        await self.session.refresh(discount_code)
        return discount_code

    async def delete_code(self, code_id: str) -> bool:
        """Delete a discount code."""
        code = await self.session.get(DiscountCode, code_id)
        if code:
            await self.session.delete(code)
            await self.session.flush()
            return True
        return False

    async def get_expiring_soon(self, days: int = 7) -> Sequence[DiscountCode]:
        """Get discount codes expiring within specified days."""
        from datetime import timedelta, timezone
        
        now = datetime.now(timezone.utc)
        future = now + timedelta(days=days)
        
        stmt = (
            select(DiscountCode)
            .where(
                DiscountCode.is_active == True,
                DiscountCode.expires_at != None,
                DiscountCode.expires_at > now,
                DiscountCode.expires_at <= future,
            )
            .order_by(DiscountCode.expires_at)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_nearly_exhausted(self, threshold: int = 10) -> Sequence[DiscountCode]:
        """Get discount codes with remaining uses below threshold."""
        stmt = (
            select(DiscountCode)
            .where(
                DiscountCode.is_active == True,
                DiscountCode.max_uses != None,
                (DiscountCode.max_uses - DiscountCode.usage_count) <= threshold,
                (DiscountCode.max_uses - DiscountCode.usage_count) > 0,
            )
            .order_by(DiscountCode.max_uses - DiscountCode.usage_count)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()
