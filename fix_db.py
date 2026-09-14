"""Simple database fixer for discount_codes table."""
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).parent / "noxbot.db"

def fix_database():
    """Fix discount_codes table in database."""
    print("=" * 70)
    print("NOXbot Database Fix: discount_codes Table")
    print("=" * 70)
    print()
    
    if not DB_PATH.exists():
        print(f"❌ Database not found: {DB_PATH}")
        print("   Run main.py first to create the database.")
        return False
    
    print(f"📂 Database: {DB_PATH}")
    
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        
        # Check if table exists
        cursor.execute("""
            SELECT name FROM sqlite_master 
            WHERE type='table' AND name='discount_codes'
        """)
        table_exists = cursor.fetchone() is not None
        
        if table_exists:
            print("✓ Table 'discount_codes' exists")
            
            # Get existing columns
            cursor.execute("PRAGMA table_info(discount_codes)")
            columns = {row[1] for row in cursor.fetchall()}
            print(f"  Existing columns ({len(columns)}): {', '.join(sorted(columns))}")
            
            # Check for discount_type column
            if 'discount_type' not in columns:
                print("\n❌ Column 'discount_type' is missing!")
                print("   This means the table structure is incomplete.")
                print("\n🔧 Dropping and recreating table...")
                
                # Drop the old table
                cursor.execute("DROP TABLE IF EXISTS discount_codes")
                conn.commit()
                print("  ✓ Old table dropped")
                
                table_exists = False
            else:
                print("  ✅ Table structure looks correct!")
        
        if not table_exists:
            print("\n🔧 Creating discount_codes table...")
            
            # Create the table with all required columns
            cursor.execute("""
                CREATE TABLE discount_codes (
                    id VARCHAR(36) PRIMARY KEY NOT NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    code VARCHAR(50) NOT NULL UNIQUE,
                    discount_type VARCHAR(20) NOT NULL,
                    discount_value INTEGER NOT NULL,
                    max_eligible_amount BIGINT,
                    expires_at DATETIME,
                    max_uses INTEGER,
                    usage_count INTEGER NOT NULL DEFAULT 0,
                    is_active BOOLEAN NOT NULL DEFAULT 1,
                    description VARCHAR(255)
                )
            """)
            conn.commit()
            print("  ✓ Table created")
            
            # Create indexes
            cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_discount_codes_code ON discount_codes(code)")
            cursor.execute("CREATE INDEX IF NOT EXISTS ix_discount_codes_is_active ON discount_codes(is_active)")
            cursor.execute("CREATE INDEX IF NOT EXISTS ix_discount_codes_code ON discount_codes(code)")
            conn.commit()
            print("  ✓ Indexes created")
            
            # Verify
            cursor.execute("PRAGMA table_info(discount_codes)")
            columns = {row[1] for row in cursor.fetchall()}
            print(f"  ✓ Final columns ({len(columns)}): {', '.join(sorted(columns))}")
        
        conn.close()
        
        print()
        print("=" * 70)
        print("✅ Database fix completed successfully!")
        print("=" * 70)
        print()
        print("Next steps:")
        print("1. Start NOXbot:")
        print("   i:\\python\\.venv\\Scripts\\python.exe main.py")
        print()
        print("2. Test the discount feature:")
        print("   - Go to admin panel")
        print("   - Click: ٪ کد تخفیف")
        print("   - Create a new discount code")
        print()
        return True
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = fix_database()
    sys.exit(0 if success else 1)
