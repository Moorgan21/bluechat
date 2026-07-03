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

"""راه‌انداز اصلی ربات چت ناشناس بلو چت.

نیاز به `pip install python-telegram-bot==21.* sqlalchemy[asyncio] asyncpg redis[hiredis]`
و این env varها: BOT_TOKEN، BOT_USERNAME، DATABASE_URL، REDIS_URL.

اجرا با `python main.py`.

ساختار پروژه، خلاصه:
    db/                 - مدل‌ها و کوئری‌های Postgres
    redis_client.py     - صف matching و state لحظه‌ای در Redis
    keyboards.py        - همه‌ی دکمه‌های شیشه‌ای و کیبورد پایین
    handlers/chat/      - matching، relay پیام/ریکشن، پاک‌کردن تاریخچه
    handlers/profile.py - پروفایل کاربر
    handlers/coins.py   - سکه، دعوت دوستان، لینک ناشناس اختصاصی
    handlers/search.py  - جستجوی هدفمند با فیلتر
    handlers/nearby.py  - افراد نزدیک بر اساس موقعیت مکانی
    handlers/report.py  - گزارش کاربر
    handlers/menu.py    - راهنما و منوی اصلی
"""

import logging
import os

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    MessageReactionHandler,
    filters,
)

import redis_client as rc
import metrics
import spam_guard
from db import init_db, get_or_create_user, async_session, refund_coins
from handlers import anon_note, chat, chatroom, coins, menu, nearby, profile, public_profile, report, search, settings

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
WEBHOOK_PORT = int(os.environ.get("WEBHOOK_PORT", "8080"))


# /start با پشتیبانی از deep-link های ref_<code> و direct_<code>
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    telegram_user = update.effective_user
    args = context.args

    invited_by = None
    direct_target_code = None

    if args:
        payload = args[0]
        if payload.startswith("ref_"):
            code = payload[len("ref_"):]
            invited_by = await _resolve_referral_code(code)
        elif payload.startswith("direct_"):
            direct_target_code = payload[len("direct_"):]

    async with async_session() as session:
        user = await get_or_create_user(
            session, telegram_user.id, telegram_user.username, telegram_user.first_name, invited_by
        )

    if direct_target_code:
        await _handle_direct_link(update, context, direct_target_code)
        return

    from handlers.profile import is_profile_complete, start_onboarding

    if not is_profile_complete(user):
        await start_onboarding(update, context)
        return

    # /start معمولی (بدون deep-link خاص) فقط منوی اصلی رو نشون می‌ده؛
    # ورود به صفِ matching فقط با زدنِ دکمه‌ی «وصل کن به یه ناشناس!»
    # اتفاق می‌افته، نه با /start.
    from keyboards import main_reply_keyboard

    await update.message.reply_text(
    f"👋 سلام {telegram_user.first_name or ''}! به ربات بلو چت خوش اومدی.\n"
    "از منوی پایین یکی از گزینه‌ها رو انتخاب کن.",
    reply_markup=main_reply_keyboard(),
    )


async def _resolve_referral_code(code: str) -> int | None:
    from sqlalchemy import select
    from db import User

    async with async_session() as session:
        result = await session.execute(select(User).where(User.referral_code == code))
        user = result.scalar_one_or_none()
        return user.id if user else None


