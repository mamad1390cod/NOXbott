"""Phase 8 — admin discount-code panel driven end-to-end through the UI."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from bot.database.session import get_session_factory
from bot.models.discount_code import DiscountCode
from tests.audit.harness import TelegramSim


@pytest.fixture
async def empty_shop():
    """Admin-only database state (settings were seeded by the harness)."""
    return {}


async def _open_discount_menu(sim: TelegramSim) -> None:
    await sim.send("/start", sim.owner_id, first_name="Owner")
    await sim.click("admin:panel", sim.owner_id)
    await sim.click("admin:discounts", sim.owner_id)


async def test_create_discount_code_wizard(sim, empty_shop):
    """The 6-step creation wizard must persist a code at the end."""
    await _open_discount_menu(sim)
    await sim.click("admin:discount:create", sim.owner_id)
    await sim.send("AUDITTEST", sim.owner_id)          # code
    await sim.click("admin:discount:type:percentage", sim.owner_id)
    await sim.send("15", sim.owner_id)                  # value
    await sim.click("admin:discount:skip_max_eligible", sim.owner_id)
    await sim.click("admin:discount:skip_expiration", sim.owner_id)
    await sim.click("admin:discount:skip_max_uses", sim.owner_id)
    await sim.click("admin:discount:skip_description", sim.owner_id)

    factory = get_session_factory()
    async with factory() as session:
        code = (
            await session.execute(select(DiscountCode).where(DiscountCode.code == "AUDITTEST"))
        ).scalar_one_or_none()

    assert code is not None, (
        "discount code wizard did not persist the code — last screen: "
        f"{sim.last_screen(sim.owner_id) and sim.last_screen(sim.owner_id)['text']}"
    )
    assert code.discount_value == 15


async def test_discount_edit_button_works(sim, empty_shop):
    """The ✏️ ویرایش button on the discount view screen must do something."""
    factory = get_session_factory()
    async with factory() as session:
        code = DiscountCode(code="EDITME", discount_type="percentage", discount_value=5)
        session.add(code)
        await session.commit()
        code_id = code.id

    await _open_discount_menu(sim)
    await sim.click(f"admin:discount:view:{code_id}", sim.owner_id)
    res = await sim.click(f"admin:discount:edit:{code_id}", sim.owner_id)
    assert res["ok"], res["error"]
    assert not res["unhandled"], "«✏️ ویرایش» button has no handler (dead button)"

    # Change the value through the edit flow.
    await sim.click(f"admin:discount:edit_field:{code_id}:value", sim.owner_id)
    await sim.send("25", sim.owner_id)

    async with factory() as session:
        updated = await session.get(DiscountCode, code_id)
    assert updated.discount_value == 25, f"edit flow did not update the code ({updated.discount_value})"


async def test_discount_list_and_pagination(sim, empty_shop):
    factory = get_session_factory()
    async with factory() as session:
        for i in range(12):
            session.add(
                DiscountCode(code=f"BULK{i:02d}", discount_type="percentage", discount_value=5)
            )
        await session.commit()

    await _open_discount_menu(sim)
    await sim.click("admin:discount:list", sim.owner_id)
    screen = sim.last_screen(sim.owner_id)
    assert "لیست کدهای تخفیف" in (screen["text"] or "")
    buttons = sim.buttons(screen["reply_markup"])
    assert any(b.startswith("admin:discount:list:1") for b in buttons), buttons
    res = await sim.click("admin:discount:list:1", sim.owner_id)
    assert res["ok"] and not res["unhandled"], "pagination callback is dead"


async def test_discount_activate_deactivate_roundtrip(sim, empty_shop):
    factory = get_session_factory()
    async with factory() as session:
        code = DiscountCode(code="TOGGLEME", discount_type="percentage", discount_value=10)
        session.add(code)
        await session.commit()
        code_id = code.id

    await _open_discount_menu(sim)
    await sim.click(f"admin:discount:view:{code_id}", sim.owner_id)

    res = await sim.click(f"admin:discount:deactivate:{code_id}", sim.owner_id)
    assert res["ok"], res["error"]
    async with factory() as session:
        assert (await session.get(DiscountCode, code_id)).is_active is False

    res = await sim.click(f"admin:discount:activate:{code_id}", sim.owner_id)
    assert res["ok"], res["error"]
    async with factory() as session:
        assert (await session.get(DiscountCode, code_id)).is_active is True
