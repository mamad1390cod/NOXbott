"""Shopping cart handlers."""

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from bot.keyboards.cart_keyboard import cart_keyboard, checkout_keyboard, confirm_cancel_keyboard
from bot.services.cart import CartService
from bot.services.order import OrderService
from bot.states import DiscountCodeStates
from bot.utils.format import format_price
from bot.utils.editing import safe_edit_text
from bot.utils.messages import require_text

router = Router(name="cart")


async def _show_cart(callback: CallbackQuery, uow, user) -> None:
    """Edit message to show the cart."""
    cart_service = CartService(uow)
    summary = await cart_service.get_cart_summary(user.id)

    if not summary["items"]:
        from bot.texts import CART_EMPTY
        from bot.keyboards.common import back_button
        kb = __import__("aiogram.types", fromlist=["InlineKeyboardMarkup"]).InlineKeyboardMarkup(
            inline_keyboard=[[back_button("menu:home")]]
        )
        await safe_edit_text(callback, CART_EMPTY(), reply_markup=kb)
        return

    lines = []
    for i, item in enumerate(summary["items"], 1):
        title = item.title
        price = format_price(item.price)
        qty = item.quantity
        item_total = format_price(item.price * item.quantity)
        lines.append(f"{i}. {title} × {qty} = <b>{item_total}</b> تومان")

    text = "🛒 <b>سبد خرید شما</b>\n\n" + "\n".join(lines)
    
    # Show discount information if applied
    if summary["discount_code"]:
        text += f"\n\n💰 جمع: <b>{format_price(summary['subtotal'])} تومان</b>"
        text += f"\n🎟 کد تخفیف: <b>{summary['discount_code']}</b>"
        text += f"\n✅ تخفیف: <b>-{format_price(summary['discount_amount'])} تومان</b>"
        text += f"\n━━━━━━━━━━━━━━━━"
        text += f"\n<b>💳 مبلغ نهایی: {format_price(summary['final_total'])} تومان</b>"
    else:
        text += f"\n\n💰 مبلغ کل: <b>{format_price(summary['total_price'])} تومان</b>"
    
    text += f"\n📦 تعداد آیتم: {summary['total_items']}"
    
    has_discount = summary["discount_code"] is not None
    await safe_edit_text(callback, text, reply_markup=cart_keyboard(summary["items"], has_discount=has_discount))


@router.callback_query(F.data == "menu:cart")
async def cb_cart(
    callback: CallbackQuery,
    uow, user,
) -> None:
    """Show the shopping cart."""
    await callback.answer()
    await _show_cart(callback, uow, user)


@router.callback_query(F.data.startswith("cart:view:"))
async def cb_cart_item_view(
    callback: CallbackQuery,
    uow, user,
) -> None:
    """View a single cart item."""
    parts = callback.data.split(":", 2)
    if len(parts) < 3:
        await callback.answer("داده‌های نامعتبر", show_alert=True)
        return
    item_id = parts[2]
    cart_service = CartService(uow)
    cart = await cart_service.get_cart(user.id)
    if not cart:
        await callback.answer("سبد خرید خالی است", show_alert=True)
        return
    item = await uow.carts.get_item(item_id)
    if not item or item.cart_id != cart.id:
        await callback.answer("آیتم یافت نشد", show_alert=True)
        return

    from bot.keyboards.cart_keyboard import cart_item_keyboard
    text = (
        f"📦 <b>{item.title}</b>\n\n"
        f"قیمت واحد: <b>{format_price(item.price)} تومان</b>\n"
        f"تعداد: {item.quantity}\n"
        f"جمع: <b>{format_price(item.price * item.quantity)} تومان</b>"
    )
    await safe_edit_text(callback, text, reply_markup=cart_item_keyboard(item))
    await callback.answer()


@router.callback_query(F.data.startswith("cart:inc:"))
async def cb_cart_increment(
    callback: CallbackQuery,
    uow, user,
) -> None:
    """Increment cart item quantity."""
    parts = callback.data.split(":", 2)
    if len(parts) < 3:
        await callback.answer("داده‌های نامعتبر", show_alert=True)
        return
    item_id = parts[2]
    cart_service = CartService(uow)
    cart = await cart_service.get_cart(user.id)
    item = await cart_service.uow.carts.get_item(item_id)
    if not item or (cart and item.cart_id != cart.id):
        await callback.answer("آیتم یافت نشد", show_alert=True)
        return
    try:
        await cart_service.update_quantity(user.id, item_id, item.quantity + 1)
    except ValueError as e:
        await callback.answer(str(e), show_alert=True)
        return
    from bot.keyboards.cart_keyboard import cart_item_keyboard
    item = await cart_service.uow.carts.get_item(item_id)
    text = (
        f"📦 <b>{item.title}</b>\n\n"
        f"قیمت واحد: <b>{format_price(item.price)} تومان</b>\n"
        f"تعداد: {item.quantity}\n"
        f"جمع: <b>{format_price(item.price * item.quantity)} تومان</b>"
    )
    await safe_edit_text(callback, text, reply_markup=cart_item_keyboard(item))
    await callback.answer()


