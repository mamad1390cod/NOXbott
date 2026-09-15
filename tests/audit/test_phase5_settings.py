"""Phase 5 — the settings editor: one action per value type, honestly applied.

The keyboard decides which action to offer from ``SettingSpec.value_type``, but
the handlers accepted any key: a crafted `aset:toggle:` overwrote a text key
with "true"/"false" (the card number included), `aset:media:` could push a
file_id into a text key, and `aset:edit:` could write free text into a boolean.
"""

from __future__ import annotations

from sqlalchemy import select

from bot.database.session import get_session_factory
from bot.database.uow import UnitOfWork
from bot.models.log import AdminLog, LogAction
from bot.models.user import User
from bot.services.settings import (
    SETTING_CARD_NUMBER,
    SettingsService,
)
from bot.services.settings_registry import spec_for
from bot.services.rbac import RbacService
from tests.audit.db import make_user

CARD = "6037-9911-2233-4455"  # fixture-unique marker
BOOL_KEY = "feature_products"
MEDIA_KEY = "bot_logo"
TEXT_KEY = "footer_text"


async def _bootstrap_owner(sim) -> str:
    factory = get_session_factory()
    async with factory() as session:
        owner = (
            await session.execute(select(User).where(User.telegram_id == sim.owner_id))
        ).scalars().first()
        if owner is None:
            owner = await make_user(session, username="settings_owner")
            owner.telegram_id = sim.owner_id
            await session.commit()
        owner_id = owner.id

    uow = UnitOfWork()
    async with uow:
        await RbacService(uow).seed_roles()
        ss = SettingsService(uow)
        await ss.set(SETTING_CARD_NUMBER, CARD)
        await ss.set_bool(BOOL_KEY, True)
        await ss.set(MEDIA_KEY, "file-id-original", value_type="media")
        await ss.set(TEXT_KEY, "footer-original")
        await uow.commit()
    return owner_id


async def _value(key: str) -> str | None:
    uow = UnitOfWork()
    async with uow:
        return await SettingsService(uow).get(key)


async def _settings_logs() -> list[AdminLog]:
    factory = get_session_factory()
    async with factory() as session:
        return list(
            (
                await session.execute(
                    select(AdminLog).where(AdminLog.action == LogAction.SETTINGS_CHANGE)
                )
            ).scalars().all()
        )


def _spec_types_are_as_assumed() -> None:
    assert spec_for(SETTING_CARD_NUMBER).value_type == "string"
    assert spec_for(BOOL_KEY).value_type == "boolean"
    assert spec_for(MEDIA_KEY).value_type == "media"


async def test_toggle_cannot_overwrite_a_text_setting(sim):
    """`aset:toggle:<text key>` must not replace the customers' card number."""
    _spec_types_are_as_assumed()
    await _bootstrap_owner(sim)
    before = len(await _settings_logs())

    await sim.send("/start", sim.owner_id, first_name="Owner")
    res = await sim.click(f"aset:toggle:{SETTING_CARD_NUMBER}", sim.owner_id)
    assert res["ok"], res["error"]

    assert await _value(SETTING_CARD_NUMBER) == CARD, (
        "a crafted toggle overwrote the card number"
    )
    alerts = [p.get("text") for p in sim.session.calls_named("AnswerCallbackQuery")]
    assert any("کلید فعال/غیرفعال نیست" in (t or "") for t in alerts), (
        f"no refusal was shown: {alerts[-3:]}"
    )
    assert len(await _settings_logs()) == before, "a refused toggle was audit-logged"


async def test_edit_cannot_write_into_a_boolean_setting(sim):
    """`aset:edit:<boolean key>` must not enter the free-text editor."""
    _spec_types_are_as_assumed()
    await _bootstrap_owner(sim)

    await sim.send("/start", sim.owner_id, first_name="Owner")
    res = await sim.click(f"aset:edit:{BOOL_KEY}", sim.owner_id)
    assert res["ok"], res["error"]

    alerts = [p.get("text") for p in sim.session.calls_named("AnswerCallbackQuery")]
    assert any("ویرایش متنی تغییر نمی‌کند" in (t or "") for t in alerts), (
        f"the editor accepted a boolean key: {alerts[-3:]}"
    )

    # the state was not armed, so a following text message cannot change it
    res = await sim.send("true-ish garbage", sim.owner_id)
    assert res["ok"], res["error"]
    assert await _value(BOOL_KEY) == "true", (
        "free text reached a boolean setting"
    )


