#!/usr/bin/env python
"""Test imports for discount code system."""

import sys
print("Testing imports...")

try:
    from bot.states import DiscountCodeStates
    print("✓ DiscountCodeStates imported successfully")
except ImportError as e:
    print(f"✗ Failed to import DiscountCodeStates: {e}")
    sys.exit(1)

try:
    from bot.handlers import cart
    print("✓ cart handlers imported successfully")
except Exception as e:
    print(f"✗ Failed to import cart handlers: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

try:
    from bot.handlers import admin_router, user_router
    print("✓ routers imported successfully")
except Exception as e:
    print(f"✗ Failed to import routers: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print("\n✅ All imports successful!")
