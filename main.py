"""
ربات چت ناشناس ملوگپ‌طور — راه‌انداز اصلی
--------------------------------------------
نیازمندی‌ها:
    pip install python-telegram-bot==21.* sqlalchemy[asyncio] asyncpg redis[hiredis]

متغیرهای محیطی لازم:
    export BOT_TOKEN="توکن ربات از BotFather"
    export BOT_USERNAME="username_ربات (بدون @)"
    export DATABASE_URL="postgresql+asyncpg://user:pass@host:5432/melogap"
    export REDIS_URL="redis://localhost:6379/0"

اجرا:
    python main.py

ساختار پروژه:
    db.py              - مدل‌ها و اتصال Postgres
    redis_client.py     - صف matching و state لحظه‌ای در Redis
    keyboards.py        - همه‌ی دکمه‌های شیشه‌ای و کیبورد پایین
    handlers/chat.py    - matching، relay پیام/ریکشن، پاک‌کردن تاریخچه
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
from db import init_db, get_or_create_user, async_session
from handlers import anon_note, chat, coins, menu, nearby, profile, public_profile, report, search, settings

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")


# ---------------------------------------------------------------------------
# /start با پشتیبانی از deep-link های ref_<code> و direct_<code>
# ---------------------------------------------------------------------------
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
    """کاربر از طریق لینک ناشناسِ مستقیم اومده. برخلاف matching عادی،
    اینجا هیچ ChatSession/جفت‌شدنِ دائمی ساخته نمی‌شه — فقط کاربر رو
    وارد state «در حال نوشتن پیام ناشناس برای صاحب لینک» می‌کنیم؛ پیام
    بعدیش با handlers.anon_note.send_anon_note ارسال می‌شه."""
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


# ---------------------------------------------------------------------------
# روتر پیام‌های متنی/مدیا: اول چک می‌کنه ورودیِ در-انتظار (پروفایل/سرچ) هست یا نه،
# وگرنه به relay چت ناشناس می‌سپاره.
# ---------------------------------------------------------------------------
REPLY_KEYBOARD_ROUTES = {
    "💬 وصل کن به یه ناشناس!": chat.start_chat,
    "💬 جستجوی کاربران 🔮": search.show_search_menu,
    "📍 افراد نزدیک 🛰": nearby.show_nearby_menu,
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


async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text or ""
    user_id = update.effective_user.id
    await rc.update_last_seen(user_id)

    # ۱) اولویت اول: اگه کاربر وسط جریان onboarding (تکمیل اجباری پروفایل) هست
    if await profile.handle_onboarding_text_input(update, context):
        return

    # ۲) اگه کاربر منتظرِ نوشتنِ یه تگِ جدیدِ واکنشه
    if await public_profile.handle_new_tag_input(update, context):
        return

    # ۳) پیام دایرکت (شناسه فرستنده به مقصد نشون داده می‌شه)
    direct_target_id = context.user_data.pop("awaiting_direct_msg_target", None)
    if direct_target_id is not None:
        try:
            await anon_note.send_direct_msg(direct_target_id, user_id, update, context)
        except Exception:
            context.user_data["awaiting_direct_msg_target"] = direct_target_id
            raise
        return

    # ۴) پیام ناشناس از طریق لینک مستقیم (بدون ساختن هیچ چت بازی)
    note_target_id = context.user_data.pop("awaiting_note_target", None)
    if note_target_id is not None:
        try:
            await anon_note.send_anon_note(note_target_id, user_id, update, context)
        except Exception:
            context.user_data["awaiting_note_target"] = note_target_id
            raise
        return

    # ۴) اگه کاربر (صاحب یه لینک ناشناس) روی دکمه‌ی «پاسخ دادن» زده و
    # منتظر نوشتنِ متنِ پاسخه.
    if await anon_note.handle_pending_reply_input(update, context):
        return

    # ۵) اگه کاربر الان توی یه گفتگوی فعاله، فقط دو دکمه‌ی مخصوص چت و
    # relay پیام مجازن؛ بقیه‌ی دکمه‌های منوی اصلی نادیده گرفته می‌شن
    # (چون در حالت عادی اصلاً دیده نمی‌شن، ولی اگه از قبل روی صفحه‌شون
    # مونده باشن یا کاربر تایپ‌شون کنه، نباید اتفاقی بیفته).
    in_active_chat = await rc.get_partner(user_id) is not None
    if in_active_chat:
        if text in IN_CHAT_KEYBOARD_ROUTES:
            await IN_CHAT_KEYBOARD_ROUTES[text](update, context)
            return
        await chat.relay_message(update, context)
        return

    # ۶) کاربر توی گفتگو نیست: دکمه‌های منوی اصلی فعالن
    if text in REPLY_KEYBOARD_ROUTES:
        await REPLY_KEYBOARD_ROUTES[text](update, context)
        return

    if await profile.handle_profile_text_input(update, context):
        return
    if await search.handle_search_age_input(update, context):
        return

    await chat.relay_message(update, context)


async def user_profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """هندلرِ دستورِ دینامیکِ /user_<code> — چون تلگرام دستوراتِ ثابت
    می‌خواد، این با یه MessageHandler و regex گرفته می‌شه، نه
    CommandHandler معمولی."""
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

    await chat.relay_message(update, context)


# ---------------------------------------------------------------------------
# روتر callback_query بر اساس پیشوند callback_data
# ---------------------------------------------------------------------------
async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = update.callback_query.data or ""
    if update.effective_user:
        await rc.update_last_seen(update.effective_user.id)

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


async def _purge_stale_queue_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """هر ۳ دقیقه: ورودی‌های منقضی‌شده‌ی صف matching رو پاک می‌کنه.
    در صورت ریستارت ربات، صف Redis پاک نمی‌شه — این job از zombie entry جلوگیری می‌کنه."""
    removed = await rc.purge_stale_queue_entries()
    if removed:
        logger.info("پاکسازی صف: %d ورودی منقضی‌شده حذف شد.", removed)


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
    app.add_handler(CommandHandler("newmsg", anon_note.deliver_pending_notes))
    app.add_handler(CommandHandler("silent", public_profile.toggle_silent_mode))
    app.add_handler(CommandHandler("settings", settings.show_settings))
    app.add_handler(MessageHandler(filters.Regex(r"^/u(?:ser)?_\S+"), user_profile_command))

    app.add_handler(MessageHandler(filters.LOCATION, nearby.handle_location_message))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))
    app.add_handler(
        MessageHandler(
            (filters.PHOTO | filters.VOICE | filters.VIDEO | filters.Sticker.ALL
             | filters.VIDEO_NOTE | filters.Document.ALL | filters.ANIMATION)
            & ~filters.COMMAND,
            media_router,
        )
    )
    app.add_handler(MessageHandler(filters.UpdateType.EDITED_MESSAGE & filters.TEXT, chat.relay_edit))
    app.add_handler(MessageReactionHandler(chat.relay_reaction))
    app.add_handler(CallbackQueryHandler(callback_router))

    app.job_queue.run_repeating(_purge_old_messages_job, interval=60 * 60 * 24, first=60 * 5)
    app.job_queue.run_repeating(_purge_stale_queue_job, interval=60 * 3, first=30)

    logger.info("ربات در حال اجراست...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
