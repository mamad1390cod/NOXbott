# 🧪 NOXbott — Audit Report

## Phase 2/3 — handlers, FSM & callback wiring

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


---

# 🧪 PHASE 4 — customer flows (deep read, root fixes, proof tests)

Scope: every **customer-facing** handler was read end-to-end and checked against the
running dispatcher (`menu`, `my_account`, `topup`, `cart`, `payments`, `checkout`,
`user_orders`, `custom_cart`, `customs`, `configs`, `products`, `customer_info`,
`support`, `notify_prefs`). Fixes are root fixes; each one has a test that **fails
on the pre-fix source** (proof runs in the appendix).

## 🔴 CRITICAL / 🟠 HIGH

### **BUG #D1 – Raw `edit_text`/`edit_media` + swallowed "message is not modified" = silent handler abort**
**Where:** `bot/handlers/menu.py` (3 sites), `bot/handlers/my_account.py` (`_render_menu`), `bot/handlers/products.py`, `bot/handlers/configs.py`, `bot/handlers/customs.py` (banner screens), `bot/middlewares/mandatory_membership.py` (gate).

**Symptom:** tapping a button a second time (or navigating back to the screen you are already on) left the button **spinning forever** — no reply, no error — while the log stayed clean.

**Root cause (two halves, both required):**
1. `safe_edit_*` helpers existed, but these paths still called the raw `callback.message.edit_text/edit_media`. Telegram rejects an edit whose text is identical to the current message with `BadRequest: message is not modified`.
2. `bot/middlewares/user_context.py:65-79` catches exactly that `TelegramBadRequest` and **returns `None`** (a deliberate anti-noise guard). The exception therefore never reaches the dispatcher, and — because the aborted coroutine is gone — **the rest of the handler never executes**: the trailing `await callback.answer()` is skipped, which is why the tap is never acknowledged.

So the defect is not "a raised exception" but a *silently truncated handler*. It is invisible in logs and reproducible only by tapping twice.

**Fix:** `safe_edit_media()` added to `bot/utils/editing.py` (mirrors `safe_edit_text`/`safe_edit_caption`, returns `bool`), and every customer-facing screen now edits through the `safe_edit_*` family, which detects "not modified" and keeps the handler alive so the tap is answered.

**Proof:** `test_rerender_of_identical_screen_survives`, `test_rerender_of_identical_banner_screens_survives` — both drive the real dispatcher with `sim.session.fail_edit_not_modified = True` (as Telegram does) and assert an `AnswerCallbackQuery` is emitted. Both fail on the pre-fix sources.

## 🟡 MEDIUM

