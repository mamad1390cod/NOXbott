"""Minimal test to verify database path resolution."""

import sys
from pathlib import Path

# Add project to path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from bot.config import get_settings
from bot.utils.paths import get_database_path, ensure_database_directory

print("=" * 70)
print("DATABASE PATH TEST")
print("=" * 70)

# Test 1: Utils module
print("\n[1] Utils module:")
db_path = get_database_path()
print(f"    get_database_path() = {db_path}")
print(f"    exists? {db_path.exists()}")
print(f"    is_absolute? {db_path.is_absolute()}")

# Test 2: Config module
print("\n[2] Config module:")
settings = get_settings()
print(f"    settings.database_url = {settings.database_url}")

# Test 3: Ensure directory
print("\n[3] Ensure directory:")
ensured_path = ensure_database_directory()
print(f"    ensure_database_directory() = {ensured_path}")
print(f"    parent exists? {ensured_path.parent.exists()}")

# Test 4: Verify no database in wrong location
print("\n[4] Check for databases in wrong locations:")
parent_db = Path("I:/python/noxbot.db")
print(f"    I:/python/noxbot.db exists? {parent_db.exists()}")

correct_db = Path("I:/python/NOXbott/noxbot.db")
print(f"    I:/python/NOXbott/noxbot.db exists? {correct_db.exists()}")

# Final verdict
print("\n" + "=" * 70)
if str(db_path) == str(correct_db) and correct_db.exists():
    print("✅ SUCCESS: Database path is correct!")
    print(f"   Final path: {db_path}")
else:
    print("❌ FAILURE: Database path is incorrect!")
    print(f"   Expected: {correct_db}")
    print(f"   Got: {db_path}")
print("=" * 70)