async def _handle_direct_link(update: Update, context: ContextTypes.DEFAULT_TYPE, code: str) -> None:
    """کاربر از طریق لینک ناشناسِ مستقیم اومده. برخلاف matching عادی
    اینجا هیچ ChatSession یا جفت‌شدنِ دائمی نمی‌سازیم، فقط می‌ذاریمش تو
    state «در حال نوشتن پیام ناشناس»؛ پیام بعدیش با
    handlers.anon_note.send_anon_note ارسال می‌شه."""
    from handlers.profile import is_profile_complete, start_onboarding

    requester_id = update.effective_user.id

    async with async_session() as session:
        me = await get_or_create_user(
            session, update.effective_user.id, update.effective_user.username, update.effective_user.first_name
        )
        if not is_profile_complete(me):
            context.user_data["pending_direct_link_code"] = code
            await start_onboarding(update, context)
            return

    target_id = await _resolve_referral_code(code)

    if target_id is None:
        await update.message.reply_text("این لینک ناشناس دیگه معتبر نیست.")
        return
    if target_id == requester_id:
        await update.message.reply_text("این لینک ناشناسِ خودته! نمی‌تونی برای خودت پیام بفرستی 🙂")
        return

    if await rc.get_partner(requester_id) is not None:
        await update.message.reply_text(
            "⚠️ الان توی یه چت فعال هستی و نمی‌تونی پیام ناشناس بفرستی. اول چتت رو پایان بده."
        )
        return

    context.user_data["awaiting_note_target"] = target_id
    from keyboards import cancel_keyboard

    await update.message.reply_text(
        "✍️ پیامت رو بنویس؛ ناشناس برای صاحب این لینک ارسال می‌شه "
        "(بدون اینکه هیچ چت بازی بین شما دو نفر ایجاد بشه):",
        reply_markup=cancel_keyboard(),
    )


# روتینگ پیام‌های متنی/مدیا؛ اول چک میشه ورودیِ در-انتظاره (پروفایل/سرچ)،
# وگرنه میره relay چت ناشناس.
REPLY_KEYBOARD_ROUTES = {
    "💬 وصل کن به یه ناشناس!": chat.start_chat,
    "💬 جستجوی کاربران 🔮": search.show_search_menu,
    "📍 افراد نزدیک 🛰": nearby.show_nearby_menu,
    "🏠 اتاق چت": chatroom.show_room_menu,
    "💰 سکه": coins.show_coins,
    "👤 پروفایل": profile.show_profile,
    "🤔 راهنما": menu.show_help,
    "🔗 معرفی به دوستان (سکه رایگان)": coins.show_invite_link,
    "🥷 لینک ناشناس من": coins.show_anon_link,
    "⚙️ تنظیمات": settings.show_settings,
}


IN_CHAT_KEYBOARD_ROUTES = {
    "👤 مشاهده پروفایل طرف مقابل": chat.show_partner_profile,
    "⛔️ پایان چت": chat.end_chat_button,
    "🔒 چت امن (غیرفعال)": chat.toggle_secure_chat_button,
    "🔒 چت امن (فعال)": chat.toggle_secure_chat_button,
}

IN_ROOM_KEYBOARD_ROUTES = {
    "🔒 چت امن (غیرفعال)": chatroom.toggle_secure_chat_button,
    "🔒 چت امن (فعال)": chatroom.toggle_secure_chat_button,
    "🚪 ترک اتاق": chatroom.leave_room_button,
    "🗑 حذف اتاق": chatroom.delete_room_button,
    "🔒 بستن اتاق": chatroom.close_room_button,
    "🔓 بازکردن اتاق": chatroom.reopen_room_button,
    "🏠 اتاق چت": chatroom.show_room_menu,
}


