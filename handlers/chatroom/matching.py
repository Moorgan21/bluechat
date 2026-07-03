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

"""فلوی عضویت در اتاقِ چت.

برخلافِ matchingِ ۱به۱ (که هر دو طرف دنبالِ همون کاری‌ن و چکِ لحظه‌ای
موقعِ ورودِ نفرِ دوم کافیه)، اینجا «عرضه» (ساختِ اتاق، یا بعداً
آزادشدنِ یه جا با ترک/اخراج) از یه اکشنِ کاملاً متفاوت میاد. برای همین
جهتِ trigger برعکسه: به‌جای اینکه جستجوگر منتظرِ notification بمونه،
همون لحظه که عرضه ظاهر می‌شه (try_fill_room_from_queue) صفِ انتظار رو
فعالانه چک می‌کنه. این یعنی نه Pub/Sub لازمه نه تسکِ پابرجای
per-searcher؛ فقط همون job_queueِ استانداردِ پروژه برای تایم‌اوت،
دقیقاً مثلِ _queue_timeout_job در chat/matching.py.
"""

import logging

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

import redis_client as rc
from db import (
    RoomStatus,
    deduct_coins,
    find_open_room_for_join,
    get_chat_room,
    join_chat_room,
    list_open_room_ids_with_spare_capacity,
    refund_coins,
)
from keyboards import in_room_reply_keyboard, main_reply_keyboard, room_join_gender_keyboard
from .creation import GENDER_LABELS_FA

logger = logging.getLogger(__name__)

ROOM_JOIN_COST = 3


async def start_join_flow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from .creation import _check_can_start_room_flow

    query = update.callback_query
    user_id = query.from_user.id

    blocked_reason = await _check_can_start_room_flow(user_id)
    if blocked_reason:
        await query.edit_message_text(blocked_reason)
        return

    await query.edit_message_text(
        f"دنبالِ چه نوع اتاقی می‌گردی؟ (هزینه‌ی جستجو: {ROOM_JOIN_COST} سکه)",
        reply_markup=room_join_gender_keyboard(),
    )


async def handle_join_gender_selected(update: Update, context: ContextTypes.DEFAULT_TYPE, value: str) -> None:
    from .creation import _check_can_start_room_flow

    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    blocked_reason = await _check_can_start_room_flow(user_id)
    if blocked_reason:
        await query.edit_message_text(blocked_reason)
        return

    new_balance = await deduct_coins(user_id, ROOM_JOIN_COST, "chat_room_join_search")
    if new_balance is None:
        await query.edit_message_text(f"🪙 سکه‌ی کافی نداری! جستجوی اتاق {ROOM_JOIN_COST} سکه هزینه داره.")
        return

    await query.edit_message_text("🔍 دنبالِ یه اتاقِ مناسب می‌گردم...")
    await try_join_room(user_id, value, context)


async def try_join_room(user_id: int, desired_gender: str, context: ContextTypes.DEFAULT_TYPE) -> None:
    """اول یه تلاشِ فوری می‌کنه؛ اگه اتاقِ بازِ سازگاری با جا پیدا شد،
    مستقیم claim می‌کنه. وگرنه وارد صف می‌شه و منتظرِ trigger (ساختِ
    اتاقِ جدید یا آزادشدنِ جا) یا تایم‌اوتِ ۲دقیقه‌ای می‌مونه."""
    room = await find_open_room_for_join(desired_gender)
    if room is not None:
        joined_room, error = await join_chat_room(user_id, room.id)
        if joined_room is not None:
            await _notify_room_join_success(user_id, joined_room, context)
            return
        # یکی زودتر جاشو گرفت (race)؛ به‌جای اینکه اینجا تسلیم بشیم،
        # می‌ریم صف، چون سکه‌ش رو قبلاً پرداخت کرده و باید امتحانِ
        # بعدی رو هم داشته باشه.

    await rc.enqueue_room_join(user_id, desired_gender)
    context.job_queue.run_once(
        _room_join_timeout_job,
        when=rc.ROOM_JOIN_TIMEOUT_SECONDS,
        data={"user_id": user_id, "desired_gender": desired_gender},
        name=f"room_join_timeout_{user_id}",
    )
    try:
        await context.bot.send_message(
            user_id,
            "⏳ فعلاً اتاقِ خالی‌ای پیدا نشد؛ به محضِ باز شدنِ یه جا بهت خبر می‌دم.",
        )
    except TelegramError:
        logger.warning("امکان اطلاع‌رسانیِ صفِ عضویتِ اتاق به user_id=%s وجود نداشت.", user_id)


