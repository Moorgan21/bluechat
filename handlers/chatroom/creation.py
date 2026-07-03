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
from db import RoomGenderPref, RoomStatus, create_chat_room, get_chat_room, get_room_member_ids
from keyboards import (
    in_room_reply_keyboard,
    room_capacity_keyboard,
    room_closed_reply_keyboard,
    room_gender_keyboard,
    room_menu_keyboard,
)

logger = logging.getLogger(__name__)

ROOM_CREATE_COST = 20
GENDER_LABELS_FA = {"male": "پسرونه", "female": "دخترونه", "any": "فرقی نداره"}
ROOM_STATUS_LABELS_FA = {"open": "باز 🟢", "closed": "بسته 🔒"}

_CREATE_GENDER_KEY = "room_create_gender"


async def show_room_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """ورودیِ منوی اتاق: هم از دکمه‌ی «🏠 اتاق چت» صدا زده می‌شه هم از
    دستورِ /room. اگه کاربر از قبل یه اتاقِ فعال داره، به‌جای منوی
    ایجاد/عضویت، وضعیتِ همون اتاق رو نشون می‌ده (و کیبوردِ پایین صفحه
    رو با وضعیتِ واقعی sync می‌کنه — مفید برای وقتی کیبورد به هر
    دلیلی از حالتِ واقعی عقب افتاده)."""
    user_id = update.effective_user.id
    active_room_id = await rc.get_active_room(user_id)
    if active_room_id is not None:
        await _show_active_room_status(update, context, user_id, active_room_id)
        return

    text = "🏠 اتاق چت\n\nمی‌تونی یه اتاقِ گروهیِ دائمی بسازی یا به یکی ملحق بشی."
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=room_menu_keyboard())
    else:
        await update.message.reply_text(text, reply_markup=room_menu_keyboard())


async def _show_active_room_status(
    update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, room_id: int
) -> None:
    room = await get_chat_room(room_id)
    if room is None or room.status == RoomStatus.deleted:
        # آینه‌ی Redis عقب‌مونده؛ خودتصحیحی و برگشت به منوی معمولی
        await rc.clear_active_room(user_id)
        await show_room_menu(update, context)
        return

    member_ids = await get_room_member_ids(room_id)
    is_owner = room.owner_id == user_id
    gender_label = GENDER_LABELS_FA.get(room.gender_pref.value, room.gender_pref.value)
    status_label = ROOM_STATUS_LABELS_FA.get(room.status.value, room.status.value)

    text = (
        "🏠 اتاقِ فعلیِ تو\n\n"
        f"نوع: {gender_label}\n"
        f"اعضا: {len(member_ids)} از {room.capacity} نفر\n"
        f"وضعیت: {status_label}\n"
        f"نقشِ تو: {'owner (مالک)' if is_owner else 'عضو'}"
    )
    if not is_owner and room.status == RoomStatus.closed:
        # عضوِ عادی وقتی اتاق بسته‌ست، همون کیبوردِ محدود-برداشته‌شده
        # رو باید ببینه، نه اینکه با چک‌کردنِ وضعیت دوباره قفل بشه رو
        # in_room_reply_keyboard.
        keyboard = room_closed_reply_keyboard()
    else:
        keyboard = in_room_reply_keyboard(
            secure=await rc.is_secure_chat(user_id),
            is_owner=is_owner,
            room_open=room.status == RoomStatus.open,
        )
    if update.callback_query:
        await update.callback_query.message.reply_text(text, reply_markup=keyboard)
    else:
        await update.message.reply_text(text, reply_markup=keyboard)


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
        conflict_check=lambda: _redis_conflict_check(user_id),
    )

    if error == "has_active_room":
        await query.edit_message_text("⚠️ همین الان یه اتاقِ فعال داری. اول باید ببندیش یا حذفش کنی.")
        return
    if error == "in_1to1":
        await query.edit_message_text("⚠️ الان توی یه گفتگوی ۱به۱ فعالی. اول اون رو تموم کن.")
        return
    if error == "in_queue":
        await query.edit_message_text("⚠️ الان توی صفِ انتظارِ چتِ ناشناسی. اول از اونجا خارج شو.")
        return
    if error == "insufficient_coins":
        await query.edit_message_text(f"🪙 سکه‌ی کافی نداری! ساختنِ اتاق {ROOM_CREATE_COST} سکه هزینه داره.")
        return
    if error is not None or room is None:
        await query.edit_message_text("مشکلی پیش اومد، دوباره تلاش کن.")
        return

    await rc.set_active_room(user_id, room.id)
    metrics.rooms_created.inc()
    gender_label = GENDER_LABELS_FA.get(gender_value, gender_value)
    await query.edit_message_text(
        "✅ اتاقت ساخته شد!\n\n"
        f"نوع: {gender_label}\n"
        f"ظرفیت: {room.capacity} نفر\n\n"
        "الان توی این اتاق تنها هستی؛ به محض این‌که یه نفر بهش ملحق بشه، بهت خبر می‌دیم."
    )
    await context.bot.send_message(
        user_id, "از الان هرچی بفرستی توی اتاقت relay می‌شه 👇", reply_markup=in_room_reply_keyboard(is_owner=True)
    )

    # trigger بعد از commitِ کاملِ ساختِ اتاق صدا زده می‌شه (نه داخلِ
    # تراکنشِ create_chat_room)، چون claim خودش یه تراکنشِ FOR UPDATEِ
    # جداست؛ تودرتو کردنشون قفلِ ردیفِ owner رو بی‌دلیل نگه می‌داشت.
    from . import matching

    await matching.try_fill_room_from_queue(room.id, context)


