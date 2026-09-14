"""Allow bot interactions only in private chats."""

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.enums import ChatType
from aiogram.types import CallbackQuery, Message, TelegramObject


class PrivateChatOnlyMiddleware(BaseMiddleware):
    """Drop group/channel updates before they reach any handler."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        chat_type: ChatType | str | None = None
        if isinstance(event, Message):
            chat_type = event.chat.type
        elif isinstance(event, CallbackQuery) and event.message:
            chat_type = event.message.chat.type
        else:
            chat = getattr(event, "chat", None)
            chat_type = getattr(chat, "type", None)

        if chat_type is not None and chat_type != ChatType.PRIVATE:
            return None
        return await handler(event, data)
