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

"""
جستجو و matching: انتخاب جنسیت مطلوب، صف‌بندی بر اساس جنسیت، پین‌کردن
پیام صف، و timeout دو دقیقه‌ای.
"""

import asyncio
import logging

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

import redis_client as rc
import metrics
from db import (
    ChatSession,
    User,
    async_session,
    deduct_coins,
    get_or_create_user,
    increment_total_chats,
    refund_coins,
)
from keyboards import (
    cancel_queue_keyboard,
    desired_gender_keyboard,
    in_chat_reply_keyboard,
    main_reply_keyboard,
)

logger = logging.getLogger(__name__)


async def try_match(user_id: int, context: ContextTypes.DEFAULT_TYPE, desired_gender: str | None) -> bool:
    """سعی می‌کنه بر اساس desired_gender ("male"/"female"/None) برای
    user_id یه کاندیدای مناسب پیدا کنه. اگه پیدا نشد، وارد صفِ مخصوصِ
    جنسیتِ خودش می‌شه، پیامِ صف رو پین می‌کنه، و یه job برای timeout دو
    دقیقه‌ای زمان‌بندی می‌کنه."""
    partner_id = await rc.pop_matching_waiting(user_id, desired_gender)

    if partner_id is None:
        entered = await rc.enqueue(user_id, desired_gender)
        if not entered:
            await context.bot.send_message(
                user_id,
                "⚠️ برای ورود به صف باید جنسیتت توی پروفایل تنظیم شده باشه. "
                "از منوی «👤 پروفایل» جنسیتت رو تنظیم کن.",
            )
            return False

        # یه لحظه صبر می‌کنیم، شاید کاربر دیگه‌ای همزمان وارد صف شده باشه
        await asyncio.sleep(0.8)
        partner_id = await rc.pop_matching_waiting(user_id, desired_gender)
        if partner_id is not None:
            await rc.dequeue(user_id)
        else:
            sent = await context.bot.send_message(
                user_id,
                "⏳ شما در صف هستید...\nبه محض پیدا شدن یک همراه، بهت خبر می‌دم.",
                reply_markup=cancel_queue_keyboard(),
            )
            try:
                await context.bot.pin_chat_message(user_id, sent.message_id, disable_notification=True)
                await rc.set_queue_pin_message(user_id, sent.message_id)
            except TelegramError:
                logger.warning("امکان پین‌کردن پیامِ صف برای user_id=%s وجود نداشت.", user_id)

            context.job_queue.run_once(
                _queue_timeout_job,
                when=rc.QUEUE_TIMEOUT_SECONDS,
                data={"user_id": user_id},
                name=f"queue_timeout_{user_id}",
            )
            return False

    await rc.dequeue(user_id)
    await _unpin_queue_message(user_id, context)
    await _unpin_queue_message(partner_id, context)

    await rc.set_partner(user_id, partner_id)

    async with async_session() as session:
        chat_session = ChatSession(user_a_id=user_id, user_b_id=partner_id)
        session.add(chat_session)
        await session.commit()
        await session.refresh(chat_session)
        await rc.set_session_id(user_id, partner_id, chat_session.id)

    await increment_total_chats([user_id, partner_id])

    text = (
        "✅ یک همراه گفتگو پیدا شد! هر چی بنویسی ناشناس براش ارسال می‌شه.\n"
        "می‌تونی روی پیام‌ها ریکشن هم بزنی، برای طرف مقابل هم نمایش داده می‌شه.\n"
        "از دکمه‌های پایین برای مشاهده پروفایل طرف مقابل یا پایان چت استفاده کن."
    )
    for uid in (user_id, partner_id):
        await context.bot.send_message(uid, text, reply_markup=in_chat_reply_keyboard())
    metrics.chats_started.inc()
    metrics.active_chats.inc()
    return True


