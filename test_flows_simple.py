"""Simple user flow test"""
import sys
from pathlib import Path

print("Starting user flow tests...")
print("="*60)

base = Path("i:/python/NOXbott/bot")

# Test 1: Check handler files
handlers = base / "handlers"
required_files = ["menu.py", "products.py", "cart.py", "payments.py"]
missing = [f for f in required_files if not (handlers / f).exists()]
print(f"Test 1 - Handler files: {'PASS' if not missing else 'FAIL: ' + str(missing)}")

# Test 2: Check menu.py content
menu_file = handlers / "menu.py"
if menu_file.exists():
    content = menu_file.read_text()
    has_start = "CommandStart" in content
    has_home = "menu:home" in content
    print(f"Test 2 - Start handler: {'PASS' if has_start else 'FAIL'}")
    print(f"Test 3 - Home callback: {'PASS' if has_home else 'FAIL'}")
else:
    print("Test 2 - FAIL: menu.py not found")
    print("Test 3 - FAIL: menu.py not found")

# Test 4: Check keyboards
keyboards = base / "keyboards" / "common.py"
if keyboards.exists():
    content = keyboards.read_text()
    has_back = "def back_button" in content
    has_main_menu = "def main_menu_keyboard" in content
    print(f"Test 4 - Back button: {'PASS' if has_back else 'FAIL'}")
    print(f"Test 5 - Main menu: {'PASS' if has_main_menu else 'FAIL'}")
else:
    print("Test 4 - FAIL: keyboards/common.py not found")
    print("Test 5 - FAIL: keyboards/common.py not found")

print("="*60)
print("Tests completed!")
