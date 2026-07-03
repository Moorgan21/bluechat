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
چرخه‌ی عمرِ چت: پایان‌دادن (/stop)، رفتن‌سراغِ نفرِ بعدی (/next)، تاییدِ
پایانِ چت با دکمه، و ثبتِ پایانِ سشن در Postgres.
"""

import logging
import time

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

import redis_client as rc
import metrics
from db import ChatSession, User, async_session, deduct_coins, refund_coins
from keyboards import desired_gender_keyboard, end_chat_confirm_keyboard, main_reply_keyboard

from .extras import offer_history_deletion
from .matching import _unpin_queue_message, try_match

logger = logging.getLogger(__name__)


async def _end_session_record(user_a: int, user_b: int, ended_by: int) -> int | None:
    session_id = await rc.get_session_id(user_a)
    if session_id is None:
        return None
    async with async_session() as session:
        chat_session = await session.get(ChatSession, session_id)
        if chat_session is not None:
            from datetime import datetime

            chat_session.ended_at = datetime.utcnow()
            chat_session.ended_by = ended_by
            await session.commit()
    await rc.clear_session_id(user_a)
    await rc.clear_session_id(user_b)
    metrics.chats_ended.inc()
    metrics.active_chats.dec()
    return session_id


async def handle_cancel_queue_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """هندلر دکمه‌ی «❌ لغو جستجو» زیرِ پیامِ صفِ انتظار. دقیقاً همون
    منطقِ خروج از صف در stop_chat رو اجرا می‌کنه."""
    await update.callback_query.answer()
    await stop_chat(update, context)


async def stop_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id

    if await rc.is_waiting(user_id):
        await rc.dequeue(user_id)
        await _unpin_queue_message(user_id, context)
        if await rc.is_chat_payer(user_id):
            await refund_coins(user_id, rc.CHAT_COIN_COST, "search_cancel_refund")
            await rc.r.delete(rc.KEY_CHAT_PAYER.format(user_id=user_id))
        await update.effective_message.reply_text(
            "از صف انتظار خارج شدی.",
            reply_markup=main_reply_keyboard(),
        )
        return

    # پیش از clear_partner، وضعیت payer و تعداد پیام‌ها رو می‌گیریم
    partner_id = await rc.get_partner(user_id)
    if partner_id is not None:
        msg_count = await rc.get_chat_msg_count(user_id, partner_id)
        user_is_payer = await rc.is_chat_payer(user_id)
        partner_is_payer = await rc.is_chat_payer(partner_id)

    partner_id = await rc.clear_partner(user_id)
    if partner_id is not None:
        session_id = await _end_session_record(user_id, partner_id, ended_by=user_id)

        async with async_session() as session:
            ender = await session.get(User, user_id)
            partner = await session.get(User, partner_id)

        ender_name = (ender.display_name or "کاربر") if ender else "کاربر"
        partner_name = (partner.display_name or "کاربر") if partner else "کاربر"
        partner_profile = f"\n👤 پروفایل عمومی: /u_{partner.referral_code}" if (partner and partner.referral_code) else ""
        ender_profile = f"\n👤 پروفایل عمومی: /u_{ender.referral_code}" if (ender and ender.referral_code) else ""

        # بازگشت سکه اگه چت ناموفق بود (کمتر از ۳ پیام)
        ender_refund_note = ""
        partner_refund_note = ""
        if msg_count < 3:
            if user_is_payer:
                await refund_coins(user_id, rc.CHAT_COIN_COST, "failed_chat_refund")
                ender_refund_note = f"\n🪙 بعلت ناموفق بودن چت، {rc.CHAT_COIN_COST} سکه برگشت به حساب شما."
            if partner_is_payer:
                await refund_coins(partner_id, rc.CHAT_COIN_COST, "failed_chat_refund")
                partner_refund_note = f"\n🪙 بعلت ناموفق بودن چت، {rc.CHAT_COIN_COST} سکه برگشت به حساب شما."

        await update.effective_message.reply_text(
            f"چت شما با {partner_name} توسط شما به پایان رسید.{partner_profile}{ender_refund_note}",
            reply_markup=main_reply_keyboard(),
        )
        try:
            await context.bot.send_message(
                partner_id,
                f"چت شما با {ender_name} توسط مقابل به پایان رسید.{ender_profile}{partner_refund_note}",
                reply_markup=main_reply_keyboard(),
            )
        except TelegramError:
            pass

        await offer_history_deletion(user_id, partner_id, context, session_id)
    else:
        await update.effective_message.reply_text(
            "الان توی هیچ گفتگویی نیستی.", reply_markup=main_reply_keyboard()
        )


async def next_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id

    prev_partner = await rc.get_partner(user_id)
    prev_msg_count = 0
    prev_user_is_payer = False
    prev_partner_is_payer = False
    if prev_partner is not None:
        prev_msg_count = await rc.get_chat_msg_count(user_id, prev_partner)
        prev_user_is_payer = await rc.is_chat_payer(user_id)
        prev_partner_is_payer = await rc.is_chat_payer(prev_partner)

    partner_id = await rc.clear_partner(user_id)
    if partner_id is not None:
        session_id = await _end_session_record(user_id, partner_id, ended_by=user_id)

        async with async_session() as session:
            ender = await session.get(User, user_id)

        ender_name = (ender.display_name or "کاربر") if ender else "کاربر"
        ender_profile = f"\n👤 پروفایل عمومی: /u_{ender.referral_code}" if (ender and ender.referral_code) else ""

        partner_refund_note = ""
        if prev_msg_count < 3:
            if prev_user_is_payer:
                await refund_coins(user_id, rc.CHAT_COIN_COST, "failed_chat_refund")
            if prev_partner_is_payer:
                await refund_coins(partner_id, rc.CHAT_COIN_COST, "failed_chat_refund")
                partner_refund_note = f"\n🪙 بعلت ناموفق بودن چت، {rc.CHAT_COIN_COST} سکه برگشت به حساب شما."

        try:
            await context.bot.send_message(
                partner_id,
                f"چت شما با {ender_name} توسط مقابل به پایان رسید.{ender_profile}{partner_refund_note}",
                reply_markup=main_reply_keyboard(),
            )
        except TelegramError:
            pass
        await offer_history_deletion(user_id, partner_id, context, session_id)

    await rc.dequeue(user_id)

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


async def end_chat_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """هندلر دکمه‌ی «⛔️ پایان چت»؛ حداقل ۱۰ ثانیه حضور لازمه."""
    user_id = update.effective_user.id
    chat_start = await rc.get_chat_start(user_id)
    if chat_start:
        remaining = rc.MIN_CHAT_SECONDS - (time.time() - chat_start)
        if remaining > 0:
            await update.message.reply_text(f"⏳ {int(remaining) + 1} ثانیه مونده تا بتونی چت رو ببندی.")
            return

    await update.message.reply_text(
        "آیا مطمئنی می‌خوای چت رو ببندی؟",
        reply_markup=end_chat_confirm_keyboard(),
    )


async def end_chat_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """هندلر دکمه‌های تأیید/انصراف پایان چت."""
    query = update.callback_query
    await query.answer()
    action = query.data.split(":", 1)[1]
    if action == "confirm":
        try:
            await query.message.delete()
        except TelegramError:
            pass
        await stop_chat(update, context)
    else:
        try:
            await query.message.delete()
        except TelegramError:
            pass
        await context.bot.send_message(query.from_user.id, "😄 به چتت برس!")
