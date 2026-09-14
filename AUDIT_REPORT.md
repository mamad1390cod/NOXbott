# 🧪 NOXbott — Phase 2/3 Audit Report (handlers, FSM & callback wiring)

**Date:** 2026-09-14
**Scope:** end-to-end UI audit of every callback/message handler (real dispatcher, real `UnitOfWork`), FSM/navigation middlewares, discount-management panel, purchase flows, admin wizards.
**Test inventory (new, `tests/audit/`):** 24 tests — crawl/scanner (7), checkout+payment (7), discount admin UI (4), fixed-flow regressions (6).
**Result:** `tests/audit` → **24 passed**; repository suite → **47 passed / 3 failed**, where all 3 failures were reproduced on the pristine baseline `0ea2986` (pre-existing, unrelated).

| # | Bug | Severity | Status |
|---|-----|----------|--------|
| A1 | `account` router never registered → CODM purchase wizard + `action:cancel` dead | CRITICAL | FIXED |
| A2 | Navigation middleware wiped FSM data on wizard callbacks | HIGH | FIXED |
| A3 | Frozen model mutation `callback.data = …` (×7) crashed discount panel | HIGH | FIXED |
| A4 | Relative-SQLite `DATABASE_URL` crashed settings import | HIGH | FIXED |
| A5 | Discount «✏️ ویرایش» buttons had no handlers (dead UI) | HIGH | FIXED |
| A6 | aiogram filters written with Python `or` → dead commands/callbacks | MEDIUM | FIXED |
| A7 | Unvalidated callback payloads crashed 6 handlers | MEDIUM | FIXED |
| A8 | Payment approve/reject used unchecked `None` result | MEDIUM | FIXED |
| A9 | Dead `adisc:*` keyboard (unhandled callbacks) | LOW | FIXED |
| B1 | `cb_custom_winner_type` "set_data replaces dict" | — | NOT-A-BUG |
| B2 | `cb_admin_edit_user_info` update_data without set_state | — | NOT-A-BUG |
| C1 | FSM navigation prefix gaps (`cart:`/`orders:`/`pay:`/`tu:`/`atu:`) | LOW | REPORT-ONLY |
| C2 | Discount actions are not audit-logged (no `LogAction`) | LOW | REPORT-ONLY |
| C3 | Payment approval + audit log use two separate commits | LOW | REPORT-ONLY |

---

## 🔴 CRITICAL

### **BUG #A1 – The `account` router was never registered (dead purchase flow + dead cancel button)**
- **Location:** `bot/handlers/__init__.py::_build_user_router()` (missing `account.router`); flow start at `bot/handlers/products.py:122-133`; handlers in `bot/handlers/account.py`
- **Severity:** CRITICAL
- **Issue:** `products.py` starts the account-info wizard for products with `requires_account_info`:
```python
if product.requires_account_info:
    await state.set_data({"pending_product_id": product_id, "product_id": product_id})
    await state.set_state(AccountInfoStates.waiting_codm_username)
    await callback.message.answer("👤 نام کاربری CODM خود را ارسال کنید:")
```
  but the only module handling `AccountInfoStates` — and the only handler for `action:cancel` — is `bot/handlers/account.py`, whose router is not included anywhere. `grep -rn "handlers.account"` → 0 hits.
- **Impact:**
  - Account-type products cannot be purchased: the bot asks for the CODM username and then ignores every reply (no message handler for the state).
  - Every «❌ انصراف» button that emits `action:cancel` (`bot/keyboards/common.py:19`, `bot/handlers/custom_cart.py:192`, `bot/handlers/payments.py:199`) is a dead callback: the client spinner never stops and the flow keeps its state.
- **Scenario:** user buys an account product → prompt → user sends username → nothing happens; user presses cancel → nothing happens.
- **Fix:**
```python
from bot.handlers import (
    account,   # ← was missing
    cart, ...
)
...
        topup.router,
        account.router,   # ← registered (CODM wizard + action:cancel)
    ):
        r.include_router(sub)
```
- **Verified:** `tests/audit/test_fixed_flows.py::test_account_info_purchase_flow_reaches_cart` (wizard → cart item with `account_data`), `::test_cancel_button_aborts_the_flow` (state cleared, no data collected afterwards), and the callback scanner no longer reports `action:cancel` as unhandled.

---

## 🟠 HIGH