async def _unpin_queue_message(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    """پیامِ پین‌شده‌ی صفِ این کاربر (اگه وجود داشته باشه) رو آنپین
    می‌کنه و job تایم‌اوتِ مربوطه رو لغو می‌کنه."""
    await rc.pop_queue_pin_message(user_id)
    try:
        await context.bot.unpin_all_chat_messages(user_id)
    except TelegramError:
        pass

    jobs = context.job_queue.get_jobs_by_name(f"queue_timeout_{user_id}")
    for job in jobs:
        job.schedule_removal()


async def _queue_timeout_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """بعد از ۲ دقیقه اجرا می‌شه: اگه کاربر هنوز توی صفه (یعنی هنوز
    match نشده)، از صف خارجش می‌کنه، پیامِ صف رو آنپین می‌کنه و بهش
    اطلاع می‌ده که کسی پیدا نشد."""
    user_id = context.job.data["user_id"]

    if not await rc.is_waiting(user_id):
        return  # قبلاً match شده یا خودش /stop زده؛ کاری لازم نیست

    await rc.dequeue(user_id)
    await _unpin_queue_message(user_id, context)

    if await rc.is_chat_payer(user_id):
        await refund_coins(user_id, rc.CHAT_COIN_COST, "search_timeout_refund")
        await rc.r.delete(rc.KEY_CHAT_PAYER.format(user_id=user_id))

    try:
        await context.bot.send_message(
            user_id,
            "❌ متاسفانه کسی پیدا نشد. می‌تونی دوباره امتحان کنی.",
            reply_markup=main_reply_keyboard(),
        )
    except TelegramError:
        logger.warning("امکان اطلاع‌رسانیِ timeout صف به user_id=%s وجود نداشت.", user_id)


async def start_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """هندلر /start و دکمه‌ی «وصل کن به یه ناشناس!». قبل از ورود به چت،
    کامل‌بودن پروفایل (نام/جنسیت/سن) رو چک می‌کنه؛ در صورت ناقص‌بودن
    وارد onboarding می‌شه. در غیر این صورت، اول می‌پرسه دنبال چه
    جنسیتی می‌گرده (دختر/پسر/فرقی‌نمی‌کنه)."""
    from handlers.profile import is_profile_complete, start_onboarding

    user_id = update.effective_user.id
    telegram_user = update.effective_user

    async with async_session() as session:
        user = await get_or_create_user(
            session, telegram_user.id, telegram_user.username, telegram_user.first_name
        )
        profile_complete = is_profile_complete(user)

    if not profile_complete:
        await start_onboarding(update, context)
        return

    if await rc.get_partner(user_id) is not None:
        await update.effective_message.reply_text(
            "الان توی یه گفتگو هستی.", reply_markup=in_chat_reply_keyboard()
        )
        return

    if await rc.is_waiting(user_id):
        await update.effective_message.reply_text("در حال حاضر توی صف انتظاری. کمی صبر کن 🙂")
        return

    async with async_session() as session:
        user = await session.get(User, user_id)
    saved_pref = user.next_gender_pref if user else None

    if saved_pref is not None:
        desired_gender = None if saved_pref == "any" else saved_pref
        if desired_gender is not None:
            new_balance = await deduct_coins(user_id, rc.CHAT_COIN_COST, "gender_search")
            if new_balance is None:
                await update.effective_message.reply_text(
                    f"🪙 سکه‌ی کافی نداری!\n"
                    f"جستجو با فیلتر جنسیت {rc.CHAT_COIN_COST} سکه هزینه داره.\n"
                    "برای جستجوی رایگان «فرقی نمی‌کنه» رو از /settings انتخاب کن."
                )
                return
            await rc.set_chat_payer(user_id)
        await update.effective_message.reply_text("👋 در حال جستجوی یه همراه برات هستم...")
        await try_match(user_id, context, desired_gender)
    else:
        await update.effective_message.reply_text(
            "می‌خوای به چه جنسیتی وصل بشی؟\n"
            "💡 می‌تونی این ترجیح رو توی /settings ذخیره کنی تا دیگه هر بار نپرسه.",
            reply_markup=desired_gender_keyboard(),
        )


async def handle_desired_gender_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """هندلر دکمه‌های «دختر/پسر/فرقی‌نمی‌کنه»؛ بعد از انتخاب matching واقعی شروع می‌شه."""
    query = update.callback_query
    await query.answer()

    value = query.data.split(":", 1)[1]  # "male" | "female" | "any"
    desired_gender = None if value == "any" else value

    user_id = query.from_user.id

    if await rc.get_partner(user_id) is not None:
        await query.edit_message_text("الان توی یه گفتگو هستی.")
        return
    if await rc.is_waiting(user_id):
        await query.edit_message_text("در حال حاضر توی صف انتظاری. کمی صبر کن 🙂")
        return

    if desired_gender is not None:
        new_balance = await deduct_coins(user_id, rc.CHAT_COIN_COST, "gender_search")
        if new_balance is None:
            await query.edit_message_text(
                f"🪙 سکه‌ی کافی نداری!\n"
                f"جستجو با فیلتر جنسیت {rc.CHAT_COIN_COST} سکه هزینه داره.\n"
                "برای جستجوی رایگان «فرقی نمی‌کنه» رو انتخاب کن."
            )
            return
        await rc.set_chat_payer(user_id)

    await query.edit_message_text("👋 در حال جستجوی یه همراه برات هستم...")
    await try_match(user_id, context, desired_gender)
