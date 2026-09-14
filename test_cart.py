"""
Comprehensive Cart & Purchase Testing

Tests:
1. Add/remove/update cart items
2. Empty cart handling
3. Multiple products
4. Quantity boundaries  
5. Price consistency
6. Stock validation
7. Discount code application
8. Cart clearing
9. Concurrent operations simulation
"""

import asyncio
import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).parent))

from bot.database import session_scope, init_db
from bot.services.cart import CartService
from bot.services.discount_code import DiscountCodeService
from bot.models.user import User, UserRole
from bot.models.product import Product, ProductType, ProductStatus
from bot.models.category import Category
from bot.models.discount_code import DiscountCode, DiscountType
from bot.database.uow import UnitOfWork
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, text

print("="*80)
print("CART & PURCHASE TESTING")
print("="*80)

results = {"pass": 0, "fail": 0, "bugs": []}

def test(name, condition, error=""):
    if condition:
        results["pass"] += 1
        print(f"✅ PASS: {name}")
        return True
    else:
        results["fail"] += 1
        results["bugs"].append({"test": name, "error": error})
        print(f"❌ FAIL: {name} - {error}")
        return False

async def setup_test_data():
    """Create test user, category, and products"""
    uow = UnitOfWork()
    async with uow:
        # Create test user
        user = User(
            telegram_id=888999000,
            username="test_cart_user",
            role=UserRole.USER,
            referral_code=f"CART{uuid4().hex[:6]}",
            wallet_balance=100000  # 100k balance
        )
        uow.session.add(user)
        await uow.flush()
        
        # Create category
        category = Category(name="Test Category", type="product")
        uow.session.add(category)
        await uow.flush()
        
        # Create products
        products = []
        for i in range(3):
            product = Product(
                title=f"Test Product {i+1}",
                price=10000 * (i+1),
                stock=10,
                unlimited_stock=False,
                status=ProductStatus.ACTIVE,
                is_visible=True,
                category_id=category.id,
                type=ProductType.DIGITAL
            )
            uow.session.add(product)
            products.append(product)
        
        await uow.flush()
        
        # Create unlimited stock product
        unlimited_product = Product(
            title="Unlimited Product",
            price=5000,
            stock=0,
            unlimited_stock=True,
            status=ProductStatus.ACTIVE,
            is_visible=True,
            category_id=category.id,
            type=ProductType.DIGITAL
        )
        uow.session.add(unlimited_product)
        await uow.flush()
        
        # Create discount code
        discount = DiscountCode(
            code="TEST10",
            discount_type=DiscountType.PERCENTAGE,
            discount_value=10,
            is_active=True,
            max_uses=10,
            usage_count=0,
            expires_at=datetime.now(timezone.utc) + timedelta(days=30)
        )
        uow.session.add(discount)
        
        await uow.commit()
        
        return user.id, [p.id for p in products], unlimited_product.id, category.id


async def cleanup_test_data():
    """Clean up test data"""
    async with session_scope() as session:
        await session.execute(text("DELETE FROM users WHERE telegram_id = 888999000"))
        await session.execute(text("DELETE FROM discount_codes WHERE code = 'TEST10'"))
        await session.commit()


async def test_add_to_cart():
    """Test adding items to cart"""
    print("\n--- TEST: Add to Cart ---")
    user_id, product_ids, unlimited_id, cat_id = await setup_test_data()
    
    try:
        uow = UnitOfWork()
        async with uow:
            cart_service = CartService(uow)
            
            # Test 1: Add first product
            item1 = await cart_service.add_product(user_id, product_ids[0], quantity=2)
            test("add_first_product", item1 is not None and item1.quantity == 2)
            
            # Test 2: Add same product again (should increment)
            item2 = await cart_service.add_product(user_id, product_ids[0], quantity=1)
            summary = await cart_service.get_cart_summary(user_id)
            test("add_same_product_increments", summary["total_items"] == 3)
            
            # Test 3: Add different product
            item3 = await cart_service.add_product(user_id, product_ids[1], quantity=1)
            summary = await cart_service.get_cart_summary(user_id)
            test("add_different_product", len(summary["items"]) == 2)
            
            # Test 4: Add unlimited stock product
            item4 = await cart_service.add_product(user_id, unlimited_id, quantity=100)
            test("add_unlimited_stock_product", item4 is not None)
            
            await uow.commit()
    except Exception as e:
        test("add_to_cart", False, str(e))
    finally:
        await cleanup_test_data()


async def test_stock_validation():
    """Test stock validation"""
    print("\n--- TEST: Stock Validation ---")
    user_id, product_ids, unlimited_id, cat_id = await setup_test_data()
    
    try:
        uow = UnitOfWork()
        async with uow:
            cart_service = CartService(uow)
            
            # Test 1: Try to add more than available stock
            try:
                await cart_service.add_product(user_id, product_ids[0], quantity=20)  # Stock is 10
                test("stock_validation_exceeds", False, "Should have raised error for insufficient stock")
            except ValueError as e:
                test("stock_validation_exceeds", "موجودی" in str(e))
            
            # Test 2: Add exact stock amount
            await cart_service.add_product(user_id, product_ids[0], quantity=10)
            test("stock_validation_exact", True)
            
            await uow.commit()
    except Exception as e:
        test("stock_validation", False, str(e))
    finally:
        await cleanup_test_data()


