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
relay: پیام‌ها، ویرایش‌ها و ریکشن‌ها رو بینِ دو طرفِ یک چتِ فعال منتقل
می‌کنه، به‌همراه فرمانِ حذفِ دوطرفه‌ی پیام.
"""

import logging

from telegram import ReplyParameters, Update
from telegram.constants import ChatAction
from telegram.error import TelegramError
from telegram.ext import ContextTypes

import redis_client as rc
import metrics
from db import store_chat_message
from keyboards import main_reply_keyboard

logger = logging.getLogger(__name__)


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
        elif msg.audio:
            sent_msg = await context.bot.send_audio(partner_id, msg.audio.file_id, caption=msg.caption, reply_parameters=reply_params, protect_content=secure)
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
                    "audio" if msg.audio else
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
