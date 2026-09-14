"""Main menu and start handlers (user side)."""

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message

from bot.config import get_settings
from bot.database.uow import UnitOfWork
from bot.keyboards.common import main_menu_keyboard
from bot.models.user import User
from bot.services.mandatory_membership import MandatoryMembershipService
from bot.content.telegram_bot_builder import CONTACT_USER_ID, OFFER_TEXT
from bot.texts import MAIN_MENU, WELCOME
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

router = Router(name="menu")


async def _send_main_menu(event: Message | CallbackQuery, edit: bool = True) -> None:
    """Send or edit the main menu."""
    settings = get_settings()
    user_id = event.from_user.id if event.from_user else 0
    is_admin = user_id in settings.admin_ids
    kb = main_menu_keyboard(is_admin=is_admin)

    if isinstance(event, CallbackQuery):
        await event.message.edit_text(MAIN_MENU(), reply_markup=kb)
        await event.answer()
    else:
        await event.answer(MAIN_MENU(), reply_markup=kb)


@router.message(CommandStart())
async def cmd_start(message: Message, uow: UnitOfWork, user: User) -> None:
    """Handle /start command."""
    # The middleware intentionally lets /start through so the user can see
    # and join the required chats.
    membership = MandatoryMembershipService(uow)
    if await membership.get_all() and not await membership.verify_user(
        message.bot, user
    ):
        await message.answer(
            await membership.gate_text(),
            reply_markup=await membership.keyboard(),
        )
        return
    await message.answer(
        WELCOME(),
        reply_markup=main_menu_keyboard(
            is_admin=message.from_user.id in get_settings().admin_ids
        ),
    )


@router.message((F.text.lower() == "منو") | (F.text == "/menu"))
async def cmd_menu(message: Message) -> None:
    """Handle /menu command."""
    await _send_main_menu(message, edit=False)


@router.callback_query(F.data == "menu:home")
async def cb_home(callback: CallbackQuery) -> None:
    """Handle home button."""
    await _send_main_menu(callback)


@router.callback_query(F.data == "menu:bot_builder")
async def cb_bot_builder(callback: CallbackQuery) -> None:
    """Show the editable Telegram bot creation offer."""
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📩 پیام به مدیر",
                    url=f"tg://user?id={6929510084}",
                )
            ],
            [InlineKeyboardButton(text="🔙 بازگشت", callback_data="menu:home")],
        ]
    )
    await callback.message.edit_text(OFFER_TEXT, reply_markup=keyboard)
    await callback.answer()


@router.callback_query((F.data == "noop") | (F.data == "action:noop"))
async def cb_noop(callback: CallbackQuery) -> None:
    """Handle no-op button clicks."""
    await callback.answer()


@router.callback_query(F.data == "membership:verify")
async def cb_membership_verify(
    callback: CallbackQuery, uow: UnitOfWork, user: User
) -> None:
    """Re-check membership and unlock the bot when all chats are joined."""
    membership = MandatoryMembershipService(uow)
    if not await membership.verify_user(callback.bot, user):
        await callback.answer("هنوز عضویت شما کامل نشده است.", show_alert=True)
        return
    await callback.message.edit_text(
        MAIN_MENU(),
        reply_markup=main_menu_keyboard(
            is_admin=user.telegram_id in get_settings().admin_ids
        ),
    )
    await callback.answer("عضویت تایید شد ✅")
