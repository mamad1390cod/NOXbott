"""Gate user interactions until required chats are joined."""

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from bot.services.mandatory_membership import MandatoryMembershipService
from bot.services.rbac import RbacService


class MandatoryMembershipMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("user")
        uow = data.get("uow")
        if user is None or uow is None:
            return await handler(event, data)
        if await RbacService(uow).is_admin(user):
            return await handler(event, data)
        if (
            isinstance(event, Message)
            and isinstance(event.text, str)
            and event.text.startswith("/start")
        ):
            return await handler(event, data)
        if isinstance(event, CallbackQuery) and event.data == "membership:verify":
            return await handler(event, data)

        if user.mandatory_membership_verified:
            return await handler(event, data)

        service = MandatoryMembershipService(uow)
        if await service.verify_user(data["bot"], user):
            return await handler(event, data)

        text = await service.gate_text()
        markup = await service.keyboard()
        if isinstance(event, CallbackQuery):
            await event.answer(
                "ابتدا عضو کانال‌ها/گروه‌ها شوید.",  # noqa: RUF001
                show_alert=True,
            )
            if event.message:
                await event.message.edit_text(text, reply_markup=markup)
        elif isinstance(event, Message):
            await event.answer(text, reply_markup=markup)
        return None
