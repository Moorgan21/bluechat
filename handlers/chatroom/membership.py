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

"""ترکِ اتاق توسطِ یه عضوِ عادی (نه owner). اگه بعدِ ترک فقط owner
بمونه، اتاق خودکار حذف می‌شه و owner باخبر می‌شه.
"""

import logging

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

import metrics
import redis_client as rc
from db import get_display_name, leave_chat_room
from keyboards import main_reply_keyboard

logger = logging.getLogger(__name__)


async def leave_room_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    result, error = await leave_chat_room(user_id)

    if error == "not_found":
        # Redis می‌گفت توی اتاقی، Postgres تاییدش نمی‌کنه؛ خودتصحیحی
        await rc.clear_active_room(user_id)
        await update.message.reply_text("این اتاق دیگه فعال نیست.", reply_markup=main_reply_keyboard())
        return

    if error == "is_owner":
        await update.message.reply_text(
            "⚠️ تو owner این اتاقی و نمی‌تونی ترکش کنی؛ باید ببندیش یا حذفش کنی."
        )
        return

    await rc.clear_active_room(user_id)

    if result["auto_deleted"]:
        metrics.room_auto_deleted.inc()
        # remaining_member_ids اینجا یعنی «کسی که قبل از حذفِ خودکار
        # هنوز تو اتاق بود» — یعنی فقط owner.
        for uid in result["remaining_member_ids"]:
            await rc.clear_active_room(uid)
            try:
                await context.bot.send_message(
                    uid,
                    "ℹ️ همه‌ی اعضا اتاق رو ترک کردن، پس اتاق خودکار حذف شد.",
                    reply_markup=main_reply_keyboard(),
                )
            except TelegramError:
                logger.warning("امکانِ اطلاع‌رسانیِ حذفِ خودکار به owner_id=%s وجود نداشت.", uid)
    else:
        display_name = await get_display_name(user_id) or "یه نفر"
        from .relay import broadcast_system_message

        await broadcast_system_message(
            result["room_id"],
            f"{display_name} ترک کرد اتاق رو.",
            context,
            member_ids=result["remaining_member_ids"],
        )

        # یه جای اتاق آزاد شد؛ صفِ عضویت رو چک کن (همون trigger که
        # فازِ ۳ بعدِ ساختِ اتاق صدا می‌زنه، اینجا بعدِ commitِ خروج).
        from .matching import try_fill_room_from_queue

        await try_fill_room_from_queue(result["room_id"], context)

    await update.message.reply_text("از اتاق خارج شدی.", reply_markup=main_reply_keyboard())
