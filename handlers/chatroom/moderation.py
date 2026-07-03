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

"""ابزارهای owner. فعلاً فقط حذفِ اتاق (تنها راهِ خروجِ owner، چون
owner نمی‌تونه مثلِ عضوِ عادی ترک کنه). بقیه‌ی ابزارها (حذفِ پیامِ
دیگران، اخراج، بستن/بازکردن) بعداً اضافه می‌شن.
"""

import logging

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

import redis_client as rc
from db import delete_chat_room
from keyboards import main_reply_keyboard, room_delete_confirm_keyboard

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
