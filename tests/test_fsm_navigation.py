from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import CallbackQuery, User

from bot.middlewares.fsm_navigation import FsmNavigationMiddleware


@pytest.mark.asyncio
async def test_navigation_clears_active_fsm():
    state = SimpleNamespace(clear=AsyncMock())
    event = CallbackQuery(
        id="callback-1",
        from_user=User(id=1, is_bot=False, first_name="Test"),
        chat_instance="chat",
        data="menu:support",
    )
    handler = AsyncMock(return_value="handled")

    result = await FsmNavigationMiddleware()(handler, event, {"state": state})

    assert result == "handled"
    state.clear.assert_awaited_once()


@pytest.mark.asyncio
async def test_stateful_confirmation_keeps_fsm_data():
    state = SimpleNamespace(clear=AsyncMock())
    event = CallbackQuery(
        id="callback-2",
        from_user=User(id=1, is_bot=False, first_name="Test"),
        chat_instance="chat",
        data="account:confirm",
    )
    handler = AsyncMock(return_value="handled")

    await FsmNavigationMiddleware()(handler, event, {"state": state})

    state.clear.assert_not_awaited()
