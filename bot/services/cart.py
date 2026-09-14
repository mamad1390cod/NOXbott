"""Cart service."""

from typing import Sequence

from bot.models.cart import Cart, CartItem
from bot.models.product import Product
from bot.models.config_shop import ConfigProduct
from bot.services.base import BaseService
from bot.database.uow import UnitOfWork

# Bug #6 - Cart limits to prevent performance issues
MAX_CART_ITEMS = 50
MAX_CART_TOTAL_QUANTITY = 100


class CartService(BaseService):
    """Cart service for shopping cart management."""

    def __init__(self, uow: UnitOfWork) -> None:
        super().__init__(uow)

    async def get_cart(self, user_id: str) -> Cart | None:
        """Get user's cart with items."""
        return await self.uow.carts.get_by_user_id(user_id)

    async def get_or_create_cart(self, user_id: str) -> Cart:
        """Get or create user's cart."""
        return await self.uow.carts.get_or_create(user_id)

    async def apply_discount_code(self, user_id: str, code: str) -> tuple[bool, str, int]:
        """Apply discount code to cart.
        
        Args:
            user_id: User ID
            code: Discount code to apply
            
        Returns:
            Tuple of (success, message, discount_amount)
        """
        from bot.services.discount_code import DiscountCodeService
        
        cart = await self.get_or_create_cart(user_id)
        cart_total = cart.total_price
        
        if cart_total == 0:
            return False, "سبد خرید خالی است", 0
        
        # Validate discount code
        discount_service = DiscountCodeService(self.uow)
        is_valid, error_msg, discount, discount_amount = await discount_service.validate_and_get_discount(
            code, cart_total
        )
        
        if not is_valid:
            return False, error_msg, 0
        
        # Apply discount to cart (without incrementing usage yet)
        cart.discount_code = discount.code
        cart.discount_amount = discount_amount
        await self.uow.flush()
        
        return True, f"✅ کد تخفیف اعمال شد! {discount_amount:,} تومان تخفیف", discount_amount

    async def remove_discount_code(self, user_id: str) -> bool:
        """Remove discount code from cart."""
        cart = await self.get_cart(user_id)
        if not cart:
            return False
        
        cart.discount_code = None
        cart.discount_amount = 0
        await self.uow.flush()
        return True

    async def add_product(
        self,
        user_id: str,
        product_id: str,
        quantity: int = 1,
        account_data: str | None = None,
    ) -> CartItem:
        """Add product to cart with atomic stock reservation (Bug #3 fixed)."""
        cart = await self.get_or_create_cart(user_id)

        # Bug #6 - Check cart limits
        total_items = len(cart.items)
        total_quantity = sum(item.quantity for item in cart.items)
        
        if total_items >= MAX_CART_ITEMS:
            raise ValueError(f"حداکثر {MAX_CART_ITEMS} نوع محصول در سبد خرید مجاز است")
        if total_quantity + quantity > MAX_CART_TOTAL_QUANTITY:
            raise ValueError(f"حداکثر {MAX_CART_TOTAL_QUANTITY} عدد محصول در سبد خرید مجاز است")

        # Verify product exists and is available
        product = await self.uow.products.get(product_id)
        if not product:
            raise ValueError("محصول یافت نشد")
        if not product.is_visible:
            raise ValueError("محصول در دسترس نیست")
        
        from bot.models.product import ProductStatus
        if product.status != ProductStatus.ACTIVE:
            raise ValueError("محصول غیرفعال است")
        
        # Bug #3 - Atomic stock check and reservation
        if not product.is_in_stock:
            raise ValueError("موجودی محصول تمام شده است")
        
        if not product.unlimited_stock:
            # Atomically reserve stock
            success = await self.uow.products.reserve_stock(product_id, quantity)
            if not success:
                raise ValueError("موجودی کافی نیست یا محصول تمام شده است")

        try:
            item = await self.uow.carts.add_item(
                cart_id=cart.id,
                product_id=product_id,
                quantity=quantity,
                account_data=account_data,
            )
            
            # Expire cart to force reload with new items
            await self.uow.session.refresh(cart, ["items"])
            
            return item
        except Exception:
            # Rollback stock reservation if cart add fails
            if not product.unlimited_stock:
                await self.uow.products.increase_stock(product_id, quantity)
            raise

    async def add_config(
        self,
        user_id: str,
        config_product_id: str,
        quantity: int = 1,
    ) -> CartItem:
        """Add config product to cart with atomic stock reservation (Bug #3 fixed)."""
        cart = await self.get_or_create_cart(user_id)

        # Bug #6 - Check cart limits
        total_items = len(cart.items)
        total_quantity = sum(item.quantity for item in cart.items)
        
        if total_items >= MAX_CART_ITEMS:
            raise ValueError(f"حداکثر {MAX_CART_ITEMS} نوع محصول در سبد خرید مجاز است")
        if total_quantity + quantity > MAX_CART_TOTAL_QUANTITY:
            raise ValueError(f"حداکثر {MAX_CART_TOTAL_QUANTITY} عدد محصول در سبد خرید مجاز است")

        # Verify config product exists
        config = await self.uow.config_products.get(config_product_id)
        if not config:
            raise ValueError("کانفیگ یافت نشد")
        if not config.is_visible:
            raise ValueError("کانفیگ در دسترس نیست")
        if not config.is_in_stock:
            raise ValueError("موجودی کانفیگ تمام شده است")

        # Bug #3 - Atomic stock reservation
        if not config.unlimited_stock:
            success = await self.uow.config_products.reserve_stock(config_product_id, quantity)
            if not success:
                raise ValueError("موجودی کافی نیست یا کانفیگ تمام شده است")

        try:
            item = await self.uow.carts.add_item(
                cart_id=cart.id,
                config_product_id=config_product_id,
                quantity=quantity,
            )
            
            # Expire cart to force reload with new items
            await self.uow.session.refresh(cart, ["items"])
            
            return item
        except Exception:
            # Rollback stock reservation if cart add fails
            if not config.unlimited_stock:
                from bot.repositories.config_shop import ConfigProductRepository
                # Increase stock back
                config.stock += quantity
                await self.uow.session.flush()
            raise

    async def update_quantity(self, user_id: str, item_id: str, quantity: int) -> CartItem | None:
        """Update item quantity in cart."""
        cart = await self.get_cart(user_id)
        if not cart:
            raise ValueError("سبد خرید یافت نشد")

        item = await self.uow.carts.get_item(item_id)
        if not item or item.cart_id != cart.id:
            raise ValueError("آیتم در سبد خرید یافت نشد")

        # Check stock for products
        if item.product_id:
            product = await self.uow.products.get(item.product_id)
            if product and not product.unlimited_stock and product.stock < quantity:
                raise ValueError("موجودی کافی نیست")
        elif item.config_product_id:
            config = await self.uow.config_products.get(item.config_product_id)
            if config and not config.unlimited_stock and config.stock < quantity:
                raise ValueError("موجودی کافی نیست")

        return await self.uow.carts.update_quantity(item_id, quantity)

    async def remove_item(self, user_id: str, item_id: str) -> bool:
        """Remove item from cart."""
        cart = await self.get_cart(user_id)
        if not cart:
            raise ValueError("سبد خرید یافت نشد")

        item = await self.uow.carts.get_item(item_id)
        if not item or item.cart_id != cart.id:
            raise ValueError("آیتم در سبد خرید یافت نشد")

        return await self.uow.carts.remove_item(item_id)

    async def clear_cart(self, user_id: str) -> int:
        """Clear all items from cart."""
        cart = await self.get_cart(user_id)
        if not cart:
            return 0
        # Also clear discount code when clearing cart
        cart.discount_code = None
        cart.discount_amount = 0
        await self.uow.flush()
        return await self.uow.carts.clear_cart(cart.id)

    async def get_cart_items(self, user_id: str) -> Sequence[CartItem]:
        """Get all cart items with products loaded."""
        cart = await self.get_cart(user_id)
        if not cart:
            return []
        return await self.uow.carts.get_cart_items(cart.id)

    async def get_cart_summary(self, user_id: str) -> dict:
        """Get cart summary with totals."""
        cart = await self.get_cart(user_id)
        if not cart:
            return {
                "items": [],
                "total_items": 0,
                "total_price": 0,
                "subtotal": 0,
                "discount_code": None,
                "discount_amount": 0,
                "final_total": 0,
                "products_count": 0,
                "configs_count": 0,
            }

        items = await self.uow.carts.get_cart_items(cart.id)

        products_count = sum(1 for item in items if item.product_id)
        configs_count = sum(1 for item in items if item.config_product_id)

        return {
            "items": items,
            "total_items": cart.total_items,
            "total_price": cart.total_price,
            "subtotal": cart.subtotal,
            "discount_code": cart.discount_code,
            "discount_amount": cart.discount_amount,
            "final_total": cart.final_total,
            "products_count": products_count,
            "configs_count": configs_count,
        }

    async def prepare_order_items(self, user_id: str) -> list[dict]:
        """Prepare cart items for order creation."""
        cart = await self.get_cart(user_id)
        if not cart:
            return []

        items = await self.uow.carts.get_cart_items(cart.id)
        order_items = []

        for item in items:
            if item.product_id:
                product = item.product
                if not product:
                    continue
                order_items.append({
                    "product_id": product.id,
                    "config_product_id": None,
                    "quantity": item.quantity,
                    "unit_price": product.discounted_price,
                    "total_price": product.discounted_price * item.quantity,
                    "title": product.title,
                    "product_type": "product",
                })
            elif item.config_product_id:
                config = item.config_product
                if not config:
                    continue
                order_items.append({
                    "product_id": None,
                    "config_product_id": config.id,
                    "quantity": item.quantity,
                    "unit_price": config.price,
                    "total_price": config.price * item.quantity,
                    "title": config.title,
                    "product_type": "config",
                })

        return order_items