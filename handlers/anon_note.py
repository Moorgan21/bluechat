"""
پیام‌های ناشناسِ نوتیفی — لینک ناشناس مستقیم
------------------------------------------------
برخلاف چت اصلی (matching دوطرفه با ChatSession)، وقتی کسی از طریق
لینک ناشناس اختصاصی (`?start=direct_<code>`) به صاحب لینک پیام می‌ده،
هیچ سشن یا جفت‌شدنِ دائمی ایجاد نمی‌شه.

جریان کار (نسخه‌ی صف‌دار + بلاک):
    1. فرستنده از طریق لینک وارد ربات می‌شه و پیامش رو می‌نویسه. اگه
       صاحب لینک قبلاً این فرستنده رو بلاک کرده باشه، پیام اصلاً وارد
       صف نمی‌شه و فرستنده متوجه بلاک‌شدنش نمی‌شه (بی‌سروصدا رد می‌شه).
    2. پیام مستقیم برای صاحب لینک ارسال نمی‌شه؛ به‌جاش توی یه صفِ
       Redis («pending notes») قرار می‌گیره.
    3. اگه این اولین پیامِ تحویل‌نشده باشه، صاحب لینک یه نوتیفِ کوتاه
       می‌گیره: «📬 یه پیام ناشناس جدید داری! جهت دریافت کلیک کن 👇
       /newmsg» — بدون محتوای واقعی پیام.
    4. وقتی صاحب لینک /newmsg رو بزنه، تمام پیام‌های صفِ اون لحظه
       (ممکنه از چند فرستنده‌ی مختلف باشن) با copy_message برای صاحب
       لینک تحویل داده می‌شن، هر کدوم با دو دکمه زیرش:
       «↩️ پاسخ دادن» و «🚫 بلاک کردن فرستنده».
    5. به هر فرستنده‌ای که پیامش تحویل داده شد، اطلاع داده می‌شه:
       «✅ پیامت رو دید.»
    6. صاحب لینک روی «پاسخ دادن» می‌زنه → می‌نویسه → پاسخ مستقیم برای
       همون فرستنده ارسال می‌شه، با دکمه‌های پاسخ/بلاکِ جدید زیرش تا
       رفت‌وبرگشت ادامه پیدا کنه.
    7. صاحب لینک روی «بلاک کردن فرستنده» می‌زنه → از این به بعد، هر
       پیامی که همون فرستنده از طریق لینکِ این صاحب بفرسته، بی‌سروصدا
       رد می‌شه (بدون اطلاع به فرستنده که بلاک شده).
"""

import logging

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

import redis_client as rc
from db import is_sender_blocked, block_sender
from keyboards import note_reply_keyboard

logger = logging.getLogger(__name__)


async def send_anon_note(
    owner_id: int, sender_id: int, update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """پیامِ دایرکت رو ثبت می‌کنه و یه نوتیفِ «مشاهده» برای صاحب لینک
    می‌فرسته. اگه فرستنده بلاک شده باشه، بی‌سروصدا رد می‌شه."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    if await is_sender_blocked(owner_id, sender_id):
        await update.message.reply_text("✅ پیامت ارسال شد.")
        return

    msg = update.message
    note_id = await rc.create_note(sender_id)
    await rc.store_note_message(note_id, msg.chat_id, msg.message_id)

    await update.message.reply_text("✅ پیامت ارسال شد.")

    try:
        await context.bot.send_message(
            owner_id,
            "📩 یه پیام ناشناس دارین",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("👀 مشاهده", callback_data=f"noterview:{note_id}")]]
            ),
        )
    except TelegramError:
        logger.warning("امکان ارسال نوتیف پیام ناشناس به owner_id=%s وجود نداشت.", owner_id)


async def send_direct_msg(
    owner_id: int, sender_id: int, update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """پیام دایرکت — شناسه‌ی عمومی فرستنده (/user_<code>) به مقصد نشون
    داده می‌شه. در صورت بلاک‌بودن، بی‌سروصدا رد می‌شه."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    from db import User, async_session

    if await is_sender_blocked(owner_id, sender_id):
        await update.message.reply_text("✅ پیامت ارسال شد.")
        return

    async with async_session() as session:
        sender = await session.get(User, sender_id)
        sender_code = sender.referral_code if sender else "نامشخص"

    msg = update.message
    note_id = await rc.create_note(sender_id)
    await rc.store_note_message(note_id, msg.chat_id, msg.message_id)

    await update.message.reply_text("✅ پیامت ارسال شد.")

    try:
        await context.bot.send_message(
            owner_id,
            f"📩 پیام دایرکت از /user_{sender_code}",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("👀 مشاهده", callback_data=f"noterview:{note_id}")]]
            ),
        )
    except TelegramError:
        logger.warning("امکان ارسال نوتیف پیام دایرکت به owner_id=%s وجود نداشت.", owner_id)


