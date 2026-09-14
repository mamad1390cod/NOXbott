"""
Comprehensive Database Testing Suite

Tests:
1. Schema validation
2. Foreign key relationships
3. Constraints (unique, not null, check)
4. Transactions & rollback
5. Migrations integrity
6. Duplicate data detection
7. Orphan records detection
8. Data integrity
"""

import asyncio
import logging
import sys
from pathlib import Path
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from bot.database import init_db, session_scope
from bot.models.user import User, UserRole
from bot.models.product import Product, ProductStatus, ProductType
from bot.models.category import Category
from bot.models.cart import Cart, CartItem
from bot.models.order import Order, OrderStatus, OrderItem, PaymentMethod
from bot.models.payment import Payment, PaymentStatus
from bot.models.discount_code import DiscountCode, DiscountType
from bot.models.custom import Custom, CustomStatus, CustomType
from bot.models.ticket import Ticket, TicketStatus, TicketCategory
from bot.models.topup import TopUpRequest, TopUpStatus, TopUpPaymentMethod
from bot.models.user_dashboard import Transaction, TransactionType

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class TestResults:
    """Test results tracker"""
    def __init__(self):
        self.tests_run = 0
        self.tests_passed = 0
        self.tests_failed = 0
        self.tests_blocked = 0
        self.failures = []
    
    def record_pass(self, test_name: str):
        self.tests_run += 1
        self.tests_passed += 1
        logger.info(f"✅ PASS: {test_name}")
    
    def record_fail(self, test_name: str, error: str):
        self.tests_run += 1
        self.tests_failed += 1
        self.failures.append((test_name, error))
        logger.error(f"❌ FAIL: {test_name} - {error}")
    
    def record_blocked(self, test_name: str, reason: str):
        self.tests_run += 1
        self.tests_blocked += 1
        logger.warning(f"⚠️ BLOCKED: {test_name} - {reason}")
    
    def print_summary(self):
        print("\n" + "="*80)
        print("DATABASE COMPREHENSIVE TEST RESULTS")
        print("="*80)
        print(f"Total Tests: {self.tests_run}")
        print(f"✅ Passed: {self.tests_passed}")
        print(f"❌ Failed: {self.tests_failed}")
        print(f"⚠️ Blocked: {self.tests_blocked}")
        print("="*80)
        if self.failures:
            print("\nFAILURES:")
            for test_name, error in self.failures:
                print(f"  - {test_name}: {error}")
        print("="*80)


results = TestResults()


# ============================================================================
# 1. SCHEMA VALIDATION
# ============================================================================

async def test_schema_tables_exist():
    """Test that all required tables exist"""
    try:
        async with session_scope() as session:
            def _inspect_tables(conn):
                insp = inspect(conn)
                return insp.get_table_names()
            
            required_tables = [
                'users', 'products', 'categories', 'carts', 'cart_items',
                'orders', 'order_items', 'order_deliveries', 'order_status_events',
                'payments', 'discount_codes', 'customs', 'custom_categories',
                'custom_registrations', 'custom_carts', 'custom_cart_items',
                'tickets', 'ticket_categories', 'ticket_messages',
                'topup_requests', 'topup_receipts', 'topup_amounts',
                'transactions', 'wishlist_items', 'achievements', 'badges',
                'config_products'
            ]
            
            existing_tables = await session.connection().run_sync(_inspect_tables)
            missing_tables = set(required_tables) - set(existing_tables)
            
            if missing_tables:
                results.record_fail("schema_tables_exist", f"Missing tables: {missing_tables}")
            else:
                results.record_pass("schema_tables_exist")
    except Exception as e:
        results.record_fail("schema_tables_exist", str(e))