async def test_quantity_update():
    """Test updating item quantities"""
    print("\n--- TEST: Quantity Update ---")
    user_id, product_ids, unlimited_id, cat_id = await setup_test_data()
    
    try:
        uow = UnitOfWork()
        async with uow:
            cart_service = CartService(uow)
            
            # Add item
            item = await cart_service.add_product(user_id, product_ids[0], quantity=2)
            item_id = item.id
            
            # Test 1: Increase quantity
            updated = await cart_service.update_quantity(user_id, item_id, 5)
            test("increase_quantity", updated is not None and updated.quantity == 5)
            
            # Test 2: Decrease quantity
            updated = await cart_service.update_quantity(user_id, item_id, 3)
            test("decrease_quantity", updated is not None and updated.quantity == 3)
            
            # Test 3: Set to zero (should remove)
            updated = await cart_service.update_quantity(user_id, item_id, 0)
            test("quantity_zero_removes", updated is None)
            
            summary = await cart_service.get_cart_summary(user_id)
            test("item_removed_from_cart", len(summary["items"]) == 0)
            
            await uow.commit()
    except Exception as e:
        test("quantity_update", False, str(e))
    finally:
        await cleanup_test_data()


async def test_remove_item():
    """Test removing items from cart"""
    print("\n--- TEST: Remove Item ---")
    user_id, product_ids, unlimited_id, cat_id = await setup_test_data()
    
    try:
        uow = UnitOfWork()
        async with uow:
            cart_service = CartService(uow)
            
            # Add multiple items
            await cart_service.add_product(user_id, product_ids[0], quantity=1)
            await cart_service.add_product(user_id, product_ids[1], quantity=1)
            
            items = await cart_service.get_cart_items(user_id)
            test("two_items_added", len(items) == 2)
            
            # Remove first item
            removed = await cart_service.remove_item(user_id, items[0].id)
            test("item_removed", removed == True)
            
            summary = await cart_service.get_cart_summary(user_id)
            test("one_item_remaining", len(summary["items"]) == 1)
            
            await uow.commit()
    except Exception as e:
        test("remove_item", False, str(e))
    finally:
        await cleanup_test_data()


async def test_clear_cart():
    """Test clearing entire cart"""
    print("\n--- TEST: Clear Cart ---")
    user_id, product_ids, unlimited_id, cat_id = await setup_test_data()
    
    try:
        uow = UnitOfWork()
        async with uow:
            cart_service = CartService(uow)
            
            # Add multiple items
            await cart_service.add_product(user_id, product_ids[0], quantity=1)
            await cart_service.add_product(user_id, product_ids[1], quantity=2)
            await cart_service.add_product(user_id, product_ids[2], quantity=3)
            
            summary = await cart_service.get_cart_summary(user_id)
            test("three_items_in_cart", len(summary["items"]) == 3)
            
            # Clear cart
            count = await cart_service.clear_cart(user_id)
            test("cart_cleared", count == 3)
            
            summary = await cart_service.get_cart_summary(user_id)
            test("cart_empty_after_clear", len(summary["items"]) == 0)
            test("total_items_zero", summary["total_items"] == 0)
            
            await uow.commit()
    except Exception as e:
        test("clear_cart", False, str(e))
    finally:
        await cleanup_test_data()


async def test_price_consistency():
    """Test price calculations"""
    print("\n--- TEST: Price Consistency ---")
    user_id, product_ids, unlimited_id, cat_id = await setup_test_data()
    
    try:
        uow = UnitOfWork()
        async with uow:
            cart_service = CartService(uow)
            
            # Product 1: 10k x 2 = 20k
            await cart_service.add_product(user_id, product_ids[0], quantity=2)
            # Product 2: 20k x 1 = 20k
            await cart_service.add_product(user_id, product_ids[1], quantity=1)
            # Expected total: 40k
            
            summary = await cart_service.get_cart_summary(user_id)
            expected_total = 40000
            test("price_calculation", summary["total_price"] == expected_total, 
                 f"Expected {expected_total}, got {summary['total_price']}")
            
            await uow.commit()
    except Exception as e:
        test("price_consistency", False, str(e))
    finally:
        await cleanup_test_data()