### **BUG #A2 – Navigation middleware cleared FSM data on wizard callbacks**
- **Location:** `bot/middlewares/fsm_navigation.py` (`_NAVIGATION_PREFIXES`, clear-before-handler)
- **Severity:** HIGH
- **Issue:** every callback starting with a navigation prefix (`admin:`, `acustom:`, `acat:`, `aprod:`, `auser:`, `aorder:`, `accat:`, …) had `state.clear()` executed *before* the handler ran. Multi-step wizards use callbacks with those same prefixes, so the data collected in earlier steps was gone by the time the step handler read it:
```python
data = await state.get_data()          # → {} (cleared by the middleware)
if not data.get("code") or not data.get("discount_type"):
    await callback.answer("❌ خطا: اطلاعات ناقص است", show_alert=True)
```
  A static scan found **21 handlers** whose trigger callbacks match a navigation prefix and which read FSM data (`admin_discounts` skip-steps, `admin_customs` start-message/prize steps, `admin_roles`, `admin_users`, `admin_categories`, `admin_custom_categories`, `admin_orders`, `admin_products`).
- **Impact:** broken admin wizards: the 6-step discount creation wizard failed at the first skip button, «تایید متن شروع» always answered «خطا: متن یافت نشد», role assignment and product-move flows lost their targets.
- **Fix:** the middleware keeps the stale-state protection but stops clobbering flows:
  1. `_STATEFUL_CALLBACKS` (existing) + new `_STATEFUL_PREFIXES` for flow steps (`admin:discount:type:`, `admin:discount:skip_`, `admin:discount:edit_field:`, `admin:roles:addrole:`, `admin:roles:setrole:`, `acustom:confirm_start_msg:`);
  2. for every other navigation callback, the handler now runs with the FSM intact and the state is cleared **afterwards, only if the handler never touched the FSM** (pure screen switch). Non-inspectable FSM doubles keep the legacy behaviour, so `tests/test_fsm_navigation.py` still passes.
- **Verified:** `/tmp/probe_fsm2.py` (6/6 scenarios with a real `MemoryStorage` context: wizard steps keep state+data, `admin:panel`/`menu:support` clear, self-clearing handler cleared once); `tests/test_fsm_navigation.py` 2 passed; `tests/audit/test_fixed_flows.py::test_custom_start_message_flow_survives_navigation`; the discount wizard + toggle tests are green.

### **BUG #A3 – Frozen aiogram models mutated with `callback.data = …` (7 sites)**
- **Location:** `bot/handlers/admin/admin_discounts.py` (activate, deactivate, delete-confirm, 4 skip-guards)
- **Severity:** HIGH
- **Issue:** the handlers faked a navigation by mutating the incoming update and re-dispatching:
```python
callback.data = f"admin:discount:view:{code_id}"
await cb_admin_discount_view(callback, uow, user)
```
  aiogram 3.31 models are frozen pydantic instances → `ValidationError: Instance is frozen`.
- **Impact:** «فعال/غیرفعال کردن», delete-confirmation and every skip-guard error path crashed with a validation error instead of refreshing the screen.
- **Fix:** extracted render helpers and call them directly (no re-dispatch, no mutation):
  `_discount_details_text(code)`, `_render_discount_menu(callback)`, `_render_discount_view(callback, code)`, `_render_discount_list(callback, page, uow)`; `grep "callback.data = " bot/` → 0 hits.
- **Verified:** `tests/audit/test_admin_discounts_ui.py::test_discount_activate_deactivate_roundtrip` and `::test_create_discount_code_wizard` (both previously failing with `Instance is frozen`).

### **BUG #A4 – Relative SQLite `DATABASE_URL` crashed the settings import**
- **Location:** `bot/config.py:134` (`validate_database_url`)
- **Severity:** HIGH
- **Issue:** `cls._PROJECT_DIR / db_path` reads a pydantic private attribute from the class object:
```
TypeError: unsupported operand type(s) for /: 'ModelPrivateAttr' and 'PosixPath'
```
  Triggered by any relative SQLite URL such as `sqlite+aiosqlite:///./noxbot.db` (the shape shown in `.env.example`). The bot cannot start at all.
- **Fix:** `db_path = get_project_root() / db_path` (module-level helper already imported).
- **Verified:** probe with `DATABASE_URL="sqlite+aiosqlite:///./noxbot.db"` → `sqlite+aiosqlite:////home/user/NOXbott/noxbot.db`.

