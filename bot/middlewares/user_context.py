"""User context middleware — registers users, updates activity, blocks bans."""

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import TelegramObject

from bot.services.user import UserService

logger = logging.getLogger(__name__)


class UserContextMiddleware(BaseMiddleware):
    """Ensures every user is registered in DB, updates last activity,
    and blocks banned users.

    Injects ``user`` (the ORM User) and ``uow`` into handler data.
    """

    def __init__(self) -> None:
        super().__init__()

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user_info = getattr(event, "from_user", None)
        if user_info is None:
            return await handler(event, data)

        from bot.database.uow import UnitOfWork as UOW

        uow = UOW()
        try:
            async with uow:
                user_service = UserService(uow)
                user = await user_service.get_or_create_user(
                    telegram_id=user_info.id,
                    username=user_info.username,
                    first_name=user_info.first_name,
                    last_name=user_info.last_name,
                    language_code=getattr(user_info, "language_code", "fa") or "fa",
                )

                # Block banned users from any interaction.
                if user.is_banned:
                    data["user"] = user
                    data["uow"] = uow
                    data["banned"] = True
                    return None

                # Update activity (but not on every single callback to save writes).
                await user_service.update_activity(user.id)

                data["user"] = user
                data["uow"] = uow
                data["banned"] = False
                
                # Call handler and ensure commit happens
                result = await handler(event, data)
                # Commit will happen automatically when exiting async with block
                return result
        except TelegramBadRequest as e:
            req_id = data.get("req_id", "UNKNOWN")
            user_id_str = str(user_info.id)[:8] + "..." if user_info else "UNKNOWN"
            if "message is not modified" in str(e).lower():
                logger.info(
                    "Ignored idempotent Telegram edit failure [req=%s, user=%s]",
                    req_id,
                    user_id_str,
                )
                return None
            logger.exception(
                "Telegram request failed [req=%s, user=%s]", req_id, user_id_str
            )
            raise
        except Exception:
            req_id = data.get("req_id", "UNKNOWN")
            user_id_str = str(user_info.id)[:8] + "..." if user_info else "UNKNOWN"
            logger.exception(
                "UserContextMiddleware error [req=%s, user=%s]",
                req_id,
                user_id_str,
            )
            raise