async def test_discount_code():
    """Test discount code application"""
    print("\n--- TEST: Discount Code ---")
    user_id, product_ids, unlimited_id, cat_id = await setup_test_data()
    
    try:
        uow = UnitOfWork()
        async with uow:
            cart_service = CartService(uow)
            
            # Add items (total: 40k)
            await cart_service.add_product(user_id, product_ids[0], quantity=2)  # 20k
            await cart_service.add_product(user_id, product_ids[1], quantity=1)  # 20k
            
            # Apply 10% discount code
            success, msg, discount_amount = await cart_service.apply_discount_code(user_id, "TEST10")
            test("discount_applied", success == True)
            test("discount_amount_correct", discount_amount == 4000, f"Expected 4000, got {discount_amount}")
            
            # Check summary
            summary = await cart_service.get_cart_summary(user_id)
            test("discount_code_stored", summary["discount_code"] == "TEST10")
            test("discount_amount_stored", summary["discount_amount"] == 4000)
            test("final_total_with_discount", summary["final_total"] == 36000)
            
            # Remove discount
            removed = await cart_service.remove_discount_code(user_id)
            test("discount_removed", removed == True)
            
            summary = await cart_service.get_cart_summary(user_id)
            test("discount_code_cleared", summary["discount_code"] is None)
            test("discount_amount_zero", summary["discount_amount"] == 0)
            
            await uow.commit()
    except Exception as e:
        test("discount_code", False, str(e))
    finally:
        await cleanup_test_data()


async def test_empty_cart_handling():
    """Test operations on empty cart"""
    print("\n--- TEST: Empty Cart Handling ---")
    user_id, product_ids, unlimited_id, cat_id = await setup_test_data()
    
    try:
        uow = UnitOfWork()
        async with uow:
            cart_service = CartService(uow)
            
            # Get summary of empty cart
            summary = await cart_service.get_cart_summary(user_id)
            test("empty_cart_summary", summary["total_items"] == 0)
            test("empty_cart_price_zero", summary["total_price"] == 0)
            
            # Try to apply discount to empty cart
            success, msg, _ = await cart_service.apply_discount_code(user_id, "TEST10")
            test("discount_on_empty_cart_fails", success == False)
            
            await uow.commit()
    except Exception as e:
        test("empty_cart_handling", False, str(e))
    finally:
        await cleanup_test_data()


async def test_invalid_product():
    """Test adding invalid products"""
    print("\n--- TEST: Invalid Product Handling ---")
    user_id, product_ids, unlimited_id, cat_id = await setup_test_data()
    
    try:
        uow = UnitOfWork()
        async with uow:
            cart_service = CartService(uow)
            
            # Test 1: Non-existent product
            try:
                await cart_service.add_product(user_id, str(uuid4()), quantity=1)
                test("invalid_product_id", False, "Should have raised error")
            except ValueError as e:
                test("invalid_product_id", "یافت نشد" in str(e))
            
            # Test 2: Invisible product
            # Create invisible product
            invisible_product = Product(
                title="Invisible Product",
                price=1000,
                stock=10,
                status=ProductStatus.ACTIVE,
                is_visible=False,  # Not visible
                category_id=cat_id,
                type=ProductType.DIGITAL
            )
            uow.session.add(invisible_product)
            await uow.flush()
            
            try:
                await cart_service.add_product(user_id, invisible_product.id, quantity=1)
                test("invisible_product", False, "Should have raised error")
            except ValueError as e:
                test("invisible_product", "دسترس نیست" in str(e))
            
            # Test 3: Inactive product
            inactive_product = Product(
                title="Inactive Product",
                price=1000,
                stock=10,
                status=ProductStatus.INACTIVE,  # Inactive
                is_visible=True,
                category_id=cat_id,
                type=ProductType.DIGITAL
            )
            uow.session.add(inactive_product)
            await uow.flush()
            
            try:
                await cart_service.add_product(user_id, inactive_product.id, quantity=1)
                test("inactive_product", False, "Should have raised error")
            except ValueError as e:
                test("inactive_product", "غیرفعال" in str(e))
            
            await uow.commit()
    except Exception as e:
        test("invalid_product", False, str(e))
    finally:
        await cleanup_test_data()


async def run_all_tests():
    """Run all cart tests"""
    await init_db()
    
    await test_add_to_cart()
    await test_stock_validation()
    await test_quantity_update()
    await test_remove_item()
    await test_clear_cart()
    await test_price_consistency()
    await test_discount_code()
    await test_empty_cart_handling()
    await test_invalid_product()
    
    # Print summary
    print("\n" + "="*80)
    print("CART TESTING SUMMARY")
    print("="*80)
    print(f"✅ Tests Passed:  {results['pass']}")
    print(f"❌ Tests Failed:  {results['fail']}")
    print(f"📊 Pass Rate:     {results['pass']/(results['pass']+results['fail'])*100:.1f}%")
    
    if results['bugs']:
        print(f"\n❌ Bugs Found ({len(results['bugs'])}):")
        for bug in results['bugs']:
            print(f"   - {bug['test']}: {bug['error']}")
    
    print("="*80)


if __name__ == "__main__":
    asyncio.run(run_all_tests())
