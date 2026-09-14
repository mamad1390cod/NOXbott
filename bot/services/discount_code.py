"""Discount code service."""

from datetime import datetime
from typing import Sequence

from bot.database.uow import UnitOfWork
from bot.models.discount_code import DiscountCode, DiscountType
from bot.services.base import BaseService


class DiscountCodeService(BaseService):
    """Discount code service for managing promotional codes."""

    def __init__(self, uow: UnitOfWork) -> None:
        super().__init__(uow)

    async def create_discount_code(
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
        """Create a new discount code.
        
        Args:
            code: Unique discount code string
            discount_type: Type of discount (percentage or fixed)
            discount_value: Discount value (percentage: 10 = 10%, fixed: amount in Toman)
            max_eligible_amount: Maximum cart amount for percentage discounts (optional)
            expires_at: Expiration datetime (optional, None = never expires)
            max_uses: Maximum usage count (optional, None = unlimited)
            is_active: Initial active status
            description: Optional description
            
        Returns:
            Created DiscountCode object
            
        Raises:
            ValueError: If validation fails
        """
        # Validate code format
        code = code.strip().upper()
        if not code:
            raise ValueError("کد تخفیف نمی‌تواند خالی باشد")
        if len(code) < 3:
            raise ValueError("کد تخفیف باید حداقل ۳ کاراکتر باشد")
        if len(code) > 50:
            raise ValueError("کد تخفیف نباید بیش از ۵۰ کاراکتر باشد")
        
        # Check if code already exists
        if await self.uow.discount_codes.exists_by_code(code):
            raise ValueError(f"کد تخفیف '{code}' قبلاً ثبت شده است")
        
        # Validate discount value
        if discount_value <= 0:
            raise ValueError("مقدار تخفیف باید بیشتر از صفر باشد")
        
        if discount_type == DiscountType.PERCENTAGE:
            if discount_value > 100:
                raise ValueError("درصد تخفیف نمی‌تواند بیشتر از ۱۰۰ باشد")
            # For percentage discounts, max_eligible_amount is allowed
        else:  # FIXED
            if discount_value > 100_000_000:  # 100 million Toman max
                raise ValueError("مبلغ تخفیف خیلی زیاد است")
            # For fixed discounts, max_eligible_amount should not be set
            if max_eligible_amount is not None:
                raise ValueError("حداکثر مبلغ مجاز فقط برای تخفیف درصدی قابل تنظیم است")
        
        # Validate max_eligible_amount
        if max_eligible_amount is not None:
            if max_eligible_amount <= 0:
                raise ValueError("حداکثر مبلغ مجاز باید بیشتر از صفر باشد")
        
        # Validate max_uses
        if max_uses is not None:
            if max_uses <= 0:
                raise ValueError("حداکثر تعداد استفاده باید بیشتر از صفر باشد")
        
        # Validate expiration
        if expires_at is not None:
            now = datetime.now(expires_at.tzinfo)
            if expires_at <= now:
                raise ValueError("تاریخ انقضا باید در آینده باشد")
        
        # Create discount code
        discount_code = await self.uow.discount_codes.create_code(
            code=code,
            discount_type=discount_type,
            discount_value=discount_value,
            max_eligible_amount=max_eligible_amount,
            expires_at=expires_at,
            max_uses=max_uses,
            is_active=is_active,
            description=description,
        )
        await self.uow.flush()
        return discount_code

    async def get_discount_code(self, code_id: str) -> DiscountCode | None:
        """Get discount code by ID."""
        return await self.uow.discount_codes.get(code_id)

    async def get_discount_code_by_code(self, code: str) -> DiscountCode | None:
        """Get discount code by code string."""
        return await self.uow.discount_codes.get_by_code(code)

    async def validate_and_get_discount(
        self, code: str, cart_total: int
    ) -> tuple[bool, str, DiscountCode | None, int]:
        """Validate discount code and calculate discount amount.
        
        Args:
            code: Discount code string
            cart_total: Cart total amount
            
        Returns:
            Tuple of (is_valid, error_message, discount_code_object, discount_amount)
        """
        # Validate via repository
        is_valid, error_msg, discount = await self.uow.discount_codes.validate_code(
            code, cart_total
        )
        
        if not is_valid:
            return False, error_msg, None, 0
        
        # Calculate discount amount
        try:
            discount_amount = discount.calculate_discount(cart_total)
        except ValueError as e:
            return False, str(e), None, 0
        
        return True, "", discount, discount_amount

    async def apply_discount_code(
        self, code: str, cart_total: int
    ) -> tuple[bool, str, int, DiscountCode | None]:
        """Apply discount code and increment usage count.
        
        This method should be called when actually creating an order, not just
        for validation in the cart view.
        
        Args:
            code: Discount code string
            cart_total: Cart total amount
            
        Returns:
            Tuple of (success, message, discount_amount, discount_code_object)
        """
        is_valid, error_msg, discount, discount_amount = await self.validate_and_get_discount(
            code, cart_total
        )
        
        if not is_valid:
            return False, error_msg, 0, None
        
        # Increment usage count
        await self.uow.discount_codes.increment_usage(discount.id)
        await self.uow.flush()
        
        return True, "کد تخفیف با موفقیت اعمال شد", discount_amount, discount

    async def update_discount_code(
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
        """Update an existing discount code.
        
        Raises:
            ValueError: If validation fails
        """
        discount_code = await self.uow.discount_codes.get(code_id)
        if not discount_code:
            raise ValueError("کد تخفیف یافت نشد")
        
        # Validate new code if provided
        if code is not None:
            code = code.strip().upper()
            if not code:
                raise ValueError("کد تخفیف نمی‌تواند خالی باشد")
            if len(code) < 3:
                raise ValueError("کد تخفیف باید حداقل ۳ کاراکتر باشد")
            if len(code) > 50:
                raise ValueError("کد تخفیف نباید بیش از ۵۰ کاراکتر باشد")
            
            # Check if new code already exists (excluding current code)
            if await self.uow.discount_codes.exists_by_code(code, exclude_id=code_id):
                raise ValueError(f"کد تخفیف '{code}' قبلاً ثبت شده است")
        
        # Validate discount value if provided
        if discount_value is not None:
            if discount_value <= 0:
                raise ValueError("مقدار تخفیف باید بیشتر از صفر باشد")
            
            check_type = discount_type or discount_code.discount_type
            if check_type == DiscountType.PERCENTAGE:
                if discount_value > 100:
                    raise ValueError("درصد تخفیف نمی‌تواند بیشتر از ۱۰۰ باشد")
            else:  # FIXED
                if discount_value > 100_000_000:
                    raise ValueError("مبلغ تخفیف خیلی زیاد است")
        
        # Validate max_eligible_amount if provided
        if max_eligible_amount is not None:
            check_type = discount_type or discount_code.discount_type
            if check_type == DiscountType.FIXED:
                raise ValueError("حداکثر مبلغ مجاز فقط برای تخفیف درصدی قابل تنظیم است")
            if max_eligible_amount <= 0:
                raise ValueError("حداکثر مبلغ مجاز باید بیشتر از صفر باشد")
        
        # Validate max_uses if provided
        if max_uses is not None:
            if max_uses <= 0:
                raise ValueError("حداکثر تعداد استفاده باید بیشتر از صفر باشد")
            if max_uses < discount_code.usage_count:
                raise ValueError(
                    f"حداکثر تعداد استفاده نمی‌تواند کمتر از تعداد استفاده فعلی "
                    f"({discount_code.usage_count}) باشد"
                )
        
        # Validate expiration if provided
        if expires_at is not None:
            now = datetime.now(expires_at.tzinfo)
            if expires_at <= now:
                raise ValueError("تاریخ انقضا باید در آینده باشد")
        
        # Update discount code
        updated = await self.uow.discount_codes.update_code(
            code_id,
            code=code,
            discount_type=discount_type,
            discount_value=discount_value,
            max_eligible_amount=max_eligible_amount,
            expires_at=expires_at,
            max_uses=max_uses,
            is_active=is_active,
            description=description,
        )
        await self.uow.flush()
        return updated

    async def activate_discount_code(self, code_id: str) -> DiscountCode | None:
        """Activate a discount code."""
        discount_code = await self.uow.discount_codes.activate_code(code_id)
        await self.uow.flush()
        return discount_code

    async def deactivate_discount_code(self, code_id: str) -> DiscountCode | None:
        """Deactivate a discount code."""
        discount_code = await self.uow.discount_codes.deactivate_code(code_id)
        await self.uow.flush()
        return discount_code

    async def delete_discount_code(self, code_id: str) -> bool:
        """Delete a discount code.
        
        Note: Only codes with zero usage should be deleted to maintain order history.
        """
        discount_code = await self.uow.discount_codes.get(code_id)
        if not discount_code:
            return False
        
        if discount_code.usage_count > 0:
            raise ValueError(
                f"نمی‌توان کد تخفیف با {discount_code.usage_count} استفاده را حذف کرد. "
                "لطفاً آن را غیرفعال کنید."
            )
        
        result = await self.uow.discount_codes.delete_code(code_id)
        await self.uow.flush()
        return result

    async def list_discount_codes(
        self,
        *,
        offset: int = 0,
        limit: int = 50,
        active_only: bool = False,
    ) -> Sequence[DiscountCode]:
        """List all discount codes."""
        return await self.uow.discount_codes.get_all_codes(
            offset=offset, limit=limit, active_only=active_only
        )

    async def count_discount_codes(self, active_only: bool = False) -> int:
        """Count discount codes."""
        return await self.uow.discount_codes.count_all(active_only=active_only)

    async def get_expiring_soon(self, days: int = 7) -> Sequence[DiscountCode]:
        """Get discount codes expiring within specified days."""
        return await self.uow.discount_codes.get_expiring_soon(days)

    async def get_nearly_exhausted(self, threshold: int = 10) -> Sequence[DiscountCode]:
        """Get discount codes with remaining uses below threshold."""
        return await self.uow.discount_codes.get_nearly_exhausted(threshold)

    async def get_statistics(self) -> dict:
        """Get discount code statistics."""
        total = await self.uow.discount_codes.count_all(active_only=False)
        active = await self.uow.discount_codes.count_all(active_only=True)
        expiring = len(await self.uow.discount_codes.get_expiring_soon(7))
        exhausted = len(await self.uow.discount_codes.get_nearly_exhausted(5))
        
        return {
            "total": total,
            "active": active,
            "inactive": total - active,
            "expiring_soon": expiring,
            "nearly_exhausted": exhausted,
        }