@router.callback_query(F.data.startswith("cart:dec:"))
async def cb_cart_decrement(
    callback: CallbackQuery,
    uow, user,
) -> None:
    """Decrement cart item quantity."""
    parts = callback.data.split(":", 2)
    if len(parts) < 3:
        await callback.answer("داده‌های نامعتبر", show_alert=True)
        return
    item_id = parts[2]
    cart_service = CartService(uow)
    cart = await cart_service.get_cart(user.id)
    item = await cart_service.uow.carts.get_item(item_id)
    if not item or (cart and item.cart_id != cart.id):
        await callback.answer("آیتم یافت نشد", show_alert=True)
        return
    try:
        item = await cart_service.update_quantity(user.id, item_id, item.quantity - 1)
    except ValueError as e:
        await callback.answer(str(e), show_alert=True)
        return
    if item is None:
        await callback.answer()
        await _show_cart(callback, uow, user)
        return
    from bot.keyboards.cart_keyboard import cart_item_keyboard
    text = (
        f"📦 <b>{item.title}</b>\n\n"
        f"قیمت واحد: <b>{format_price(item.price)} تومان</b>\n"
        f"تعداد: {item.quantity}\n"
        f"جمع: <b>{format_price(item.price * item.quantity)} تومان</b>"
    )
    await safe_edit_text(callback, text, reply_markup=cart_item_keyboard(item))
    await callback.answer()


@router.callback_query(F.data.startswith("cart:del:"))
async def cb_cart_delete_item(
    callback: CallbackQuery,
    uow, user,
) -> None:
    """Delete a cart item."""
    parts = callback.data.split(":", 2)
    if len(parts) < 3:
        await callback.answer("داده‌های نامعتبر", show_alert=True)
        return
    item_id = parts[2]
    cart_service = CartService(uow)
    try:
        await cart_service.remove_item(user.id, item_id)
    except ValueError as e:
        await callback.answer(str(e), show_alert=True)
        return
    await callback.answer("آیتم حذف شد")
    await _show_cart(callback, uow, user)


@router.callback_query(F.data == "cart:clear")
async def cb_cart_clear(
    callback: CallbackQuery,
    uow, user,
) -> None:
    """Clear the cart."""
    cart_service = CartService(uow)
    await cart_service.clear_cart(user.id)
    await callback.answer("سبد خرید خالی شد")
    await _show_cart(callback, uow, user)


@router.callback_query(F.data == "cart:checkout")
async def cb_cart_checkout(
    callback: CallbackQuery,
    uow, user,
) -> None:
    """Start checkout with wallet balance check."""
    cart_service = CartService(uow)
    summary = await cart_service.get_cart_summary(user.id)
    if not summary["items"]:
        await callback.answer("سبد خرید خالی است", show_alert=True)
        return

    # Use final_total which includes discount
    total_price = summary['final_total']
    wallet_balance = user.wallet_balance or 0
    has_sufficient_balance = wallet_balance >= total_price

    if has_sufficient_balance:
        # Show wallet payment option
        discount_text = ""
        if summary['discount_code']:
            discount_text = (
                f"\n\n🎟 کد تخفیف: <b>{summary['discount_code']}</b>\n"
                f"💰 مبلغ اولیه: <b>{format_price(summary['subtotal'])} تومان</b>\n"
                f"✅ تخفیف: <b>-{format_price(summary['discount_amount'])} تومان</b>"
            )
        
        text = (
            "🧾 <b>تایید نهایی سفارش</b>\n\n"
            f"📦 تعداد آیتم: {summary['total_items']}\n"
            f"{discount_text}\n"
            f"💳 مبلغ پرداختی: <b>{format_price(total_price)} تومان</b>\n\n"
            f"💳 موجودی کیف پول: <b>{format_price(wallet_balance)} تومان</b>\n"
            f"✅ موجودی کافی است!\n\n"
            "پرداخت مستقیم از کیف پول انجام خواهد شد."
        )
        from bot.keyboards.cart_keyboard import wallet_checkout_keyboard
        await safe_edit_text(callback, text, reply_markup=wallet_checkout_keyboard())
    else:
        # Insufficient balance - show topup option
        shortage = total_price - wallet_balance
        discount_text = ""
        if summary['discount_code']:
            discount_text = (
                f"🎟 کد تخفیف: <b>{summary['discount_code']}</b>\n"
                f"💰 مبلغ اولیه: <b>{format_price(summary['subtotal'])} تومان</b>\n"
                f"✅ تخفیف: <b>-{format_price(summary['discount_amount'])} تومان</b>\n\n"
            )
        
        text = (
            "❌ <b>موجودی کیف پول کافی نیست</b>\n\n"
            f"{discount_text}"
            f"💰 مبلغ سفارش: <b>{format_price(total_price)} تومان</b>\n"
            f"💳 موجودی فعلی: <b>{format_price(wallet_balance)} تومان</b>\n"
            f"📉 کسری: <b>{format_price(shortage)} تومان</b>\n\n"
            "می‌توانید مبلغ سفارش را کارتی پرداخت کنید و رسید بفرستید،\n"
            "یا ابتدا کیف پول خود را شارژ کنید."
        )
        from bot.keyboards.cart_keyboard import insufficient_balance_keyboard
        await safe_edit_text(callback, text, reply_markup=insufficient_balance_keyboard())
    
    await callback.answer()