async def test_schema_columns():
    """Test that critical columns exist in key tables"""
    try:
        async with session_scope() as session:
            def _get_columns(conn, table_name):
                insp = inspect(conn)
                return {col['name'] for col in insp.get_columns(table_name)}
            
            # Check users table
            user_cols = await session.connection().run_sync(lambda conn: _get_columns(conn, 'users'))
            required_user_cols = {'id', 'telegram_id', 'username', 'role', 'wallet_balance'}
            if not required_user_cols.issubset(user_cols):
                results.record_fail("schema_columns_users", f"Missing columns: {required_user_cols - user_cols}")
                return
            
            # Check products table
            product_cols = await session.connection().run_sync(lambda conn: _get_columns(conn, 'products'))
            required_product_cols = {'id', 'title', 'price', 'stock', 'status', 'category_id'}
            if not required_product_cols.issubset(product_cols):
                results.record_fail("schema_columns_products", f"Missing columns: {required_product_cols - product_cols}")
                return
            
            # Check orders table
            order_cols = await session.connection().run_sync(lambda conn: _get_columns(conn, 'orders'))
            required_order_cols = {'id', 'user_id', 'status', 'total_amount', 'final_amount', 'order_number'}
            if not required_order_cols.issubset(order_cols):
                results.record_fail("schema_columns_orders", f"Missing columns: {required_order_cols - order_cols}")
                return
            
            # Check carts table for discount fields
            cart_cols = await session.connection().run_sync(lambda conn: _get_columns(conn, 'carts'))
            required_cart_cols = {'id', 'user_id', 'discount_code', 'discount_amount'}
            if not required_cart_cols.issubset(cart_cols):
                results.record_fail("schema_columns_carts", f"Missing columns: {required_cart_cols - cart_cols}")
                return
            
            results.record_pass("schema_columns")
    except Exception as e:
        results.record_fail("schema_columns", str(e))


# ============================================================================
# 2. FOREIGN KEY RELATIONSHIPS
# ============================================================================

async def test_foreign_keys_exist():
    """Test that critical foreign keys are defined"""
    try:
        async with session_scope() as session:
            def _get_foreign_keys(conn, table_name):
                insp = inspect(conn)
                return insp.get_foreign_keys(table_name)
            
            # Check cart_items -> carts
            cart_items_fks = await session.connection().run_sync(lambda conn: _get_foreign_keys(conn, 'cart_items'))
            cart_fk_found = any(fk['referred_table'] == 'carts' for fk in cart_items_fks)
            
            # Check order_items -> orders
            order_items_fks = await session.connection().run_sync(lambda conn: _get_foreign_keys(conn, 'order_items'))
            order_fk_found = any(fk['referred_table'] == 'orders' for fk in order_items_fks)
            
            # Check payments -> users
            payments_fks = await session.connection().run_sync(lambda conn: _get_foreign_keys(conn, 'payments'))
            user_fk_found = any(fk['referred_table'] == 'users' for fk in payments_fks)
            
            if not all([cart_fk_found, order_fk_found, user_fk_found]):
                results.record_fail("foreign_keys_exist", "Some foreign keys missing")
            else:
                results.record_pass("foreign_keys_exist")
    except Exception as e:
        results.record_fail("foreign_keys_exist", str(e))


async def test_foreign_key_cascade():
    """Test that CASCADE deletions work correctly"""
    try:
        async with session_scope() as session:
            # Create test user
            user = User(
                telegram_id=999888777,
                username="test_cascade_user",
                role=UserRole.USER,
                referral_code=f"REF{uuid4().hex[:8]}"
            )
            session.add(user)
            await session.flush()
            
            # Create cart
            cart = Cart(user_id=user.id)
            session.add(cart)
            await session.flush()
            cart_id = cart.id
            
            # Create cart item
            category = Category(name="Test Cat", type="product")
            session.add(category)
            await session.flush()
            
            product = Product(
                title="Test Product",
                price=10000,
                stock=10,
                category_id=category.id
            )
            session.add(product)
            await session.flush()
            
            cart_item = CartItem(
                cart_id=cart.id,
                product_id=product.id,
                quantity=1
            )
            session.add(cart_item)
            await session.commit()
            
            # Delete user (should cascade to cart and cart_items)
            await session.delete(user)
            await session.commit()
            
            # Verify cascade
            result = await session.execute(select(Cart).where(Cart.id == cart_id))
            deleted_cart = result.scalar_one_or_none()
            
            if deleted_cart is not None:
                results.record_fail("foreign_key_cascade", "Cart was not deleted after user deletion")
            else:
                results.record_pass("foreign_key_cascade")
                
    except Exception as e:
        results.record_fail("foreign_key_cascade", str(e))


# ============================================================================
# 3. CONSTRAINTS
# ============================================================================

