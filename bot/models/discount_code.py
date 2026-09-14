"""Discount code model."""

import enum
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from bot.models.base import Base, TimestampMixin, UUIDMixin


class DiscountType(str, enum.Enum):
    """Discount type."""

    PERCENTAGE = "percentage"
    FIXED = "fixed"


class DiscountCode(Base, UUIDMixin, TimestampMixin):
    """Discount code model for promotional offers."""

    __tablename__ = "discount_codes"

    # Unique discount code (e.g., "SUMMER2024", "SAVE20")
    code: Mapped[str] = mapped_column(
        String(50),
        unique=True,
        nullable=False,
        index=True,
    )

    # Discount type: percentage or fixed amount
    discount_type: Mapped[DiscountType] = mapped_column(
        Enum(DiscountType),
        nullable=False,
    )

    # Discount value (percentage: 10 = 10%, fixed: amount in Toman)
    discount_value: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    # Maximum eligible cart amount for percentage discounts
    # (e.g., max_eligible_amount=100000 with 10% discount means max 10000 discount)
    # NULL means no limit
    max_eligible_amount: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )

    # Expiration date/time (NULL = never expires)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Maximum number of uses (NULL = unlimited)
    max_uses: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    # Current usage count
    usage_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    # Active/inactive status
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
        index=True,
    )

    # Optional description
    description: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    __table_args__ = (
        UniqueConstraint("code", name="uq_discount_codes_code"),
    )

    @property
    def is_expired(self) -> bool:
        """Check if discount code is expired."""
        if self.expires_at is None:
            return False
        return datetime.now(self.expires_at.tzinfo) > self.expires_at

    @property
    def is_exhausted(self) -> bool:
        """Check if discount code has reached maximum uses."""
        if self.max_uses is None:
            return False
        return self.usage_count >= self.max_uses

    @property
    def is_valid(self) -> bool:
        """Check if discount code is valid (active, not expired, not exhausted)."""
        return self.is_active and not self.is_expired and not self.is_exhausted

    @property
    def remaining_uses(self) -> int | None:
        """Get remaining uses (None = unlimited)."""
        if self.max_uses is None:
            return None
        return max(0, self.max_uses - self.usage_count)

    def calculate_discount(self, cart_total: int) -> int:
        """Calculate discount amount for given cart total.
        
        Args:
            cart_total: Total cart amount in Toman
            
        Returns:
            Discount amount in Toman
            
        Raises:
            ValueError: If cart total exceeds max eligible amount for percentage discounts
        """
        if self.discount_type == DiscountType.PERCENTAGE:
            # Check max eligible amount for percentage discounts
            if self.max_eligible_amount is not None and cart_total > self.max_eligible_amount:
                raise ValueError(
                    f"مبلغ سبد خرید ({cart_total:,} تومان) بیشتر از حداکثر مبلغ مجاز "
                    f"({self.max_eligible_amount:,} تومان) برای این کد تخفیف است"
                )
            # Calculate percentage discount
            return int((cart_total * self.discount_value) / 100)
        else:  # FIXED
            # Fixed amount discount (cannot exceed cart total)
            return min(self.discount_value, cart_total)

    def __repr__(self) -> str:
        return f"<DiscountCode(code={self.code}, type={self.discount_type}, value={self.discount_value}, active={self.is_active})>"
