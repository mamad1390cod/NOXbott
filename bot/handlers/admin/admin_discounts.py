"""Admin discount code management handlers."""

import logging
from datetime import datetime, timezone

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from bot.keyboards.common import back_button
from bot.keyboards.discount_keyboard import (
    admin_discount_cancel_keyboard,
    admin_discount_confirm_delete_keyboard,
    admin_discount_edit_keyboard,
    admin_discount_list_keyboard,
    admin_discount_menu_keyboard,
    admin_discount_skip_keyboard,
    admin_discount_type_keyboard,
    admin_discount_view_keyboard,
)
from bot.models.discount_code import DiscountType
from bot.services.discount_code import DiscountCodeService
from bot.states import AdminDiscountCodeStates
from bot.utils.editing import safe_edit_text
from bot.utils.format import format_price

router = Router(name="admin_discounts")
logger = logging.getLogger(__name__)

ITEMS_PER_PAGE = 10

# «✏️ ویرایش» field → FSM state that collects the new value.
_EDIT_FIELD_STATES = {
    "code": AdminDiscountCodeStates.waiting_edit_code,
    "value": AdminDiscountCodeStates.waiting_edit_value,
    "max_eligible": AdminDiscountCodeStates.waiting_edit_max_eligible,
    "expiration": AdminDiscountCodeStates.waiting_edit_expiration,
    "max_uses": AdminDiscountCodeStates.waiting_edit_max_uses,
    "description": AdminDiscountCodeStates.waiting_edit_description,
}


def _discount_details_text(code) -> str:
    """Build the discount-code detail screen text."""
    status = "✅ فعال" if code.is_active else "❌ غیرفعال"
    type_text = "درصدی" if code.discount_type == DiscountType.PERCENTAGE else "مبلغ ثابت"
    if code.discount_type == DiscountType.PERCENTAGE:
        value_text = f"{code.discount_value}%"
    else:
        value_text = f"{format_price(code.discount_value)} تومان"

    expiry_text = "ندارد"
    if code.expires_at:
        if code.is_expired:
            expiry_text = f"❌ منقضی شده ({code.expires_at.strftime('%Y-%m-%d %H:%M')})"
        else:
            expiry_text = code.expires_at.strftime("%Y-%m-%d %H:%M")

    max_uses_text = "نامحدود" if code.max_uses is None else f"{code.max_uses} بار"
    remaining_text = "نامحدود" if code.remaining_uses is None else f"{code.remaining_uses} بار"

    max_eligible_text = "ندارد"
    if code.max_eligible_amount:
        max_eligible_text = f"{format_price(code.max_eligible_amount)} تومان"

    desc_text = code.description or "ندارد"

    return (
        f"🎟 <b>جزئیات کد تخفیف</b>\n\n"
        f"📝 کد: <code>{code.code}</code>\n"
        f"📊 وضعیت: {status}\n"
        f"🔢 نوع: {type_text}\n"
        f"💰 مقدار: {value_text}\n"
        f"💳 حداکثر مبلغ مجاز: {max_eligible_text}\n"
        f"📅 انقضا: {expiry_text}\n"
        f"🔢 حداکثر استفاده: {max_uses_text}\n"
        f"✅ استفاده شده: {code.usage_count} بار\n"
        f"⏳ باقی‌مانده: {remaining_text}\n"
        f"📄 توضیحات: {desc_text}\n"
        f"📆 ایجاد شده: {code.created_at.strftime('%Y-%m-%d')}"
    )


async def _render_discount_menu(callback: CallbackQuery) -> None:
    """Render the discount management menu (does not answer the callback)."""
    text = (
        "🎟 <b>مدیریت کدهای تخفیف</b>\n\n"
        "از این بخش می‌توانید کدهای تخفیف ایجاد و مدیریت کنید."
    )
    await safe_edit_text(callback, text, reply_markup=admin_discount_menu_keyboard())


async def _render_discount_view(callback: CallbackQuery, code) -> None:
    """Render the detail screen of an already-loaded discount code."""
    await safe_edit_text(
        callback,
        _discount_details_text(code),
        reply_markup=admin_discount_view_keyboard(code),
    )


