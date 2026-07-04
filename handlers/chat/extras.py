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
امکاناتِ جانبیِ حینِ چتِ فعال: مشاهده‌ی پروفایلِ طرفِ مقابل، چتِ امن، و
پیشنهاد/تاییدِ پاک‌کردنِ دوطرفه‌ی تاریخچه بعد از پایانِ چت.
"""

import logging

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

import redis_client as rc
from db import User, async_session, clear_photo_file_id, is_sender_blocked, mark_session_history_deleted
from keyboards import end_chat_actions_keyboard, in_chat_reply_keyboard, main_reply_keyboard, public_profile_keyboard

logger = logging.getLogger(__name__)


async def show_partner_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """هندلر دکمه‌ی «👤 مشاهده پروفایل طرف مقابل»؛ فقط حینِ یه گفتگوی فعال کار می‌کنه."""
    from handlers.profile import GENDER_LABELS

    user_id = update.effective_user.id
    partner_id = await rc.get_partner(user_id)

    if partner_id is None:
        await update.message.reply_text(
            "الان توی گفتگویی نیستی.", reply_markup=main_reply_keyboard()
        )
        return

    async with async_session() as session:
        partner = await session.get(User, partner_id)

    if partner is None:
        await update.message.reply_text("پروفایل طرف مقابل در دسترس نیست.")
        return

    last_seen_ts = await rc.get_last_seen(partner_id)
    last_seen_text = rc.format_last_seen(last_seen_ts)
    location_line = ""
    if partner.province or partner.city:
        parts = [p for p in (partner.province, partner.city) if p]
        location_line = f"📍 موقعیت: {' — '.join(parts)}\n"

    text = (
        f"👤 پروفایل همراه گفتگو\n\n"
        f"نام نمایشی: {partner.display_name or 'تنظیم‌نشده'}\n"
        f"بیوگرافی: {partner.bio or '—'}\n"
        f"جنسیت: {GENDER_LABELS.get(partner.gender, 'تنظیم‌نشده')}\n"
        f"سن: {partner.age or '—'}\n"
        f"{location_line}"
        f"آخرین بازدید: {last_seen_text}\n"
        f"🔗 پروفایل عمومی: /user_{partner.referral_code}"
    )

    is_blocked = await is_sender_blocked(user_id, partner_id)
    keyboard = public_profile_keyboard(partner_id, partner.reactions_enabled, is_blocked=is_blocked)

    if partner.photo_file_id:
        try:
            await context.bot.send_photo(
                update.effective_chat.id, partner.photo_file_id, caption=text, reply_markup=keyboard
            )
        except TelegramError:
            await clear_photo_file_id(partner_id)
            await update.message.reply_text(text, reply_markup=keyboard)
    else:
        await update.message.reply_text(text, reply_markup=keyboard)

    # اطلاع به پارتنر که پروفایلشون مشاهده شد
    try:
        await context.bot.send_message(partner_id, "👀 همراه گفتگوت پروفایل شما رو مشاهده کرد.")
    except TelegramError:
        pass


async def toggle_secure_chat_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """هندلر دکمه‌ی «🔒 چت امن»؛ پیام‌ها رو با protect_content می‌فرسته که
    فوروارد و ذخیره‌سازی غیرممکن بشه."""
    user_id = update.effective_user.id
    partner_id = await rc.get_partner(user_id)

    if partner_id is None:
        await update.message.reply_text("الان توی گفتگویی نیستی.", reply_markup=main_reply_keyboard())
        return

    enabled = await rc.toggle_secure_chat(user_id)

    if enabled:
        text = (
            "🔒 چت امن فعال شد.\n"
            "پیام‌های تو قابل فوروارد و ذخیره‌سازی نیستن.\n"
            "⚠️ جلوگیری از اسکرین‌شات در دستِ تلگرام هست، نه ربات."
        )
    else:
        text = "🔓 چت امن غیرفعال شد. پیام‌هات دوباره قابل فوروارده."

    await update.message.reply_text(text, reply_markup=in_chat_reply_keyboard(secure=enabled))
    partner_text = (
        "🔒 طرف مقابل چت امن رو برای پیام‌هاش فعال کرد."
        if enabled else
        "🔓 طرف مقابل چت امن رو برای پیام‌هاش غیرفعال کرد."
    )
    try:
        await context.bot.send_message(partner_id, partner_text)
    except TelegramError:
        pass


async def offer_history_deletion(
    user_a: int, user_b: int, context: ContextTypes.DEFAULT_TYPE, session_id: int | None
) -> None:
    # اگه اصلاً پیامی رد و بدل نشده، پیشنهادِ پاک‌سازی/گزارش الکیه؛ به‌جاش
    # صراحتاً می‌گیم چیزی برای این دو کار نیست (نه دکمه‌ای، نه jobِ
    # auto-purge‌ای که چیزِ خالی رو «پاک» کنه).
    if not await rc.has_chat_history(user_a, session_id) and not await rc.has_chat_history(user_b, session_id):
        for uid in (user_a, user_b):
            try:
                await context.bot.send_message(uid, "ℹ️ این گفتگو هیچ پیامی نداشت؛ چیزی برای پاک‌سازی یا گزارش‌دادن وجود نداره.")
            except TelegramError:
                logger.warning("امکان اطلاع‌رسانیِ نبودِ تاریخچه به user_id=%s وجود نداشت.", uid)
        return

    await rc.start_pending_delete(user_a, user_b, session_id)

    text = (
        "اگه می‌خوای تاریخچه‌ی این گفتگو کامل و برای هر دو طرف پاک بشه، دکمه‌ی زیر رو بزن.\n"
        "(تا وقتی طرف مقابل هم تایید نکنه، چیزی حذف نمی‌شه.)\n\n"
        "⏳ توجه: این دکمه و دکمه‌ی «🚫 گزارش این گفتگو» فقط تا ۲ دقیقه معتبرن. "
        "بعد از ۲ دقیقه، تاریخچه‌ی متنیِ این گفتگو خودکار از روی سرور پاک می‌شه "
        "(بدونِ اینکه پیام‌های خودِ تلگرامتون حذف بشه) و دیگه نه پاکسازیِ دستی "
        "ممکنه نه گزارش‌دادن.\n\n"
        "⚠️ توجه: بعد از پاک‌شدنِ تاریخچه، دیگه امکان گزارش‌دادن یا بررسیِ این "
        "گفتگو وجود نداره (چون متنش کامل حذف می‌شه)."
    )

    # کیبورد برای هر نفر جدا ساخته می‌شه چون دکمه‌ی گزارش باید طرفِ
    # مقابلِ همون شخص رو گزارش بده (reported_id برای هر کاربر فرق داره).
    for uid, partner_of_uid in ((user_a, user_b), (user_b, user_a)):
        keyboard = end_chat_actions_keyboard(user_a, user_b, session_id, reported_id=partner_of_uid)
        try:
            await context.bot.send_message(uid, text, reply_markup=keyboard)
        except TelegramError:
            logger.warning("امکان ارسال پیشنهاد حذف تاریخچه به user_id=%s وجود نداشت.", uid)

    context.job_queue.run_once(
        _auto_purge_session_history_job,
        when=rc.TTL_PENDING_DELETE,
        data={"user_a": user_a, "user_b": user_b, "session_id": session_id},
        name=f"post_chat_purge_{session_id if session_id is not None else rc.pair_key(user_a, user_b)}",
    )


async def _auto_purge_session_history_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """بعد از ۲ دقیقه از پایانِ چت (حتی اگه هیچ‌کدوم دکمه‌ای نزده باشن)،
    تاریخچه‌ی متنیِ این گفتگو از سرور (Redis + Postgres) پاک می‌شه، ولی
    برخلافِ پاکسازیِ دستی، پیام‌های خودِ تلگرام کاربرها دست‌نخورده
    می‌مونن — این فقط یه سیاستِ نگه‌داری/حریمِ‌خصوصیِ خودکاره، نه یه
    اقدامِ عمدیِ کاربر که بخواد از چتش چیزی محو بشه."""
    data = context.job.data
    user_a, user_b, session_id = data["user_a"], data["user_b"], data["session_id"]

    await rc.clear_pending_delete(user_a, user_b, session_id)
    await rc.pop_history(user_a, session_id)
    await rc.pop_history(user_b, session_id)
    if session_id is not None:
        await mark_session_history_deleted(session_id)


async def handle_delete_history_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    try:
        parts = query.data.split(":")
        _, user_a_str, user_b_str, session_id_str = parts
        user_a, user_b = int(user_a_str), int(user_b_str)
        session_id = int(session_id_str) if session_id_str != "none" else None
    except (ValueError, AttributeError):
        return

    clicker_id = query.from_user.id
    if clicker_id not in (user_a, user_b):
        return

    already_confirmed_before = clicker_id in (await rc.get_pending_delete_set(user_a, user_b, session_id) or set())
    confirmed = await rc.confirm_pending_delete(user_a, user_b, clicker_id, session_id)
    if confirmed is None:
        await query.edit_message_text("⚠️ این درخواست دیگه معتبر نیست (مهلتِ ۲دقیقه‌ای گذشته).")
        return

    other_id = user_b if clicker_id == user_a else user_a

    if len(confirmed) < 2:
        await query.edit_message_text(
            "✅ تاییدت ثبت شد. به محض اینکه طرف مقابل هم تایید کنه، تاریخچه برای هر دو نفر پاک می‌شه."
        )
        if not already_confirmed_before:
            try:
                await context.bot.send_message(
                    other_id,
                    "همراه گفتگوی قبلیت درخواست پاک‌کردن تاریخچه داده. اگه موافقی، دکمه‌ی "
                    "«🗑 پاک کردن تاریخچه چت» رو توی همون پیام بزن.",
                )
            except TelegramError:
                pass
        return

    await rc.clear_pending_delete(user_a, user_b, session_id)
    for job in context.job_queue.get_jobs_by_name(
        f"post_chat_purge_{session_id if session_id is not None else rc.pair_key(user_a, user_b)}"
    ):
        job.schedule_removal()
    await query.edit_message_text("در حال پاک‌کردن تاریخچه برای هر دو طرف... 🗑")

    any_deleted = False
    for uid in (user_a, user_b):
        message_ids = await rc.pop_history(uid, session_id)
        for mid in message_ids:
            any_deleted = True
            try:
                await context.bot.delete_message(chat_id=uid, message_id=mid)
            except TelegramError:
                continue

    # حذف واقعیِ متنِ پیام‌ها از Postgres (اگه سشنی برای این جفت وجود
    # داشته باشه). بعد از این، این گفتگو دیگه قابل قضاوتِ AI نیست.
    if session_id is not None:
        await mark_session_history_deleted(session_id)

    # حالتِ لبه: بینِ پیشنهاد و کلیک، تاریخچه از یه راهِ دیگه (مثلاً
    # همون jobِ auto-purge) خالی شده — به‌جای «پاک شد»ِ گمراه‌کننده،
    # صراحتاً می‌گیم چیزی برای پاک‌سازی نبود.
    summary = "✅ تاریخچه‌ی این گفتگو برای هر دو طرف پاک شد." if any_deleted else "ℹ️ تاریخچه‌ای برای پاک‌سازی پیدا نشد (شاید قبلاً پاک شده بود)."
    for uid in (user_a, user_b):
        try:
            await context.bot.send_message(uid, summary)
        except TelegramError:
            pass