async def handle_view_note(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """هندلر دکمه‌ی «👀 مشاهده» زیر نوتیف پیام دایرکت — پیام اصلی رو
    کپی می‌کنه، به فرستنده خبر سین می‌ده، و دکمه‌های پاسخ/بلاک رو
    نمایش می‌ده."""
    query = update.callback_query
    await query.answer()

    note_id = query.data.split(":", 1)[1]
    owner_id = query.from_user.id

    sender_id = await rc.get_note_sender(note_id)
    if sender_id is None:
        await query.edit_message_text("⚠️ این پیام دیگه در دسترس نیست (منقضی شده).")
        return

    note_msg = await rc.get_note_message(note_id)
    if note_msg is None:
        await query.edit_message_text("⚠️ این پیام دیگه در دسترس نیست.")
        return

    source_chat_id, source_message_id = note_msg

    try:
        await query.edit_message_text("📩 پیام:")
    except TelegramError:
        pass

    try:
        await context.bot.copy_message(
            chat_id=owner_id,
            from_chat_id=source_chat_id,
            message_id=source_message_id,
            reply_markup=note_reply_keyboard(note_id, sender_id),
        )
    except TelegramError:
        logger.exception("خطا در کپی پیام دایرکت به owner_id=%s", owner_id)
        await query.message.reply_text("⚠️ متاسفانه پیام اصلی دیگه در دسترس نیست.")
        return

    try:
        await context.bot.send_message(sender_id, "✅ صاحب لینک پیامت رو دید.")
    except TelegramError:
        pass


async def deliver_pending_notes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """هندلر دستور /newmsg: تمام پیام‌های در صفِ صاحب لینک رو تحویل
    می‌ده و به فرستنده‌های هرکدوم اطلاع می‌ده که پیامشون دیده شد.
    پیام‌های فرستنده‌های بلاک‌شده (اگه بعد از ارسال و قبل از /newmsg
    بلاک شده باشن) از صف حذف می‌شن و تحویل داده نمی‌شن."""
    owner_id = update.effective_user.id
    pending = await rc.pop_all_pending_notes(owner_id)
    await rc.clear_unseen_notified(owner_id)

    if not pending:
        await update.message.reply_text("پیام ناشناسِ در انتظاری نداری.")
        return

    deliverable = []
    for item in pending:
        if await is_sender_blocked(owner_id, item["sender_id"]):
            continue
        deliverable.append(item)

    if not deliverable:
        await update.message.reply_text("پیام ناشناسِ در انتظاری نداری.")
        return

    await update.message.reply_text(f"📨 {len(deliverable)} پیام ناشناس جدید داری:")

    delivered_sender_ids: set[int] = set()
    for item in deliverable:
        sender_id = item["sender_id"]
        source_chat_id = item["chat_id"]
        source_message_id = item["message_id"]

        note_id = await rc.create_note(sender_id)
        keyboard = note_reply_keyboard(note_id, sender_id)

        try:
            await context.bot.copy_message(
                chat_id=owner_id,
                from_chat_id=source_chat_id,
                message_id=source_message_id,
                reply_markup=keyboard,
            )
            delivered_sender_ids.add(sender_id)
        except TelegramError:
            logger.exception(
                "خطا در تحویل پیامِ صف‌شده به owner_id=%s از sender_id=%s", owner_id, sender_id
            )

    for sender_id in delivered_sender_ids:
        try:
            await context.bot.send_message(sender_id, "✅ صاحب لینک پیامت رو دید.")
        except TelegramError:
            logger.warning("امکان اطلاع‌رسانیِ دیده‌شدن پیام به sender_id=%s وجود نداشت.", sender_id)


async def handle_direct_msg_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """هندلر دکمه‌ی «📩 پیام دایرکت» زیر پروفایل عمومی — کاربر رو وارد
    state نوشتن پیام دایرکت می‌کنه. برخلاف پیام ناشناس، شناسه‌ی عمومی
    فرستنده به مقصد نشون داده می‌شه."""
    query = update.callback_query
    await query.answer()

    target_id = int(query.data.split(":", 1)[1])
    sender_id = query.from_user.id

    if target_id == sender_id:
        await query.message.reply_text("نمی‌تونی برای خودت پیام دایرکت بفرستی 🙂")
        return

    partner_id = await rc.get_partner(sender_id)
    if partner_id == target_id:
        await query.message.reply_text(
            "الان توی چت فعال با این شخص هستی! از همون چت پیامت رو بفرست."
        )
        return

    context.user_data["awaiting_direct_msg_target"] = target_id
    from keyboards import cancel_keyboard
    await query.message.reply_text(
        "✍️ پیامت رو بنویس (متن، عکس، ویس و ...) — شناسه‌ی عمومیت برای مقصد نمایش داده می‌شه:",
        reply_markup=cancel_keyboard(),
    )


async def handle_reply_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """هندلر دکمه‌ی «↩️ پاسخ دادن» زیر یک پیامِ نوتیفی."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    query = update.callback_query
    await query.answer()

    note_id = query.data.split(":", 1)[1]
    sender_id = await rc.get_note_sender(note_id)

    if sender_id is None:
        await query.message.reply_text("⚠️ این پیام دیگه معتبر نیست (منقضی شده).")
        return

    owner_id = query.from_user.id
    await rc.set_awaiting_reply(owner_id, note_id)

    cancel_keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("❌ لغو پاسخ", callback_data="notereplycancel")]]
    )
    await query.message.reply_text(
        "✍️ پاسخت رو بنویس و بفرست (ناشناس برای فرستنده ارسال می‌شه):",
        reply_markup=cancel_keyboard,
    )


async def handle_cancel_reply_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """هندلر دکمه‌ی «❌ لغو پاسخ» — اگه صاحب لینک وسطِ نوشتنِ پاسخ پشیمون
    بشه، این state رو پاک می‌کنه که پیامِ بعدیش دیگه به‌عنوان پاسخ
    ارسال نشه."""
    query = update.callback_query
    await query.answer()

    owner_id = query.from_user.id
    await rc.clear_awaiting_reply(owner_id)
    await query.edit_message_text("❌ پاسخ لغو شد.")


async def handle_block_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """هندلر دکمه‌ی «🚫 بلاک کردن فرستنده» زیر یک پیامِ نوتیفی. فرستنده
    برای همیشه (تا وقتی صاحب لینک آنبلاکش نکنه) نمی‌تونه از طریق این
    لینک پیام بفرسته."""
    query = update.callback_query
    await query.answer()

    sender_id = int(query.data.split(":", 1)[1])
    owner_id = query.from_user.id

    await block_sender(owner_id, sender_id)
    await query.message.reply_text(
        "🚫 این فرستنده بلاک شد و دیگه نمی‌تونه از طریق لینک ناشناست پیام بفرسته."
    )


async def handle_pending_reply_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """اگه صاحب لینک منتظر نوشتنِ متنِ پاسخ به یک پیام نوتیفی بود، این
    پیامش رو مستقیم (بدون صف‌کردن) به فرستنده‌ی اصلی relay می‌کنه —
    چون این‌بار طرف مقابل منتظر جواب یک پیام مشخصه، نه در حال باز کردن
    پیام‌های جدید. خروجی True یعنی مصرف شد."""
    owner_id = update.effective_user.id
    note_id = await rc.pop_awaiting_reply(owner_id)
    if note_id is None:
        return False

    sender_id = await rc.get_note_sender(note_id)
    if sender_id is None:
        await update.message.reply_text("⚠️ این پیام دیگه معتبر نیست (فرستنده در دسترس نیست).")
        return True

    msg = update.message
    reply_note_id = await rc.create_note(owner_id)
    # اینجا کیبورد فقط دکمه‌ی «پاسخ دادن» داره (بدون دکمه‌ی بلاک)، چون
    # این پیام برای فرستنده‌ی اصلی ارسال می‌شه و بلاک‌کردن فقط قابلیتِ
    # صاحب لینکه، نه فرستنده‌ها.
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("↩️ پاسخ دادن", callback_data=f"noterep:{reply_note_id}")]]
    )

    try:
        await context.bot.copy_message(
            chat_id=sender_id,
            from_chat_id=msg.chat_id,
            message_id=msg.message_id,
            reply_markup=keyboard,
        )
        await update.message.reply_text("✅ پاسخت ارسال شد.")
    except TelegramError:
        logger.exception("خطا در ارسال پاسخ نوتیفی به sender_id=%s", sender_id)
        await update.message.reply_text("⚠️ ارسال پاسخ با خطا مواجه شد. شاید طرف مقابل ربات رو بلاک کرده.")

    return True