### **BUG #D2 – Dead button after the first-purchase info wizard**
**Where:** `bot/handlers/customer_info.py:119` emitted `callback_data="cart:view"`; no handler in the codebase filters it (dead payload → the tap falls through to the dispatcher's unhandled-callback probe and the button spins).
**Fix:** emit the live `menu:cart` (same destination as every other cart entry point).
**Proof:** `test_first_purchase_cart_button_is_live`.

### **BUG #D3 – `OrderService.refund_order` re-implemented the refund (and skipped the wallet credit)**
**Where:** `bot/services/order.py` carried its own refund body next to `bot/services/refund.py`.
**Impact:** a second, divergent implementation of money movement — the customer's wallet was not credited through the canonical path, so refunds could leave the balance and the ledger inconsistent.
**Fix:** `OrderService.refund_order` now **delegates** to `RefundService.refund_order` (single implementation, single audit trail).
**Proof:** `test_refund_credits_customer_wallet` — wallet 200 000 → 150 000 after a 50 000 wallet purchase → **200 000** again after the refund; the test reads the persisted balance, not the return value.

### **BUG #D4 – Closing a ticket left a stale screen with now-dead actions**
**Where:** `bot/handlers/support.py` (`cb_ticket_close`) + `bot/keyboards/ticket.py` (`ticket_detail_keyboard`).
**Symptom:** after «✅ تکمیل شد» the user still saw the ticket rendered as open with «✍️ پاسخ» / «✅ تکمیل شد» buttons; «پاسخ» is explicitly rejected for closed tickets, so the visible buttons no longer did anything useful. The handler also fired a second edit carrying only `"✅ تیکت بسته شد."`, which **overwrote the detail screen** and — because it passed no `reply_markup` — left the old keyboard attached (Telegram keeps the previous keyboard when an edit omits one).
**Fix:** detail rendering extracted to one helper (`_ticket_detail_text`), `ticket_detail_keyboard(..., closed=...)` drops the reply/close row for closed tickets, `cb_ticket_close` refreshes the screen with the closed state, and the contradictory second edit was removed (the tap still gets its toast).
**Proof:** `test_ticket_close_refreshes_and_hides_actions` — asserts the **effective message state** (text says «بسته», neither `ticket:reply:` nor `ticket:close:` is offered). Fails on the pre-fix source.

## 🟢 LOW

### **BUG #D5 – Wallet ledger signed entries from a hard-coded type list**
**Where:** `bot/handlers/my_account.py:440` — `sign = '+' if t.type.value in ('deposit','reward','refund','topup','admin_credit') else '-'`.
**Why it is wrong:** every ledger writer stores **signed** amounts (debits negative — `WalletPaymentService.deduct_wallet`, `RefundService`, `ADMIN_DEBIT`; credits positive), so the amount is already the authoritative sign. The list is a duplicate of that information and silently mislabels any type missing from it — `TransactionType.ADJUSTMENT` (a legitimate credit) renders with a minus, and every type added later inherits the bug.
**Fix:** derive the sign from the amount (`'+' if t.amount >= 0 else '-'`), keeping `abs()` for the display.
**Proof:** `test_wallet_ledger_signs_credits_from_amount` — an `ADJUSTMENT` credit of 25 000 renders `+۲۵,۰۰۰`, a `SPEND` of 10 000 renders `-۱۰,۰۰۰`. Fails on the pre-fix source and passes after (verified in isolation: reverting only `my_account.py` fails exactly this test).

### **BUG #D6 – Dead code in the custom-registration flow**
`bot/handlers/custom_cart.py` contained a no-op accumulation (`total += 0`) in the price summary → removed (no behaviour change).

## 🟠 HIGH — payment wiring, second pass (explicitly approved by the owner)

### **BUG #D7 – 🔺 the card-payment-for-order flow was unreachable (the old O1)**
**Where:** `bot/handlers/payments.py` (`pay:submit:`), `bot/keyboards/cart_keyboard.py` (`insufficient_balance_keyboard`), `bot/handlers/cart.py` (insufficient-balance screen), `bot/keyboards/order.py` (order detail), `bot/handlers/admin/admin_payments.py` (resend request).

**Symptom:** a customer whose wallet balance was too low was told «موجودی کیف پول کافی نیست» and had **no path to buy at all** — while the entire card-receipt flow behind it (payment record, receipt upload, anti-abuse check and the admin approval that drives the order to APPROVED) was already implemented and working. A full payload census (170 exact filters, 162 prefix filters, 41 helper-emitted literals) confirmed that **nothing** emitted `pay:submit:<order_id>`.

**Root cause:** the customer-facing entry point was never written, and the lifecycle lacked the step it needs: `create_order_from_cart` leaves an order in `PENDING`, while `PENDING → PAYMENT_UPLOADED` is not a legal transition — so even after sending a receipt the order could not advance (`submit_payment` raised, the receipt handler swallowed the error, and the order silently stayed PENDING while an admin was reviewing its receipt).

**Fix (approved payment-wiring change):**
* `insufficient_balance_keyboard` offers «💳 پرداخت کارتی و ارسال رسید» (`checkout:card`) and the cart screen text explains both options;
* new handler `cb_checkout_card` creates the order with `create_order_from_cart(payment_method=CARD)`, moves it `PENDING → WAITING_PAYMENT` (the legal "instructions shown" step) and enters the receipt flow;
* the receipt body is a single shared helper (`_begin_receipt_submission`) used by **both** `checkout:card` and `pay:submit:` — no duplicated flow;
* the wallet-checkout screen shows the same choice screen instead of a bare alert with no way forward;
* the order-detail keyboard offers «💳 ارسال رسید پرداخت» (`pay:submit:<order_id>`) for an unpaid card order, so the flow survives a lost conversation;
* `OrderService.submit_payment` advances a still-`PENDING` order to `WAITING_PAYMENT` first, so no caller can leave an order silently stuck;
* «🔄 درخواست رسید مجدد» (admin) now attaches the `pay:submit:` button — previously it told the customer to send a new receipt **with no way to do it**;
* the rejection path releases the order to CANCELLED (verified) and notifies the customer.

**Proof:** `tests/audit/test_checkout_payment.py` — `test_insufficient_balance_screen_offers_card_payment`, `test_card_checkout_creates_order_and_accepts_receipt`, `test_admin_approval_completes_the_card_order`, `test_request_receipt_again_reaches_the_customer`. All fail on the pre-fix sources with the tests unchanged. Assertions are on persisted state: order `WAITING_PAYMENT → PAYMENT_UPLOADED → APPROVED`, payment `PENDING → APPROVED`, stock consumed exactly once (5 → 4) and **the wallet untouched** (a card payment must not debit it).

### **BUG #D8 – Admin payment review crashed on a lazy relationship (`MissingGreenlet`)**
**Where:** `bot/handlers/admin/admin_payments.py` — `payment.user` accessed directly in the approve / reject / request-again notifications.

**Symptom:** the admin's tap was never answered and the customer was never notified. Found by the new test `test_rejecting_a_card_payment_notifies_without_crashing`, which failed with `sqlalchemy.exc.MissingGreenlet` at `admin_payments.py:211` before the fix.

**Root cause:** `payment.user` is a lazy relationship; touching it inside an async handler performs IO outside the greenlet and raises. The approve path happened to receive an eager-loaded payment (which is why approval worked), the reject and request-again paths did not.

**Fix:** one helper — `_notify_payment_customer()` — resolves the customer with an explicit awaited `uow.users.get(payment.user_id)` and is used by all three notification sites, so the lazy load can no longer be reached.

**Proof:** `test_request_receipt_again_reaches_the_customer` (MissingGreenlet pre-fix; also asserts the customer receives a working button) and `test_rejecting_a_card_payment_notifies_without_crashing`.

## ⚪ NOT-A-BUG (checked, documented to prevent re-litigation)

### **B3 – "Cancelling an order leaves a stale screen"**
`bot/handlers/user_orders.py:141-142` already re-renders via `safe_edit_text("سفارش لغو شد.")` plus a home button. A patch was written, **verified unnecessary and reverted**; the file is byte-identical to the baseline commit.

### **B4 – `notify_prefs.py` toggles**
`bot/handlers/notify_prefs.py` answers each toggle with an alert and never edits the message, so the D1 class cannot trigger there. Read, no change.

## 📋 REPORT-ONLY / RESIDUAL RISKS (not changed)

### **O1 – ✅ RESOLVED (was: `pay:submit:<order_id>` had no producer)**
See **BUG #D7 / #D8** above. The card-payment-for-order flow is reachable and verified
end to end; wiring it up (rather than deleting it) was an explicit decision by the
owner, which is why payment logic was touched at all.

### **O2 – The same D1 bug class still exists in 4 admin-scope sites (deferred to the admin phase)**
`admin_backup.py:35`, `admin_backup.py:89`, `admin_orders.py:642`, `admin_payments.py:117` still call raw `edit_text`/`edit_media`. They are not customer flows, so they were left untouched and are listed here so the class is not lost.

### **O3 – Hygiene backlog: 19 pre-existing ruff findings (`E9`/`F`/`B`)**
Measured on the changed files: **baseline HEAD = 20, after Phase 4 = 19** — i.e. **zero introduced**, one removed. They are 18 auto-fixable unused imports/assignments (`F401`/`F841`) plus one hidden fix; sweeping them is a separate, repo-wide decision, not a Phase-4 change.

---

# 🧪 PHASE 5 — admin flows (in progress)

Scope: the admin surface (`bot/handlers/admin/*`, ~8 200 lines) plus the shared
editing helpers both surfaces depend on. Same method as Phase 4: read → root
fix → one proof test per defect that **fails on the pre-fix source**.

## 🟠 HIGH

### **BUG #D9 – a message's type cannot be changed: banner screens never rendered**
**Where:** every screen that shows a photo — `products.py`, `configs.py`, `customs.py` (customer) and `admin_payments.py` (receipt review) — through `bot/utils/editing.py`.

**Symptom:** tapping a product **that has a banner image** did *nothing*: the tap was answered, the log stayed clean, and the product list stayed on screen. Same for configs, customs and the admin payment review opened from its (text) list. The item could not be opened at all.

**Root cause:** Telegram refuses `editMessageMedia` on a message that is not a media message ("Bad Request: there is no media in the message to edit") and refuses `editMessageText` on a media message ("there is no text in the message to edit"). The screens used "edit in place" navigation, so a text list could never become a photo detail and vice versa. `safe_edit_media` (Phase 4) caught the error and returned `False` — which stopped the *crash* but also stopped the *render*: the helper told the handler "fine", the handler answered the tap, and the user saw the old screen.

**Fix — one root fix in `bot/utils/editing.py`:**
* `message_has_media()` tells the two message kinds apart from the callback's own message;
* when the target content cannot fit the current message type, the screen is **replaced**: the new message is sent first, the old one deleted afterwards (best-effort), so a failed delete still leaves a usable screen;
* `safe_edit_text`, `safe_edit_caption` and `safe_edit_media` all use it, so *every* screen — customer and admin — switches type correctly;
* the specific BadRequest texts are still handled defensively for messages the bot did not send.

**Proof:** `tests/audit/test_phase5_admin.py::test_banner_reached_from_a_text_list_renders`, `::test_text_list_rendered_again_after_a_banner`, `::test_admin_payment_detail_with_receipt_renders` — and the Phase-4 banner test, strengthened into two passes (render, then identical re-render). All fail on the pre-fix sources with the real Telegram error in the log.

### **BUG #D10 – the same "silent abort" class in the admin surface (4 raw edits)**
**Where:** `admin_backup.py:35` (backup menu), `admin_backup.py:89` (upload instructions), `admin_orders.py:642` (post-refund refresh), `admin_payments.py:142` (receipt review).

**Symptom / root cause:** identical to **#D1** — a raw `edit_text`/`edit_media` that Telegram answers with "message is not modified" is swallowed by `UserContextMiddleware`, so the rest of the handler never runs: the admin's tap stays unanswered and the button spins. Re-opening the backup menu or the upload screen (their buttons stay on the keyboard) reproduced it on every second tap.

**Fix:** all four sites use the `safe_edit_*` family; a dead assignment (`refunded_order`) in the refund handler was removed at the same time.

**Proof:** `test_backup_menu_rerender_survives`, `test_backup_upload_screen_tapped_twice_survives`, `test_admin_payment_detail_rerender_survives` — all three inject Telegram's exact error (`fail_edit_not_modified`) and assert the tap is still answered. All fail on the pre-fix sources.

## 📊 Test-infrastructure upgrades (this phase)

1. **Telegram's edit rules are now modelled** (`tests/audit/harness.py`): editing *text* into a media message, *media* into a text message, or a *caption* into a text message raises exactly what Telegram raises. Without this the fake session happily "edited" anything, which is why this whole bug class was invisible.
2. **The callback carries a faithful message**: `click()` now hands the handler the message the button really sits on (photo + caption for a banner screen, text otherwise), so the app can tell the two cases apart — as it must in production.
3. Message state tracks media-ness, and `DeleteMessage` clears it (needed for the replace path).

Thank you — the crawl test and every existing suite still pass with the stricter semantics (`tests/audit` → 41 passed).

### ✅ Phase 5 verification appendix (so far)

```bash
# phase-5 proof tests (admin surface + message-type switching)
python3 -m pytest tests/audit/test_phase5_admin.py -q
#   → 11 passed in 12.73s

# admin order lifecycle (approve / transitions / logging / refund / cleanup)
python3 -m pytest tests/audit/test_phase5_orders.py -q
#   → 3 passed in 7.05s
# pre-fix admin_orders.py, tests unchanged → 2 failed, 1 passed  (D11)
# pre-fix sources, tests unchanged → 6 failed (the real Telegram error is visible:
#   "Bad Request: there is no media in the message to edit")

# the Phase-4 banner test, now two passes (render, then identical re-render)
python3 -m pytest tests/audit/test_phase4_flows.py -q
#   → 6 passed in 8.64s
# pre-fix editing.py → 1 failed (the banner never renders)

# full audit suite with the stricter harness semantics
python3 -m pytest tests/audit -q
#   → 49 passed in 70.69s         (was 41 before this batch)

# repository suite on a throwaway DB
DATABASE_URL="sqlite+aiosqlite:////tmp/repo_p5.db" python3 -m pytest tests/ -q --ignore=tests/audit
#   → 47 passed, 3 failed — unchanged (the same three pre-existing failures)
```

### **BUG #D11 – «✅ تایید پرداخت» on the order screen did nothing (unreachable transition)**
**Where:** `bot/handlers/admin/admin_orders.py` — `cb_aorder_approve` (the order detail's approve button).

**Symptom:** for an order waiting for review (`PAYMENT_UPLOADED`) — exactly the state in which the keyboard shows «✅ تایید پرداخت» — tapping it changed nothing. The admin saw a short alert (`انتقال غیرمجاز از payment_uploaded به approved`), the order stayed `PAYMENT_UPLOADED`, the payment stayed `PENDING`, and **no audit-log row was written** (the log line sits after the failed transition).

**Root cause:** the button ran a bare `transition_to(order, APPROVED)`, but `TRANSITIONS[PAYMENT_UPLOADED]` only allows `PAYMENT_REVIEWING / CANCELLED / REJECTED`. Approving from "receipt uploaded" requires walking `uploaded → reviewing → approved` — which is precisely what the *payment* route already does (`OrderService.approve_payment` → `_advance_to`). The order route simply never used it, so the button's primary use case was a no-op.

**Fix:** `cb_aorder_approve` now performs the domain operation `OrderService.approve_payment(...)`, which walks the legal steps, marks the payment record approved, and is the same code path the payment-review button uses. The shared post-transition tail (audit log + refreshed detail screen) was extracted into `_after_transition()` so both routes log and re-render identically.

**Proof:** `tests/audit/test_phase5_orders.py::test_admin_approve_then_deliver_completes_the_lifecycle` (APPROVED → PREPARING → DELIVERED through the real buttons, and the payment must end APPROVED) and `::test_admin_transitions_are_logged` — both fail on the pre-fix source; the second one is what exposed the silent "no audit log" consequence.

### ✅ Verified-correct (read + tested, no change needed)

* **Top-up approval is safe against double-credit** (`TopUpService.approve_request`): status guard + idempotency key (`topup:<tracking_code>`, checked via `transactions.find_by_ref_id`) + user row lock (`users.get_with_lock`). Re-approving raises instead of paying twice.
* **Admin refund pays exactly once**: `test_admin_refund_credits_the_customer_wallet` approves a card payment (wallet untouched, as a card purchase must be) and refunds it — balance becomes exactly the order amount, and a second refund tap does **not** pay again (`RefundService` guards, and the status has moved to REFUNDED).
* **Destructive tools delete only what they promise**: `test_cleanup_deletes_completed_orders_but_keeps_the_money` — «پاک‌سازی سفارش‌های تکمیل‌شده» removes COMPLETED orders, keeps an APPROVED one, and leaves both the wallet balance and the financial ledger untouched; `test_cleanup_requires_the_permission` — a non-owner without `DELETE_ORDERS` cannot run it.
* **Lazy-relationship class (#D8) re-checked across the admin surface**: every `.user` access in `admin_customs.py`, `admin_abuse.py`, `admin_tickets.py`, `admin_topup.py` and `admin_orders.py` is fed by a repository method that eager-loads it (`selectinload`), so no further `MissingGreenlet` crash exists there.

### 🧪 Additional semantic guards added this batch

`tests/audit/test_phase5_admin.py` (6 → 11) and `tests/audit/test_phase5_orders.py` (3, new):
admin ticket reply reaches the customer and moves the status; the admin-side ticket close lands
and drops the stale «پاسخ» button; cancelling a custom notifies every registered player; the
admin order lifecycle (approve → prepare → deliver, payment marked approved); audit logging;
refund single-credit; cleanup safety + permission guard. These are **regression guards** (they
pass on today's code) — the defect-pinning proofs are the ones called out per bug above.

**Still to read in this phase:** the admin handlers themselves — `admin_topup.py`
(top-up approval, money), `admin_orders.py`, `admin_customs.py`, `admin_tickets.py`
and the rest of `admin/*`, including the lazy-relationship class that produced
**#D8** (a candidate pattern: `admin_topup.py:186`, `admin_tickets.py:139/184`,
`admin_customs.py:400/432/554`, `admin_orders.py:531-557` all touch `.user`).

## 📊 Test-infrastructure upgrades that make this phase provable

A test that does not model Telegram's real behaviour cannot see this class of bug, so the harness was corrected first:

1. **Keyboard retention on edit** (`tests/audit/harness.py`): Telegram keeps the previous `reply_markup` when an edit omits it — the fake session now tracks per-message state and applies the same rule, so stale action buttons are visible to assertions (`test_ticket_close_*` fails without this).
2. **`fail_edit_not_modified` now also covers `EditMessageMedia`**, and the tests assert `AnswerCallbackQuery` is still emitted (the actual user-visible symptom).
3. **Crawler literal extraction** (`tests/audit/test_ui_crawl.py`) also scans the `back_button`/`home_button`/`get_cancel_button` helper literals — this is how D2 (`cart:view`) stays dead-button-proof.
4. **DB factories** (`tests/audit/db.py`): `make_custom`, `make_custom_category`, and `image_url` support on `make_product`/`make_config`, so banner/detail paths are testable.

## ✅ Phase 4 verification appendix (exact commands & results)

```bash
# customer-flow proof tests
python3 -m pytest tests/audit/test_phase4_flows.py -q
#   → 6 passed in 10.19s
# proof they pin real defects (pre-fix sources restored from git, tests unchanged):
#   → 5 failed / 1 passed  (all six assertions above fail without their fix)
# isolation proof for D5: revert only bot/handlers/my_account.py
#   → 1 failed (test_wallet_ledger_signs_credits_from_amount), 5 passed

# full audit suite (throwaway SQLite, no repo data touched)
python3 -m pytest tests/audit -q
#   → 35 passed in 38.70s          (was 24 passed before this phase)

# card-payment wiring (D7/D8) — in the file that never exercised this flow before
python3 -m pytest tests/audit/test_checkout_payment.py -q
#   → 12 passed in 13.20s
# pre-fix sources, tests unchanged → 5 failed, 7 passed
#   (insufficient-balance screen, card checkout, admin approval,
#    request-receipt-again, reject-notification)

# repository suite on a throwaway DB
BOT_TOKEN=… OWNER_ID=… ADMIN_PASSWORD=… \
DATABASE_URL="sqlite+aiosqlite:////tmp/repo_p4.db" \
python3 -m pytest tests/ -q --ignore=tests/audit
#   → 47 passed, 3 failed — unchanged from the Phase 2/3 baseline
#     (test_wallet_reference_is_unique, test_same_order_concurrent_wallet_callbacks_charge_once,
#      test_500_concurrent_customer_checkouts_and_admin_reads; all pre-existing, reproduced on 0ea2986)

# static check on every file this phase touched
python3 -m ruff check --select E9,F,B <changed files>
#   → 19 findings, all pre-existing (HEAD baseline on the same files: 20) — no new ones
```

**Data-safety note:** Phase 4 was developed and verified exclusively against throwaway SQLite
databases under `/tmp` (`/tmp/repo_p4.db`, plus the per-test DBs created by `tests/audit`).
The production `noxbot.db` was **not** present in this environment during Phase 4 (see the
workspace note below), so no production row could be read or modified.

**Workspace warning (operational, not a code defect):** the sandbox restored from a snapshot
took the working tree back to commit `fdddb0e` and, because `.gitignore` excludes `*.db`,
`backups/`, `exports/` and `logs/`, those **untracked runtime artifacts are gone from the
sandbox**. They were never part of git (`git check-ignore` confirms `noxbot.db` is ignored),
so anything committed and pushed (`5ff63e4` and this phase) is intact; the live database and
the log/backup folders only exist on the machine that runs the bot.
