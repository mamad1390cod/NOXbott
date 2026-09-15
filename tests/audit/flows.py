"""High-level user journeys for the audit suite (drives the real handlers)."""

from __future__ import annotations

from typing import Any

from bot.database.session import get_session_factory
from bot.models.user import User
from tests.audit.harness import TelegramSim


class ShopDriver:
    """Semantic wrapper around TelegramSim for the shop journeys."""

    def __init__(self, sim: TelegramSim, user_id: int) -> None:
        self.sim = sim
        self.user_id = user_id

    # -- low level --------------------------------------------------------- #
    async def send(self, text: str, **kw: Any) -> dict[str, Any]:
        return await self.sim.send(text, self.user_id, **kw)

    async def click(self, data: str, **kw: Any) -> dict[str, Any]:
        return await self.sim.click(data, self.user_id, **kw)

    def screen_text(self) -> str:
        screen = self.sim.last_screen(self.user_id)
        return (screen or {}).get("text") or ""

    def buttons(self) -> list[str]:
        screen = self.sim.last_screen(self.user_id)
        return self.sim.buttons((screen or {}).get("reply_markup"))

    def alerts(self) -> list[str]:
        """Texts of all answerCallbackQuery(show_alert=True) calls for this user."""
        out = []
        for payload in self.sim.session.calls_named("AnswerCallbackQuery"):
            if payload.get("show_alert") and payload.get("text"):
                out.append(payload["text"])
        return out

    def alert_texts(self) -> list[str]:
        return [p.get("text") for p in self.sim.session.calls_named("AnswerCallbackQuery") if p.get("text")]

    # -- journeys ---------------------------------------------------------- #
    async def start(self) -> dict[str, Any]:
        return await self.send("/start")

    async def add_product_to_cart(self, category_id: str, product_id: str) -> None:
        await self.click("menu:products")
        await self.click(f"prod_cat:{category_id}")
        await self.click(f"prod_sel:{product_id}")
        await self.click(f"prod_add:{product_id}")

    async def open_cart(self) -> None:
        await self.click("menu:cart")

    async def checkout_wallet(self) -> dict[str, Any]:
        await self.click("cart:checkout")
        return await self.click("checkout:confirm")

    async def submit_receipt_flow(self, order_id: str, file_id: str = "receipt_1") -> dict[str, Any]:
        await self.click(f"pay:submit:{order_id}")
        return await self.sim.send_photo(self.user_id, file_id=file_id)

    async def checkout_card(self, file_id: str = "card_receipt_1") -> dict[str, Any]:
        """Insufficient-balance journey: choose card payment and send the receipt."""
        await self.click("cart:checkout")
        res = await self.click("checkout:card")
        if not res["ok"]:
            return res
        return await self.sim.send_photo(self.user_id, file_id=file_id)


async def finish_account_info(user_id: str) -> None:
    """Mark a user as having complete customer info so add-to-cart is direct."""
    factory = get_session_factory()
    async with factory() as session:
        user = await session.get(User, user_id)
        user.email = "audit@example.com"
        user.password = "secret"
        user.customer_name = "Audit Customer"
        await session.commit()