async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text or ""
    user_id = update.effective_user.id
    await rc.update_last_seen(user_id)

    _spam = await spam_guard.check_message(user_id)
    if _spam == spam_guard.SpamResult.ALREADY_BLOCKED:
        return  # silent drop، ریپلای نمی‌فرستیم
    if _spam == spam_guard.SpamResult.JUST_BLOCKED:
        secs = await spam_guard.remaining_block(user_id)
        await update.message.reply_text(f"⚠️ خیلی سریع پیام می‌فرستی! {secs} ثانیه صبر کن.")
        return

    # اول onboarding (تکمیل اجباری پروفایل) چون همه‌چیز دیگه باید صبر کنه
    if await profile.handle_onboarding_text_input(update, context):
        return

    # بعد اینکه منتظر نوشتنِ یه تگِ جدیدِ واکنشه
    if await public_profile.handle_new_tag_input(update, context):
        return

    # پیام دایرکت (شناسه فرستنده به مقصد نشون داده می‌شه)
    direct_target_id = context.user_data.pop("awaiting_direct_msg_target", None)
    if direct_target_id is not None:
        try:
            await anon_note.send_direct_msg(direct_target_id, user_id, update, context)
        except Exception:
            context.user_data["awaiting_direct_msg_target"] = direct_target_id
            raise
        return

    # پیام ناشناس از طریق لینک مستقیم، بدون ساختن هیچ چت بازی
    note_target_id = context.user_data.pop("awaiting_note_target", None)
    if note_target_id is not None:
        try:
            await anon_note.send_anon_note(note_target_id, user_id, update, context)
        except Exception:
            context.user_data["awaiting_note_target"] = note_target_id
            raise
        return

    # صاحبِ یه لینک ناشناس روی «پاسخ دادن» زده و منتظر متنِ پاسخه
    if await anon_note.handle_pending_reply_input(update, context):
        return

    # توی گفتگوی فعال فقط دو دکمه‌ی مخصوصِ چت و relay پیام مجازن؛ بقیه‌ی
    # دکمه‌های منوی اصلی معمولاً دیده نمی‌شن، ولی اگه از قبل رو صفحه
    # مونده باشن نباید کاری انجام بدن.
    in_active_chat = await rc.get_partner(user_id) is not None
    if in_active_chat:
        if text in IN_CHAT_KEYBOARD_ROUTES:
            await IN_CHAT_KEYBOARD_ROUTES[text](update, context)
            return
        await chat.relay_message(update, context)
        return

    # قفلِ یک‌اتاقِ-فعال یعنی این با in_active_chat بالا mutually
    # exclusiveه؛ چکِ Redis (نه Postgres) برای اینکه هر پیام مجبور
    # نباشه سراغِ دیتابیس بره، دقیقاً مثلِ get_partner.
    active_room_id = await rc.get_active_room(user_id)
    if active_room_id is not None:
        if text in IN_ROOM_KEYBOARD_ROUTES:
            await IN_ROOM_KEYBOARD_ROUTES[text](update, context)
            return
        await chatroom.relay_room_message(update, context, active_room_id)
        return

    # اینجا دیگه توی گفتگو نیست، دکمه‌های منوی اصلی فعالن
    if text in REPLY_KEYBOARD_ROUTES:
        await REPLY_KEYBOARD_ROUTES[text](update, context)
        return

    if await profile.handle_profile_text_input(update, context):
        return
    if await search.handle_search_age_input(update, context):
        return

    await chat.relay_message(update, context)


