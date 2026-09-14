from types import SimpleNamespace

import pytest

from bot.config import get_settings
from bot.middlewares.throttling import ThrottlingMiddleware


@pytest.mark.asyncio
async def test_owner_callbacks_are_not_dropped_by_throttle():
    middleware = ThrottlingMiddleware(rate_limit=1, time_window=60)
    event = SimpleNamespace(from_user=SimpleNamespace(id=get_settings().admin_id))
    calls = 0

    async def handler(event, data):
        nonlocal calls
        calls += 1
        return "handled"

    assert await middleware(handler, event, {}) == "handled"
    assert await middleware(handler, event, {}) == "handled"
    assert calls == 2


@pytest.mark.asyncio
async def test_regular_users_are_still_throttled():
    middleware = ThrottlingMiddleware(rate_limit=1, time_window=60)
    event = SimpleNamespace(from_user=SimpleNamespace(id=123456789))

    async def handler(event, data):
        return "handled"

    assert await middleware(handler, event, {}) == "handled"
    assert await middleware(handler, event, {}) is None