async def _render_discount_list(callback: CallbackQuery, page: int, uow) -> None:
    """Render one page of the discount-code list (does not answer the callback)."""
    discount_service = DiscountCodeService(uow)

    total_count = await discount_service.count_discount_codes(active_only=False)
    total_pages = (total_count + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE

    codes = await discount_service.list_discount_codes(
        offset=page * ITEMS_PER_PAGE,
        limit=ITEMS_PER_PAGE,
        active_only=False,
    )

    if not codes:
        await safe_edit_text(
            callback,
            "❌ هیچ کد تخفیفی یافت نشد.",
            reply_markup=admin_discount_menu_keyboard(),
        )
        return

    text = f"📋 <b>لیست کدهای تخفیف</b>\n\n📊 تعداد کل: {total_count}"

    await safe_edit_text(
        callback,
        text,
        reply_markup=admin_discount_list_keyboard(codes, page, total_pages),
    )


@router.callback_query(F.data == "admin:discounts")
async def cb_admin_discount_menu(callback: CallbackQuery, uow, user) -> None:
    """Show admin discount code management menu."""
    await _render_discount_menu(callback)
    await callback.answer()


@router.callback_query(F.data == "admin:discount:menu")
async def cb_admin_discount_menu_back(callback: CallbackQuery, state: FSMContext, uow, user) -> None:
    """Return to discount menu (clearing state)."""
    await state.clear()
    await _render_discount_menu(callback)
    await callback.answer()


@router.callback_query(F.data.startswith("admin:discount:list"))
async def cb_admin_discount_list(callback: CallbackQuery, uow, user) -> None:
    """List all discount codes with pagination."""
    parts = callback.data.split(":")
    try:
        page = int(parts[3]) if len(parts) > 3 else 0
    except ValueError:
        await callback.answer("داده‌های نامعتبر", show_alert=True)
        return

    await _render_discount_list(callback, page, uow)
    await callback.answer()


@router.callback_query(F.data.startswith("admin:discount:view:"))
async def cb_admin_discount_view(callback: CallbackQuery, uow, user) -> None:
    """View a specific discount code."""
    code_id = callback.data.split(":", 3)[3]
    discount_service = DiscountCodeService(uow)
    code = await discount_service.get_discount_code(code_id)
    if not code:
        await callback.answer("کد تخفیف یافت نشد", show_alert=True)
        return

    await _render_discount_view(callback, code)
    await callback.answer()


@router.callback_query(F.data.startswith("admin:discount:activate:"))
async def cb_admin_discount_activate(callback: CallbackQuery, uow, user) -> None:
    """Activate a discount code."""
    code_id = callback.data.split(":", 3)[3]
    
    discount_service = DiscountCodeService(uow)
    code = await discount_service.activate_discount_code(code_id)
    
    if not code:
        await callback.answer("کد تخفیف یافت نشد", show_alert=True)
        return
    
    await uow.commit()
    await callback.answer("✅ کد تخفیف فعال شد")

    # Refresh the detail screen with the new status.
    await _render_discount_view(callback, code)


@router.callback_query(F.data.startswith("admin:discount:deactivate:"))
async def cb_admin_discount_deactivate(callback: CallbackQuery, uow, user) -> None:
    """Deactivate a discount code."""
    code_id = callback.data.split(":", 3)[3]
    
    discount_service = DiscountCodeService(uow)
    code = await discount_service.deactivate_discount_code(code_id)
    
    if not code:
        await callback.answer("کد تخفیف یافت نشد", show_alert=True)
        return
    
    await uow.commit()
    await callback.answer("❌ کد تخفیف غیرفعال شد")

    # Refresh the detail screen with the new status.
    await _render_discount_view(callback, code)


@router.callback_query(F.data.startswith("admin:discount:delete:"))
async def cb_admin_discount_delete(callback: CallbackQuery, uow, user) -> None:
    """Show delete confirmation."""
    code_id = callback.data.split(":", 3)[3]
    
    discount_service = DiscountCodeService(uow)
    code = await discount_service.get_discount_code(code_id)
    
    if not code:
        await callback.answer("کد تخفیف یافت نشد", show_alert=True)
        return
    
    text = (
        f"⚠️ <b>حذف کد تخفیف</b>\n\n"
        f"آیا از حذف کد تخفیف <code>{code.code}</code> مطمئن هستید؟\n\n"
        f"⚠️ این عملیات غیرقابل بازگشت است!"
    )
    
    await safe_edit_text(callback, text, reply_markup=admin_discount_confirm_delete_keyboard(code_id))
    await callback.answer()


@router.callback_query(F.data.startswith("admin:discount:delete_confirm:"))
async def cb_admin_discount_delete_confirm(callback: CallbackQuery, uow, user) -> None:
    """Confirm and delete discount code."""
    code_id = callback.data.split(":", 3)[3]
    
    discount_service = DiscountCodeService(uow)
    
    try:
        success = await discount_service.delete_discount_code(code_id)
        if success:
            await uow.commit()
            await callback.answer("✅ کد تخفیف حذف شد")
            # Go back to the first page of the list
            await _render_discount_list(callback, 0, uow)
        else:
            await callback.answer("کد تخفیف یافت نشد", show_alert=True)
    except ValueError as e:
        await callback.answer(str(e), show_alert=True)


# ============================================================================
# CREATE DISCOUNT CODE
# ============================================================================

@router.callback_query(F.data == "admin:discount:create")
async def cb_admin_discount_create_start(
    callback: CallbackQuery, state: FSMContext, uow, user
) -> None:
    """Start creating a new discount code."""
    # Clear any existing state first
    await state.clear()
    
    text = (
        "➕ <b>ایجاد کد تخفیف جدید</b>\n\n"
        "📝 لطفاً کد تخفیف را وارد کنید:\n\n"
        "💡 نکته: کد باید حداقل ۳ کاراکتر و حداکثر ۵۰ کاراکتر باشد"
    )
    
    await state.set_state(AdminDiscountCodeStates.waiting_code)
    await safe_edit_text(callback, text, reply_markup=admin_discount_cancel_keyboard())
    await callback.answer()


@router.message(AdminDiscountCodeStates.waiting_code)
async def process_discount_code_input(
    message: Message, state: FSMContext, uow, user
) -> None:
    """Process discount code input."""
    code = message.text.strip().upper()
    
    if len(code) < 3:
        await message.answer("❌ کد تخفیف باید حداقل ۳ کاراکتر باشد. لطفاً دوباره وارد کنید:")
        return
    
    if len(code) > 50:
        await message.answer("❌ کد تخفیف نباید بیش از ۵۰ کاراکتر باشد. لطفاً دوباره وارد کنید:")
        return
    
    # Check if code already exists
    discount_service = DiscountCodeService(uow)
    existing = await discount_service.get_discount_code_by_code(code)
    if existing:
        await message.answer(f"❌ کد تخفیف '{code}' قبلاً ثبت شده است. لطفاً کد دیگری وارد کنید:")
        return
    
    # Save code and ask for type
    await state.update_data(code=code)
    await state.set_state(AdminDiscountCodeStates.waiting_type)
    
    text = (
        f"✅ کد: <code>{code}</code>\n\n"
        "🔢 <b>نوع تخفیف را انتخاب کنید:</b>"
    )
    
    await message.answer(text, reply_markup=admin_discount_type_keyboard())


@router.callback_query(F.data.startswith("admin:discount:type:"))
async def cb_admin_discount_type(callback: CallbackQuery, state: FSMContext, uow, user) -> None:
    """Process discount type selection."""
    type_str = callback.data.split(":", 3)[3]
    
    if type_str not in ["percentage", "fixed"]:
        await callback.answer("نوع نامعتبر", show_alert=True)
        return
    
    discount_type = DiscountType.PERCENTAGE if type_str == "percentage" else DiscountType.FIXED
    
    await state.update_data(discount_type=discount_type)
    await state.set_state(AdminDiscountCodeStates.waiting_value)
    
    if discount_type == DiscountType.PERCENTAGE:
        text = (
            "📊 <b>نوع: درصدی</b>\n\n"
            "🔢 لطفاً درصد تخفیف را وارد کنید (۱ تا ۱۰۰):"
        )
    else:
        text = (
            "💰 <b>نوع: مبلغ ثابت</b>\n\n"
            "🔢 لطفاً مبلغ تخفیف را به تومان وارد کنید:"
        )
    
    await safe_edit_text(callback, text, reply_markup=admin_discount_cancel_keyboard())
    await callback.answer()


@router.message(AdminDiscountCodeStates.waiting_value)
async def process_discount_value(message: Message, state: FSMContext, uow, user) -> None:
    """Process discount value input."""
    try:
        value = int(message.text.strip())
    except ValueError:
        await message.answer("❌ لطفاً یک عدد صحیح وارد کنید:")
        return
    
    data = await state.get_data()
    discount_type = data.get("discount_type")
    
    if discount_type == DiscountType.PERCENTAGE:
        if value < 1 or value > 100:
            await message.answer("❌ درصد تخفیف باید بین ۱ تا ۱۰۰ باشد. لطفاً دوباره وارد کنید:")
            return
        await state.update_data(discount_value=value)
        await state.set_state(AdminDiscountCodeStates.waiting_max_eligible)
        
        text = (
            f"✅ درصد تخفیف: {value}%\n\n"
            "💳 <b>حداکثر مبلغ مجاز (تومان):</b>\n\n"
            "این مبلغ مشخص می‌کند که کد تخفیف برای سبدهای خرید با مبلغ کمتر یا مساوی این مقدار اعمال می‌شود.\n\n"
            "مثال: اگر ۱۰۰۰۰۰ وارد کنید، کد تخفیف فقط برای سبدهای تا ۱۰۰۰۰۰ تومان کار می‌کند.\n\n"
            "💡 برای عدم محدودیت، دکمه 'رد کردن' را بزنید."
        )
        
        await message.answer(text, reply_markup=admin_discount_skip_keyboard("admin:discount:skip_max_eligible"))
    else:
        if value < 1:
            await message.answer("❌ مبلغ تخفیف باید بیشتر از صفر باشد. لطفاً دوباره وارد کنید:")
            return
        await state.update_data(discount_value=value, max_eligible_amount=None)
        await state.set_state(AdminDiscountCodeStates.waiting_expiration)
        
        text = (
            f"✅ مبلغ تخفیف: {format_price(value)} تومان\n\n"
            "📅 <b>تاریخ انقضا:</b>\n\n"
            "تاریخ و ساعت را به فرمت زیر وارد کنید:\n"
            "<code>YYYY-MM-DD HH:MM</code>\n\n"
            "مثال: <code>2026-12-31 23:59</code>\n\n"
            "💡 برای بدون تاریخ انقضا، دکمه 'رد کردن' را بزنید."
        )
        
        await message.answer(text, reply_markup=admin_discount_skip_keyboard("admin:discount:skip_expiration"))


@router.message(AdminDiscountCodeStates.waiting_max_eligible)
async def process_max_eligible(message: Message, state: FSMContext, uow, user) -> None:
    """Process max eligible amount input."""
    try:
        amount = int(message.text.strip())
        if amount < 1:
            await message.answer("❌ مبلغ باید بیشتر از صفر باشد. لطفاً دوباره وارد کنید:")
            return
    except ValueError:
        await message.answer("❌ لطفاً یک عدد صحیح وارد کنید:")
        return
    
    await state.update_data(max_eligible_amount=amount)
    await state.set_state(AdminDiscountCodeStates.waiting_expiration)
    
    text = (
        f"✅ حداکثر مبلغ مجاز: {format_price(amount)} تومان\n\n"
        "📅 <b>تاریخ انقضا:</b>\n\n"
        "تاریخ و ساعت را به فرمت زیر وارد کنید:\n"
        "<code>YYYY-MM-DD HH:MM</code>\n\n"
        "مثال: <code>2026-12-31 23:59</code>\n\n"
        "💡 برای بدون تاریخ انقضا، دکمه 'رد کردن' را بزنید."
    )
    
    await message.answer(text, reply_markup=admin_discount_skip_keyboard("admin:discount:skip_expiration"))


@router.callback_query(F.data == "admin:discount:skip_max_eligible")
async def cb_skip_max_eligible(callback: CallbackQuery, state: FSMContext, uow, user) -> None:
    """Skip max eligible amount."""
    # Validate state
    data = await state.get_data()
    if not data.get("code") or not data.get("discount_type"):
        await callback.answer("❌ خطا: اطلاعات ناقص است", show_alert=True)
        await state.clear()
        await _render_discount_menu(callback)
        return
    
    await state.update_data(max_eligible_amount=None)
    await state.set_state(AdminDiscountCodeStates.waiting_expiration)
    
    text = (
        "⏭ حداکثر مبلغ مجاز: نامحدود\n\n"
        "📅 <b>تاریخ انقضا:</b>\n\n"
        "تاریخ و ساعت را به فرمت زیر وارد کنید:\n"
        "<code>YYYY-MM-DD HH:MM</code>\n\n"
        "مثال: <code>2026-12-31 23:59</code>\n\n"
        "💡 برای بدون تاریخ انقضا، دکمه 'رد کردن' را بزنید."
    )
    
    await safe_edit_text(callback, text, reply_markup=admin_discount_skip_keyboard("admin:discount:skip_expiration"))
    await callback.answer()


@router.message(AdminDiscountCodeStates.waiting_expiration)
async def process_expiration(message: Message, state: FSMContext, uow, user) -> None:
    """Process expiration date input."""
    date_str = message.text.strip()
    
    try:
        # Parse datetime in format YYYY-MM-DD HH:MM
        expires_at = datetime.strptime(date_str, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        
        if expires_at <= datetime.now(timezone.utc):
            await message.answer("❌ تاریخ انقضا باید در آینده باشد. لطفاً دوباره وارد کنید:")
            return
    except ValueError:
        await message.answer(
            "❌ فرمت تاریخ نادرست است. لطفاً به فرمت <code>YYYY-MM-DD HH:MM</code> وارد کنید:\n\n"
            "مثال: <code>2026-12-31 23:59</code>"
        )
        return
    
    await state.update_data(expires_at=expires_at)
    await state.set_state(AdminDiscountCodeStates.waiting_max_uses)
    
    text = (
        f"✅ تاریخ انقضا: {expires_at.strftime('%Y-%m-%d %H:%M')}\n\n"
        "🔢 <b>حداکثر تعداد استفاده:</b>\n\n"
        "چند بار این کد تخفیف قابل استفاده است؟\n\n"
        "💡 برای نامحدود، دکمه 'رد کردن' را بزنید."
    )
    
    await message.answer(text, reply_markup=admin_discount_skip_keyboard("admin:discount:skip_max_uses"))


@router.callback_query(F.data == "admin:discount:skip_expiration")
async def cb_skip_expiration(callback: CallbackQuery, state: FSMContext, uow, user) -> None:
    """Skip expiration date."""
    # Validate state
    data = await state.get_data()
    if not data.get("code") or not data.get("discount_type"):
        await callback.answer("❌ خطا: اطلاعات ناقص است", show_alert=True)
        await state.clear()
        await _render_discount_menu(callback)
        return
    
    await state.update_data(expires_at=None)
    await state.set_state(AdminDiscountCodeStates.waiting_max_uses)
    
    text = (
        "⏭ تاریخ انقضا: ندارد\n\n"
        "🔢 <b>حداکثر تعداد استفاده:</b>\n\n"
        "چند بار این کد تخفیف قابل استفاده است؟\n\n"
        "💡 برای نامحدود، دکمه 'رد کردن' را بزنید."
    )
    
    await safe_edit_text(callback, text, reply_markup=admin_discount_skip_keyboard("admin:discount:skip_max_uses"))
    await callback.answer()


@router.message(AdminDiscountCodeStates.waiting_max_uses)
async def process_max_uses(message: Message, state: FSMContext, uow, user) -> None:
    """Process max uses input."""
    try:
        max_uses = int(message.text.strip())
        if max_uses < 1:
            await message.answer("❌ تعداد باید بیشتر از صفر باشد. لطفاً دوباره وارد کنید:")
            return
    except ValueError:
        await message.answer("❌ لطفاً یک عدد صحیح وارد کنید:")
        return
    
    await state.update_data(max_uses=max_uses)
    await state.set_state(AdminDiscountCodeStates.waiting_description)
    
    text = (
        f"✅ حداکثر تعداد استفاده: {max_uses} بار\n\n"
        "📄 <b>توضیحات (اختیاری):</b>\n\n"
        "یک توضیح کوتاه برای این کد تخفیف وارد کنید.\n\n"
        "💡 برای رد کردن، دکمه 'رد کردن' را بزنید."
    )
    
    await message.answer(text, reply_markup=admin_discount_skip_keyboard("admin:discount:skip_description"))


@router.callback_query(F.data == "admin:discount:skip_max_uses")
async def cb_skip_max_uses(callback: CallbackQuery, state: FSMContext, uow, user) -> None:
    """Skip max uses."""
    # Validate state
    data = await state.get_data()
    if not data.get("code") or not data.get("discount_type"):
        await callback.answer("❌ خطا: اطلاعات ناقص است", show_alert=True)
        await state.clear()
        await _render_discount_menu(callback)
        return
    
    await state.update_data(max_uses=None)
    await state.set_state(AdminDiscountCodeStates.waiting_description)
    
    text = (
        "⏭ حداکثر تعداد استفاده: نامحدود\n\n"
        "📄 <b>توضیحات (اختیاری):</b>\n\n"
        "یک توضیح کوتاه برای این کد تخفیف وارد کنید.\n\n"
        "💡 برای رد کردن، دکمه 'رد کردن' را بزنید."
    )
    
    await safe_edit_text(callback, text, reply_markup=admin_discount_skip_keyboard("admin:discount:skip_description"))
    await callback.answer()


@router.message(AdminDiscountCodeStates.waiting_description)
async def process_description(message: Message, state: FSMContext, uow, user) -> None:
    """Process description input and create discount code."""
    description = message.text.strip()
    await state.update_data(description=description)
    await _create_discount_code(message, state, uow, user)


@router.callback_query(F.data == "admin:discount:skip_description")
async def cb_skip_description(callback: CallbackQuery, state: FSMContext, uow, user) -> None:
    """Skip description and create discount code."""
    # Get current state data
    data = await state.get_data()
    
    # Validate that we have all required data
    if not data.get("code") or not data.get("discount_type") or not data.get("discount_value"):
        await callback.answer(
            "❌ خطا: اطلاعات ناقص است. لطفاً دوباره شروع کنید.", show_alert=True
        )
        await state.clear()
        await _render_discount_menu(callback)
        return
    
    # Set description to None
    await state.update_data(description=None)
    
    # Create the discount code
    await _create_discount_code(callback.message, state, uow, user)
    await callback.answer()


async def _create_discount_code(message: Message, state: FSMContext, uow, user) -> None:
    """Helper to create discount code from collected data."""
    data = await state.get_data()
    
    # Validate all required fields exist
    required_fields = ["code", "discount_type", "discount_value"]
    missing_fields = [field for field in required_fields if field not in data]
    
    if missing_fields:
        error_msg = f"❌ خطا: فیلدهای مورد نیاز کامل نیست: {', '.join(missing_fields)}\n\nلطفاً دوباره از ابتدا شروع کنید."
        await message.answer(error_msg)
        await state.clear()
        return
    
    discount_service = DiscountCodeService(uow)
    
    try:
        code = await discount_service.create_discount_code(
            code=data["code"],
            discount_type=data["discount_type"],
            discount_value=data["discount_value"],
            max_eligible_amount=data.get("max_eligible_amount"),
            expires_at=data.get("expires_at"),
            max_uses=data.get("max_uses"),
            is_active=True,
            description=data.get("description"),
        )
        
        await uow.commit()
        await state.clear()
        
        type_text = "درصدی" if code.discount_type == DiscountType.PERCENTAGE else "مبلغ ثابت"
        value_text = f"{code.discount_value}%" if code.discount_type == DiscountType.PERCENTAGE else f"{format_price(code.discount_value)} تومان"
        
        text = (
            "✅ <b>کد تخفیف با موفقیت ایجاد شد!</b>\n\n"
            f"📝 کد: <code>{code.code}</code>\n"
            f"🔢 نوع: {type_text}\n"
            f"💰 مقدار: {value_text}"
        )
        
        from bot.keyboards.common import back_button
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="👁 مشاهده", callback_data=f"admin:discount:view:{code.id}")],
            [back_button("admin:discount:menu")],
        ])
        
        await message.answer(text, reply_markup=kb)
    
    except KeyError as e:
        await message.answer(f"❌ خطا: فیلد '{str(e)}' یافت نشد. لطفاً دوباره از ابتدا شروع کنید.")
        await state.clear()
    except ValueError as e:
        await message.answer(f"❌ خطا: {str(e)}")
        await state.clear()
    except Exception as e:
        logger.exception("Failed to create discount code")
        await message.answer(f"❌ خطای سیستمی: {str(e)}")
        await state.clear()


