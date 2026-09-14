"""Clear stale FSM flows when a user navigates to another screen."""

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, TelegramObject


_NAVIGATION_PREFIXES = (
    "menu:",
    "admin:",
    "dash:",
    "ticket:",
    "fin:",
    "abuse:",
    "aprod:",
    "acat:",
    "aconfig:",
    "acustom:",
    "atick:",
    "apay:",
    "auser:",
    "abroadcast:",
    "aset:",
    "aorder:",
    "arole:",
    "atopup:",
    "amembership:",
    "backup:",
    "accat:",
)

# These callbacks intentionally consume data collected by the current flow.
_STATEFUL_CALLBACKS = {
    "account:confirm",
    "customreg:confirm",
    "abroad:send_now",
}


class FsmNavigationMiddleware(BaseMiddleware):
    """Reset stale form state before handling navigation callbacks."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, CallbackQuery):
            callback_data = event.data or ""
            state = data.get("state")
            is_navigation = (
                callback_data == "action:cancel"
                or callback_data.startswith(_NAVIGATION_PREFIXES)
            )
            if (
                is_navigation
                and callback_data not in _STATEFUL_CALLBACKS
                and callable(getattr(state, "clear", None))
            ):
                await state.clear()
        return await handler(event, data)
