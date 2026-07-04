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

"""پیام‌های ناشناس از طریق لینکِ ناشناسِ مستقیم."""

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
    می‌فرسته. اگه فرستنده بلاک شده باشه، صراحتاً بهش گفته می‌شه (نه
    یه موفقیتِ ساختگی) که دیگه نمی‌تونه از طریقِ این لینک پیام بفرسته."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    if await is_sender_blocked(owner_id, sender_id):
        await update.message.reply_text("🚫 صاحبِ این لینک بلاکت کرده و نمی‌تونی از طریقِ این لینک پیام ناشناس بفرستی.")
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
    """پیام دایرکت؛ شناسه‌ی عمومیِ فرستنده (/user_<code>) به مقصد نشون
    داده می‌شه. هر پیام rc.DIRECT_MSG_COIN_COST سکه هزینه داره و همون
    لحظه کسر می‌شه، چه مقصد ببینتش چه نه (برخلافِ پیامِ ناشناس که
    رایگانه). اگه فرستنده بلاک شده باشه، صراحتاً بهش گفته می‌شه (مثلِ
    send_anon_note) و سکه‌ای کسر نمی‌شه."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    from db import User, async_session, deduct_coins

    if await is_sender_blocked(owner_id, sender_id):
        await update.message.reply_text("🚫 این کاربر بلاکت کرده و امکانِ ارسالِ پیامِ دایرکت بهش نداری.")
        return

    if await deduct_coins(sender_id, rc.DIRECT_MSG_COIN_COST, "direct_msg_cost") is None:
        await update.message.reply_text(
            f"🪙 سکه‌ی کافی نداری! ارسالِ پیامِ دایرکت {rc.DIRECT_MSG_COIN_COST} سکه هزینه داره."
        )
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
    """هندلر دکمه‌ی «👀 مشاهده» زیر نوتیف پیام دایرکت؛ پیام اصلی رو کپی
    می‌کنه، به فرستنده خبرِ سین می‌ده و دکمه‌های پاسخ/بلاک رو نشون می‌ده."""
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




async def handle_direct_msg_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """هندلر دکمه‌ی «📩 پیام دایرکت» زیر پروفایل عمومی؛ کاربر رو وارد state
    نوشتن پیام دایرکت می‌کنه. برخلاف پیام ناشناس، شناسه‌ی عمومیِ
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

    if await is_sender_blocked(target_id, sender_id):
        await query.message.reply_text("🚫 این کاربر بلاکت کرده و امکانِ ارسالِ پیامِ دایرکت بهش نداری.")
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
    """هندلر دکمه‌ی «❌ لغو پاسخ»؛ اگه صاحب لینک وسطِ نوشتنِ پاسخ پشیمون شه
    این state رو پاک می‌کنه که پیامِ بعدیش دیگه پاسخ حساب نشه."""
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
    """اگه صاحب لینک منتظر نوشتنِ پاسخِ یه پیام نوتیفی بود، پیامش رو
    مستقیم به فرستنده‌ی اصلی relay می‌کنه، چون این‌بار طرف مقابل منتظرِ
    جوابِ یه پیام مشخصه نه در حالِ باز کردنِ پیام‌های جدید. خروجی True
    یعنی مصرف شد."""
    owner_id = update.effective_user.id
    note_id = await rc.pop_awaiting_reply(owner_id)
    if note_id is None:
        return False

    sender_id = await rc.get_note_sender(note_id)
    if sender_id is None:
        await update.message.reply_text("⚠️ این پیام دیگه معتبر نیست (فرستنده در دسترس نیست).")
        return True

    # این همون گیت‌ِ بلاکیه که send_anon_note/send_direct_msg برای پیامِ
    # اول اعمال می‌کنن؛ بدونِ این چک، طرفی که بلاک شده می‌تونست از طریقِ
    # زنجیره‌ی پاسخ‌ها (noterep) دوباره برای صاحبِ لینک پیام بفرسته.
    if await is_sender_blocked(sender_id, owner_id):
        await update.message.reply_text("🚫 صاحبِ این لینک بلاکت کرده و نمی‌تونی از طریقِ این لینک پیام بفرستی.")
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