# ============================================================================
# EDIT DISCOUNT CODE
# ============================================================================

def _edit_field_prompt(field: str, code) -> str:
    """Prompt asking for the new value of one editable field."""
    if field == "code":
        return (
            "📝 <b>ویرایش کد تخفیف</b>\n\n"
            f"کد فعلی: <code>{code.code}</code>\n\n"
            "کد جدید را وارد کنید (۳ تا ۵۰ کاراکتر):"
        )

    if field == "value":
        if code.discount_type == DiscountType.PERCENTAGE:
            return (
                "🔢 <b>ویرایش مقدار تخفیف</b>\n\n"
                f"مقدار فعلی: {code.discount_value}%\n\n"
                "درصد جدید را وارد کنید (۱ تا ۱۰۰):"
            )
        return (
            "🔢 <b>ویرایش مقدار تخفیف</b>\n\n"
            f"مقدار فعلی: {format_price(code.discount_value)} تومان\n\n"
            "مبلغ جدید را به تومان وارد کنید:"
        )

    if field == "max_eligible":
        current = (
            f"{format_price(code.max_eligible_amount)} تومان"
            if code.max_eligible_amount
            else "ندارد"
        )
        return (
            "💰 <b>ویرایش حداکثر مبلغ مجاز</b>\n\n"
            f"مقدار فعلی: {current}\n\n"
            "حداکثر مبلغ مجاز جدید را به تومان وارد کنید:"
        )

    if field == "expiration":
        current = (
            code.expires_at.strftime("%Y-%m-%d %H:%M") if code.expires_at else "ندارد"
        )
        return (
            "📅 <b>ویرایش تاریخ انقضا</b>\n\n"
            f"مقدار فعلی: {current}\n\n"
            "تاریخ و ساعت جدید را به فرمت زیر وارد کنید:\n"
            "<code>YYYY-MM-DD HH:MM</code>\n\n"
            "مثال: <code>2026-12-31 23:59</code>"
        )

    if field == "max_uses":
        current = f"{code.max_uses} بار" if code.max_uses is not None else "نامحدود"
        return (
            "🔢 <b>ویرایش حداکثر تعداد استفاده</b>\n\n"
            f"مقدار فعلی: {current}\n\n"
            "حداکثر تعداد استفاده جدید را وارد کنید:"
        )

    if field == "description":
        current = code.description or "ندارد"
        return (
            "📄 <b>ویرایش توضیحات</b>\n\n"
            f"مقدار فعلی: {current}\n\n"
            "توضیحات جدید را وارد کنید (حداکثر ۲۵۵ کاراکتر):"
        )

    return ""


