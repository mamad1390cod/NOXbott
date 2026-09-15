"""Phase 5 — RBAC screens: the owner role must stay unassignable and the
screens must never report a change that did not happen.

The role pickers hide the «مالک» role, but callback payloads can be crafted:
the handlers trusted the keyboard. And every profile action (enable / disable /
change role / remove) acted on whatever id the payload carried, logging an
"admin changed" entry even when no profile existed.
"""

from __future__ import annotations

from sqlalchemy import select

from bot.database.session import get_session_factory
from bot.database.uow import UnitOfWork
from bot.models.log import AdminLog, LogAction
from bot.models.rbac import AdminProfile, Permission, RoleSlug
from bot.models.user import User
from bot.services.rbac import RbacService
from tests.audit.db import make_user

_owner_user_id: str = ""


async def _bootstrap_owner(sim) -> str:
    global _owner_user_id
    factory = get_session_factory()
    async with factory() as session:
        owner = (
            await session.execute(select(User).where(User.telegram_id == sim.owner_id))
        ).scalars().first()
        if owner is None:
            owner = await make_user(session, username="roles_owner")
            owner.telegram_id = sim.owner_id
            await session.commit()
        _owner_user_id = owner.id
    return _owner_user_id


async def _new_user(username: str) -> tuple[str, int]:
    factory = get_session_factory()
    async with factory() as session:
        user = await make_user(session, username=username)
        await session.commit()
        return user.id, user.telegram_id


async def _role_id(slug: str) -> str:
    uow = UnitOfWork()
    async with uow:
        rbac = RbacService(uow)
        await rbac.seed_roles()
        await uow.commit()
    uow = UnitOfWork()
    async with uow:
        role = await RbacService(uow).get_role_by_slug(slug)
        assert role is not None, f"role {slug} missing"
        return role.id


async def _make_admin(telegram_id: int, slug: str) -> str:
    """Promote ``telegram_id`` through the service (as the owner would)."""
    uow = UnitOfWork()
    async with uow:
        rbac = RbacService(uow)
        owner = await uow.users.get(_owner_user_id)
        profile = await rbac.create_admin(telegram_id, slug, added_by=owner)
        await uow.commit()
        return profile.user_id


async def _profile_role_slug(user_id: str) -> str | None:
    """Read the profile through the repository (it eager-loads the role)."""
    uow = UnitOfWork()
    async with uow:
        profile = await uow.admin_profiles.get_by_user_id(user_id)
        return profile.role.slug if profile and profile.role else None


async def _settings_change_logs() -> list[AdminLog]:
    factory = get_session_factory()
    async with factory() as session:
        return list(
            (
                await session.execute(
                    select(AdminLog).where(AdminLog.action == LogAction.SETTINGS_CHANGE)
                )
            ).scalars().all()
        )


async def test_owner_role_cannot_be_assigned_via_crafted_addrole(sim):
    """A crafted `addrole` payload must not mint a full-power admin."""
    await _bootstrap_owner(sim)
    owner_role_id = await _role_id(RoleSlug.OWNER.value)
    victim_id, victim_tg = await _new_user("addrole_victim")

    await sim.send("/start", sim.owner_id, first_name="Owner")
    res = await sim.click("admin:roles", sim.owner_id)
    assert res["ok"], res["error"]
    res = await sim.click("admin:roles:add", sim.owner_id)
    assert res["ok"], res["error"]
    res = await sim.send(str(victim_tg), sim.owner_id)
    assert res["ok"], res["error"]

    res = await sim.click(f"admin:roles:addrole:{owner_role_id}", sim.owner_id)
    assert res["ok"], res["error"]

    factory = get_session_factory()
    async with factory() as session:
        profile = (
            await session.execute(
                select(AdminProfile).where(AdminProfile.user_id == victim_id)
            )
        ).scalars().first()
        assert profile is None, "the owner role was handed out via a crafted payload"

    alerts = [p.get("text") for p in sim.session.calls_named("AnswerCallbackQuery")]
    assert any("نقش مالک قابل تخصیص نیست" in (t or "") for t in alerts), (
        f"no refusal was shown: {alerts[-3:]}"
    )


