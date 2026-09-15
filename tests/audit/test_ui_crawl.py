"""Phase 3 — end-to-end UI crawl.

Feeds real Telegram updates into the real dispatcher and clicks every button
reachable from the main menu for both a normal user and the owner/admin.
Records dead buttons (no handler) and crashing buttons (exception).
"""

from __future__ import annotations

import pytest

from bot.database.session import get_session_factory
from tests.audit.db import make_category, make_config, make_coupon, make_product, make_user, uid
from tests.audit.harness import crawl


@pytest.fixture
async def seeded():
    """Seed a realistic shop so the crawl reaches deep screens."""
    factory = get_session_factory()
    async with factory() as session:
        cat = await make_category(session)
        user = await make_user(session, balance=500_000, username="crawl_user")
        await make_product(session, price=50_000, stock=5, category_id=cat.id)
        await make_config(session, price=30_000, stock=3, category_id=cat.id)
        await make_coupon(session, code="AUDIT10", value=10)
        await session.commit()
        return {"user": user, "category": cat}


async def test_owner_crawl_all_buttons(sim, seeded):
    """Every button reachable by the owner must be answered and must not crash."""
    report = await crawl(sim, sim.owner_id, max_clicks=300)
    assert report.clicked, "crawler clicked nothing"
    assert not report.crashed, f"button handlers crashed: {report.crashed}"
    assert not report.dead, f"dead buttons (no handler): {report.dead}"


async def test_new_user_start_flow(sim):
    """A brand-new user pressing /start must get the welcome screen."""
    res = await sim.send("/start", 999_111_222, first_name="Newbie", username="newbie")
    assert res["ok"], res["error"]
    last = sim.last_screen(999_111_222)
    assert last is not None, "no message sent to a new user"
    assert "خوش آمدید" in (last["text"] or "") or "NOX" in (last["text"] or "")
    buttons = sim.buttons(last["reply_markup"])
    assert "menu:products" in buttons
    assert "menu:cart" in buttons
    # A regular user must NOT see the admin button.
    assert "admin:panel" not in buttons


async def test_owner_sees_admin_button(sim):
    await sim.send("/start", sim.owner_id, first_name="Owner")
    last = sim.last_screen(sim.owner_id)
    assert "admin:panel" in sim.buttons(last["reply_markup"])


async def test_banned_user_is_blocked(sim):
    factory = get_session_factory()
    tg_id = 999_333_444
    async with factory() as session:
        await make_user(session, telegram_id=tg_id, is_banned=True, username="banned_one")
        await session.commit()

    send_before = len(sim.session.sent)
    res = await sim.send("/start", tg_id, username="banned_one")
    assert res["ok"], res["error"]
    new = sim.session.sent[send_before:]
    assert not new, f"banned user received messages: {[m['text'] for m in new]}"


async def test_back_button_returns_to_previous_screen(sim, seeded):
    """Back from the products list must re-render the main menu."""
    await sim.send("/start", sim.owner_id, first_name="Owner")
    await sim.click("menu:products", sim.owner_id)
    home_buttons = sim.buttons(sim.last_screen(sim.owner_id)["reply_markup"])
    assert "menu:home" in home_buttons, "products screen has no back button"
    res = await sim.click("menu:home", sim.owner_id)
    assert res["ok"], res["error"]
    screen = sim.last_screen(sim.owner_id)
    assert "menu:products" in sim.buttons(screen["reply_markup"])


async def test_stale_callback_after_message_deleted_does_not_crash(sim, seeded):
    """Old callback data (message no longer on screen) must not crash handlers."""
    await sim.send("/start", sim.owner_id)
    res = await sim.click("cart:del:does-not-exist", sim.owner_id, message_id=1)
    assert res["ok"], res["error"]


async def test_all_callbacks_have_handlers(sim, seeded):
    """Static check: every callback_data literal in keyboards/handlers is handled.

    This is the automated version of "no dead button anywhere in the UI".
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "bot"
    literals: set[str] = set()
    pattern = re.compile(r'callback_data\s*=\s*f?["\']([^"\']+)["\']')
    # Keyboard helpers take the callback as a positional argument, so their
    # literals are invisible to the ``callback_data=`` scan above. A dead one
    # (``back_button("cart:view")``) shipped exactly because of that blind spot.
    helper_pattern = re.compile(
        r'(?:back_button|home_button|get_cancel_button)\(\s*["\']([^"\']+)["\']'
    )
    for path in list(root.rglob("*.py")):
        if "__pycache__" in str(path):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for match in list(pattern.finditer(text)) + list(helper_pattern.finditer(text)):
            value = match.group(1)
            # Drop f-string placeholders → keep the static prefix with a wildcard
            value = re.sub(r"\{[^}]*\}", "*", value)
            literals.add(value)

    from tests.audit.harness import CALLBACK_RE

    clicked = set()
    for literal in literals:
        if not CALLBACK_RE.match(literal.split("*")[0] + "x"):
            continue
        # Skip buttons that require real IDs (tested separately with live data)
        data = literal if "*" not in literal else literal.split("*")[0] + uid("probe")[:6]
        try:
            res = await sim.click(data, sim.owner_id, message_id=1)
        except Exception as exc:  # noqa: BLE001
            pytest.fail(f"callback {data!r} raised {type(exc).__name__}: {exc}")
        clicked.add(literal)
        if res["unhandled"] and "*" not in literal:
            pytest.fail(f"dead callback (no handler matched): {literal!r}")

    assert len(clicked) >= 30, f"only {len(clicked)} callback literals exercised"