async def _apply_discount_edit(
    message: Message, state: FSMContext, uow, *, field_label: str, **changes
) -> None:
    """Persist one edited field and show the refreshed detail screen."""
    data = await state.get_data()
    code_id = data.get("edit_code_id")

    if not code_id:
        await message.answer(
            "❌ خطا: اطلاعات ویرایش یافت نشد. لطفاً دوباره از منوی کدهای تخفیف شروع کنید."
        )
        await state.clear()
        return

    discount_service = DiscountCodeService(uow)

    try:
        code = await discount_service.update_discount_code(code_id, **changes)
    except ValueError as e:
        # Keep the state so the admin can retry with a valid value.
        await message.answer(f"❌ {e}\n\nلطفاً مقدار دیگری وارد کنید:")
        return

    await uow.commit()
    await state.clear()

    await message.answer(
        f"✅ <b>{field_label} با موفقیت ویرایش شد</b>\n\n{_discount_details_text(code)}",
        reply_markup=admin_discount_view_keyboard(code),
    )


@router.callback_query(F.data.startswith("admin:discount:edit_field:"))
async def cb_admin_discount_edit_field(
    callback: CallbackQuery, state: FSMContext, uow, user
) -> None:
    """Ask for the new value of the selected field."""
    parts = callback.data.split(":")
    if len(parts) < 5:
        await callback.answer("درخواست نامعتبر", show_alert=True)
        return

    code_id, field = parts[3], parts[4]
    field_state = _EDIT_FIELD_STATES.get(field)

    if field_state is None:
        await callback.answer("فیلد نامعتبر", show_alert=True)
        return

    discount_service = DiscountCodeService(uow)
    code = await discount_service.get_discount_code(code_id)

    if not code:
        await callback.answer("کد تخفیف یافت نشد", show_alert=True)
        return

    await state.update_data(edit_code_id=code_id, edit_field=field)
    await state.set_state(field_state)

    await safe_edit_text(
        callback,
        _edit_field_prompt(field, code),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[back_button(f"admin:discount:edit:{code_id}")]]
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:discount:edit:"))
async def cb_admin_discount_edit(
    callback: CallbackQuery, state: FSMContext, uow, user
) -> None:
    """Show the list of editable fields for a discount code."""
    code_id = callback.data.split(":", 3)[3]

    discount_service = DiscountCodeService(uow)
    code = await discount_service.get_discount_code(code_id)

    if not code:
        await callback.answer("کد تخفیف یافت نشد", show_alert=True)
        return

    await state.clear()
    await state.update_data(edit_code_id=code_id)
    await state.set_state(AdminDiscountCodeStates.waiting_edit_field)

    text = (
        "✏️ <b>ویرایش کد تخفیف</b>\n\n"
        f"📝 کد: <code>{code.code}</code>\n\n"
        "کدام مورد را می‌خواهید ویرایش کنید؟"
    )

    await safe_edit_text(callback, text, reply_markup=admin_discount_edit_keyboard(code_id))
    await callback.answer()


