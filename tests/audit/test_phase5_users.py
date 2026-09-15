"""Phase 5 — admin user management: privilege and data-loss guards.

The admin surface is gated per sub-router (``IsAdmin`` + ``HasPermission``).
These tests drive the *real* dispatcher, so they also prove the gates do not
break the legitimate paths.
"""

from __future__ import annotations

from sqlalchemy import select

from bot.database.session import get_session_factory
from bot.models.user import User
from tests.audit.db import make_user


async def _make_helper_with_role(role_slug: str, username: str) -> tuple[int, str]:
    """A user holding an ACTIVE AdminProfile with the given role."""
    from bot.services.rbac import RbacService

    factory = get_session_factory()
    async with factory() as session:
        owner = await session.get(User, _owner_user_id)
        helper = await make_user(session, username=username)
        await session.commit()
        helper_id, helper_tg = helper.id, helper.telegram_id

    from bot.database.uow import UnitOfWork

    uow = UnitOfWork()
    async with uow:
        rbac = RbacService(uow)
        await rbac.seed_roles()
        owner = await uow.users.get(_owner_user_id)
        await rbac.create_admin(helper_tg, role_slug, added_by=owner)
        await uow.commit()
    return helper_tg, helper_id


_owner_user_id: str = ""


async def _bootstrap_owner(sim) -> str:
    """Ensure the owner has a user row and return its id."""
    global _owner_user_id
    factory = get_session_factory()
    async with factory() as session:
        owner = (
            await session.execute(select(User).where(User.telegram_id == sim.owner_id))
        ).scalars().first()
        if owner is None:
            owner = await make_user(session, username="the_owner")
            owner.telegram_id = sim.owner_id
            await session.commit()
        _owner_user_id = owner.id
    return _owner_user_id


async def test_owner_account_cannot_be_deleted(sim):
    """A tap must never erase the owner row (orders/payments cascade with it)."""
    owner_id = await _bootstrap_owner(sim)

    await sim.send("/start", sim.owner_id, first_name="Owner")
    res = await sim.click(f"auser:del:{owner_id}", sim.owner_id)
    assert res["ok"], res["error"]
    res = await sim.click(f"auser:confirm_del:{owner_id}", sim.owner_id)
    assert res["ok"], res["error"]

    factory = get_session_factory()
    async with factory() as session:
        assert await session.get(User, owner_id) is not None, (
            "the owner account was deleted"
        )
    alerts = [p.get("text") for p in sim.session.calls_named("AnswerCallbackQuery")]
    assert any("مالک قابل حذف نیست" in (t or "") for t in alerts), (
        f"the deletion was not refused with an explanation: {alerts[-3:]}"
    )


async def test_active_admin_cannot_be_deleted(sim):
    """An admin row must be revoked before it can be deleted."""
    await _bootstrap_owner(sim)
    helper_tg, helper_id = await _make_helper_with_role("support_manager", "helper_del")

    await sim.send("/start", sim.owner_id, first_name="Owner")
    res = await sim.click(f"auser:del:{helper_id}", sim.owner_id)
    assert res["ok"], res["error"]
    res = await sim.click(f"auser:confirm_del:{helper_id}", sim.owner_id)
    assert res["ok"], res["error"]

    factory = get_session_factory()
    async with factory() as session:
        assert await session.get(User, helper_id) is not None, (
            "an active admin was deleted directly"
        )


async def test_plain_customer_can_still_be_deleted(sim):
    """The guard must not block the normal case."""
    await _bootstrap_owner(sim)
    factory = get_session_factory()
    async with factory() as session:
        customer = await make_user(session, username="deletable")
        await session.commit()
        customer_id = customer.id

    await sim.send("/start", sim.owner_id, first_name="Owner")
    res = await sim.click(f"auser:del:{customer_id}", sim.owner_id)
    assert res["ok"], res["error"]
    res = await sim.click(f"auser:confirm_del:{customer_id}", sim.owner_id)
    assert res["ok"], res["error"]

    factory = get_session_factory()
    async with factory() as session:
        assert await session.get(User, customer_id) is None, (
            "a plain customer could not be deleted"
        )


async def test_moderator_cannot_promote_a_user_to_admin(sim):
    """MANAGE_USERS is not MANAGE_ADMINS: a moderator must not build admins."""
    from bot.models.rbac import AdminProfile

    await _bootstrap_owner(sim)
    moderator_tg, _moderator_id = await _make_helper_with_role("moderator", "moderator1")

    factory = get_session_factory()
    async with factory() as session:
        victim = await make_user(session, username="promote_me")
        await session.commit()
        victim_id = victim.id

    await sim.send("/start", moderator_tg, first_name="Mod")
    res = await sim.click(f"auser:makeadmin:{victim_id}", moderator_tg)
    assert res["ok"], res["error"]

    async with factory() as session:
        profile = (
            await session.execute(
                select(AdminProfile).where(AdminProfile.user_id == victim_id)
            )
        ).scalars().first()
        assert profile is None, (
            "a moderator (MANAGE_USERS only) promoted a user to admin"
        )
    alerts = [p.get("text") for p in sim.session.calls_named("AnswerCallbackQuery")]
    assert any("مدیریت ادمین" in (t or "") for t in alerts), (
        f"the promotion was not refused with an explanation: {alerts[-3:]}"
    )


async def test_moderator_cannot_demote_another_admin(sim):
    """A moderator must not be able to strip a higher role's access."""
    from bot.models.rbac import AdminProfile

    await _bootstrap_owner(sim)
    moderator_tg, _ = await _make_helper_with_role("moderator", "moderator2")
    _admin_tg, admin_id = await _make_helper_with_role("financial_manager", "finmgr")

    await sim.send("/start", moderator_tg, first_name="Mod")
    res = await sim.click(f"auser:removeadmin:{admin_id}", moderator_tg)
    assert res["ok"], res["error"]

    factory = get_session_factory()
    async with factory() as session:
        profile = (
            await session.execute(
                select(AdminProfile).where(AdminProfile.user_id == admin_id)
            )
        ).scalars().first()
        assert profile is not None, (
            "a moderator removed a financial manager's admin access"
        )


async def test_owner_can_still_manage_admins(sim):
    """The guards must not break the owner's legitimate flow."""
    from bot.models.rbac import AdminProfile

    await _bootstrap_owner(sim)
    factory = get_session_factory()
    async with factory() as session:
        candidate = await make_user(session, username="promote_ok")
        await session.commit()
        candidate_id = candidate.id

    await sim.send("/start", sim.owner_id, first_name="Owner")
    res = await sim.click(f"auser:makeadmin:{candidate_id}", sim.owner_id)
    assert res["ok"], res["error"]

    async with factory() as session:
        profile = (
            await session.execute(
                select(AdminProfile).where(AdminProfile.user_id == candidate_id)
            )
        ).scalars().first()
        assert profile is not None, "the owner could not promote a user"

    res = await sim.click(f"auser:removeadmin:{candidate_id}", sim.owner_id)
    assert res["ok"], res["error"]

    async with factory() as session:
        profile = (
            await session.execute(
                select(AdminProfile).where(AdminProfile.user_id == candidate_id)
            )
        ).scalars().first()
        assert profile is None, "the owner could not revoke an admin"
