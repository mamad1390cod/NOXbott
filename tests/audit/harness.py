"""End-to-end test harness for NOXbot.

The harness replaces Telegram's transport with an in-memory fake session so the
*real* dispatcher, middlewares, filters, routers and handlers are exercised
exactly as they run in production — only the network boundary is faked.

Capabilities:
* `TelegramSim` — feeds real `Update` objects into the real Dispatcher and
  records every outgoing Telegram API call.
* Button crawling — walks every reachable screen/button pair and reports
  callbacks that no handler answered ("dead buttons") and handlers that raised.
"""

from __future__ import annotations

import itertools
import re
import time
from datetime import datetime, timezone
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ChatType, ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import TelegramMethod
from aiogram.types import (
    CallbackQuery,
    Chat,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    PhotoSize,
    Update,
    User as TgUser,
)

# --------------------------------------------------------------------------- #
#  Fake transport
# --------------------------------------------------------------------------- #


class FakeTelegramSession(BaseSession):
    """In-memory Telegram session: records calls, synthesises responses."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.sent: list[dict[str, Any]] = []
        self._mid = itertools.count(10_000)
        self.bot_id = 777_000
        self.bot_username = "nox_test_bot"
        # Fault injection
        self.fail_edit_not_modified = False
        self.fail_next_with: dict[str, str] = {}

    # -- BaseSession interface ------------------------------------------- #
    async def close(self) -> None:  # pragma: no cover - nothing to release
        return None

    async def stream_content(self, *args: Any, **kwargs: Any):  # noqa: ANN201
        if False:  # pragma: no cover
            yield b""
        return

    def _bot_user(self) -> TgUser:
        return TgUser(id=self.bot_id, is_bot=True, first_name="NOXbot", username=self.bot_username)

    def _chat(self, chat_id: Any) -> Chat:
        cid = int(chat_id) if str(chat_id).lstrip("-").isdigit() else abs(hash(str(chat_id))) % 10**9
        return Chat(id=cid, type=ChatType.PRIVATE)

    async def make_request(self, bot: Bot, method: TelegramMethod[Any], timeout: int | None = None) -> Any:
        name = type(method).__name__
        payload = method.model_dump(exclude_none=True)
        self.calls.append((name, payload))

        error = self.fail_next_with.pop(name, None)
        if error:
            raise TelegramBadRequest(method=method, message=error)
        if name in ("EditMessageText", "EditMessageCaption") and self.fail_edit_not_modified:
            raise TelegramBadRequest(
                method=method,
                message="Bad Request: message is not modified: specified new message content "
                "and reply markup are exactly the same as a current content and reply markup of the message",
            )

        if name == "GetMe":
            return self._bot_user()

        if name in ("SendMessage", "SendPhoto", "SendDocument", "SendVideo", "SendAnimation"):
            text = payload.get("text") or payload.get("caption") or ""
            msg = Message(
                message_id=next(self._mid),
                date=datetime.now(timezone.utc),
                chat=self._chat(payload.get("chat_id", 0)),
                from_user=self._bot_user(),
                text=payload.get("text"),
                caption=payload.get("caption"),
                photo=(
                    [PhotoSize(file_id="bot_photo", file_unique_id="u", width=1, height=1)]
                    if name == "SendPhoto"
                    else None
                ),
            )
            self.sent.append(
                {
                    "kind": name,
                    "chat_id": payload.get("chat_id"),
                    "message_id": msg.message_id,
                    "text": text,
                    "reply_markup": payload.get("reply_markup"),
                    "parse_mode": payload.get("parse_mode"),
                    "at": time.time(),
                }
            )
            return msg

        if name in ("EditMessageText", "EditMessageCaption"):
            mid = payload.get("message_id") or 0
            chat_id = payload.get("chat_id")
            text = payload.get("text") or payload.get("caption") or ""
            self.sent.append(
                {
                    "kind": "Edit" + name,
                    "chat_id": chat_id,
                    "message_id": mid,
                    "text": text,
                    "reply_markup": payload.get("reply_markup"),
                    "parse_mode": payload.get("parse_mode"),
                    "at": time.time(),
                }
            )
            return Message(
                message_id=int(mid or 1),
                date=datetime.now(timezone.utc),
                chat=self._chat(chat_id if chat_id is not None else 0),
                from_user=self._bot_user(),
                text=payload.get("text"),
                caption=payload.get("caption"),
            )

        if name == "CopyMessage":
            from aiogram.types import MessageId

            return MessageId(message_id=next(self._mid))

        if name == "ForwardMessage":
            return Message(
                message_id=next(self._mid),
                date=datetime.now(timezone.utc),
                chat=self._chat(payload.get("chat_id", 0)),
                from_user=self._bot_user(),
            )

        if name == "SendChatAction":
            return True

        return True

    # -- Assertions helpers ---------------------------------------------- #
    def texts(self) -> list[str]:
        return [m["text"] for m in self.sent]

    def sent_to(self, chat_id: int) -> list[dict[str, Any]]:
        return [m for m in self.sent if str(m["chat_id"]) == str(chat_id)]

    def last_to(self, chat_id: int) -> dict[str, Any] | None:
        found = self.sent_to(chat_id)
        return found[-1] if found else None

    def calls_named(self, name: str) -> list[dict[str, Any]]:
        return [p for (n, p) in self.calls if n == name]

    def clear(self) -> None:
        self.calls.clear()
        self.sent.clear()


# --------------------------------------------------------------------------- #
#  Event bookkeeping (dead buttons / unhandled events / errors)
# --------------------------------------------------------------------------- #


class EventLog:
    def __init__(self) -> None:
        self.unhandled: list[str] = []
        self.errors: list[dict[str, Any]] = []
        self.handled: list[str] = []

    def reset(self) -> None:
        self.unhandled.clear()
        self.errors.clear()
        self.handled.clear()


# --------------------------------------------------------------------------- #
#  Telegram simulator
# --------------------------------------------------------------------------- #


class TelegramSim:
    """Drives the real Dispatcher with fake Telegram updates."""

    def __init__(self, owner_id: int = 999_000_001) -> None:
        self.session = FakeTelegramSession()
        self.bot = Bot(
            token="123456:TESTTOKEN",
            session=self.session,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        self.storage = MemoryStorage()
        self.dp = Dispatcher(storage=self.storage)
        self.events = EventLog()
        self._update_id = itertools.count(1)
        self.owner_id = owner_id

        # Swap the shared loader singletons so handlers using get_bot() get the fake.
        import bot.loader as loader

        loader._bot = self.bot
        loader._dp = self.dp

        from bot.handlers import admin_router, user_router
        from bot.middlewares import (
            AbuseMiddleware,
            FsmNavigationMiddleware,
            MaintenanceMiddleware,
            MandatoryMembershipMiddleware,
            PrivateChatOnlyMiddleware,
            RbacMiddleware,
            ThrottlingMiddleware,
            UserContextMiddleware,
        )
        from bot.middlewares.request_context import RequestContextMiddleware

        self.dp.update.outer_middleware(RequestContextMiddleware())
        for observer in (self.dp.message, self.dp.callback_query):
            observer.middleware(PrivateChatOnlyMiddleware())
        self.dp.callback_query.middleware(FsmNavigationMiddleware())
        for observer in (self.dp.message, self.dp.callback_query, self.dp.my_chat_member):
            observer.middleware(UserContextMiddleware())
        for observer in (self.dp.message, self.dp.callback_query, self.dp.my_chat_member):
            observer.middleware(MaintenanceMiddleware())
        for observer in (self.dp.message, self.dp.callback_query, self.dp.my_chat_member):
            observer.middleware(AbuseMiddleware())
        for observer in (self.dp.message, self.dp.callback_query, self.dp.my_chat_member):
            observer.middleware(RbacMiddleware())
        for observer in (self.dp.message, self.dp.callback_query):
            observer.middleware(ThrottlingMiddleware(rate_limit=10_000))

        self.dp.include_router(user_router)
        self.dp.include_router(admin_router)
        self._mount_dead_probe()

    # -- probe router ----------------------------------------------------- #
    def _mount_dead_probe(self) -> None:
        """Trailing router that only runs when nothing else handled the event."""
        from aiogram import Router

        probe = Router(name="dead_probe")

        @probe.callback_query()
        async def _unhandled_cb(callback: CallbackQuery) -> None:  # noqa: ANN202
            self.events.unhandled.append(f"callback:{callback.data}")

        @probe.message()
        async def _unhandled_msg(message: Message) -> None:  # noqa: ANN202
            self.events.unhandled.append(f"message:{message.text or message.content_type}")

        self.dp.include_router(probe)

    # -- update factory --------------------------------------------------- #
    def tg_user(self, user_id: int, first_name: str = "Test", username: str | None = "tester") -> TgUser:
        return TgUser(id=user_id, is_bot=False, first_name=first_name, username=username)

    def _chat(self, user_id: int) -> Chat:
        return Chat(id=user_id, type=ChatType.PRIVATE)

    async def send(
        self,
        text: str,
        user_id: int,
        *,
        first_name: str = "Test",
        username: str | None = "tester",
        expect_error: bool = False,
    ) -> dict[str, Any]:
        """Feed a text message into the dispatcher."""
        self.events.reset()
        update = Update(
            update_id=next(self._update_id),
            message=Message(
                message_id=next(self._update_id) + 100_000,
                date=datetime.now(timezone.utc),
                chat=self._chat(user_id),
                from_user=self.tg_user(user_id, first_name, username),
                text=text,
            ),
        )
        return await self._feed(update, expect_error=expect_error)

    async def send_photo(
        self,
        user_id: int,
        caption: str | None = None,
        *,
        file_id: str | None = None,
        expect_error: bool = False,
    ) -> dict[str, Any]:
        """Feed a photo message (PaymentStates.waiting_receipt)."""
        self.events.reset()
        fid = file_id or f"photo_{next(self._update_id)}"
        update = Update(
            update_id=next(self._update_id),
            message=Message(
                message_id=next(self._update_id) + 100_000,
                date=datetime.now(timezone.utc),
                chat=self._chat(user_id),
                from_user=self.tg_user(user_id),
                caption=caption,
                photo=[PhotoSize(file_id=fid, file_unique_id=fid, width=90, height=90)],
            ),
        )
        return await self._feed(update, expect_error=expect_error)

    async def click(
        self,
        callback_data: str,
        user_id: int,
        *,
        message_id: int | None = None,
        chat_id: int | None = None,
        expect_error: bool = False,
        text: str | None = None,
    ) -> dict[str, Any]:
        """Feed a callback query as if the user pressed the button."""
        self.events.reset()
        cid = chat_id if chat_id is not None else user_id
        mid = message_id if message_id is not None else self._last_message_id(cid) or 1
        update = Update(
            update_id=next(self._update_id),
            callback_query=CallbackQuery(
                id=f"cb{next(self._update_id)}",
                from_user=self.tg_user(user_id),
                chat_instance="ci",
                data=callback_data,
                message=Message(
                    message_id=mid,
                    date=datetime.now(timezone.utc),
                    chat=self._chat(cid),
                    from_user=self.bot_user(),
                    text=text if text is not None else "screen",
                    reply_markup=InlineKeyboardMarkup(
                        inline_keyboard=[[InlineKeyboardButton(text="x", callback_data=callback_data)]]
                    ),
                ),
            ),
        )
        return await self._feed(update, expect_error=expect_error)

    def bot_user(self) -> TgUser:
        return TgUser(id=self.session.bot_id, is_bot=True, first_name="NOXbot", username=self.session.bot_username)

    def _last_message_id(self, chat_id: int) -> int | None:
        for msg in reversed(self.session.sent):
            if str(msg["chat_id"]) == str(chat_id) and msg["kind"].startswith("Send"):
                return msg["message_id"]
        return None

    async def _feed(self, update: Update, *, expect_error: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {"ok": True, "error": None, "result": None}
        before = len(self.session.calls)
        try:
            result["result"] = await self.dp.feed_update(self.bot, update)
        except Exception as exc:  # noqa: BLE001 - harness must capture everything
            result["ok"] = False
            result["error"] = exc
            self.events.errors.append({"error": repr(exc), "type": type(exc).__name__})
            if not expect_error:
                raise
        finally:
            result["calls"] = self.session.calls[before:]
            result["unhandled"] = list(self.events.unhandled)
        return result

    # -- button helpers --------------------------------------------------- #
    @staticmethod
    def buttons(markup: Any) -> list[str]:
        """Extract callback_data values from a raw reply_markup dict."""
        if not markup:
            return []
        rows = markup.get("inline_keyboard") if isinstance(markup, dict) else None
        if not rows:
            return []
        out: list[str] = []
        for row in rows:
            for btn in row:
                data = btn.get("callback_data")
                if data is not None:
                    out.append(data)
        return out

    def last_screen(self, chat_id: int) -> dict[str, Any] | None:
        return self.session.last_to(chat_id)


# --------------------------------------------------------------------------- #
#  UI crawler
# --------------------------------------------------------------------------- #


CALLBACK_RE = re.compile(r"^[A-Za-z0-9_:.\-]{1,64}$")


class CrawlReport:
    def __init__(self) -> None:
        self.clicked: list[str] = []
        self.dead: list[str] = []
        self.crashed: list[tuple[str, str]] = []
        self.alert_only: list[str] = []
        self.screens: dict[str, list[str]] = {}
        self.visited_buttons: set[str] = set()


async def crawl(
    sim: TelegramSim,
    user_id: int,
    *,
    start: str = "/start",
    max_clicks: int = 400,
    skip_prefixes: tuple[str, ...] = (),
) -> CrawlReport:
    """Breadth-first click of every button reachable from the entry screen.

    A button is "dead" when no router/handler answered its callback. A button is
    "crashed" when its handler raised an exception.
    """
    report = CrawlReport()
    await sim.send(start, user_id)
    queue: list[tuple[str, int, str]] = []  # (callback_data, message_id, screen_text)

    def enqueue_from(chat_id: int) -> None:
        screen = sim.session.last_to(chat_id)
        if not screen:
            return
        for data in sim.buttons(screen["reply_markup"]):
            if not CALLBACK_RE.match(data):
                continue
            if any(data.startswith(p) for p in skip_prefixes):
                continue
            key = data
            if key in report.visited_buttons or any(q[0] == data for q in queue):
                continue
            queue.append((data, screen["message_id"], screen["text"] or ""))

    enqueue_from(user_id)
    clicks = 0
    while queue and clicks < max_clicks:
        data, message_id, screen_text = queue.pop(0)
        if data in report.visited_buttons:
            continue
        report.visited_buttons.add(data)
        clicks += 1
        screenshot_before = len(sim.session.sent)
        try:
            res = await sim.click(data, user_id, message_id=message_id, text=screen_text)
        except Exception as exc:  # noqa: BLE001
            report.crashed.append((data, f"{type(exc).__name__}: {exc}"))
            continue
        report.clicked.append(data)
        if res["unhandled"]:
            report.dead.append(data)
            continue
        # Record the screen(s) produced by the click.
        for msg in sim.session.sent[screenshot_before:]:
            if msg["kind"].startswith("Send") or msg["kind"].startswith("Edit"):
                report.screens.setdefault(data, []).append((msg["text"] or "")[:120])
        enqueue_from(user_id)
        # Some flows answer in the user's private chat (admin notifications etc.)
        if sim.owner_id != user_id:
            enqueue_from(sim.owner_id)
    return report