@router.message(AdminDiscountCodeStates.waiting_edit_field)
async def process_edit_field_choice(message: Message, state: FSMContext) -> None:
    """Text typed on the edit menu: ask for a button press instead."""
    data = await state.get_data()
    code_id = data.get("edit_code_id")

    if not code_id:
        await message.answer("❌ لطفاً از منوی مدیریت کدهای تخفیف شروع کنید.")
        return

    await message.answer(
        "❌ لطفاً یکی از گزینه‌های زیر را انتخاب کنید:",
        reply_markup=admin_discount_edit_keyboard(code_id),
    )


@router.message(AdminDiscountCodeStates.waiting_edit_code)
async def process_edit_code(message: Message, state: FSMContext, uow, user) -> None:
    """Edit the discount code string."""
    new_code = (message.text or "").strip().upper()

    if len(new_code) < 3 or len(new_code) > 50:
        await message.answer("❌ کد تخفیف باید بین ۳ تا ۵۰ کاراکتر باشد. لطفاً دوباره وارد کنید:")
        return

    await _apply_discount_edit(
        message, state, uow, field_label="کد تخفیف", code=new_code
    )


@router.message(AdminDiscountCodeStates.waiting_edit_value)
async def process_edit_value(message: Message, state: FSMContext, uow, user) -> None:
    """Edit the discount value."""
    try:
        value = int((message.text or "").strip())
    except ValueError:
        await message.answer("❌ لطفاً یک عدد صحیح وارد کنید:")
        return

    if value < 1:
        await message.answer("❌ مقدار تخفیف باید بیشتر از صفر باشد. لطفاً دوباره وارد کنید:")
        return

    await _apply_discount_edit(
        message, state, uow, field_label="مقدار تخفیف", discount_value=value
    )