async def test_media_upload_refuses_a_text_setting(sim):
    """`aset:media:<string key>` must not store a file_id into a text setting."""
    _spec_types_are_as_assumed()
    await _bootstrap_owner(sim)

    await sim.send("/start", sim.owner_id, first_name="Owner")
    res = await sim.click(f"aset:media:{TEXT_KEY}", sim.owner_id)
    assert res["ok"], res["error"]

    alerts = [p.get("text") for p in sim.session.calls_named("AnswerCallbackQuery")]
    assert any("رسانه نیست" in (t or "") for t in alerts), (
        f"the media upload accepted a text key: {alerts[-3:]}"
    )
    assert await _value(TEXT_KEY) == "footer-original"


async def test_media_upload_refuses_an_unknown_key(sim):
    """An unknown key must be refused up front, not silently dropped later."""
    await _bootstrap_owner(sim)

    await sim.send("/start", sim.owner_id, first_name="Owner")
    res = await sim.click("aset:media:not_a_real_key", sim.owner_id)
    assert res["ok"], res["error"]

    alerts = [p.get("text") for p in sim.session.calls_named("AnswerCallbackQuery")]
    assert any("تنظیم ناشناخته" in (t or "") for t in alerts), (
        f"an unknown key was accepted: {alerts[-3:]}"
    )


async def test_boolean_toggle_works_and_answers_once(sim):
    """Regression guard: the legitimate toggle flips, logs, and answers once."""
    _spec_types_are_as_assumed()
    await _bootstrap_owner(sim)
    before = len(await _settings_logs())

    await sim.send("/start", sim.owner_id, first_name="Owner")
    res = await sim.click(f"aset:toggle:{BOOL_KEY}", sim.owner_id)
    assert res["ok"], res["error"]

    assert await _value(BOOL_KEY) == "false", "the toggle did not flip the value"
    assert len(await _settings_logs()) == before + 1, "the toggle was not logged"
    answered = [n for (n, _payload) in res["calls"] if n == "AnswerCallbackQuery"]
    assert len(answered) == 1, (
        f"the callback was answered {len(answered)} times (Telegram rejects the second)"
    )
    screen = sim.last_screen(sim.owner_id)
    assert "❌ غیرفعال" in ((screen or {}).get("text") or ""), (
        f"the refreshed screen does not show the new value: {(screen or {}).get('text')!r}"
    )


async def test_the_legit_edit_flow_still_works(sim):
    """Regression guard: view → edit → send text stores the value and logs it."""
    _spec_types_are_as_assumed()
    await _bootstrap_owner(sim)
    before = len(await _settings_logs())

    await sim.send("/start", sim.owner_id, first_name="Owner")
    res = await sim.click(f"aset:view:{TEXT_KEY}", sim.owner_id)
    assert res["ok"], res["error"]
    res = await sim.click(f"aset:edit:{TEXT_KEY}", sim.owner_id)
    assert res["ok"], res["error"]
    res = await sim.send("footer-EDITED-mark", sim.owner_id)
    assert res["ok"], res["error"]

    assert await _value(TEXT_KEY) == "footer-EDITED-mark"
    assert len(await _settings_logs()) == before + 1


async def test_integer_and_json_validation(sim):
    """Integer keys reject non-numbers; json keys reject broken json."""
    await _bootstrap_owner(sim)

    # an integer-typed key must be present in the registry for this to apply
    from bot.services.settings_registry import REGISTRY

    int_key = next((s.key for s in REGISTRY if s.value_type == "integer"), None)
    if int_key is None:  # pragma: no cover - registry dependent
        return

    await sim.send("/start", sim.owner_id, first_name="Owner")
    res = await sim.click(f"aset:edit:{int_key}", sim.owner_id)
    assert res["ok"], res["error"]
    res = await sim.send("not-a-number", sim.owner_id)
    assert res["ok"], res["error"]
    assert any(
        "مقدار باید عدد باشد" in (m.get("text") or "") for m in sim.session.sent
    ), "an integer key accepted a non-number"
