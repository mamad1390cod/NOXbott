"""Admin database backup and restore handlers.

Both paths use the *same* database resolution as the running application
(``bot.utils.backup`` reads the engine URL) — the previous hardcoded
``Path("noxbot.db")`` pointed at a file relative to the working directory, so a
backup could capture an unrelated/stale file and a "successful" restore could
leave the live database untouched.

Restoring is destructive, so it now: validates the uploaded file is a healthy
SQLite database before anything is replaced, snapshots the current database
first, and requires ``RESTORE_DATABASE`` (downloading requires
``BACKUP_DATABASE``) instead of the payment permissions the whole router used
to accept.
"""

import logging
import tempfile
from datetime import datetime
from pathlib import Path

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, FSInputFile

from bot.keyboards.common import back_button, single_button_kb
from bot.models.rbac import Permission
from bot.models.user import User
from bot.states import AdminBackupStates
from bot.utils.backup import create_backup, stage_restore
from bot.utils.editing import safe_edit_text

router = Router(name="admin_backup")
logger = logging.getLogger(__name__)


@router.callback_query(F.data == "admin:backup")
async def cb_admin_backup(callback: CallbackQuery) -> None:
    """Show backup menu."""
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="💾 دانلود بکاپ", callback_data="abackup:download")],
        [types.InlineKeyboardButton(text="📤 آپلود بکاپ", callback_data="abackup:upload")],
        [back_button("admin:panel")],
    ])
    await safe_edit_text(
        callback,
        "💾 <b>مدیریت بکاپ دیتابیس</b>\n\n"
        "یک گزینه را انتخاب کنید:",
        reply_markup=keyboard,
    )
    await callback.answer()


@router.callback_query(F.data == "abackup:download")
async def cb_backup_download(
    callback: CallbackQuery, user: User, permissions: set[Permission]
) -> None:
    """Send a consistent snapshot of the live database."""
    if Permission.BACKUP_DATABASE not in permissions:
        await callback.answer("⛔️ دسترسی بکاپ‌گیری ندارید.", show_alert=True)
        return

    backup_path: Path | None = None
    try:
        backup_path = await create_backup()
        file = FSInputFile(backup_path, filename=backup_path.name)
        await callback.message.answer_document(
            file,
            caption=f"💾 <b>بکاپ دیتابیس</b>\n\n"
                    f"📅 تاریخ: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                    f"📦 حجم: {backup_path.stat().st_size / 1024:.2f} KB\n\n"
                    f"⚠️ این فایل را در جای امن نگهداری کنید."
        )
        await callback.answer("✅ بکاپ با موفقیت ارسال شد", show_alert=True)
        logger.info("Admin %s downloaded database backup", user.telegram_id)
    except Exception as e:
        logger.exception("Failed to create backup: %s", e)
        await callback.answer(f"❌ خطا در ایجاد بکاپ: {e}", show_alert=True)
    finally:
        if backup_path is not None:
            backup_path.unlink(missing_ok=True)


@router.callback_query(F.data == "abackup:upload")
async def cb_backup_upload(
    callback: CallbackQuery, state: FSMContext, permissions: set[Permission]
) -> None:
    """Start the restore process."""
    if Permission.RESTORE_DATABASE not in permissions:
        await callback.answer("⛔️ دسترسی بازیابی بکاپ ندارید.", show_alert=True)
        return
    await state.set_state(AdminBackupStates.waiting_backup_file)
    await safe_edit_text(
        callback,
        "📤 <b>آپلود بکاپ</b>\n\n"
        "فایل بکاپ دیتابیس (.db) را ارسال کنید:\n\n"
        "⚠️ <b>هشدار:</b> این عملیات دیتابیس فعلی را با فایل آپلود شده جایگزین می‌کند.\n"
        "از دیتابیس فعلی یک نسخه پشتیبان خودکار گرفته می‌شود، اما ادامه با احتیاط:",
        reply_markup=single_button_kb(back_button("admin:backup")),
    )
    await callback.answer()


@router.message(AdminBackupStates.waiting_backup_file, F.document)
async def collect_backup_file(
    message: Message, state: FSMContext, user: User, permissions: set[Permission]
) -> None:
    """Receive and (after validation) restore a backup file."""
    if Permission.RESTORE_DATABASE not in permissions:
        await state.clear()
        await message.answer("⛔️ دسترسی بازیابی بکاپ ندارید.")
        return

    document = message.document
    file_name = (document.file_name or "").lower()
    if not file_name.endswith(".db"):
        await message.answer(
            "❌ فایل نامعتبر است. فقط فایل‌های .db قابل قبول هستند.",
            reply_markup=single_button_kb(back_button("admin:backup")),
        )
        await state.clear()
        return

    workdir = Path(tempfile.mkdtemp(prefix="noxbot_restore_"))
    temp_path = workdir / "uploaded_backup.db"
    try:
        file = await message.bot.get_file(document.file_id)
        await message.bot.download_file(file.file_path, temp_path)

        # The upload is validated and *staged*: swapping the database file while
        # the bot runs corrupts it (live connections keep writing the old image's
        # pages). main.py applies it on the next start.
        stage_restore(
            temp_path,
            uploaded_name=document.file_name,
            admin_telegram_id=user.telegram_id,
        )
    except ValueError as e:
        logger.warning("Rejected backup upload from %s: %s", user.telegram_id, e)
        await state.clear()
        await message.answer(
            f"❌ این فایل یک دیتابیس سالم نیست: {e}\n"
            "دیتابیس فعلی دست‌نخورده باقی ماند.",
            reply_markup=single_button_kb(back_button("admin:backup")),
        )
        return
    except Exception as e:
        logger.exception("Failed to restore backup: %s", e)
        await state.clear()
        await message.answer(
            f"❌ خطا در بازیابی بکاپ: {e}",
            reply_markup=single_button_kb(back_button("admin:backup")),
        )
        return
    finally:
        temp_path.unlink(missing_ok=True)
        workdir.rmdir() if workdir.exists() and not any(workdir.iterdir()) else None

    await state.clear()
    await message.answer(
        "✅ <b>بکاپ معتبر است و برای بازیابی ثبت شد</b>\n\n"
        "🔄 برای اعمال، بات را ریستارت کنید — در استارتاپ بعدی اعمال می‌شود.\n"
        "💡 از دیتابیس فعلی یک نسخه <code>*.pre_restore_*.db</code> نگه داشته می‌شود.",
        reply_markup=single_button_kb(back_button("admin:panel")),
    )
    logger.warning("Admin %s staged a database restore", user.telegram_id)


@router.message(AdminBackupStates.waiting_backup_file)
async def invalid_backup_file(message: Message) -> None:
    """Handle invalid file type."""
    await message.answer(
        "❌ لطفاً یک فایل .db ارسال کنید.",
        reply_markup=single_button_kb(back_button("admin:backup")),
    )