@router.message(AdminDiscountCodeStates.waiting_edit_max_eligible)
async def process_edit_max_eligible(message: Message, state: FSMContext, uow, user) -> None:
    """Edit the maximum eligible cart amount."""
    try:
        amount = int((message.text or "").strip())
    except ValueError:
        await message.answer("❌ لطفاً یک عدد صحیح وارد کنید:")
        return

    if amount < 1:
        await message.answer("❌ مبلغ باید بیشتر از صفر باشد. لطفاً دوباره وارد کنید:")
        return

    await _apply_discount_edit(
        message, state, uow, field_label="حداکثر مبلغ مجاز", max_eligible_amount=amount
    )


@router.message(AdminDiscountCodeStates.waiting_edit_expiration)
async def process_edit_expiration(message: Message, state: FSMContext, uow, user) -> None:
    """Edit the expiration date."""
    date_str = (message.text or "").strip()

    try:
        expires_at = datetime.strptime(date_str, "%Y-%m-%d %H:%M").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        await message.answer(
            "❌ فرمت تاریخ نادرست است. لطفاً به فرمت <code>YYYY-MM-DD HH:MM</code> وارد کنید:\n\n"
            "مثال: <code>2026-12-31 23:59</code>"
        )
        return

    if expires_at <= datetime.now(timezone.utc):
        await message.answer("❌ تاریخ انقضا باید در آینده باشد. لطفاً دوباره وارد کنید:")
        return

    await _apply_discount_edit(
        message, state, uow, field_label="تاریخ انقضا", expires_at=expires_at
    )


