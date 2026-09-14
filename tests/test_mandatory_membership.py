import json
from types import SimpleNamespace

import pytest
from aiogram.enums import ChatMemberStatus

from bot.services.mandatory_membership import SETTING_KEY, MandatoryMembershipService


class FakeSettings:
    def __init__(self):
        self.value = "[]"

    async def get_by_key(self, key):
        return SimpleNamespace(value=self.value) if key == SETTING_KEY else None

    async def set_value(self, key, value, **kwargs):
        self.value = value
        return SimpleNamespace(value=value)


class FakeUow:
    def __init__(self):
        self.settings = FakeSettings()

    async def flush(self):
        return None


@pytest.mark.asyncio
async def test_mandatory_membership_crud_and_json_storage():
    uow = FakeUow()
    service = MandatoryMembershipService(uow)

    await service.add("کانال اصلی", "-1001", "https://t.me/main")
    assert await service.get_all() == [
        {"title": "کانال اصلی", "chat_id": "-1001", "link": "https://t.me/main"}
    ]

    await service.update(0, "گروه اصلی", "-1002", "https://t.me/group")
    assert (await service.get_all())[0]["chat_id"] == "-1002"

    await service.remove(0)
    assert await service.get_all() == []
    json.loads(uow.settings.value)


@pytest.mark.asyncio
async def test_verification_is_persisted_and_revoked_after_leaving():
    uow = FakeUow()
    service = MandatoryMembershipService(uow)
    await service.add("کانال اصلی", "-1001", "https://t.me/main")

    class FakeBot:
        status = ChatMemberStatus.MEMBER

        async def get_chat_member(self, **kwargs):
            return SimpleNamespace(status=self.status, is_member=True)

    user = SimpleNamespace(telegram_id=42, mandatory_membership_verified=False)
    bot = FakeBot()

    assert await service.verify_user(bot, user) is True
    assert user.mandatory_membership_verified is True

    bot.status = ChatMemberStatus.LEFT
    assert await service.verify_user(bot, user) is False
    assert user.mandatory_membership_verified is False
