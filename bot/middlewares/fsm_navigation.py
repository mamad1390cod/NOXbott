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

# Callback steps that live *inside* a multi-step form and therefore must keep
# the data collected by the earlier steps of that form.
_STATEFUL_PREFIXES = (
    "admin:discount:type:",
    "admin:discount:skip_",
    "admin:discount:edit_field:",
    "admin:roles:addrole:",
    "admin:roles:setrole:",
    "acustom:confirm_start_msg:",
)


class FsmNavigationMiddleware(BaseMiddleware):
    """Reset stale form state when a callback really navigates to another screen.

    Not every prefixed callback is navigation: multi-step forms have callback
    steps of their own (``admin:discount:type:...``, ``admin:roles:setrole:...``,
    ...). The old implementation cleared the FSM for *all* of them, so every
    wizard lost the data it had collected from earlier message steps.

    The middleware now observes whether the handler engaged with the FSM: a
    handler that reads/sets/clears state is part of flow and keeps its data,
    while a handler that leaves the FSM untouched is treated as a pure screen
    switch and the stale state is cleared afterwards.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, CallbackQuery):
            return await handler(event, data)

        callback_data = event.data or ""
        state = data.get("state")
        is_navigation = (
            callback_data == "action:cancel"
            or callback_data.startswith(_NAVIGATION_PREFIXES)
        )
        if (
            not is_navigation
            or callback_data in _STATEFUL_CALLBACKS
            or callback_data.startswith(_STATEFUL_PREFIXES)
            or not callable(getattr(state, "clear", None))
        ):
            return await handler(event, data)

        get_state = getattr(state, "get_state", None)
        get_data_ = getattr(state, "get_data", None)
        if not (callable(get_state) and callable(get_data_)):
            # FSM context that cannot be inspected (lightweight test doubles):
            # keep the legacy clear-before behavior.
            await state.clear()
            return await handler(event, data)

        before = (await state.get_state(), await state.get_data())
        result = await handler(event, data)
        after = (await state.get_state(), await state.get_data())
        if before == after and (before[0] is not None or before[1]):
            # The handler never touched the FSM: this was a real navigation to
            # another screen, so drop the stale flow.
            await state.clear()
        return result
