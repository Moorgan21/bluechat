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

"""فلوی ساختِ اتاقِ چت: انتخابِ جنسیت، انتخابِ ظرفیت، کسرِ سکه، ثبت."""

import logging

from telegram import Update
from telegram.ext import ContextTypes

import metrics
import redis_client as rc
from db import RoomGenderPref, create_chat_room
from keyboards import room_capacity_keyboard, room_gender_keyboard, room_menu_keyboard

logger = logging.getLogger(__name__)

ROOM_CREATE_COST = 20
GENDER_LABELS_FA = {"male": "پسرونه", "female": "دخترونه", "any": "فرقی نداره"}

_CREATE_GENDER_KEY = "room_create_gender"


async def show_room_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = "🏠 اتاق چت\n\nمی‌تونی یه اتاقِ گروهیِ دائمی بسازی یا به یکی ملحق بشی."
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=room_menu_keyboard())
    else:
        await update.message.reply_text(text, reply_markup=room_menu_keyboard())


async def room_menu_callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from . import matching  # ایمپورتِ دیرهنگام برای جلوگیری از چرخه؛ matching هم از creation ایمپورت می‌کنه

    query = update.callback_query
    prefix, _, value = query.data.partition(":")

    if prefix == "roommenu" and value == "create":
        await query.answer()
        await _start_create_flow(update, context)
    elif prefix == "roommenu" and value == "join":
        await query.answer()
        await matching.start_join_flow(update, context)
    elif prefix == "roomgender":
        await _handle_gender_selected(update, context, value)
    elif prefix == "roomcap":
        await _handle_capacity_selected(update, context, value)
    elif prefix == "roomjoingender":
        await matching.handle_join_gender_selected(update, context, value)


async def _start_create_flow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    blocked_reason = await _check_can_start_room_flow(user_id)
    if blocked_reason:
        await update.callback_query.edit_message_text(blocked_reason)
        return

    await update.callback_query.edit_message_text(
        "اتاق رو برای چه جنسیتی می‌سازی؟",
        reply_markup=room_gender_keyboard(),
    )


async def _handle_gender_selected(update: Update, context: ContextTypes.DEFAULT_TYPE, value: str) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    blocked_reason = await _check_can_start_room_flow(user_id)
    if blocked_reason:
        await query.edit_message_text(blocked_reason)
        return

    context.user_data[_CREATE_GENDER_KEY] = value  # "male" | "female" | "any"

    await query.edit_message_text(
        f"ظرفیتِ اتاق رو انتخاب کن (هزینه‌ی ساخت: {ROOM_CREATE_COST} سکه):",
        reply_markup=room_capacity_keyboard(),
    )


async def _handle_capacity_selected(update: Update, context: ContextTypes.DEFAULT_TYPE, value: str) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    gender_value = context.user_data.pop(_CREATE_GENDER_KEY, None)
    if gender_value is None:
        await query.edit_message_text("این فلو منقضی شده. دوباره از «🏠 اتاق چت» شروع کن.")
        return

    blocked_reason = await _check_can_start_room_flow(user_id)
    if blocked_reason:
        await query.edit_message_text(blocked_reason)
        return

    try:
        capacity = int(value)
    except ValueError:
        capacity = 5

    room, error = await create_chat_room(
        owner_id=user_id,
        gender_pref=RoomGenderPref(gender_value),
        capacity=capacity,
        cost=ROOM_CREATE_COST,
    )

    if error == "has_active_room":
        await query.edit_message_text("⚠️ همین الان یه اتاقِ فعال داری. اول باید ببندیش یا حذفش کنی.")
        return
    if error == "insufficient_coins":
        await query.edit_message_text(f"🪙 سکه‌ی کافی نداری! ساختنِ اتاق {ROOM_CREATE_COST} سکه هزینه داره.")
        return
    if error is not None or room is None:
        await query.edit_message_text("مشکلی پیش اومد، دوباره تلاش کن.")
        return

    metrics.rooms_created.inc()
    gender_label = GENDER_LABELS_FA.get(gender_value, gender_value)
    await query.edit_message_text(
        "✅ اتاقت ساخته شد!\n\n"
        f"نوع: {gender_label}\n"
        f"ظرفیت: {room.capacity} نفر\n\n"
        "الان توی این اتاق تنها هستی؛ به محض این‌که یه نفر بهش ملحق بشه، بهت خبر می‌دیم."
    )

    # trigger بعد از commitِ کاملِ ساختِ اتاق صدا زده می‌شه (نه داخلِ
    # تراکنشِ create_chat_room)، چون claim خودش یه تراکنشِ FOR UPDATEِ
    # جداست؛ تودرتو کردنشون قفلِ ردیفِ owner رو بی‌دلیل نگه می‌داشت.
    from . import matching

    await matching.try_fill_room_from_queue(room.id, context)


async def _check_can_start_room_flow(user_id: int) -> str | None:
    """اگه کاربر آزاد نباشه (توی چتِ ۱به۱، صفِ انتظار، یا از قبل یه
    اتاقِ فعال داره)، دلیلش رو برمی‌گردونه؛ وگرنه None. چکِ
    active_room_id واقعی و اتمیک داخلِ create_chat_room انجام می‌شه؛
    این فقط برای فیدبکِ سریع و بدونِ کسرِ سکه‌ست."""
    if await rc.get_partner(user_id) is not None:
        return "⚠️ الان توی یه گفتگوی ۱به۱ فعالی. اول اون رو تموم کن."
    if await rc.is_waiting(user_id):
        return "⚠️ الان توی صفِ انتظارِ چتِ ناشناسی. اول از اونجا خارج شو."
    return None
