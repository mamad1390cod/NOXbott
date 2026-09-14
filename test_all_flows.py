"""Comprehensive User Flow Testing"""
import re
from pathlib import Path
from collections import defaultdict

print("="*80)
print("COMPREHENSIVE USER FLOW TESTING")
print("="*80)

base = Path("i:/python/NOXbott/bot")
results = {"pass": 0, "fail": 0, "warnings": []}

def test(name, condition, error=""):
    if condition:
        results["pass"] += 1
        print(f"✅ PASS: {name}")
        return True
    else:
        results["fail"] += 1
        print(f"❌ FAIL: {name} - {error}")
        return False

def warn(message):
    results["warnings"].append(message)
    print(f"⚠️  WARN: {message}")

# ===========================================================================
# TEST 1: HANDLER FILES EXIST
# ===========================================================================
print("\n--- HANDLER FILES ---")
handlers_dir = base / "handlers"
required_handlers = [
    "menu.py", "products.py", "cart.py", "payments.py",
    "user_orders.py", "customs.py", "custom_cart.py", 
    "configs.py", "support.py", "topup.py", "my_account.py"
]

for handler in required_handlers:
    test(
        f"handler_{handler}",
        (handlers_dir / handler).exists(),
        f"{handler} not found"
    )

# ===========================================================================
# TEST 2: CALLBACK HANDLERS
# ===========================================================================
print("\n--- CALLBACK HANDLERS ---")

def find_callbacks(file_path):
    """Find all callback_data in a file"""
    if not file_path.exists():
        return set()
    content = file_path.read_text(encoding='utf-8', errors='ignore')
    patterns = [
        r'callback_data=["\']([^"\']+)["\']',
        r'F\.data\s*==\s*["\']([^"\']+)["\']',
    ]
    found = set()
    for pattern in patterns:
        found.update(re.findall(pattern, content))
    return found

# Collect all callback_data from keyboards
keyboard_callbacks = set()
keyboards_dir = base / "keyboards"
for kb_file in keyboards_dir.glob("*.py"):
    keyboard_callbacks.update(find_callbacks(kb_file))

# Collect all handled callbacks from handlers
handled_callbacks = set()
for handler_file in handlers_dir.glob("**/*.py"):
    handled_callbacks.update(find_callbacks(handler_file))

# Check critical callbacks
critical_callbacks = [
    "menu:home",
    "menu:products",
    "menu:cart",
    "menu:customs",
    "action:noop",
]

for cb in critical_callbacks:
    test(
        f"callback_handled_{cb}",
        cb in handled_callbacks,
        f"Callback {cb} not found in handlers"
    )

# ===========================================================================
# TEST 3: START COMMAND
# ===========================================================================
print("\n--- START COMMAND ---")
menu_file = handlers_dir / "menu.py"
if menu_file.exists():
    content = menu_file.read_text(encoding='utf-8')
    test(
        "start_command_handler",
        "CommandStart" in content and "cmd_start" in content,
        "/start handler not found"
    )
    test(
        "start_has_welcome",
        "WELCOME" in content,
        "Welcome message not used in /start"
    )
    test(
        "start_has_membership_check",
        "MandatoryMembershipService" in content,
        "Membership check missing in /start"
    )
else:
    test("start_command_handler", False, "menu.py not found")

# ===========================================================================
# TEST 4: NAVIGATION STRUCTURE
# ===========================================================================
print("\n--- NAVIGATION ---")
common_kb = keyboards_dir / "common.py"
if common_kb.exists():
    content = common_kb.read_text(encoding='utf-8')
    test(
        "back_button_defined",
        "def back_button" in content,
        "back_button not defined"
    )
    test(
        "main_menu_keyboard",
        "def main_menu_keyboard" in content,
        "main_menu_keyboard not defined"
    )
    test(
        "pagination_keyboard",
        "def pagination_keyboard" in content,
        "pagination_keyboard not defined"
    )

# ===========================================================================
# TEST 5: CALLBACK ANSWERS
# ===========================================================================
print("\n--- CALLBACK ANSWERS ---")
handlers_without_answer = []
for handler_file in handlers_dir.glob("**/*.py"):
    if handler_file.name == "__init__.py":
        continue
    content = handler_file.read_text(encoding='utf-8', errors='ignore')
    
    # Find callback handlers
    callback_handlers = re.findall(
        r'@router\.callback_query.*?\nasync def (\w+)\([^)]*CallbackQuery[^)]*\):.*?(?=\n(?:async def|@router|$))',
        content,
        re.DOTALL
    )
    
    for handler_name in callback_handlers:
        # Check if this handler calls answer()
        handler_section = re.search(
            rf'async def {handler_name}\([^)]*\):.*?(?=\n(?:async def|@router|class |$))',
            content,
            re.DOTALL
        )
        if handler_section:
            handler_code = handler_section.group(0)
            if 'await callback.answer' not in handler_code and 'await event.answer' not in handler_code:
                handlers_without_answer.append(f"{handler_file.name}:{handler_name}")

