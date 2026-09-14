from types import SimpleNamespace

import pytest
from aiogram.enums import ChatType

from bot.middlewares.private_chat import PrivateChatOnlyMiddleware


@pytest.mark.asyncio
async def test_group_message_is_dropped_before_handlers():
    called = False

    async def handler(event, data):
        nonlocal called
        called = True
        return "handled"

    event = SimpleNamespace(chat=SimpleNamespace(type=ChatType.GROUP))
    result = await PrivateChatOnlyMiddleware()(handler, event, {})

    assert result is None
    assert called is False


@pytest.mark.asyncio
async def test_private_message_reaches_handlers():
    async def handler(event, data):
        return "handled"

    event = SimpleNamespace(chat=SimpleNamespace(type=ChatType.PRIVATE))
    result = await PrivateChatOnlyMiddleware()(handler, event, {})

    assert result == "handled"