async def test_unique_constraints():
    """Test UNIQUE constraints"""
    try:
        # Cleanup first
        async with session_scope() as session:
            await session.execute(text("DELETE FROM users WHERE telegram_id IN (111222333, 777888999, 444555666, 999888777)"))
            await session.commit()
        
        async with session_scope() as session:
            # Test telegram_id uniqueness
            user1 = User(
                telegram_id=111222333,
                username="user_unique_1",
                role=UserRole.USER,
                referral_code=f"REF{uuid4().hex[:8]}"
            )
            session.add(user1)
            await session.commit()
            
            # Try to create duplicate
            user2 = User(
                telegram_id=111222333,  # Same telegram_id
                username="user_unique_2",
                role=UserRole.USER,
                referral_code=f"REF{uuid4().hex[:8]}"
            )
            session.add(user2)
            
            try:
                await session.commit()
                results.record_fail("unique_constraints", "Duplicate telegram_id was allowed")
            except IntegrityError:
                await session.rollback()
                results.record_pass("unique_constraints")
                
    except Exception as e:
        results.record_fail("unique_constraints", str(e))


async def test_not_null_constraints():
    """Test NOT NULL constraints"""
    try:
        async with session_scope() as session:
            # Try to create product without required fields
            product = Product(
                # title is missing (required)
                price=10000
            )
            session.add(product)
            
            try:
                await session.commit()
                results.record_fail("not_null_constraints", "NULL value was allowed in NOT NULL column")
            except IntegrityError:
                await session.rollback()
                results.record_pass("not_null_constraints")
                
    except Exception as e:
        results.record_fail("not_null_constraints", str(e))


# ============================================================================
# 4. TRANSACTIONS & ROLLBACK
# ============================================================================

async def test_transaction_rollback():
    """Test that transaction rollback works"""
    try:
        user_id = None
        try:
            async with session_scope() as session:
                user = User(
                    telegram_id=444555666,
                    username="test_rollback",
                    role=UserRole.USER,
                    referral_code=f"REF{uuid4().hex[:8]}"
                )
                session.add(user)
                await session.flush()
                user_id = user.id
                
                # Force an error
                raise Exception("Intentional error for rollback test")
        except Exception:
            pass
        
        # Verify user was not created
        async with session_scope() as session:
            result = await session.execute(select(User).where(User.id == user_id))
            rolled_back_user = result.scalar_one_or_none()
            
            if rolled_back_user is not None:
                results.record_fail("transaction_rollback", "User was committed despite rollback")
            else:
                results.record_pass("transaction_rollback")
                
    except Exception as e:
        results.record_fail("transaction_rollback", str(e))


async def test_transaction_commit():
    """Test that transaction commit works"""
    try:
        # Cleanup first
        async with session_scope() as session:
            await session.execute(text("DELETE FROM users WHERE telegram_id = 777888999"))
            await session.commit()
        
        async with session_scope() as session:
            user = User(
                telegram_id=777888999,
                username="test_commit",
                role=UserRole.USER,
                referral_code=f"REF{uuid4().hex[:8]}"
            )
            session.add(user)
            await session.commit()
            user_id = user.id
        
        # Verify in new session
        async with session_scope() as session:
            result = await session.execute(select(User).where(User.id == user_id))
            committed_user = result.scalar_one_or_none()
            
            if committed_user is None:
                results.record_fail("transaction_commit", "User was not committed")
            else:
                results.record_pass("transaction_commit")
                
    except Exception as e:
        results.record_fail("transaction_commit", str(e))


# ============================================================================
# 5. DUPLICATE DATA DETECTION
# ============================================================================

async def test_duplicate_orders():
    """Test detection of duplicate orders"""
    try:
        async with session_scope() as session:
            # Check for duplicate order_numbers
            result = await session.execute(
                text("""
                    SELECT order_number, COUNT(*) as count
                    FROM orders
                    GROUP BY order_number
                    HAVING COUNT(*) > 1
                """)
            )
            duplicates = result.fetchall()
            
            if duplicates:
                results.record_fail("duplicate_orders", f"Found {len(duplicates)} duplicate order numbers")
            else:
                results.record_pass("duplicate_orders")
                
    except Exception as e:
        results.record_fail("duplicate_orders", str(e))