async def _check_can_start_room_flow(user_id: int) -> str | None:
    """اگه کاربر آزاد نباشه (توی چتِ ۱به۱، صفِ انتظارِ ۱به۱، صفِ
    عضویتِ اتاق، یا از قبل یه اتاقِ فعال داره)، پیامِ فارسیِ دلیلش رو
    برمی‌گردونه؛ وگرنه None. این فقط یه پیش‌چکِ UX برای فیدبکِ سریع و
    بدونِ کسرِ سکه‌ست، قبل از شروعِ فلو؛ چکِ اتمیکِ واقعی (که TOCTOU
    بینِ این پیش‌چک و لحظه‌ی commit رو می‌بنده) با _redis_conflict_check
    و پارامترِ conflict_check داخلِ خودِ تراکنشِ
    create_chat_room/join_chat_room انجام می‌شه.

    چکِ is_waiting_room_join اینجا لازمه چون create_chat_room/
    join_chat_room هیچ‌کدوم صفِ عضویتِ اتاق رو نمی‌بینن (فقط
    active_room_id و conflict_checkِ ۱به۱ رو)؛ بدونش، کاربرِ منتظرِ
    یه اتاق می‌تونست دوباره جستجو کنه یا حتی اتاقِ جدید بسازه و
    بی‌سروصدا دوبار (یا بیشتر) سکه بده."""
    if await rc.get_partner(user_id) is not None:
        return "⚠️ الان توی یه گفتگوی ۱به۱ فعالی. اول اون رو تموم کن."
    if await rc.is_waiting(user_id):
        return "⚠️ الان توی صفِ انتظارِ چتِ ناشناسی. اول از اونجا خارج شو."
    if await rc.is_waiting_room_join(user_id) is not None:
        return "⚠️ همین الان منتظرِ پیدا شدنِ یه اتاقی. اول جستجوی فعلی رو لغو کن یا صبر کن تموم بشه."
    return None


async def _redis_conflict_check(user_id: int) -> str | None:
    """نسخه‌ی ماشین‌خوانِ همون چکِ بالا، برای تزریق به‌عنوانِ
    conflict_check داخلِ تراکنشِ create_chat_room/join_chat_room؛ کدِ
    خام برمی‌گردونه ("in_1to1"/"in_queue")، نه پیامِ فارسی، چون caller
    (که ممکنه create یا join یا حتی trigger صف باشه) خودش می‌دونه
    چه پیامی مناسبِ context خودشه."""
    if await rc.get_partner(user_id) is not None:
        return "in_1to1"
    if await rc.is_waiting(user_id):
        return "in_queue"
    return None
