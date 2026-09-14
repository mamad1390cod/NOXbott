"""Admin cleanup actions for resetting test/sales history safely."""

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton

from bot.keyboards.common import back_button, single_button_kb
from bot.models.log import LogAction
from bot.models.rbac import Permission
from bot.models.user import User
from bot.services.admin import AdminService
from bot.utils.editing import safe_edit_text

router = Router(name="admin_cleanup")


@router.callback_query(F.data == "admin:cleanup")
async def cb_cleanup_menu(
    callback: CallbackQuery, uow, user: User, permissions: set[Permission]
) -> None:
    counts = await AdminService(uow).get_cleanup_counts()
    text = (
        "🗑 <b>پاک‌سازی داده‌های فروش</b>\n\n"
        f"🎉 سفارش‌های تکمیل‌شده: <b>{counts['completed_orders']}</b>\n"
        f"💰 شارژهای کیف پول تأییدشده: <b>{counts['approved_topups']}</b>\n\n"
        "این عملیات فقط تاریخچه سفارش‌های تکمیل‌شده و درخواست‌های شارژ تأییدشده "
        "را حذف می‌کند.\n"
        "موجودی کاربران و تراکنش‌های مالی حذف یا تغییر نمی‌کنند.\n\n"
        "برای شروع یکی از گزینه‌ها را انتخاب کنید:"
    )
    keyboard = single_button_kb(back_button("admin:panel"))
    rows = []
    if Permission.DELETE_ORDERS in permissions:
        rows.append([
            InlineKeyboardButton(
                text="🧾 حذف سفارش‌های تکمیل‌شده",
                callback_data="admin:cleanup:orders",
            )
        ])
    if Permission.MANAGE_PAYMENTS in permissions:
        rows.append([
            InlineKeyboardButton(
                text="💰 حذف شارژهای تأییدشده",
                callback_data="admin:cleanup:topups",
            )
        ])
    keyboard.inline_keyboard[0:0] = rows
    await safe_edit_text(callback, text, reply_markup=keyboard)
    await callback.answer()


async def _confirm(callback: CallbackQuery, title: str, action: str) -> None:
    keyboard = single_button_kb(back_button("admin:cleanup"))
    keyboard.inline_keyboard.insert(
        0,
        [
            InlineKeyboardButton(
                text="✅ بله، حذف کن",
                callback_data=f"admin:cleanup:confirm:{action}",
            ),
        ],
    )
    await safe_edit_text(
        callback,
        f"⚠️ <b>تأیید نهایی</b>\n\n{title}\n\nاین عملیات قابل برگشت نیست.",
        reply_markup=keyboard,
    )
    await callback.answer()


@router.callback_query(F.data == "admin:cleanup:orders")
async def cb_confirm_orders(
    callback: CallbackQuery, uow, user: User, permissions: set[Permission]
) -> None:
    if Permission.DELETE_ORDERS not in permissions:
        await callback.answer("دسترسی حذف سفارش‌ها را ندارید.", show_alert=True)
        return
    await _confirm(
        callback,
        "همه سفارش‌های با وضعیت «تکمیل‌شده» و آیتم‌ها، رسیدها و رویدادهای وابسته حذف شوند؟",
        "orders",
    )


@router.callback_query(F.data == "admin:cleanup:topups")
async def cb_confirm_topups(
    callback: CallbackQuery, uow, user: User, permissions: set[Permission]
) -> None:
    if Permission.MANAGE_PAYMENTS not in permissions:
        await callback.answer("دسترسی مدیریت شارژ را ندارید.", show_alert=True)
        return
    await _confirm(
        callback,
        "همه درخواست‌های شارژ کیف پول با وضعیت «تأییدشده» حذف شوند؟",
        "topups",
    )


@router.callback_query(F.data == "admin:cleanup:confirm:orders")
async def cb_delete_orders(
    callback: CallbackQuery, uow, user: User, permissions: set[Permission]
) -> None:
    if Permission.DELETE_ORDERS not in permissions:
        await callback.answer("دسترسی حذف سفارش‌ها را ندارید.", show_alert=True)
        return
    service = AdminService(uow)
    count = await service.delete_completed_orders()
    await service.log_action(
        user,
        LogAction.SETTINGS_CHANGE,
        target_type="completed_orders",
        description=f"پاک‌سازی {count} سفارش تکمیل‌شده",
    )
    await uow.commit()
    await safe_edit_text(
        callback,
        f"✅ {count} سفارش تکمیل‌شده حذف شد.\nتراکنش‌های مالی و موجودی کاربران حفظ شدند.",
        reply_markup=single_button_kb(back_button("admin:cleanup")),
    )
    await callback.answer()


@router.callback_query(F.data == "admin:cleanup:confirm:topups")
async def cb_delete_topups(
    callback: CallbackQuery, uow, user: User, permissions: set[Permission]
) -> None:
    if Permission.MANAGE_PAYMENTS not in permissions:
        await callback.answer("دسترسی مدیریت شارژ را ندارید.", show_alert=True)
        return
    service = AdminService(uow)
    count = await service.delete_approved_topups()
    await service.log_action(
        user,
        LogAction.SETTINGS_CHANGE,
        target_type="approved_topups",
        description=f"پاک‌سازی {count} شارژ تأییدشده",
    )
    await uow.commit()
    await safe_edit_text(
        callback,
        f"✅ {count} درخواست شارژ تأییدشده حذف شد.\n"
        "موجودی کاربران و تراکنش‌های مالی حفظ شدند.",
        reply_markup=single_button_kb(back_button("admin:cleanup")),
    )
    await callback.answer()
