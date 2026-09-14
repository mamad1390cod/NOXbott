#!/usr/bin/env python3
"""Create discount_codes table if it doesn't exist.

This script ensures the discount_codes table and all its columns exist in the database.
Safe to run multiple times - only creates missing elements.
"""

import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from sqlalchemy import text, inspect
from bot.database.engine import get_engine
from bot.models import DiscountCode


async def create_discount_table():
    """Create discount_codes table if it doesn't exist."""
    engine = get_engine()
    
    print("=" * 70)
    print("Database Migration: Create discount_codes Table")
    print("=" * 70)
    print()
    
    async with engine.begin() as conn:
        # Check if table exists
        def check_table(connection):
            inspector = inspect(connection)
            tables = inspector.get_table_names()
            return 'discount_codes' in tables
        
        table_exists = await conn.run_sync(check_table)
        
        if table_exists:
            print("✓ Table 'discount_codes' already exists")
            
            # Check columns
            def get_columns(connection):
                inspector = inspect(connection)
                return {col['name'] for col in inspector.get_columns('discount_codes')}
            
            existing_columns = await conn.run_sync(get_columns)
            print(f"  Existing columns: {', '.join(sorted(existing_columns))}")
            
            # Required columns from the model
            required_columns = {
                'id', 'created_at', 'updated_at',
                'code', 'discount_type', 'discount_value',
                'max_eligible_amount', 'expires_at', 'max_uses',
                'usage_count', 'is_active', 'description'
            }
            
            missing_columns = required_columns - existing_columns
            
            if missing_columns:
                print(f"\n⚠️  Missing columns detected: {', '.join(missing_columns)}")
                print("   Attempting to add missing columns...")
                
                # Add missing columns one by one
                column_definitions = {
                    'discount_type': "VARCHAR(20) NOT NULL DEFAULT 'percentage'",
                    'discount_value': "INTEGER NOT NULL DEFAULT 0",
                    'max_eligible_amount': "BIGINT",
                    'expires_at': "DATETIME",
                    'max_uses': "INTEGER",
                    'usage_count': "INTEGER NOT NULL DEFAULT 0",
                    'is_active': "BOOLEAN NOT NULL DEFAULT 1",
                    'description': "VARCHAR(255)",
                }
                
                for col in missing_columns:
                    if col in column_definitions:
                        try:
                            sql = f"ALTER TABLE discount_codes ADD COLUMN {col} {column_definitions[col]}"
                            await conn.execute(text(sql))
                            print(f"  ✓ Added column: {col}")
                        except Exception as e:
                            print(f"  ✗ Failed to add column {col}: {e}")
                
                # Verify again
                final_columns = await conn.run_sync(get_columns)
                still_missing = required_columns - final_columns
                
                if still_missing:
                    print(f"\n❌ Still missing columns: {', '.join(still_missing)}")
                    print("   You may need to recreate the table or run manual migration.")
                    return False
                else:
                    print("\n✅ All required columns now exist!")
            else:
                print("  ✅ All required columns exist!")
                
        else:
            print("⚠️  Table 'discount_codes' does not exist. Creating...")
            
            # Create the table using SQLAlchemy metadata
            def create_table(connection):
                DiscountCode.__table__.create(connection, checkfirst=True)
            
            await conn.run_sync(create_table)
            print("✅ Table 'discount_codes' created successfully!")
            
            # Verify
            table_exists_now = await conn.run_sync(check_table)
            if table_exists_now:
                existing_columns = await conn.run_sync(get_columns)
                print(f"  Columns: {', '.join(sorted(existing_columns))}")
            else:
                print("❌ Table creation failed!")
                return False
    
    print()
    print("=" * 70)
    print("✅ Migration completed successfully!")
    print("=" * 70)
    print()
    print("Next: Start your bot with: i:\\python\\.venv\\Scripts\\python.exe main.py")
    return True


async def main():
    try:
        success = await create_discount_table()
        return 0 if success else 1
    except Exception as e:
        print(f"\n❌ Migration failed with error:")
        print(f"   {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