async def test_owner_role_cannot_be_assigned_via_crafted_setrole(sim):
    """Changing an existing admin's role to «مالک» must be refused too."""
    owner_id = await _bootstrap_owner(sim)
    owner_role_id = await _role_id(RoleSlug.OWNER.value)
    moderator_role_id = await _role_id(RoleSlug.MODERATOR.value)
    helper_id, helper_tg = await _new_user("setrole_helper")
    await _make_admin(helper_tg, RoleSlug.MODERATOR.value)

    await sim.send("/start", sim.owner_id, first_name="Owner")
    res = await sim.click("admin:roles", sim.owner_id)
    assert res["ok"], res["error"]
    res = await sim.click("admin:roles:list", sim.owner_id)
    assert res["ok"], res["error"]
    res = await sim.click(f"admin:roles:profile:{helper_id}", sim.owner_id)
    assert res["ok"], res["error"]
    res = await sim.click(f"rc:{helper_id}", sim.owner_id)
    assert res["ok"], res["error"]

    # the picker shows every role except owner — the payload must be refused.
    screen = sim.last_screen(sim.owner_id)
    offered = sim.buttons((screen or {}).get("reply_markup"))
    assert not any(btn.endswith(owner_role_id) for btn in offered), (
        f"the role picker offered the owner role: {offered}"
    )

    res = await sim.click(f"admin:roles:setrole:{owner_role_id}", sim.owner_id)
    assert res["ok"], res["error"]

    assert await _profile_role_slug(helper_id) == RoleSlug.MODERATOR.value, (
        "a crafted setrole payload escalated an existing admin to the owner role"
    )
    alerts = [p.get("text") for p in sim.session.calls_named("AnswerCallbackQuery")]
    assert any("نقش مالک قابل تخصیص نیست" in (t or "") for t in alerts), (
        f"no refusal was shown: {alerts[-3:]}"
    )
    assert moderator_role_id  # the moderator role exists and was the starting point
    assert owner_id


async def test_delegated_admin_cannot_mint_a_full_power_admin(sim):
    """A MANAGE_ADMINS delegate must not exceed the scope the owner gave it."""
    await _bootstrap_owner(sim)
    owner_role_id = await _role_id(RoleSlug.OWNER.value)
    super_role_id = await _role_id(RoleSlug.SUPER_ADMIN.value)

    # The owner delegates admin management the documented way: the delegate
    # gets MANAGE_ADMINS on the super-admin role (the default set withholds it).
    uow = UnitOfWork()
    async with uow:
        rbac = RbacService(uow)
        role = await uow.admin_roles.get(super_role_id)
        perms = role.permission_set() | {Permission.MANAGE_ADMINS}
        await rbac.set_role_permissions(super_role_id, perms)
        await uow.commit()

    delegate_id, delegate_tg = await _new_user("delegate_admin")
    await _make_admin(delegate_tg, RoleSlug.SUPER_ADMIN.value)
    victim_id, victim_tg = await _new_user("mint_victim")

    await sim.send("/start", delegate_tg, first_name="Delegate")
    res = await sim.click("admin:roles:add", delegate_tg)
    assert res["ok"], res["error"]
    res = await sim.send(str(victim_tg), delegate_tg)
    assert res["ok"], res["error"]
    res = await sim.click(f"admin:roles:addrole:{owner_role_id}", delegate_tg)
    assert res["ok"], res["error"]

    factory = get_session_factory()
    async with factory() as session:
        victim = await session.get(User, victim_id)
        profile = (
            await session.execute(
                select(AdminProfile).where(AdminProfile.user_id == victim_id)
            )
        ).scalars().first()
    assert profile is None, "a delegated admin minted an owner-level account"
    assert victim is not None


async def test_stale_profile_actions_are_refused_without_phantom_logs(sim):
    """Acting on a profile that no longer exists must not fake a change."""
    await _bootstrap_owner(sim)
    moderator_role_id = await _role_id(RoleSlug.MODERATOR.value)
    stranger_id, _stranger_tg = await _new_user("no_profile_stranger")

    before = len(await _settings_change_logs())

    await sim.send("/start", sim.owner_id, first_name="Owner")
    res = await sim.click(f"admin:roles:disable:{stranger_id}", sim.owner_id)
    assert res["ok"], res["error"]
    res = await sim.click(f"admin:roles:enable:{stranger_id}", sim.owner_id)
    assert res["ok"], res["error"]
    res = await sim.click(f"admin:roles:remove:{stranger_id}", sim.owner_id)
    assert res["ok"], res["error"]

    res = await sim.click(f"rc:{stranger_id}", sim.owner_id)
    assert res["ok"], res["error"]
    res = await sim.click(f"admin:roles:setrole:{moderator_role_id}", sim.owner_id)
    assert res["ok"], res["error"]

    alerts = [p.get("text") for p in sim.session.calls_named("AnswerCallbackQuery")]
    assert any("پروفایل ادمین یافت نشد" in (t or "") for t in alerts), (
        f"stale actions were not refused: {alerts[-5:]}"
    )
    assert any("دیگر ادمین نیست" in (t or "") for t in alerts), (
        f"the stale role change was not refused: {alerts[-5:]}"
    )

    after = await _settings_change_logs()
    assert len(after) == before, (
        f"phantom audit entries were written for a non-existent profile: "
        f"{[log.description for log in after[before:]]}"
    )


