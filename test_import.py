#!/usr/bin/env python3
"""Test imports after discount handler fix"""

try:
    print("Testing imports...")
    
    from bot.handlers.admin import admin_discounts_router
    print("✅ admin_discounts_router imported successfully")
    
    from bot.handlers.admin import admin_orphans_router
    print("✅ admin_orphans_router imported successfully")
    
    from bot.handlers import admin_router
    print("✅ admin_router imported successfully")
    
    # Check that admin_discounts router is registered
    print(f"\n✅ All imports successful!")
    print(f"   admin_discounts_router name: {admin_discounts_router.name}")
    print(f"   admin_orphans_router name: {admin_orphans_router.name}")
    
except Exception as e:
    print(f"\n❌ Import failed: {e}")
    import traceback
    traceback.print_exc()
    exit(1)