async def test_duplicate_discount_codes():
    """Test detection of duplicate discount codes"""
    try:
        async with session_scope() as session:
            result = await session.execute(
                text("""
                    SELECT code, COUNT(*) as count
                    FROM discount_codes
                    GROUP BY code
                    HAVING COUNT(*) > 1
                """)
            )
            duplicates = result.fetchall()
            
            if duplicates:
                results.record_fail("duplicate_discount_codes", f"Found {len(duplicates)} duplicate codes")
            else:
                results.record_pass("duplicate_discount_codes")
                
    except Exception as e:
        results.record_fail("duplicate_discount_codes", str(e))


# ============================================================================
# 6. ORPHAN RECORDS DETECTION
# ============================================================================

async def test_orphan_cart_items():
    """Test detection of orphan cart items"""
    try:
        async with session_scope() as session:
            result = await session.execute(
                text("""
                    SELECT ci.id
                    FROM cart_items ci
                    LEFT JOIN carts c ON ci.cart_id = c.id
                    WHERE c.id IS NULL
                """)
            )
            orphans = result.fetchall()
            
            if orphans:
                results.record_fail("orphan_cart_items", f"Found {len(orphans)} orphan cart items")
            else:
                results.record_pass("orphan_cart_items")
                
    except Exception as e:
        results.record_fail("orphan_cart_items", str(e))


async def test_orphan_order_items():
    """Test detection of orphan order items"""
    try:
        async with session_scope() as session:
            result = await session.execute(
                text("""
                    SELECT oi.id
                    FROM order_items oi
                    LEFT JOIN orders o ON oi.order_id = o.id
                    WHERE o.id IS NULL
                """)
            )
            orphans = result.fetchall()
            
            if orphans:
                results.record_fail("orphan_order_items", f"Found {len(orphans)} orphan order items")
            else:
                results.record_pass("orphan_order_items")
                
    except Exception as e:
        results.record_fail("orphan_order_items", str(e))


async def test_orphan_payments():
    """Test detection of orphan payments"""
    try:
        async with session_scope() as session:
            result = await session.execute(
                text("""
                    SELECT p.id
                    FROM payments p
                    LEFT JOIN users u ON p.user_id = u.id
                    WHERE u.id IS NULL
                """)
            )
            orphans = result.fetchall()
            
            if orphans:
                results.record_fail("orphan_payments", f"Found {len(orphans)} orphan payments")
            else:
                results.record_pass("orphan_payments")
                
    except Exception as e:
        results.record_fail("orphan_payments", str(e))


# ============================================================================
# 7. DATA INTEGRITY
# ============================================================================

async def test_order_amount_integrity():
    """Test that order amounts are consistent"""
    try:
        async with session_scope() as session:
            result = await session.execute(
                text("""
                    SELECT id, total_amount, discount_amount, final_amount
                    FROM orders
                    WHERE final_amount != (total_amount - discount_amount)
                    OR final_amount < 0
                    OR total_amount < 0
                """)
            )
            inconsistent = result.fetchall()
            
            if inconsistent:
                results.record_fail("order_amount_integrity", f"Found {len(inconsistent)} orders with inconsistent amounts")
            else:
                results.record_pass("order_amount_integrity")
                
    except Exception as e:
        results.record_fail("order_amount_integrity", str(e))


async def test_stock_integrity():
    """Test that stock values are valid"""
    try:
        async with session_scope() as session:
            result = await session.execute(
                text("""
                    SELECT id, title, stock
                    FROM products
                    WHERE stock < 0 AND unlimited_stock = 0
                """)
            )
            invalid_stock = result.fetchall()
            
            if invalid_stock:
                results.record_fail("stock_integrity", f"Found {len(invalid_stock)} products with negative stock")
            else:
                results.record_pass("stock_integrity")
                
    except Exception as e:
        results.record_fail("stock_integrity", str(e))


async def test_wallet_balance_integrity():
    """Test that wallet balances are valid"""
    try:
        async with session_scope() as session:
            result = await session.execute(
                text("""
                    SELECT id, telegram_id, wallet_balance
                    FROM users
                    WHERE wallet_balance < 0
                """)
            )
            negative_balances = result.fetchall()
            
            if negative_balances:
                results.record_fail("wallet_balance_integrity", f"Found {len(negative_balances)} users with negative balance")
            else:
                results.record_pass("wallet_balance_integrity")
                
    except Exception as e:
        results.record_fail("wallet_balance_integrity", str(e))