### **BUG #A5 – Discount edit buttons were dead (no handlers)**
- **Location:** `bot/keyboards/discount_keyboard.py:99-114` emitted `admin:discount:edit:{id}` / `admin:discount:edit_field:{id}:{field}`; no handler existed
- **Severity:** HIGH
- **Issue:** the view screen's «✏️ ویرایش» button produced an unhandled callback; the whole edit flow (states `waiting_edit_*` and `DiscountCodeService.update_discount_code` already existed) was never wired.
- **Fix:** implemented the flow in `admin_discounts.py`: field menu (`waiting_edit_field`) → per-field prompt with the current value → per-field message handler (code/value/max_eligible/expiration/max_uses/description) → `update_discount_code()` → commit → state clear → refreshed detail screen. Invalid values keep the state and ask again (`ValueError` from the service is surfaced verbatim).
- **Verified:** `test_discount_edit_button_works` (click «ویرایش» → «مقدار» → send `25` → DB `discount_value == 25`).

---

## 🟡 MEDIUM

### **BUG #A6 – aiogram filters combined with Python `or` (4 sites, silently dead handlers)**
- **Location:** `bot/handlers/menu.py:55`, `bot/handlers/menu.py:85`, `bot/handlers/my_account.py:111`, `bot/handlers/profile.py:16`
- **Severity:** MEDIUM
- **Issue:** `F.data == "a" or F.data == "b"` evaluates the truthiness of the first `MagicFilter` and discards the second condition (aiogram expects the `|` operator):
```python
@router.callback_query(F.data == "noop" or F.data == "action:noop")   # ❌ action:noop dead
@router.message(F.text.lower() == "منو" or F.text == "/menu")          # ❌ /menu dead
@router.message(F.text == "/profile" or F.text == "/panel")            # ❌ /panel dead
```
- **Impact:** `/menu`, `/panel` and `action:noop` (the «1/2» page counters) were unhandled callbacks/commands — the spinner never stops and users see nothing.
- **Fix:** proper unions, e.g. `@router.callback_query((F.data == "noop") | (F.data == "action:noop"))`, `@router.message((F.text == "/profile") | (F.text == "/panel"))`, and a three-way union for `/account | /panel | /profile`.
- **Verified:** `tests/audit/test_fixed_flows.py::test_slash_commands_are_live`, `::test_noop_page_indicator_is_handled`.

### **BUG #A7 – Callback payloads parsed without validation (6 handlers crashed)**
- **Location:** `bot/handlers/admin/admin_abuse.py:186`, `admin_orders.py:110` + `:173` (`OrderStatus(val)`), `user_orders.py:87`, `admin_discounts.py:151`, `admin_membership.py:95,109`
- **Severity:** MEDIUM
- **Issue:** `int(parts[2])` / `OrderStatus(val)` on attacker- or stale-controlled callback data raised `ValueError` inside the handler (unhandled exception, no feedback for the user).
- **Fix:** defensive parsing that answers «دادههای نامعتبر»/«آیدی یافت نشد» and returns; `admin_roles.py`/`my_account.py` already used this pattern.
- **Verified:** the callback scanner probes **every** `callback_data` literal in `bot/` with a non-numeric placeholder (`probe-`) and fails on any exception — 7 passed after the fix (it previously failed with `ValueError: 'probe-' is not a valid OrderStatus`).

### **BUG #A8 – Payment approve/reject used an unchecked service result**
- **Location:** `bot/handlers/admin/admin_payments.py` (`cb_payment_approve`, `cb_payment_reject`)
- **Severity:** MEDIUM
- **Issue:** `PaymentService.approve_payment()` returns `None` for an unknown payment (it raises `ValueError` only for already-reviewed ones), but the handler continued:
```python
payment = await ps.approve_payment(payment_id, user.id)   # may be None
...
description=f"تایید پرداخت {format_price(payment.amount)}"   # AttributeError
```
- **Impact:** clicking a stale approve/reject button (payment already removed) crashed the handler; the admin received no feedback.
- **Fix:** `if not payment: await callback.answer("پرداخت یافت نشد", show_alert=True); return` in both handlers + a length guard on the reject payload split.
- **Verified:** `tests/audit/test_fixed_flows.py::test_stale_payment_buttons_answer_without_crashing`.

---

## 🟢 LOW