async def _room_join_timeout_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """بعد از ۲ دقیقه: اگه هنوز توی صفه (یعنی trigger یا claimِ فوری
    گیرش نیاورده)، از صف درش میاره، سکه‌ش رو برمی‌گردونه، و خبرش می‌ده."""
    user_id = context.job.data["user_id"]
    desired_gender = context.job.data["desired_gender"]

    if await rc.is_waiting_room_join(user_id) is None:
        return  # قبلاً claim شده؛ کاری لازم نیست

    await rc.dequeue_room_join(user_id, desired_gender)
    await refund_coins(user_id, ROOM_JOIN_COST, "room_join_timeout_refund")

    try:
        await context.bot.send_message(
            user_id,
            "❌ متاسفانه اتاقِ مناسبی پیدا نشد و سکه‌ت برگشت. می‌تونی دوباره امتحان کنی.",
            reply_markup=main_reply_keyboard(),
        )
    except TelegramError:
        logger.warning("امکان اطلاع‌رسانیِ timeoutِ عضویتِ اتاق به user_id=%s وجود نداشت.", user_id)


async def try_fill_room_from_queue(room_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    """این تابع «trigger»‌ه: بعد از commitِ کاملِ یه عملیاتِ عرضه (ساختِ
    اتاقِ جدید، یا بعداً آزادشدنِ جا با ترک/اخراج) صدا زده می‌شه، نه
    داخلِ تراکنشِ همون عملیات — چون claim خودش یه تراکنشِ FOR UPDATEِ
    جداست و تودرتو کردنشون قفل رو بی‌دلیل نگه می‌داره. تا وقتی ظرفیت
    خالیه و کاندیدای سازگار تو صفه، حلقه می‌زنه (یه اتاقِ تازه‌ساخته
    می‌تونه چند نفرو یک‌جا جذب کنه، نه فقط یکی)."""
    room = await get_chat_room(room_id)
    if room is None or room.status != RoomStatus.open:
        return

    compatible_genders = rc.room_join_compatible_genders(room.gender_pref.value)

    while True:
        candidate = await rc.peek_oldest_room_join_candidate(compatible_genders)
        if candidate is None:
            return

        candidate_id, gender_bucket = candidate
        joined_room, error = await join_chat_room(candidate_id, room_id)

        if error in ("room_full", "room_not_open", "not_found"):
            # مشکل از خودِ اتاقه، نه از کاندیدا؛ دست‌نخورده تو صف
            # می‌مونه تا وقتِ خودش (تایم‌اوت یا اتاقِ دیگه) برسه.
            return

        await rc.dequeue_room_join(candidate_id, gender_bucket)
        _cancel_room_join_timeout_job(candidate_id, context)

        if joined_room is None:
            continue  # کاندیدای بی‌اعتبار بود (مثلاً has_active_room)، بعدی رو امتحان کن

        await _notify_room_join_success(candidate_id, joined_room, context)


async def sweep_room_join_queue(context: ContextTypes.DEFAULT_TYPE) -> None:
    """سیفتی‌نتِ دوره‌ای: اگه trigger به هر دلیلی (مثلاً کرشِ پروسه
    بینِ commit و trigger) از دست رفته باشه، اینجا دوباره تلاش می‌شه.
    purge_stale_room_join_queue هم برای حالتِ نادرِ ریستارتِ ربات حینِ
    انتظارِ کاربره (job_queueِ پیشِ‌فرض بینِ ریستارت‌ها پایدار نمی‌مونه)
    — دقیقاً همون trade-offِ purge_stale_queue_entries صفِ ۱به۱ (بدونِ
    بازگشتِ سکه در اون حالتِ نادر)."""
    await rc.purge_stale_room_join_queue()
    for room_id in await list_open_room_ids_with_spare_capacity():
        await try_fill_room_from_queue(room_id, context)


def _cancel_room_join_timeout_job(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    for job in context.job_queue.get_jobs_by_name(f"room_join_timeout_{user_id}"):
        job.schedule_removal()


async def _notify_room_join_success(user_id: int, room, context: ContextTypes.DEFAULT_TYPE) -> None:
    await rc.set_active_room(user_id, room.id)
    gender_label = GENDER_LABELS_FA.get(room.gender_pref.value, room.gender_pref.value)
    try:
        await context.bot.send_message(
            user_id,
            f"✅ به یه اتاقِ {gender_label} ملحق شدی! از الان هرچی بفرستی توی اتاق relay می‌شه 👇",
            reply_markup=in_room_reply_keyboard(),
        )
    except TelegramError:
        logger.warning("امکان اطلاع‌رسانیِ عضویتِ موفق به user_id=%s وجود نداشت.", user_id)