if handlers_without_answer:
    warn(f"{len(handlers_without_answer)} callback handlers without answer(): {handlers_without_answer[:5]}")

test(
    "callback_answers_coverage",
    len(handlers_without_answer) < 10,  # Allow some missing
    f"Too many handlers without answer: {len(handlers_without_answer)}"
)

# ===========================================================================
# TEST 6: STATE MACHINES
# ===========================================================================
print("\n--- STATE MACHINES ---")
states_dir = base / "states"
if states_dir.exists():
    state_files = list(states_dir.glob("*.py"))
    test(
        "state_files_exist",
        len(state_files) > 0,
        "No state files found"
    )
    
    # Check if states are used in handlers
    states_used = False
    for handler_file in handlers_dir.glob("**/*.py"):
        content = handler_file.read_text(encoding='utf-8', errors='ignore')
        if 'from bot.states' in content or 'FSMContext' in content:
            states_used = True
            break
    
    test(
        "states_are_used",
        states_used,
        "State files exist but not used in handlers"
    )
else:
    warn("states directory not found")

# ===========================================================================
# TEST 7: ERROR HANDLING
# ===========================================================================
print("\n--- ERROR HANDLING ---")
handlers_with_try_catch = 0
total_handlers = 0

for handler_file in handlers_dir.glob("**/*.py"):
    if handler_file.name == "__init__.py":
        continue
    content = handler_file.read_text(encoding='utf-8', errors='ignore')
    
    # Count async def functions
    funcs = re.findall(r'async def \w+', content)
    total_handlers += len(funcs)
    
    # Count try-except blocks
    if 'try:' in content:
        handlers_with_try_catch += 1

error_handling_ratio = handlers_with_try_catch / max(1, len(list(handlers_dir.glob("**/*.py"))))
test(
    "error_handling_present",
    error_handling_ratio > 0.2,  # At least 20% of files have error handling
    f"Low error handling coverage: {error_handling_ratio:.1%}"
)

# ===========================================================================
# TEST 8: DEAD LINKS (unused callbacks)
# ===========================================================================
print("\n--- DEAD LINKS ---")
used_callbacks = keyboard_callbacks
handled_callbacks_clean = {cb for cb in handled_callbacks if not '{' in cb}

# Filter out parametric patterns
def is_parametric(cb):
    return '{' in cb or re.match(r'.*:\d+$', cb) or ':page:' in cb

potentially_dead = []
for cb in used_callbacks:
    if is_parametric(cb):
        continue
    if cb not in handled_callbacks:
        # Check if prefix is handled
        prefix = ':'.join(cb.split(':')[:2]) + ':'
        if not any(h.startswith(prefix) for h in handled_callbacks):
            potentially_dead.append(cb)

if potentially_dead:
    warn(f"Potentially dead links: {potentially_dead[:10]}")

test(
    "no_dead_links",
    len(potentially_dead) < 5,
    f"{len(potentially_dead)} unhandled callbacks found"
)

# ===========================================================================
# TEST 9: ADMIN PANEL
# ===========================================================================
print("\n--- ADMIN PANEL ---")
admin_dir = handlers_dir / "admin"
if admin_dir.exists():
    admin_files = list(admin_dir.glob("*.py"))
    test(
        "admin_handlers_exist",
        len(admin_files) > 5,
        f"Only {len(admin_files)} admin handler files found"
    )
    
    # Check for admin:panel callback
    test(
        "admin_panel_callback",
        "admin:panel" in handled_callbacks,
        "admin:panel callback not handled"
    )
else:
    test("admin_handlers_exist", False, "admin directory not found")

# ===========================================================================
# TEST 10: USER ACCOUNT FLOWS
# ===========================================================================
print("\n--- USER ACCOUNT ---")
account_handlers = ["my_account.py", "profile.py", "customer_info.py"]
for handler in account_handlers:
    file_path = handlers_dir / handler
    if file_path.exists():
        content = file_path.read_text(encoding='utf-8', errors='ignore')
        test(
            f"account_{handler}_has_callbacks",
            "callback_query" in content or "message" in content,
            f"{handler} has no handlers"
        )

# ===========================================================================
# SUMMARY
# ===========================================================================
print("\n" + "="*80)
print("SUMMARY")
print("="*80)
print(f"✅ Tests Passed:  {results['pass']}")
print(f"❌ Tests Failed:  {results['fail']}")
print(f"⚠️  Warnings:      {len(results['warnings'])}")
print(f"📊 Pass Rate:     {results['pass']/(results['pass']+results['fail'])*100:.1f}%")

if results['warnings']:
    print(f"\n⚠️  Warnings (first 10):")
    for w in results['warnings'][:10]:
        print(f"   - {w}")

print("="*80)
print(f"\n{'✅ ALL TESTS PASSED' if results['fail'] == 0 else '❌ SOME TESTS FAILED'}")
print("="*80)