@router.message(AdminDiscountCodeStates.waiting_edit_max_uses)
async def process_edit_max_uses(message: Message, state: FSMContext, uow, user) -> None:
    """Edit the maximum number of uses."""
    try:
        max_uses = int((message.text or "").strip())
    except ValueError:
        await message.answer("❌ لطفاً یک عدد صحیح وارد کنید:")
        return

    if max_uses < 1:
        await message.answer("❌ تعداد باید بیشتر از صفر باشد. لطفاً دوباره وارد کنید:")
        return

    await _apply_discount_edit(
        message, state, uow, field_label="حداکثر تعداد استفاده", max_uses=max_uses
    )


@router.message(AdminDiscountCodeStates.waiting_edit_description)
async def process_edit_description(message: Message, state: FSMContext, uow, user) -> None:
    """Edit the description."""
    description = (message.text or "").strip()

    if not description:
        await message.answer("❌ توضیحات نمی‌تواند خالی باشد. لطفاً دوباره وارد کنید:")
        return

    if len(description) > 255:
        await message.answer("❌ توضیحات نباید بیشتر از ۲۵۵ کاراکتر باشد. لطفاً دوباره وارد کنید:")
        return

    await _apply_discount_edit(
        message, state, uow, field_label="توضیحات", description=description
    )


@router.callback_query(F.data == "admin:discount:stats")
async def cb_admin_discount_stats(callback: CallbackQuery, uow, user) -> None:
    """Show discount code statistics."""
    discount_service = DiscountCodeService(uow)
    stats = await discount_service.get_statistics()
    
    text = (
        "📊 <b>آمار کدهای تخفیف</b>\n\n"
        f"📋 کل کدها: {stats['total']}\n"
        f"✅ فعال: {stats['active']}\n"
        f"❌ غیرفعال: {stats['inactive']}\n"
        f"⏰ در حال انقضا (۷ روز): {stats['expiring_soon']}\n"
        f"📉 نزدیک به اتمام (۵ استفاده): {stats['nearly_exhausted']}"
    )
    
    from bot.keyboards.common import back_button
    from aiogram.types import InlineKeyboardMarkup
    
    kb = InlineKeyboardMarkup(inline_keyboard=[[back_button("admin:discount:menu")]])
    await safe_edit_text(callback, text, reply_markup=kb)
    await callback.answer()