### **BUG #A9 – Dead `admin_discounts_keyboard()` (`adisc:add` / `adisc:list`)**
- **Location:** `bot/keyboards/admin.py:551` (deleted)
- **Severity:** LOW
- **Issue:** the keyboard builder emitted `adisc:*` callbacks but had zero references repo-wide and no handlers — dead buttons flagged by the static scan.
- **Fix:** removed the unused builder (the live panel uses `admin_discount_menu_keyboard()` → `admin:discount:*`).

---

## ⚪ NOT-A-BUG (checked, documented to prevent re-litigation)

### **B1 – `cb_custom_winner_type` "`set_data` drops `pick_custom_id`"**
`bot/handlers/admin/admin_customs.py:460-480`: `update_data(pick_custom_id=…)` runs only in the **player** branch, `set_data({"winner_custom_id": …})` only in the **team** branch. The branches are mutually exclusive, so nothing is dropped; the player branch's data is consumed by the `cpk:` handler (not navigation-prefixed → never cleared).

### **B2 – `cb_admin_edit_user_info` calls `update_data` without `set_state`**
`bot/handlers/admin/admin_users.py:232-247`: the follow-up step (`auser:editfield:`) re-seeds `admin_edit_user_id` and sets the edit state itself; the message handlers in `my_account.py:522-600` correctly resolve the target via `data.get("admin_edit_user_id") or user.id`. No functional impact.

---

## 📋 REPORT-ONLY / RESIDUAL RISKS (not changed)

### **C1 – FSM navigation prefix gaps (`cart:`/`orders:`/`pay:`/`tu:`/`atu:`) are deliberate**
13 handlers live under prefixes that `_NAVIGATION_PREFIXES` does not cover. Widening the list is **not** safe: those families contain multi-step flows that read data written by an earlier callback — e.g. `tu:amt:` stores `topup_amount` (`topup.py:101`) and later `tu:m:crypto` reads it (`topup.py:127`), so clearing on `tu:` would break top-ups. The correct long-term fix is per-flow navigation detection (explicit screen-entry set) rather than more prefixes; the A2 fix already resolves the reverse failure (wizard data being wiped).

### **C2 – Discount actions are not audit-logged**
`bot/models/log.py` has no discount `LogAction` value, so `admin_discounts.py` create/edit/delete/toggle write no `admin_logs` entry (every other admin module does). Adding an enum value touches the model/schema → needs approval; left as-is.

### **C3 – Payment approval commits twice**
`admin_payments.py`: `await uow.commit()` (payment) and a second commit for the audit log. A crash between the two leaves an approved payment without its log entry; a single transaction would be atomic. Behaviour unchanged, flagged for a later refactor.

---

## ✅ Verification appendix (exact commands)

```bash
# audit suite (isolated throwaway SQLite, no repo data touched)
python3 -m pytest tests/audit -q                      # → 24 passed in 57.35s
python3 -m pytest tests/audit/test_ui_crawl.py -q     # → 7 passed  (was 6 passed / 1 failed)
python3 -m pytest tests/audit/test_admin_discounts_ui.py -q   # → 4 passed (was 3 failed / 1 passed)
python3 -m pytest tests/audit/test_fixed_flows.py -q  # → 6 passed (new)

# middleware unit tests (repo)
DATABASE_URL="sqlite+aiosqlite:////tmp/x.db" … python3 -m pytest tests/test_fsm_navigation.py -q  # → 2 passed

# repository suite on a throwaway DB
DATABASE_URL="sqlite+aiosqlite:////tmp/repo_tests.db" … python3 -m pytest tests/ -q --ignore=tests/audit
# → 47 passed, 3 failed — all 3 reproduced on the pristine baseline commit 0ea2986:
#   tests/test_regressions.py::test_wallet_reference_is_unique
#   tests/test_regressions.py::test_same_order_concurrent_wallet_callbacks_charge_once
#   tests/test_load_stress.py::test_500_concurrent_customer_checkouts_and_admin_reads
```

**Data-safety note:** an early repository-suite run in this session was started with `DATABASE_URL` pointing at the bundled `noxbot.db`. Row counts and recorded aggregates were re-checked immediately afterwards and are unchanged (orders 8 — 4 REFUNDED/4 CANCELLED; payments 8; transactions 27 with identical per-type sums), and every later run used a throwaway database. No production data was modified.

**Still open (next phases):** remaining handler reads (`admin_orders.py`, `topup.py`, `custom_cart.py`, `my_account.py`, remaining `admin/*`), ruff scope decision (231 pre-existing findings in `admin_discounts.py` alone), and the C1/C2/C3 items above.