async def test_remove_admin_refreshes_to_a_live_screen(sim):
    """Removing an admin must not leave the removed profile's buttons on screen."""
    await _bootstrap_owner(sim)
    helper_id, helper_tg = await _new_user("removable_admin")
    await _make_admin(helper_tg, RoleSlug.VIEWER.value)

    await sim.send("/start", sim.owner_id, first_name="Owner")
    res = await sim.click(f"admin:roles:profile:{helper_id}", sim.owner_id)
    assert res["ok"], res["error"]
    res = await sim.click(f"admin:roles:remove:{helper_id}", sim.owner_id)
    assert res["ok"], res["error"]

    factory = get_session_factory()
    async with factory() as session:
        profile = (
            await session.execute(
                select(AdminProfile).where(AdminProfile.user_id == helper_id)
            )
        ).scalars().first()
    assert profile is None, "the admin profile was not removed"

    screen = sim.last_screen(sim.owner_id)
    buttons = sim.buttons((screen or {}).get("reply_markup"))
    assert not any(btn.startswith(f"admin:roles:remove:{helper_id}") for btn in buttons), (
        f"the removed profile's buttons are still on screen: {buttons}"
    )
    text = (screen or {}).get("text") or ""
    assert "لیست ادمین" in text or "هنوز ادمینی" in text, (
        f"the screen was not refreshed to a live one: {text[:120]!r}"
    )


async def test_permission_toggle_still_works_and_is_logged(sim):
    """Regression guard: the legitimate permission editor is untouched."""
    await _bootstrap_owner(sim)
    moderator_role_id = await _role_id(RoleSlug.MODERATOR.value)
    index = list(Permission).index(Permission.VIEW_STATISTICS)

    await sim.send("/start", sim.owner_id, first_name="Owner")
    res = await sim.click("admin:roles:roles", sim.owner_id)
    assert res["ok"], res["error"]
    res = await sim.click(f"admin:roles:role:{moderator_role_id}", sim.owner_id)
    assert res["ok"], res["error"]
    before = len(await _settings_change_logs())

    res = await sim.click(f"rp:{moderator_role_id}:{index}", sim.owner_id)
    assert res["ok"], res["error"]

    uow = UnitOfWork()
    async with uow:
        role = await uow.admin_roles.get(moderator_role_id)
        assert Permission.VIEW_STATISTICS in role.permission_set(), (
            "tapping an unset permission did not grant it"
        )
    logs = await _settings_change_logs()
    assert len(logs) == before + 1, "the permission change was not audit-logged"
    assert "دسترسی" in (logs[-1].description or "")

    # and toggling it again removes it (the button is a real toggle)
    res = await sim.click(f"rp:{moderator_role_id}:{index}", sim.owner_id)
    assert res["ok"], res["error"]
    uow = UnitOfWork()
    async with uow:
        role = await uow.admin_roles.get(moderator_role_id)
        assert Permission.VIEW_STATISTICS not in role.permission_set()


async def test_moderator_cannot_open_the_roles_panel(sim):
    """The roles router is MANAGE_ADMINS-gated: a moderator gets no edit screen."""
    await _bootstrap_owner(sim)
    moderator_role_id = await _role_id(RoleSlug.MODERATOR.value)
    helper_id, helper_tg = await _new_user("moderator_viewer")
    await _make_admin(helper_tg, RoleSlug.MODERATOR.value)

    await sim.send("/start", helper_tg, first_name="Moderator")
    res = await sim.click("admin:roles", helper_tg)
    assert res["ok"], res["error"]

    # The router gate blocks it. NOTE: nothing answers the tap in that case —
    # recorded here as a known UX gap (see the report's open items).
    assert any("admin:roles" in item for item in res["unhandled"]), (
        f"a moderator reached the roles panel: {res['unhandled']}"
    )

    res = await sim.click(f"rp:{moderator_role_id}:{list(Permission).index(Permission.MANAGE_ADMINS)}", helper_tg)
    assert res["ok"], res["error"]
    uow = UnitOfWork()
    async with uow:
        role = await uow.admin_roles.get(moderator_role_id)
        assert Permission.MANAGE_ADMINS not in role.permission_set(), (
            "a moderator managed to grant themselves MANAGE_ADMINS"
        )
    assert helper_id
