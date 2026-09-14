"""Discount code keyboards (admin)."""

from typing import Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.keyboards.common import back_button
from bot.models.discount_code import DiscountCode, DiscountType


def admin_discount_menu_keyboard() -> InlineKeyboardMarkup:
    """Main admin discount code management menu."""
    keyboard = [
        [
            InlineKeyboardButton(text="📋 لیست کدها", callback_data="admin:discount:list"),
        ],
        [
            InlineKeyboardButton(text="➕ ایجاد کد جدید", callback_data="admin:discount:create"),
        ],
        [
            InlineKeyboardButton(text="📊 آمار", callback_data="admin:discount:stats"),
        ],
        [
            back_button("admin:panel"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def admin_discount_list_keyboard(
    codes: Sequence[DiscountCode], page: int = 0, total_pages: int = 1
) -> InlineKeyboardMarkup:
    """List discount codes with pagination."""
    keyboard = []
    
    for code in codes:
        status_emoji = "✅" if code.is_active else "❌"
        type_emoji = "%" if code.discount_type == DiscountType.PERCENTAGE else "💰"
        keyboard.append([
            InlineKeyboardButton(
                text=f"{status_emoji} {code.code} ({type_emoji}{code.discount_value})",
                callback_data=f"admin:discount:view:{code.id}"
            )
        ])
    
    # Pagination
    if total_pages > 1:
        nav_row = []
        if page > 0:
            nav_row.append(
                InlineKeyboardButton(text="⬅️ قبلی", callback_data=f"admin:discount:list:{page-1}")
            )
        nav_row.append(
            InlineKeyboardButton(text=f"{page+1}/{total_pages}", callback_data="noop")
        )
        if page < total_pages - 1:
            nav_row.append(
                InlineKeyboardButton(text="➡️ بعدی", callback_data=f"admin:discount:list:{page+1}")
            )
        keyboard.append(nav_row)
    
    keyboard.append([back_button("admin:discount:menu")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def admin_discount_view_keyboard(code: DiscountCode) -> InlineKeyboardMarkup:
    """View a specific discount code."""
    keyboard = []
    
    # Toggle active status
    if code.is_active:
        keyboard.append([
            InlineKeyboardButton(text="❌ غیرفعال کردن", callback_data=f"admin:discount:deactivate:{code.id}")
        ])
    else:
        keyboard.append([
            InlineKeyboardButton(text="✅ فعال کردن", callback_data=f"admin:discount:activate:{code.id}")
        ])
    
    # Edit
    keyboard.append([
        InlineKeyboardButton(text="✏️ ویرایش", callback_data=f"admin:discount:edit:{code.id}")
    ])
    
    # Delete (only if no usage)
    if code.usage_count == 0:
        keyboard.append([
            InlineKeyboardButton(text="🗑 حذف", callback_data=f"admin:discount:delete:{code.id}")
        ])
    
    keyboard.append([back_button("admin:discount:list")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def admin_discount_edit_keyboard(code_id: str) -> InlineKeyboardMarkup:
    """Edit discount code menu."""
    keyboard = [
        [
            InlineKeyboardButton(text="📝 ویرایش کد", callback_data=f"admin:discount:edit_field:{code_id}:code")
        ],
        [
            InlineKeyboardButton(text="🔢 ویرایش مقدار", callback_data=f"admin:discount:edit_field:{code_id}:value")
        ],
        [
            InlineKeyboardButton(text="💰 حداکثر مبلغ مجاز", callback_data=f"admin:discount:edit_field:{code_id}:max_eligible")
        ],
        [
            InlineKeyboardButton(text="📅 تاریخ انقضا", callback_data=f"admin:discount:edit_field:{code_id}:expiration")
        ],
        [
            InlineKeyboardButton(text="🔢 حداکثر استفاده", callback_data=f"admin:discount:edit_field:{code_id}:max_uses")
        ],
        [
            InlineKeyboardButton(text="📄 توضیحات", callback_data=f"admin:discount:edit_field:{code_id}:description")
        ],
        [
            back_button(f"admin:discount:view:{code_id}")
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def admin_discount_type_keyboard() -> InlineKeyboardMarkup:
    """Choose discount type."""
    keyboard = [
        [
            InlineKeyboardButton(text="📊 درصدی", callback_data="admin:discount:type:percentage")
        ],
        [
            InlineKeyboardButton(text="💰 مبلغ ثابت", callback_data="admin:discount:type:fixed")
        ],
        [
            InlineKeyboardButton(text="❌ انصراف", callback_data="admin:discount:menu")
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def admin_discount_confirm_delete_keyboard(code_id: str) -> InlineKeyboardMarkup:
    """Confirm discount code deletion."""
    keyboard = [
        [
            InlineKeyboardButton(text="✅ بله، حذف شود", callback_data=f"admin:discount:delete_confirm:{code_id}"),
            InlineKeyboardButton(text="❌ خیر", callback_data=f"admin:discount:view:{code_id}"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def admin_discount_skip_keyboard(callback_data: str) -> InlineKeyboardMarkup:
    """Skip optional field."""
    keyboard = [
        [
            InlineKeyboardButton(text="⏭ رد کردن", callback_data=callback_data),
        ],
        [
            InlineKeyboardButton(text="❌ انصراف", callback_data="admin:discount:menu"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def admin_discount_cancel_keyboard() -> InlineKeyboardMarkup:
    """Cancel discount code creation/editing."""
    keyboard = [
        [
            InlineKeyboardButton(text="❌ انصراف", callback_data="admin:discount:menu"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)
