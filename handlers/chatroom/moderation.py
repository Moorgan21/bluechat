# Copyright (C) 2026 Dariush Lashani
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""ابزارهای owner: حذفِ اتاق (تنها راهِ خروجِ owner)، بستن/بازکردنِ
اتاق، و پاک‌سازیِ یک‌طرفه‌ی تاریخچه بعدِ حذف. حذفِ پیامِ دیگران و
اخراج دکمه ندارن؛ با ریپلای‌کردنِ «حذف»/«اخراج» تو relay.py هندل
می‌شن، همون‌جایی که پیام‌ها مدیریت می‌شن.
"""

import logging

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

import redis_client as rc
from db import delete_chat_room, set_room_open_status
from keyboards import (
    in_room_reply_keyboard,
    main_reply_keyboard,
    purge_history_keyboard,
    room_delete_confirm_keyboard,
)

logger = logging.getLogger(__name__)


async def delete_room_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """دکمه‌ی «🗑 حذف اتاق»؛ چون غیرقابلِ بازگشته، اول تاییدِ صریح
    می‌گیره (همون الگوی end_chat_confirm_keyboard برای پایانِ چتِ ۱به۱)."""
    await update.message.reply_text(
        "⚠️ حذفِ اتاق غیرقابلِ بازگشته و همه‌ی اعضا ازش بیرون میان. مطمئنی؟",
        reply_markup=room_delete_confirm_keyboard(),
    )


async def delete_room_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    action = query.data.split(":", 1)[1]

    if action == "cancel":
        try:
            await query.message.delete()
        except TelegramError:
            pass
        return

    user_id = query.from_user.id
    result, error = await delete_chat_room(user_id)

    try:
        await query.message.delete()
    except TelegramError:
        pass

    if error == "not_found":
        await rc.clear_active_room(user_id)
        await context.bot.send_message(user_id, "این اتاق دیگه فعال نیست.", reply_markup=main_reply_keyboard())
        return
    if error == "not_owner":
        await context.bot.send_message(user_id, "⚠️ فقط owner می‌تونه اتاق رو حذف کنه.")
        return

    # برخلافِ ترکِ معمولی (که فقط خودِ leaver کیبوردش عوض می‌شه، بقیه
    # همچنان تو اتاقن)، اینجا اتاق برای *همه* تموم می‌شه، پس همه باید
    # کیبوردِ منو رو پس بگیرن؛ برای همین یه پیامِ per-recipient مستقیم
    # می‌فرستیم، نه broadcast_system_messageِ عمومی (که reply_markup نداره).
    for uid in result["member_ids"]:
        await rc.clear_active_room(uid)
        text = "🗑 اتاقت حذف شد." if uid == user_id else "ℹ️ این اتاق توسطِ owner حذف شد."
        try:
            await context.bot.send_message(uid, text, reply_markup=main_reply_keyboard())
        except TelegramError:
            logger.warning("امکانِ اطلاع‌رسانیِ حذفِ اتاق به user_id=%s وجود نداشت.", uid)

    # ChatRoomMemberِ Postgres همین الان پاک شد، پس اگه بعداً بخوایم
    # تاریخچه رو پاک کنیم دیگه نمی‌تونیم بفهمیم اعضا کیا بودن؛ برای
    # همین لیست رو تو Redis نگه می‌داریم (TTLِ هم‌راستا با محدودیتِ
    # حذفِ پیامِ تلگرام).
    await rc.store_deleted_room_members(result["room_id"], result["member_ids"])
    await context.bot.send_message(
        user_id,
        "🧹 می‌خوای کاملِ تاریخچه‌ی پیام‌های این اتاق رو (برای همه‌ی اعضای سابق) پاک کنم؟",
        reply_markup=purge_history_keyboard(result["room_id"]),
    )


async def purge_history_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """فقط از طریقِ پیامِ بعدِ حذفِ اتاق قابلِ‌دسترسیه (roomdelete نه
    خودِ اتاق، چون تا اونجا active_room_idِ owner دیگه معتبر نیست)."""
    query = update.callback_query
    await query.answer()
    room_id = int(query.data.split(":", 1)[1])
    user_id = query.from_user.id

    member_ids = await rc.get_deleted_room_members(room_id)
    if member_ids is None:
        await query.edit_message_text("این درخواست دیگه معتبر نیست (منقضی شده یا قبلاً پاک شده).")
        return

    try:
        await query.message.delete()
    except TelegramError:
        pass

    deleted_count = 0
    skipped_count = 0
    for uid in member_ids:
        for mid in await rc.pop_room_history(room_id, uid):
            try:
                await context.bot.delete_message(uid, mid)
                deleted_count += 1
            except TelegramError:
                skipped_count += 1

    summary = f"✅ {deleted_count} پیام پاک شد."
    if skipped_count:
        summary += f" {skipped_count} پیامِ قدیمی‌تر از ۴۸ ساعت طبقِ محدودیتِ خودِ تلگرام قابلِ حذف نبودن."
    await context.bot.send_message(user_id, summary)


async def _set_room_status(update: Update, context: ContextTypes.DEFAULT_TYPE, is_open: bool) -> None:
    user_id = update.effective_user.id
    result, error = await set_room_open_status(user_id, is_open)

    if error == "not_found":
        await rc.clear_active_room(user_id)
        await update.message.reply_text("این اتاق دیگه فعال نیست.", reply_markup=main_reply_keyboard())
        return
    if error == "not_owner":
        await update.message.reply_text("⚠️ فقط owner می‌تونه اتاق رو ببنده یا باز کنه.")
        return

    from .relay import broadcast_system_message

    other_ids = [uid for uid in result["member_ids"] if uid != user_id]
    if is_open:
        # با بازشدنِ اتاق، اعضا برمی‌گردن به کیبوردِ محدودِ داخلِ اتاق
        # (چون دوباره می‌شه پیام رد و بدل کرد).
        await broadcast_system_message(
            result["room_id"],
            "اتاق دوباره باز شد؛ می‌تونید پیام بدید.",
            context,
            member_ids=other_ids,
            reply_markup=in_room_reply_keyboard(),
        )
        text = "🔓 اتاق باز شد."
    else:
        # با بسته‌شدنِ اتاق، اعضا (نه owner) به منوی اصلیِ کامل و
        # بدون‌تغییر هدایت می‌شن؛ همچنان active_room_id دارن (نمی‌تونن
        # وارد چتِ ۱به۱ بشن یا اتاقِ دیگه بسازن/بهش ملحق بشن) ولی
        # می‌تونن با /room وضعیتِ همین اتاق رو هر وقت خواستن ببینن.
        await broadcast_system_message(
            result["room_id"],
            "اتاقت موقتاً بسته شد و فعلاً پیامی رد و بدل نمی‌شه. با دستورِ /room "
            "می‌تونی وضعیتِ اتاقتو چک کنی؛ فعلاً می‌تونی از بقیه‌ی امکاناتِ ربات "
            "استفاده کنی.",
            context,
            member_ids=other_ids,
            reply_markup=main_reply_keyboard(),
        )
        text = "🔒 اتاق بسته شد."

    await update.message.reply_text(text, reply_markup=in_room_reply_keyboard(is_owner=True, room_open=is_open))


async def close_room_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _set_room_status(update, context, is_open=False)


async def reopen_room_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _set_room_status(update, context, is_open=True)
