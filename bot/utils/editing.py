"""Safe message-editing helpers.

Telegram puts two hard constraints on editing an existing message that the bot's
screens walk straight into:

1. an edit whose content + reply markup are identical to the current message is
   rejected with ``message is not modified`` (e.g. tapping the same button
   twice, or navigating back to the screen already on display);
2. a message's *type* cannot be changed: text cannot be edited into media
   (``there is no media in the message to edit``) and media cannot be edited
   into text (``there is no text in the message to edit``).

``UserContextMiddleware`` deliberately swallows only the first error, so an
unhandled second one aborts the handler *silently* — the trailing
``callback.answer()`` never runs and the tapped button spins forever.

These helpers therefore (a) ignore "not modified" and (b) transparently replace
the message when the target content cannot fit the current message type, which
is what the user expects to see either way.
"""

import logging

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InputMediaPhoto

logger = logging.getLogger(__name__)

_NOT_MODIFIED = ("message is not modified", "not modified")
_NO_TEXT = ("there is no text in the message to edit",)
_NO_MEDIA = ("there is no media in the message to edit",)
_NO_CAPTION = ("there is no caption in the message to edit",)


def message_has_media(message) -> bool:
    """True when the message carries media (a caption-bearing message)."""
    if message is None:
        return False
    return bool(
        getattr(message, "photo", None)
        or getattr(message, "document", None)
        or getattr(message, "video", None)
        or getattr(message, "animation", None)
        or getattr(message, "audio", None)
    )


async def _replace_message(
    callback: CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    photo: str | None = None,
    parse_mode: str = "HTML",
) -> bool:
    """Send a fresh message and delete the one that cannot hold the content.

    Used when a screen switches between text and media: Telegram cannot edit a
    message across types, so the old message is dropped and the new screen takes
    its place. The new message is sent *before* the old one is deleted, so a
    failed delete still leaves a usable screen.
    """
    message = callback.message
    try:
        if photo is not None:
            await message.answer_photo(photo=photo, caption=text, reply_markup=reply_markup)
        else:
            await message.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except Exception as e:  # noqa: BLE001 - any send failure must not kill the handler
        logger.warning("could not replace message: %s", e)
        return False
    try:
        await message.delete()
    except Exception as e:  # noqa: BLE001 - deleting is best-effort
        logger.debug("could not delete the replaced message: %s", e)
    return True


async def safe_edit_text(
    callback: CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    parse_mode: str = "HTML",
) -> bool:
    """Show ``text`` on the callback's message, whatever its current type.

    Returns True when the screen shows the requested content (including the
    "already identical" no-op), False only when nothing could be shown.
    """
    # A photo/document message cannot be edited into a text message; replace it.
    if message_has_media(callback.message):
        return await _replace_message(callback, text, reply_markup, parse_mode=parse_mode)
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
        return True
    except TelegramBadRequest as e:
        if any(token in str(e) for token in _NOT_MODIFIED):
            return True  # already identical — nothing to do
        if any(token in str(e) for token in _NO_TEXT):
            return await _replace_message(callback, text, reply_markup, parse_mode=parse_mode)
        logger.warning("edit_text failed: %s", e)
        return False
    except Exception as e:  # noqa: BLE001
        logger.warning("edit_text error: %s", e)
        return False


async def safe_edit_caption(
    callback: CallbackQuery,
    caption: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> bool:
    """Like safe_edit_text but for media messages (changes caption only)."""
    if not message_has_media(callback.message):
        # No media to caption — show the text instead of failing.
        return await _replace_message(callback, caption, reply_markup)
    try:
        await callback.message.edit_caption(caption=caption, reply_markup=reply_markup)
        return True
    except TelegramBadRequest as e:
        if any(token in str(e) for token in _NOT_MODIFIED):
            return True
        if any(token in str(e) for token in _NO_CAPTION):
            return await _replace_message(callback, caption, reply_markup)
        logger.warning("edit_caption failed: %s", e)
        return False
    except Exception as e:  # noqa: BLE001
        logger.warning("edit_caption error: %s", e)
        return False


async def safe_edit_media(
    callback: CallbackQuery,
    media: InputMediaPhoto,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> bool:
    """Show a photo + caption screen, whatever the current message type is.

    A text message cannot be edited into a media message, so a banner screen
    reached from a text list (the normal path: list → tap an entry with a
    picture) replaces the message instead of doing nothing.
    """
    if not message_has_media(callback.message):
        return await _replace_message(
            callback,
            media.caption or "",
            reply_markup,
            photo=media.media,
        )
    try:
        await callback.message.edit_media(media, reply_markup=reply_markup)
        return True
    except TelegramBadRequest as e:
        if any(token in str(e) for token in _NOT_MODIFIED):
            return True
        if any(token in str(e) for token in _NO_MEDIA):
            return await _replace_message(
                callback, media.caption or "", reply_markup, photo=media.media
            )
        logger.warning("edit_media failed: %s", e)
        return False
    except Exception as e:  # noqa: BLE001
        logger.warning("edit_media error: %s", e)
        return False