async def user_profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/user_<code> دستورِ دینامیکه و تلگرام دستوراتِ ثابت می‌خواد، برای
    همین با MessageHandler و regex می‌گیریمش، نه CommandHandler معمولی."""
    text = (update.message.text or "").strip()
    if text.startswith("/user_"):
        code = text[len("/user_"):].split()[0]
    elif text.startswith("/u_"):
        code = text[len("/u_"):].split()[0]
    else:
        code = ""
    if not code:
        await update.message.reply_text("فرمتِ لینک نامعتبره.")
        return
    await public_profile.show_public_profile_by_code(update, context, code)


async def media_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    await rc.update_last_seen(user_id)

    _spam = await spam_guard.check_message(user_id)
    if _spam == spam_guard.SpamResult.ALREADY_BLOCKED:
        return
    if _spam == spam_guard.SpamResult.JUST_BLOCKED:
        secs = await spam_guard.remaining_block(user_id)
        await update.message.reply_text(f"⚠️ خیلی سریع پیام می‌فرستی! {secs} ثانیه صبر کن.")
        return

    direct_target_id = context.user_data.pop("awaiting_direct_msg_target", None)
    if direct_target_id is not None:
        try:
            await anon_note.send_direct_msg(direct_target_id, user_id, update, context)
        except Exception:
            context.user_data["awaiting_direct_msg_target"] = direct_target_id
            raise
        return

    note_target_id = context.user_data.pop("awaiting_note_target", None)
    if note_target_id is not None:
        try:
            await anon_note.send_anon_note(note_target_id, user_id, update, context)
        except Exception:
            context.user_data["awaiting_note_target"] = note_target_id
            raise
        return

    if await anon_note.handle_pending_reply_input(update, context):
        return

    if update.message.photo and await profile.handle_profile_photo_input(update, context):
        return

    active_room_id = await rc.get_active_room(user_id)
    if active_room_id is not None:
        await chatroom.relay_room_message(update, context, active_room_id)
        return

    await chat.relay_message(update, context)


async def edited_message_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """چتِ ۱به۱ و اتاق mutually exclusive‌ن، پس صدا زدنِ هر دو امنه؛
    هرکدوم اگه precondition خودش (partner/active_room) برقرار نباشه،
    خودش زود return می‌کنه."""
    await chat.relay_edit(update, context)
    await chatroom.relay_room_edit(update, context)


# روتر callback_query بر اساس پیشوند callback_data
async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = update.callback_query.data or ""
    if update.effective_user:
        await rc.update_last_seen(update.effective_user.id)
        _spam = await spam_guard.check_command(update.effective_user.id)
        if _spam == spam_guard.SpamResult.ALREADY_BLOCKED:
            await update.callback_query.answer()  # فقط loading رو برمی‌داره، پیامی نمی‌ده
            return
        if _spam == spam_guard.SpamResult.JUST_BLOCKED:
            secs = await spam_guard.remaining_block(update.effective_user.id)
            await update.callback_query.answer(f"⚠️ خیلی سریع! {secs} ثانیه صبر کن.", show_alert=True)
            return

    if data.startswith("delhist:"):
        await chat.handle_delete_history_callback(update, context)
    elif data.startswith("matchgender:"):
        await chat.handle_desired_gender_callback(update, context)
    elif data.startswith("noterview:"):
        await anon_note.handle_view_note(update, context)
    elif data.startswith("directmsg:"):
        await anon_note.handle_direct_msg_button(update, context)
    elif data.startswith("noterep:"):
        await anon_note.handle_reply_button(update, context)
    elif data.startswith("noteblock:"):
        await anon_note.handle_block_button(update, context)
    elif data == "notereplycancel":
        await anon_note.handle_cancel_reply_button(update, context)
    elif data == "cancelqueue":
        await chat.handle_cancel_queue_button(update, context)
    elif data.startswith("profile:"):
        await profile.profile_callback_router(update, context)
    elif data.startswith("gender:"):
        if not await profile.onboarding_gender_callback(update, context):
            await profile.gender_callback_router(update, context)
    elif data.startswith("endchat:"):
        await chat.end_chat_confirm_callback(update, context)
    elif data.startswith("obprov:"):
        if not await profile.onboarding_province_callback(update, context):
            await profile.edit_province_callback(update, context)
    elif data.startswith("obcity:"):
        await profile.handle_city_callback(update, context)
    elif data.startswith("citypg:"):
        await profile.handle_city_page_callback(update, context)
    elif data.startswith("search:"):
        await search.search_callback_router(update, context)
    elif data.startswith("searchgender:"):
        await search.search_gender_callback_router(update, context)
    elif data.startswith("nearby:"):
        await nearby_callback_router(update, context, data)
    elif data.startswith("coins:history"):
        await coins.show_coin_history(update, context)
    elif data.startswith("report:reason:") or data == "report:cancel":
        await report.report_reason_callback(update, context)
    elif data == "report:start":
        await report.start_report(update, context)
    elif data.startswith("reportsession:"):
        await report.start_report_after_chat(update, context)
    elif data.startswith("profilereport:"):
        await report.handle_profile_report(update, context)
    elif data.startswith("pubblock:"):
        await public_profile.handle_public_block_button(update, context)
    elif data.startswith("chatreq:"):
        await public_profile.handle_chat_request_button(update, context)
    elif data.startswith("chatreqview:"):
        await public_profile.handle_view_chat_request(update, context)
    elif data.startswith("chatreqaccept:"):
        await public_profile.handle_chat_request_accept(update, context)
    elif data.startswith("chatreqreject:"):
        await public_profile.handle_chat_request_reject(update, context)
    elif data.startswith("reactopen:"):
        await public_profile.handle_open_reaction_picker(update, context)
    elif data.startswith("reactsend:"):
        await public_profile.handle_send_reaction(update, context)
    elif data == "reactsettings:open":
        await public_profile.open_reaction_settings(update, context)
    elif data.startswith("reactsettings:"):
        await public_profile.reaction_settings_router(update, context)
    elif data.startswith("settings:"):
        await settings.handle_settings_callback(update, context)
    elif (
        data.startswith("roommenu:")
        or data.startswith("roomgender:")
        or data.startswith("roomcap:")
        or data.startswith("roomjoingender:")
    ):
        await chatroom.room_menu_callback_router(update, context)
    elif data.startswith("roomdelete:"):
        await chatroom.delete_room_confirm_callback(update, context)
    elif data.startswith("roompurge:"):
        await chatroom.purge_history_callback(update, context)
    elif data == "generic:cancel":
        await update.callback_query.answer()
        context.user_data.pop("awaiting_note_target", None)
        context.user_data.pop("awaiting_direct_msg_target", None)
        from handlers.profile import AWAITING_FIELD_KEY
        context.user_data.pop(AWAITING_FIELD_KEY, None)
        try:
            await update.callback_query.delete_message()
        except Exception:
            pass
        await update.callback_query.message.reply_text("❌ لغو شد.")
    elif data == "menu:main":
        await menu.back_to_main_menu(update, context)
    elif data == "menu:profile":
        await profile.show_profile(update, context)
    elif data == "menu:search":
        await search.show_search_menu(update, context)
    elif data == "menu:invite":
        await coins.show_invite_link(update, context)


async def nearby_callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str) -> None:
    action = data.split(":", 1)[1]
    if action == "share_location":
        await nearby.request_location_share(update, context)
    elif action == "show":
        await nearby.show_nearby_users(update, context)
    elif action == "update_location":
        await nearby.request_location_share(update, context)
    elif action == "delete_location":
        await nearby.delete_location(update, context)


async def post_init(application: Application) -> None:
    await init_db()
    logger.info("دیتابیس مقداردهی اولیه شد.")
    from telegram import BotCommand
    await application.bot.set_my_commands([
        BotCommand("start",    "شروع / منوی اصلی"),
        BotCommand("stop",     "پایان چت یا خروج از صف"),
        BotCommand("next",     "چت بعدی — همراه جدید پیدا کن"),
        BotCommand("settings", "تنظیمات شخصی"),
        BotCommand("help",     "راهنما"),
        BotCommand("report",   "گزارش تخلف"),
        BotCommand("silent",   "حالت سکوت پروفایل عمومی"),
        BotCommand("room",     "وضعیتِ اتاقِ چتِ فعلی یا ساختن/عضویت"),
    ])


async def _update_metrics_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        total = 0
        for gender in ("male", "female", "other", "unknown"):
            total += await rc.r.zcard(f"bluechat:waiting_queue:{gender}")
        metrics.waiting_users.set(total)
    except Exception:
        pass


async def _purge_stale_queue_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """هر ۳ دقیقه ورودی‌های منقضیِ صفِ matching رو پاک می‌کنه. چون با
    ریستارتِ ربات صفِ Redis خودش پاک نمی‌شه، این job جلوی zombie entry
    رو می‌گیره."""
    removed = await rc.purge_stale_queue_entries()
    if removed:
        logger.info("پاکسازی صف: %d ورودی منقضی‌شده حذف شد.", removed)


async def _room_join_sweep_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """سیفتی‌نتِ صفِ عضویتِ اتاق: هر ۱ دقیقه دوباره تلاش می‌کنه صفِ
    انتظار رو با اتاق‌های بازِ دارایِ ظرفیتِ خالی پر کنه، برای مواردی
    که trigger بعد از ساختِ اتاق (یا بعداً ترک/اخراج) به هر دلیلی از
    دست رفته باشه."""
    await chatroom.sweep_room_join_queue(context)


async def _active_room_mirror_sync_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """جهتِ برعکسِ آینه‌ی active_room_id: کاربرهایی که Postgres می‌گه
    اتاقِ فعال دارن ولی کلیدِ Redis‌شون (مثلاً به‌خاطرِ کرش بینِ commit
    و ست‌کردنِ آینه) گم شده، اینجا دوباره sync می‌شن."""
    from db import list_users_with_active_room

    for user_id, room_id in await list_users_with_active_room():
        if await rc.get_active_room(user_id) is None:
            await rc.set_active_room(user_id, room_id)


async def _expire_chat_requests_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """هر ۳۰ ثانیه: درخواست‌های چتی که بیشتر از ۲ دقیقه بدونِ پاسخ (نه
    قبول نه رد) موندن رو خودکار لغو می‌کنه، سکه‌ی هزینه‌شده رو به
    درخواست‌کننده برمی‌گردونه، و بهش اطلاع می‌ده."""
    expired = await rc.pop_expired_chat_requests()
    for item in expired:
        requester_id = item["requester_id"]
        target_id = item["target_id"]
        await refund_coins(requester_id, rc.CHAT_COIN_COST, "chat_request_timeout_refund")

        target = await public_profile.async_session_get_user(target_id)
        target_code = target.referral_code if target else "نامشخص"
        try:
            await context.bot.send_message(
                requester_id,
                f"⏳ درخواست چتِ شما به پروفایلِ عمومیِ /user_{target_code} وضعیتش نامعلوم موند "
                f"(بدونِ پاسخ) و {rc.CHAT_COIN_COST} سکه به حسابتون برگشت.",
            )
        except TelegramError:
            pass


async def _purge_old_messages_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """هر ۲۴ ساعت اجرا می‌شه: پیام‌های متنیِ قدیمی‌تر از ۲۴ ساعت رو از
    Postgres پاک می‌کنه (مستقل از پاک‌کردنِ دستیِ دوطرفه). این کار
    حریمِ خصوصی رو تضمین می‌کنه و همچنین یعنی گزارش‌ها باید ظرف ۲۴
    ساعت از پایانِ گفتگو ثبت بشن تا قابلِ بررسی باشن."""
    from db import purge_old_chat_messages

    deleted_count = await purge_old_chat_messages(older_than_hours=24)
    if deleted_count:
        logger.info("پاک‌سازیِ خودکار: %d پیامِ قدیمی‌تر از ۲۴ ساعت حذف شد.", deleted_count)


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("متغیر محیطی BOT_TOKEN تنظیم نشده.")

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("stop", chat.stop_chat))
    app.add_handler(CommandHandler("next", chat.next_chat))
    app.add_handler(CommandHandler("help", menu.show_help))
    app.add_handler(CommandHandler("report", report.start_report))
    app.add_handler(CommandHandler("cancel", profile.cancel_profile_edit))

    app.add_handler(CommandHandler("silent", public_profile.toggle_silent_mode))
    app.add_handler(CommandHandler("settings", settings.show_settings))
    app.add_handler(CommandHandler("room", chatroom.show_room_menu))
    app.add_handler(MessageHandler(filters.Regex(r"^/u(?:ser)?_\S+"), user_profile_command))

    app.add_handler(MessageHandler(filters.LOCATION, nearby.handle_location_message))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))
    app.add_handler(
        MessageHandler(
            (filters.PHOTO | filters.VOICE | filters.AUDIO | filters.VIDEO | filters.Sticker.ALL
             | filters.VIDEO_NOTE | filters.Document.ALL | filters.ANIMATION)
            & ~filters.COMMAND,
            media_router,
        )
    )
    app.add_handler(MessageHandler(filters.UpdateType.EDITED_MESSAGE & filters.TEXT, edited_message_router))
    app.add_handler(MessageReactionHandler(chat.relay_reaction))
    app.add_handler(CallbackQueryHandler(callback_router))

    app.job_queue.run_repeating(_purge_old_messages_job, interval=60 * 60 * 24, first=60 * 5)
    app.job_queue.run_repeating(_purge_stale_queue_job, interval=60 * 3, first=30)
    app.job_queue.run_repeating(_room_join_sweep_job, interval=60, first=40)
    app.job_queue.run_repeating(_active_room_mirror_sync_job, interval=60 * 2, first=50)
    app.job_queue.run_repeating(_expire_chat_requests_job, interval=30, first=30)

    app.job_queue.run_repeating(_update_metrics_job, interval=15, first=10)

    metrics.start_metrics_server()
    logger.info("ربات در حال اجراست...")
    if WEBHOOK_URL:
        app.run_webhook(
            listen="0.0.0.0",
            port=WEBHOOK_PORT,
            webhook_url=WEBHOOK_URL,
            url_path=WEBHOOK_URL.split("/", 3)[-1],
            secret_token=WEBHOOK_SECRET or None,
            allowed_updates=Update.ALL_TYPES,
        )
    else:
        app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
