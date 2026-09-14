# Discount Code Import Error Fix

## Issue
```
NameError: name 'DiscountCodeStates' is not defined
Location: bot/handlers/cart.py:293
```

## Root Cause
The `DiscountCodeStates` class was defined in `bot/states/__init__.py` but was not imported in `bot/handlers/cart.py`. The decorator `@router.message(DiscountCodeStates.waiting_code)` attempted to use the class before it was available.

## Fix Applied

### File: `bot/handlers/cart.py`

**Added import at line 9:**
```python
from bot.states import DiscountCodeStates
```

**Removed redundant local import:**
The function `cb_cart_add_discount` had a local import `from bot.states import DiscountCodeStates` which was removed since it's now imported at the module level.

### Complete import section (lines 1-13):
```python
"""Shopping cart handlers."""

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from bot.keyboards.cart_keyboard import cart_keyboard, checkout_keyboard, confirm_cancel_keyboard
from bot.services.cart import CartService
from bot.services.order import OrderService
from bot.states import DiscountCodeStates  # <-- ADDED
from bot.utils.format import format_price
from bot.utils.editing import safe_edit_text

router = Router(name="cart")
```

## Verification Steps

1. **Compile check:**
   ```cmd
   cd I:\python\NOXbott
   I:\python\.venv\Scripts\python.exe -m compileall bot
   ```
   Expected: No syntax errors

2. **Start bot:**
   ```cmd
   cd I:\python\NOXbott
   I:\python\.venv\Scripts\python.exe main.py
   ```
   Expected: Bot starts without NameError

3. **Test imports:**
   ```python
   from bot.states import DiscountCodeStates
   from bot.handlers import cart
   # Should complete without errors
   ```

## Related Files
- `bot/states/__init__.py` - Contains `DiscountCodeStates` definition (lines ~350-360)
- `bot/handlers/admin/admin_discounts.py` - Also uses `AdminDiscountCodeStates` (correctly imported)
- `bot/handlers/cart.py` - Fixed file

## Status
✅ **FIXED** - Import added, redundant local import removed

The original error "NameError: name 'DiscountCodeStates' is not defined" should now be completely resolved.
