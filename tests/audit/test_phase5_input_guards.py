"""Phase 5 — non-text messages in text states.

Every wizard state asked for a value and then did ``message.text.strip()``.
A photo, sticker, voice note or document reaches such a handler with
``message.text is None``: the update was answered with nothing, an
``AttributeError`` was logged and the wizard stayed armed. The structural test
below keeps the whole class fixed (it is what found the sites).
"""

from __future__ import annotations

import ast
import pathlib

from bot.database.session import get_session_factory
from sqlalchemy import select

from bot.models.user import User
from bot.services.rbac import RbacService
from bot.database.uow import UnitOfWork
from tests.audit.db import make_user

TEXTY = (
    "strip", "split", "splitlines", "upper", "lower", "replace",
    "isdigit", "startswith", "endswith", "lstrip", "rstrip",
)
HANDLERS = pathlib.Path(__file__).resolve().parents[2] / "bot" / "handlers"


def _unguarded_sites(path: pathlib.Path) -> list[int]:
    """Line numbers where ``message.text`` is dereferenced without a guard."""
    tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    found: list[int] = []
    for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))]:
        skip: set[int] = set()
        for deco in fn.decorator_list:
            skip.update(id(x) for x in ast.walk(deco))
        uses, handled = [], False
        for node in ast.walk(fn):
            if id(node) in skip:
                continue
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id == "require_text":
                    handled = True
                elif isinstance(node.func, ast.Attribute) and node.func.attr in TEXTY:
                    base = node.func.value
                    if isinstance(base, ast.Attribute) and base.attr == "text":
                        if isinstance(base.value, ast.Name) and base.value.id in {"F", "f"}:
                            continue  # aiogram magic filter — matches only when text exists
                        uses.append(node.lineno)
            if isinstance(node, (ast.If, ast.IfExp)) and any(
                isinstance(c, ast.Attribute) and c.attr == "text" for c in ast.walk(node.test)
            ):
                handled = True
        if uses and not handled:
            found.extend(uses)
    return found


def test_every_handler_guards_message_text():
    """No handler may dereference ``message.text`` without handling ``None``."""
    offenders: list[str] = []
    for path in sorted(HANDLERS.rglob("*.py")):
        for lineno in _unguarded_sites(path):
            offenders.append(f"{path.relative_to(HANDLERS.parents[1])}:{lineno}")
    assert not offenders, (
        "handlers crash on non-text messages (use bot.utils.messages.require_text): "
        + ", ".join(offenders)
    )


async def _bootstrap_owner(sim) -> str:
    factory = get_session_factory()
    async with factory() as session:
        owner = (
            await session.execute(select(User).where(User.telegram_id == sim.owner_id))
        ).scalars().first()
        if owner is None:
            owner = await make_user(session, username="guards_owner")
            owner.telegram_id = sim.owner_id
            await session.commit()
        owner_id = owner.id
    uow = UnitOfWork()
    async with uow:
        await RbacService(uow).seed_roles()
        await uow.commit()
    return owner_id


async def test_admin_order_filter_state_survives_a_photo(sim):
    """A photo in the order-number filter must ask for text, not crash."""
    await _bootstrap_owner(sim)
    await sim.send("/start", sim.owner_id, first_name="Owner")

    res = await sim.click("aorder:f_number", sim.owner_id)
    assert res["ok"], res["error"]
    res = await sim.send_photo(sim.owner_id, caption=None)
    assert res["ok"], res["error"]
    assert any(
        "متن" in (m.get("text") or "") for m in sim.session.sent_to(sim.owner_id)
    ), "the admin was not told to send text"

    # the state stayed armed: the real answer still works
    res = await sim.send("NOX-2026-000001", sim.owner_id)
    assert res["ok"], res["error"]
    assert any(
        "فیلتر شماره اعمال شد" in (m.get("text") or "") for m in sim.session.sent_to(sim.owner_id)
    ), "the wizard was lost after a non-text message"


async def test_finance_date_filter_state_survives_a_photo(sim):
    await _bootstrap_owner(sim)
    await sim.send("/start", sim.owner_id, first_name="Owner")

    res = await sim.click("fin:f_date", sim.owner_id)
    assert res["ok"], res["error"]
    res = await sim.send_photo(sim.owner_id)
    assert res["ok"], res["error"]

    res = await sim.send("2026-01-01", sim.owner_id)
    assert res["ok"], res["error"]
    res = await sim.send("2026-01-31", sim.owner_id)
    assert res["ok"], res["error"]
    assert any(
        "بازه تاریخ اعمال شد" in (m.get("text") or "") for m in sim.session.sent_to(sim.owner_id)
    ), "the date wizard did not survive the non-text message"


async def test_customer_discount_state_survives_a_photo(sim):
    """The customer-side discount wizard had the same crash."""
    from tests.audit.db import make_category, make_product
    from tests.audit.flows import ShopDriver, finish_account_info

    factory = get_session_factory()
    async with factory() as session:
        cat = await make_category(session)
        user = await make_user(session, username="guard_customer", balance=100_000)
        product = await make_product(session, price=50_000, stock=5, category_id=cat.id)
        await session.commit()
        tg_id, user_id, cat_id, product_id = (
            user.telegram_id, user.id, cat.id, product.id,
        )
    await finish_account_info(user_id)

    driver = ShopDriver(sim, tg_id)
    await driver.start()
    await driver.add_product_to_cart(cat_id, product_id)

    res = await sim.click("cart:add_discount", tg_id)
    assert res["ok"], res["error"]
    res = await sim.send_photo(tg_id)
    assert res["ok"], res["error"]
    assert any(
        "کد تخفیف" in (m.get("text") or "") for m in sim.session.sent_to(tg_id)
    ), "the customer was not asked for the code again"


async def test_finance_clear_answers_once(sim):
    """«پاک کردن فیلتر» must answer the tap exactly once."""
    await _bootstrap_owner(sim)
    await sim.send("/start", sim.owner_id, first_name="Owner")

    res = await sim.click("fin:clear", sim.owner_id)
    assert res["ok"], res["error"]
    answered = [n for (n, _p) in res["calls"] if n == "AnswerCallbackQuery"]
    assert len(answered) == 1, (
        f"the callback was answered {len(answered)} times (Telegram rejects the second)"
    )
