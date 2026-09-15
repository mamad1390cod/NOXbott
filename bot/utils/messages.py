"""Small message-input helpers for FSM handlers.

Handlers that ask the admin (or the customer) for a value are registered on a
state alone, so a photo, sticker, voice note or document reaches them with
``message.text is None`` — and ``message.text.strip()`` then raised
``AttributeError``: the update was answered with nothing, the exception was
logged, and the wizard stayed armed.
"""

from __future__ import annotations

from aiogram.types import Message

DEFAULT_PROMPT = "⚠️ لطفاً پاسخ را به‌صورت متن ارسال کنید:"


async def require_text(message: Message, prompt: str = DEFAULT_PROMPT) -> str | None:
    """Return the trimmed message text, or ask for text and return ``None``.

    Callers must ``return`` when the result is ``None`` (the user was already
    told what to send); the FSM state stays armed so the retry works.
    """
    if not message.text or not message.text.strip():
        await message.answer(prompt)
        return None
    return message.text.strip()