@router.callback_query(F.data == "cart:add_discount")
async def cb_cart_add_discount(
    callback: CallbackQuery,
    state: FSMContext,
    uow, user,
) -> None:
    """Start discount code entry flow."""
    from bot.keyboards.cart_keyboard import discount_code_cancel_keyboard
    
    cart_service = CartService(uow)
    summary = await cart_service.get_cart_summary(user.id)
    
    if not summary["items"]:
        await callback.answer("سبد خرید خالی است", show_alert=True)
        return
    
    text = (
        "🎟 <b>افزودن کد تخفیف</b>\n\n"
        "لطفاً کد تخفیف خود را وارد کنید:\n\n"
        "💡 نکته: کد تخفیف به حروف بزرگ و کوچک حساس نیست"
    )
    
    await safe_edit_text(callback, text, reply_markup=discount_code_cancel_keyboard())
    await state.set_state(DiscountCodeStates.waiting_code)
    await callback.answer()


@router.message(DiscountCodeStates.waiting_code)
async def process_discount_code(
    message: types.Message,
    state: FSMContext,
    uow, user,
) -> None:
    """Process discount code entered by user."""
    from bot.services.cart import CartService
    
    code = await require_text(message)
    if code is None:
        return
    
    if not code:
        await message.answer("❌ کد تخفیف نمی‌تواند خالی باشد. لطفاً دوباره تلاش کنید:")
        return
    
    cart_service = CartService(uow)
    
    try:
        success, msg, discount_amount = await cart_service.apply_discount_code(user.id, code)
        
        if success:
            await state.clear()
            # Show cart with discount applied
            from bot.keyboards.common import back_button
            from aiogram.types import InlineKeyboardMarkup
            
            summary = await cart_service.get_cart_summary(user.id)
            
            text = (
                f"✅ <b>{msg}</b>\n\n"
                f"💰 مبلغ اولیه: <b>{format_price(summary['subtotal'])} تومان</b>\n"
                f"🎟 کد تخفیف: <b>{code.upper()}</b>\n"
                f"✅ تخفیف: <b>-{format_price(discount_amount)} تومان</b>\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"💳 مبلغ نهایی: <b>{format_price(summary['final_total'])} تومان</b>\n\n"
                "برای مشاهده سبد خرید دکمه زیر را بزنید."
            )
            
            kb = InlineKeyboardMarkup(inline_keyboard=[[back_button("menu:cart")]])
            await message.answer(text, reply_markup=kb)
        else:
            await message.answer(f"❌ {msg}\n\nلطفاً کد دیگری وارد کنید یا /cancel را بزنید:")
    
    except Exception as e:
        await message.answer(f"❌ خطا در اعمال کد تخفیف: {str(e)}\n\nلطفاً دوباره تلاش کنید:")


@router.callback_query(F.data == "cart:remove_discount")
async def cb_cart_remove_discount(
    callback: CallbackQuery,
    uow, user,
) -> None:
    """Remove discount code from cart."""
    cart_service = CartService(uow)
    
    await cart_service.remove_discount_code(user.id)
    await callback.answer("✅ کد تخفیف حذف شد")
    
    # Refresh cart display
    await _show_cart(callback, uow, user)