# ============================================================================
# 8. MIGRATIONS
# ============================================================================

async def test_migration_order_deliveries():
    """Test that order_deliveries migration was applied"""
    try:
        async with session_scope() as session:
            def _check_table(conn):
                insp = inspect(conn)
                tables = insp.get_table_names()
                if 'order_deliveries' not in tables:
                    return False, "table missing"
                columns = {col['name'] for col in insp.get_columns('order_deliveries')}
                required = {'id', 'order_id', 'delivery_type', 'config_text', 'status'}
                if not required.issubset(columns):
                    return False, f"Missing columns: {required - columns}"
                return True, "OK"
            
            success, message = await session.connection().run_sync(_check_table)
            if not success:
                results.record_fail("migration_order_deliveries", message)
            else:
                results.record_pass("migration_order_deliveries")
                    
    except Exception as e:
        results.record_fail("migration_order_deliveries", str(e))


async def test_migration_custom_emoji():
    """Test that custom_categories emoji column exists"""
    try:
        async with session_scope() as session:
            def _check_column(conn):
                insp = inspect(conn)
                columns = {col['name'] for col in insp.get_columns('custom_categories')}
                return 'emoji' in columns
            
            has_emoji = await session.connection().run_sync(_check_column)
            if not has_emoji:
                results.record_fail("migration_custom_emoji", "emoji column missing from custom_categories")
            else:
                results.record_pass("migration_custom_emoji")
                
    except Exception as e:
        results.record_fail("migration_custom_emoji", str(e))


async def test_migration_customer_info():
    """Test that user customer info columns exist"""
    try:
        async with session_scope() as session:
            def _check_columns(conn):
                insp = inspect(conn)
                columns = {col['name'] for col in insp.get_columns('users')}
                required = {'email', 'password', 'customer_name', 'phone'}
                return required.issubset(columns), required - columns
            
            has_all, missing = await session.connection().run_sync(_check_columns)
            if not has_all:
                results.record_fail("migration_customer_info", f"Missing columns: {missing}")
            else:
                results.record_pass("migration_customer_info")
                
    except Exception as e:
        results.record_fail("migration_customer_info", str(e))


# ============================================================================
# MAIN TEST RUNNER
# ============================================================================

async def run_all_tests():
    """Run all database tests"""
    logger.info("="*80)
    logger.info("STARTING COMPREHENSIVE DATABASE TESTS")
    logger.info("="*80)
    
    # Initialize database
    await init_db()
    
    # Schema tests
    logger.info("\n--- SCHEMA VALIDATION ---")
    await test_schema_tables_exist()
    await test_schema_columns()
    
    # Foreign key tests
    logger.info("\n--- FOREIGN KEY RELATIONSHIPS ---")
    await test_foreign_keys_exist()
    await test_foreign_key_cascade()
    
    # Constraint tests
    logger.info("\n--- CONSTRAINTS ---")
    await test_unique_constraints()
    await test_not_null_constraints()
    
    # Transaction tests
    logger.info("\n--- TRANSACTIONS & ROLLBACK ---")
    await test_transaction_rollback()
    await test_transaction_commit()
    
    # Duplicate detection
    logger.info("\n--- DUPLICATE DATA DETECTION ---")
    await test_duplicate_orders()
    await test_duplicate_discount_codes()
    
    # Orphan detection
    logger.info("\n--- ORPHAN RECORDS DETECTION ---")
    await test_orphan_cart_items()
    await test_orphan_order_items()
    await test_orphan_payments()
    
    # Data integrity
    logger.info("\n--- DATA INTEGRITY ---")
    await test_order_amount_integrity()
    await test_stock_integrity()
    await test_wallet_balance_integrity()
    
    # Migration tests
    logger.info("\n--- MIGRATIONS ---")
    await test_migration_order_deliveries()
    await test_migration_custom_emoji()
    await test_migration_customer_info()
    
    # Print summary
    results.print_summary()


if __name__ == "__main__":
    asyncio.run(run_all_tests())
