#!/usr/bin/env python3
"""Ultra-minimal test - just print the database URL."""
import sys
sys.path.insert(0, r'I:\python\NOXbott')

try:
    from bot.config import get_settings
    s = get_settings()
    print(s.database_url)
except Exception as e:
    print(f"ERROR: {e}")
    import traceback
    traceback.print_exc()
