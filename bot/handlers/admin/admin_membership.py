"""Admin management for mandatory channel/group membership."""

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.database.uow import UnitOfWork
from bot.keyboards.common import back_button
from bot.services.mandatory_membership import MandatoryMembershipService
from bot.states import MandatoryMembershipStates
from bot.utils.editing import safe_edit_text

router = Router(name="admin_membership")


def _keyboard(items: list[dict[str, str]]) -> types.InlineKeyboardMarkup:
    rows = [
        [
            types.InlineKeyboardButton(
                text=f"✏️ {item['title']}", callback_data=f"amem:edit:{i}"
            ),
            types.InlineKeyboardButton(
                text="🗑", callback_data=f"amem:delete:{i}"
            ),
        ]
        for i, item in enumerate(items)
    ]
    rows.append(
        [types.InlineKeyboardButton(text="افزودن", callback_data="amem:add")]
    )
    rows.append([back_button("admin:panel")])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


async def _show(callback: CallbackQuery, service: MandatoryMembershipService) -> None:
    items = await service.get_all()
    text = "📢 <b>عضویت اجباری</b>\n\n"
    text += "\n".join(
        f"{i + 1}. {item['title']} — <code>{item['chat_id']}</code>\n{item['link']}"
        for i, item in enumerate(items)
    ) or "هنوز کانال یا گروهی ثبت نشده است."
    await safe_edit_text(callback, text, reply_markup=_keyboard(items))


@router.callback_query(F.data == "admin:membership")
async def cb_membership(callback: CallbackQuery, uow: UnitOfWork) -> None:
    await _show(callback, MandatoryMembershipService(uow))
    await callback.answer()


@router.callback_query(F.data == "amem:add")
async def cb_add(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(MandatoryMembershipStates.waiting_title)
    await callback.message.answer("عنوان کانال/گروه را ارسال کنید:")
    await callback.answer()


@router.message(MandatoryMembershipStates.waiting_title)
async def add_title(message: Message, state: FSMContext) -> None:
    await state.update_data(title=message.text.strip())
    await state.set_state(MandatoryMembershipStates.waiting_chat_id)
    await message.answer("شناسه عددی کانال/گروه را ارسال کنید (مثلاً -1001234567890):")


@router.message(MandatoryMembershipStates.waiting_chat_id)
async def add_chat_id(message: Message, state: FSMContext) -> None:
    chat_id = message.text.strip()
    if not (chat_id.lstrip("-").isdigit()):
        await message.answer("شناسه باید عددی باشد:")
        return
    await state.update_data(chat_id=chat_id)
    await state.set_state(MandatoryMembershipStates.waiting_link)
    await message.answer("لینک عضویت را ارسال کنید (https://t.me/...):")


@router.message(MandatoryMembershipStates.waiting_link)
async def add_link(message: Message, state: FSMContext, uow: UnitOfWork) -> None:
    link = message.text.strip()
    if not link.startswith(("https://t.me/", "http://t.me/", "https://telegram.me/")):
        await message.answer("لینک معتبر تلگرام ارسال کنید:")
        return
    data = await state.get_data()
    try:
        await MandatoryMembershipService(uow).add(data["title"], data["chat_id"], link)
        await state.clear()
        await uow.commit()
        await message.answer("✅ مورد با موفقیت اضافه شد.")
    except ValueError as exc:
        await message.answer(f"❌ {exc}")


@router.callback_query(F.data.startswith("amem:delete:"))
async def cb_delete(callback: CallbackQuery, uow: UnitOfWork) -> None:
    try:
        index = int(callback.data.rsplit(":", 1)[1])
    except ValueError:
        await callback.answer("مورد نامعتبر است.", show_alert=True)
        return
    try:
        await MandatoryMembershipService(uow).remove(index)
        await uow.commit()
        await _show(callback, MandatoryMembershipService(uow))
        await callback.answer("حذف شد ✅")
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)


@router.callback_query(F.data.startswith("amem:edit:"))
async def cb_edit(
    callback: CallbackQuery, state: FSMContext, uow: UnitOfWork
) -> None:
    try:
        index = int(callback.data.rsplit(":", 1)[1])
    except ValueError:
        await callback.answer("مورد نامعتبر است.", show_alert=True)
        return
    items = await MandatoryMembershipService(uow).get_all()
    if index >= len(items):
        await callback.answer("مورد یافت نشد", show_alert=True)
        return
    await state.update_data(edit_index=index, edit_item=items[index])
    await state.set_state(MandatoryMembershipStates.waiting_edit)
    await callback.message.answer(
        "اطلاعات جدید را در یک خط ارسال کنید:\n"
        "<code>عنوان | شناسه عددی | لینک</code>"
    )
    await callback.answer()


@router.message(MandatoryMembershipStates.waiting_edit)
async def do_edit(message: Message, state: FSMContext, uow: UnitOfWork) -> None:
    parts = [part.strip() for part in message.text.split("|")]
    valid_link = parts[2].startswith(
        ("https://t.me/", "http://t.me/", "https://telegram.me/")
    ) if len(parts) == 3 else False
    if len(parts) != 3 or not parts[1].lstrip("-").isdigit() or not valid_link:
        await message.answer("فرمت نامعتبر است. نمونه: عنوان | -1001234567890 | https://t.me/example")
        return
    data = await state.get_data()
    try:
        await MandatoryMembershipService(uow).update(data["edit_index"], *parts)
        await state.clear()
        await uow.commit()
        await message.answer("✅ ویرایش شد.")
    except ValueError as exc:
        await message.answer(f"❌ {exc}")
