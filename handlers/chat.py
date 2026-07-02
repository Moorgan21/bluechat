"""
هندلرهای هسته‌ی چت ناشناس: انتخاب جنسیت مطلوب، matching صف‌بندی‌شده
بر اساس جنسیت، پین‌کردن پیام صف، timeout دو دقیقه‌ای، relay پیام/
ریکشن، پایان چت و پاک‌کردن تاریخچه‌ی دوطرفه.
"""

import asyncio
import logging
import os
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyParameters, Update
from telegram.constants import ChatAction
from telegram.error import TelegramError
from telegram.ext import ContextTypes

import redis_client as rc
import metrics
from db import (
    ChatSession,
    User,
    async_session,
    clear_photo_file_id,
    deduct_coins,
    get_or_create_user,
    increment_total_chats,
    mark_session_history_deleted,
    refund_coins,
    store_chat_message,
)
from keyboards import (
    cancel_queue_keyboard,
    delete_history_keyboard,
    desired_gender_keyboard,
    end_chat_actions_keyboard,
    end_chat_confirm_keyboard,
    in_chat_reply_keyboard,
    main_reply_keyboard,
    public_profile_keyboard,
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

        # یه لحظه صبر کن — شاید کاربر دیگه‌ای همزمان وارد صف شده باشه
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
    """هندلر دکمه‌های «دختر/پسر/فرقی‌نمی‌کنه» — بعد از انتخاب، matching
    واقعی شروع می‌شه."""
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


def _profile_url_button(referral_code: str) -> InlineKeyboardMarkup:
    bot_username = os.environ.get("BOT_USERNAME", "")
    url = f"https://t.me/{bot_username}?start=u_{referral_code}"
    return InlineKeyboardMarkup([[InlineKeyboardButton("👤 مشاهده پروفایل عمومی", url=url)]])


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


async def relay_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    partner_id = await rc.get_partner(user_id)

    if partner_id is None:
        if await rc.is_waiting(user_id):
            await update.message.reply_text("هنوز در صف انتظاری. لطفاً صبر کن ⏳")
        else:
            await update.message.reply_text(
                "هیچ گفتگویی فعال نیست. از منو «وصل کن به یه ناشناس!» رو بزن.",
                reply_markup=main_reply_keyboard(),
            )
        return

    msg = update.message

    # فرمان حذف: اگه کاربر «حذف» یا «del» رو روی یکی از پیام‌های خودش ریپلای کرد
    if msg.text and msg.text.strip().lower() in ("حذف", "del") and msg.reply_to_message:
        replied_mid = msg.reply_to_message.message_id
        if await rc.is_own_message(user_id, replied_mid):
            linked = await rc.get_linked_message(user_id, replied_mid)
            if linked:
                _, partner_msg_id = linked
                try:
                    await context.bot.delete_message(partner_id, partner_msg_id)
                except TelegramError:
                    pass
                try:
                    await context.bot.delete_message(user_id, replied_mid)
                except TelegramError:
                    pass
            try:
                await msg.delete()
            except TelegramError:
                pass
        else:
            await update.message.reply_text("فقط می‌تونی پیام‌هایی که خودت فرستادی رو حذف کنی.")
            try:
                await msg.delete()
            except TelegramError:
                pass
        return

    await context.bot.send_chat_action(partner_id, ChatAction.TYPING)

    # اگه کاربر به پیامی ریپلای کرده، ID معادل اون پیام در چت پارتنر رو پیدا می‌کنیم
    reply_params = None
    if msg.reply_to_message:
        linked = await rc.get_linked_message(user_id, msg.reply_to_message.message_id)
        if linked is not None:
            _, partner_msg_id = linked
            reply_params = ReplyParameters(message_id=partner_msg_id)

    secure = await rc.is_secure_chat(user_id)

    sent_msg = None
    try:
        if msg.text:
            sent_msg = await context.bot.send_message(partner_id, msg.text, reply_parameters=reply_params, protect_content=secure)
        elif msg.photo:
            sent_msg = await context.bot.send_photo(partner_id, msg.photo[-1].file_id, caption=msg.caption, reply_parameters=reply_params, protect_content=secure)
        elif msg.sticker:
            sent_msg = await context.bot.send_sticker(partner_id, msg.sticker.file_id, reply_parameters=reply_params, protect_content=secure)
        elif msg.voice:
            sent_msg = await context.bot.send_voice(partner_id, msg.voice.file_id, caption=msg.caption, reply_parameters=reply_params, protect_content=secure)
        elif msg.video:
            sent_msg = await context.bot.send_video(partner_id, msg.video.file_id, caption=msg.caption, reply_parameters=reply_params, protect_content=secure)
        elif msg.video_note:
            sent_msg = await context.bot.send_video_note(partner_id, msg.video_note.file_id, reply_parameters=reply_params, protect_content=secure)
        elif msg.document:
            sent_msg = await context.bot.send_document(partner_id, msg.document.file_id, caption=msg.caption, reply_parameters=reply_params, protect_content=secure)
        elif msg.animation:
            sent_msg = await context.bot.send_animation(partner_id, msg.animation.file_id, caption=msg.caption, reply_parameters=reply_params, protect_content=secure)
        else:
            await update.message.reply_text("این نوع پیام پشتیبانی نمی‌شه.")
            return

        if sent_msg is not None:
            metrics.messages_relayed.inc()
            await rc.link_messages(user_id, msg.message_id, partner_id, sent_msg.message_id)
            await rc.record_message(user_id, msg.message_id)
            await rc.record_message(partner_id, sent_msg.message_id)
            await rc.mark_own_message(user_id, msg.message_id)
            await rc.increment_chat_msg_count(user_id, partner_id)

            # ذخیره‌ی متنِ پیام در Postgres (فقط برای امکانِ قضاوتِ AI در
            # صورت گزارش‌شدن). محتوای مدیا ذخیره نمی‌شه، فقط نوعش.
            session_id = await rc.get_session_id(user_id)
            if session_id is not None:
                content_type = "text" if msg.text else (
                    "photo" if msg.photo else
                    "sticker" if msg.sticker else
                    "voice" if msg.voice else
                    "video" if msg.video else
                    "video_note" if msg.video_note else
                    "document" if msg.document else
                    "animation" if msg.animation else "other"
                )
                await store_chat_message(
                    session_id, user_id, msg.text if msg.text else None, content_type
                )

    except TelegramError:
        logger.exception("خطا در ارسال پیام به partner_id=%s", partner_id)
        await update.message.reply_text("⚠️ ارسال پیام با خطا مواجه شد. همراهت شاید ربات رو بلاک کرده.")


async def relay_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """وقتی کاربر پیام متنی‌اش رو ویرایش می‌کنه، نسخه‌ی relay‌شده هم آپدیت میشه."""
    msg = update.edited_message
    if msg is None or not msg.text:
        return

    user_id = msg.from_user.id
    partner_id = await rc.get_partner(user_id)
    if partner_id is None:
        return

    linked = await rc.get_linked_message(user_id, msg.message_id)
    if linked is None:
        return

    _, partner_msg_id = linked

    from datetime import datetime, timezone, timedelta
    edit_time = datetime.now(tz=timezone(timedelta(hours=3, minutes=30)))
    time_str = edit_time.strftime("%H:%M")

    secure = await rc.is_secure_chat(user_id)
    try:
        await context.bot.edit_message_text(
            chat_id=partner_id,
            message_id=partner_msg_id,
            text=f"{msg.text}\n\n✏️ ویرایش شده · {time_str}",
            protect_content=secure,
        )
    except TelegramError:
        pass


async def relay_reaction(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    reaction_update = update.message_reaction
    if reaction_update is None or reaction_update.user is None:
        return

    user_id = reaction_update.user.id
    message_id = reaction_update.message_id

    linked = await rc.get_linked_message(user_id, message_id)
    if linked is None:
        return

    target_user_id, target_message_id = linked
    new_reactions = list(reaction_update.new_reaction)

    try:
        await context.bot.set_message_reaction(
            chat_id=target_user_id,
            message_id=target_message_id,
            reaction=new_reactions if new_reactions else None,
        )
    except TelegramError:
        logger.exception(
            "خطا در ست کردن ریکشن برای target_user_id=%s message_id=%s",
            target_user_id, target_message_id,
        )


async def show_partner_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """هندلر دکمه‌ی «👤 مشاهده پروفایل طرف مقابل» — فقط حین یک گفتگوی
    فعال کار می‌کنه."""
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

    keyboard = public_profile_keyboard(partner_id, partner.reactions_enabled)

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
    """هندلر دکمه‌ی «🔒 چت امن» — پیام‌ها رو با protect_content ارسال
    می‌کنه تا فوروارد و ذخیره‌سازی غیرممکن بشه."""
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


async def end_chat_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """هندلر دکمه‌ی «⛔️ پایان چت» — حداقل ۱۰ ثانیه حضور لازمه."""
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


async def offer_history_deletion(
    user_a: int, user_b: int, context: ContextTypes.DEFAULT_TYPE, session_id: int | None
) -> None:
    await rc.start_pending_delete(user_a, user_b)

    text = (
        "اگه می‌خوای تاریخچه‌ی این گفتگو کامل و برای هر دو طرف پاک بشه، دکمه‌ی زیر رو بزن.\n"
        "(تا وقتی طرف مقابل هم تایید نکنه، چیزی حذف نمی‌شه.)\n\n"
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

    already_confirmed_before = clicker_id in (await rc.get_pending_delete_set(user_a, user_b) or set())
    confirmed = await rc.confirm_pending_delete(user_a, user_b, clicker_id)
    if confirmed is None:
        await query.edit_message_text("این درخواست دیگه معتبر نیست (منقضی شده یا گفتگوی جدیدی شروع شده).")
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

    await rc.clear_pending_delete(user_a, user_b)
    await query.edit_message_text("در حال پاک‌کردن تاریخچه برای هر دو طرف... 🗑")

    for uid in (user_a, user_b):
        message_ids = await rc.pop_history(uid)
        for mid in message_ids:
            try:
                await context.bot.delete_message(chat_id=uid, message_id=mid)
            except TelegramError:
                continue

    # حذف واقعیِ متنِ پیام‌ها از Postgres (اگه سشنی برای این جفت وجود
    # داشته باشه). بعد از این، این گفتگو دیگه قابل قضاوتِ AI نیست.
    if session_id is not None:
        await mark_session_history_deleted(session_id)

    for uid in (user_a, user_b):
        try:
            await context.bot.send_message(uid, "✅ تاریخچه‌ی این گفتگو برای هر دو طرف پاک شد.")
        except TelegramError:
            pass
