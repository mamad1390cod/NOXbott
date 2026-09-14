"""Mandatory channel/group membership checks."""

import json
import logging
from typing import Any

from aiogram import Bot
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.database.uow import UnitOfWork
from bot.services.settings import SettingsService

logger = logging.getLogger(__name__)

SETTING_KEY = "mandatory_memberships"


def _normalize(items: Any) -> list[dict[str, str]]:
    if not isinstance(items, list):
        return []
    result = []
    for item in items:
        if not isinstance(item, dict):
            continue
        chat_id = str(item.get("chat_id", "")).strip()
        title = str(item.get("title", "")).strip()
        link = str(item.get("link", "")).strip()
        if chat_id and title and link:
            result.append({"chat_id": chat_id, "title": title, "link": link})
    return result


class MandatoryMembershipService:
    """Stores and verifies required Telegram memberships."""

    def __init__(self, uow: UnitOfWork) -> None:
        self.uow = uow
        self.settings = SettingsService(uow)

    async def get_all(self) -> list[dict[str, str]]:
        raw = await self.settings.get(SETTING_KEY, "[]")
        try:
            return _normalize(json.loads(raw or "[]"))
        except json.JSONDecodeError:
            logger.error("Invalid mandatory membership setting")
            return []

    async def save_all(self, items: list[dict[str, str]]) -> None:
        value = json.dumps(_normalize(items), ensure_ascii=False)
        await self.settings.set(SETTING_KEY, value)

    async def add(self, title: str, chat_id: str, link: str) -> list[dict[str, str]]:
        items = await self.get_all()
        if any(item["chat_id"] == chat_id for item in items):
            raise ValueError("این شناسه قبلاً ثبت شده است")
        items.append(
            {"title": title.strip(), "chat_id": chat_id.strip(), "link": link.strip()}
        )
        await self.save_all(items)
        return items

    async def remove(self, index: int) -> list[dict[str, str]]:
        items = await self.get_all()
        if index < 0 or index >= len(items):
            raise ValueError("مورد یافت نشد")
        items.pop(index)
        await self.save_all(items)
        return items

    async def update(
        self, index: int, title: str, chat_id: str, link: str
    ) -> list[dict[str, str]]:
        items = await self.get_all()
        if index < 0 or index >= len(items):
            raise ValueError("مورد یافت نشد")
        if any(
            i != index and item["chat_id"] == chat_id
            for i, item in enumerate(items)
        ):
            raise ValueError("این شناسه قبلاً ثبت شده است")
        items[index] = {
            "title": title.strip(),
            "chat_id": chat_id.strip(),
            "link": link.strip(),
        }
        await self.save_all(items)
        return items

    async def is_member(self, bot: Bot, user_id: int) -> bool:
        for item in await self.get_all():
            try:
                member = await bot.get_chat_member(
                    chat_id=item["chat_id"], user_id=user_id
                )
            except (TelegramBadRequest, TelegramForbiddenError) as exc:
                logger.warning(
                    "Could not verify required chat %s: %s", item["chat_id"], exc
                )
                return False
            if member.status in {ChatMemberStatus.LEFT, ChatMemberStatus.KICKED}:
                return False
            if member.status == ChatMemberStatus.RESTRICTED and not member.is_member:
                return False
        return True

    async def verify_user(self, bot: Bot, user) -> bool:
        """Check membership and persist the successful verification."""
        verified = await self.is_member(bot, user.telegram_id)
        user.mandatory_membership_verified = verified
        await self.uow.flush()
        return verified

    async def keyboard(self) -> InlineKeyboardMarkup:
        items = await self.get_all()
        rows = [
            [InlineKeyboardButton(text=f"📢 {item['title']}", url=item["link"])]
            for item in items
        ]
        rows.append(
            [
                InlineKeyboardButton(
                    text="✅ بررسی عضویت", callback_data="membership:verify"
                )
            ]
        )
        return InlineKeyboardMarkup(inline_keyboard=rows)

    async def gate_text(self) -> str:
        items = await self.get_all()
        titles = "\n".join(f"• {item['title']}" for item in items)
        return (
            "🔒 <b>عضویت اجباری</b>\n\n"
            "برای استفاده از امکانات بات ابتدا در موارد زیر عضو شوید:\n"
            f"{titles}\n\n"
            "پس از عضویت، روی «بررسی عضویت» بزنید."
        )
