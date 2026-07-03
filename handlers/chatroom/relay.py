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

"""رله‌ی پیام‌ها داخلِ اتاقِ چت: فن‌اوتِ یک‌به‌چند، ویرایش/حذفِ پیامِ
خود، و چتِ امن. برخلافِ handlers/chat/relay.py (که یه نگاشتِ زوجیِ
ساده‌ست)، اینجا هر پیام باید برای چند نفر فرستاده بشه، پس نگاشتش دوتا
کلید داره (نگاه کن به redis_client.py: KEY_ROOM_MSG_RECIPIENTS و
KEY_ROOM_MSG_ORIGIN).

قبل از هر رله، وضعیتِ Redis (KEY_USER_ACTIVE_ROOM) با Postgres چک
می‌شه؛ اگه تناقض داشت (اتاق حذف شده یا کاربر دیگه عضو نیست)، خودش رو
تصحیح می‌کنه: کلیدِ Redis رو پاک می‌کنه و کاربر رو به منو برمی‌گردونه.
"""

import logging

from telegram import ReplyParameters, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

import metrics
import redis_client as rc
from db import (
    RoomStatus,
    get_chat_room,
    get_display_name,
    get_display_name_and_referral_code,
    get_room_member_ids,
)
from keyboards import in_room_reply_keyboard, main_reply_keyboard

logger = logging.getLogger(__name__)


async def broadcast_system_message(
    room_id: int,
    message: str,
    context: ContextTypes.DEFAULT_TYPE,
    member_ids: list[int] | None = None,
    reply_markup=None,
) -> None:
    """پیامِ سیستمی (ترک، اخراج، بستن/بازکردنِ اتاق و غیره) رو با
    پیشوندِ ثابتِ ℹ️ به همه‌ی اعضای *فعلیِ* اتاق می‌فرسته؛ مستقل از
    تابعِ relayِ معمولی، تا هیچ‌وقت شبیهِ پیامِ جعل‌شده‌ی یه کاربر با
    فرمتِ «نام: متن» به نظر نرسه.

    member_ids رو می‌شه از بیرون داد (مثلاً لیستِ اعضایی که هنوز DELETE
    نشدن، برای پیامِ اطلاع به یه عضوِ در-حالِ-جداشدن)؛ اگه ندی، از
    Postgres خونده می‌شه (یعنی وضعیتِ *بعد* از تغییر، چون معمولاً این
    تابع بعد از commitِ همون تغییر صدا زده می‌شه).

    reply_markup اختیاریه و به همه‌ی گیرنده‌ها یکسان اعمال می‌شه؛ مثلاً
    وقتی اتاق بسته می‌شه، این جاییه که کیبوردِ اعضا رو از
    in_room_reply_keyboard به main_reply_keyboard تغییر می‌دیم (چون
    دیگه نمی‌تونن پیام بدن، ولی نباید بلاتکلیف بمونن)."""
    if member_ids is None:
        member_ids = await get_room_member_ids(room_id)
    text = f"ℹ️ {message}"
    for member_id in member_ids:
        try:
            await context.bot.send_message(member_id, text, reply_markup=reply_markup)
        except TelegramError:
            pass


async def relay_room_message(update: Update, context: ContextTypes.DEFAULT_TYPE, room_id: int) -> None:
    user_id = update.effective_user.id
    room = await get_chat_room(room_id)
    member_ids = await get_room_member_ids(room_id) if room is not None else []

    if room is None or room.status == RoomStatus.deleted or user_id not in member_ids:
        # Redisِ می‌گفت توی اتاقی، Postgres تاییدش نمی‌کنه؛ خودتصحیحی
        await rc.clear_active_room(user_id)
        await update.effective_message.reply_text(
            "این اتاق دیگه فعال نیست.", reply_markup=main_reply_keyboard()
        )
        return

    if room.status == RoomStatus.closed:
        # اگه به اینجا رسیده، یعنی suppress نشده (یا owner، یا عمداً
        # با /room دوباره وارد شده)، پس هندلرِ اتاق براش فعاله؛ کیبورد
        # همون کیبوردِ داخلِ اتاق می‌مونه، فقط اجازه‌ی relay نداره.
        keyboard = in_room_reply_keyboard(
            is_owner=user_id == room.owner_id,
            room_open=False,
        )
        await update.effective_message.reply_text(
            "🔒 این اتاق فعلاً بسته‌ست و پیام رد و بدل نمی‌شه.", reply_markup=keyboard
        )
        return

    msg = update.message

    if msg.text and msg.reply_to_message:
        command = msg.text.strip().lower()
        if command in ("حذف", "del"):
            await _handle_delete_command(update, context, room)
            return
        if command == "اخراج" and user_id == room.owner_id:
            await _handle_kick_command(update, context, room)
            return

    await _relay_new_message(update, context, room, member_ids)


async def relay_room_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """وقتی کاربر پیامِ متنیِ خودش رو داخلِ اتاق ویرایش می‌کنه."""
    msg = update.edited_message
    if msg is None or not msg.text:
        return

    user_id = msg.from_user.id
    room_id = await rc.get_active_room(user_id)
    if room_id is None:
        return

    room = await get_chat_room(room_id)
    if room is None or room.status == RoomStatus.deleted:
        await rc.clear_active_room(user_id)
        return

    origin = await rc.get_room_msg_origin(room_id, user_id, msg.message_id)
    if origin != (user_id, msg.message_id):
        return  # فقط پیامِ اصلیِ خودش قابلِ ویرایشه

    label = await _build_sender_label(room, user_id)
    from datetime import datetime, timedelta, timezone

    edit_time = datetime.now(tz=timezone(timedelta(hours=3, minutes=30)))
    time_str = edit_time.strftime("%H:%M")
    secure = await rc.is_secure_chat(user_id)

    recipients_map = await rc.get_room_msg_recipients(room_id, user_id, msg.message_id)
    for recipient_id, local_ids in recipients_map.items():
        if recipient_id == user_id:
            continue
        for mid in local_ids:
            try:
                await context.bot.edit_message_text(
                    chat_id=recipient_id,
                    message_id=mid,
                    text=f"{label}: {msg.text}\n\n✏️ ویرایش شده · {time_str}",
                    protect_content=secure,
                )
            except TelegramError:
                pass


async def toggle_secure_chat_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """چتِ امنِ اتاق. پرچمِ زیرین (KEY_SECURE_CHAT) با ۱به۱ مشترکه چون
    صرفاً per-userه، ولی این تابع عمداً از تابعِ ۱به۱
    (chat/extras.py:toggle_secure_chat_button) جداست: اون یکی بعد از
    toggle مستقیم به partner_id نوتیف می‌فرسته (فرضِ «دقیقاً یه گیرنده»)
    که توی اتاق درست نیست؛ اینجا فقط پرچم رو عوض می‌کنه و به کسِ دیگه‌ای
    خبر نمی‌ده."""
    user_id = update.effective_user.id
    room_id = await rc.get_active_room(user_id)
    is_owner = False
    room_open = True
    if room_id is not None:
        room = await get_chat_room(room_id)
        if room is not None:
            is_owner = room.owner_id == user_id
            room_open = room.status == RoomStatus.open

    new_state = await rc.toggle_secure_chat(user_id)
    await update.message.reply_text(
        "🔒 چتِ امن فعال شد." if new_state else "🔓 چتِ امن غیرفعال شد.",
        reply_markup=in_room_reply_keyboard(secure=new_state, is_owner=is_owner, room_open=room_open),
    )


async def _relay_new_message(update: Update, context: ContextTypes.DEFAULT_TYPE, room, member_ids: list[int]) -> None:
    user_id = update.effective_user.id
    msg = update.message
    recipients = [m for m in member_ids if m != user_id]

    label = await _build_sender_label(room, user_id)
    secure = await rc.is_secure_chat(user_id)

    reply_origin = None
    if msg.reply_to_message:
        reply_origin = await rc.get_room_msg_origin(room.id, user_id, msg.reply_to_message.message_id)

    delivered = False
    for recipient_id in recipients:
        reply_params = None
        if reply_origin is not None:
            origin_sender, origin_msg = reply_origin
            recip_map = await rc.get_room_msg_recipients(room.id, origin_sender, origin_msg)
            local_ids = recip_map.get(recipient_id)
            if local_ids:
                reply_params = ReplyParameters(message_id=local_ids[-1])

        try:
            sent_ids = await _send_one(context.bot, recipient_id, msg, label, reply_params, secure)
        except TelegramError:
            logger.warning("رله‌ی پیامِ اتاق به recipient_id=%s شکست خورد (شاید بلاک کرده).", recipient_id)
            continue

        delivered = True
        await rc.set_room_msg_recipient_ids(room.id, user_id, msg.message_id, recipient_id, sent_ids)
        for mid in sent_ids:
            await rc.set_room_msg_origin(room.id, recipient_id, mid, user_id, msg.message_id)
            await rc.record_room_message(room.id, recipient_id, mid)

    # فرستنده هم به origin خودش map می‌شه، هم برای تشخیصِ «این پیامِ
    # خودمه؟» هم برای اینکه دستورِ حذف بتونه نسخه‌ی خودشو هم پاک کنه.
    await rc.set_room_msg_recipient_ids(room.id, user_id, msg.message_id, user_id, [msg.message_id])
    await rc.set_room_msg_origin(room.id, user_id, msg.message_id, user_id, msg.message_id)
    await rc.record_room_message(room.id, user_id, msg.message_id)

    if delivered:
        metrics.room_messages.inc()


async def _handle_delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE, room) -> None:
    """پیام‌رسانِ حذف: کاربرِ عادی فقط پیامِ خودشو، owner پیامِ هرکسی رو
    می‌تونه پاک کنه (چون origin رو با شناسه‌ی خودِ owner resolve
    می‌کنیم، نه با شرطِ سخت‌گیرانه‌ی «باید مالِ خودم باشه»)."""
    user_id = update.effective_user.id
    msg = update.message
    replied_id = msg.reply_to_message.message_id

    origin = await rc.get_room_msg_origin(room.id, user_id, replied_id)
    if origin is None:
        return
    origin_sender_id, origin_msg_id = origin

    is_own = origin_sender_id == user_id
    is_owner = user_id == room.owner_id
    if not is_own and not is_owner:
        await update.effective_message.reply_text("فقط می‌تونی پیام‌های خودت رو حذف کنی.")
        try:
            await msg.delete()
        except TelegramError:
            pass
        return

    recipients_map = await rc.get_room_msg_recipients(room.id, origin_sender_id, origin_msg_id)
    for recipient_id, local_ids in recipients_map.items():
        for mid in local_ids:
            try:
                await context.bot.delete_message(recipient_id, mid)
            except TelegramError:
                pass

    try:
        await msg.delete()
    except TelegramError:
        pass

    if is_owner and not is_own:
        try:
            await context.bot.send_message(origin_sender_id, "ℹ️ owner یکی از پیام‌هات رو تو اتاق حذف کرد.")
        except TelegramError:
            pass


async def _handle_kick_command(update: Update, context: ContextTypes.DEFAULT_TYPE, room) -> None:
    """owner با ریپلای‌کردنِ «اخراج» روی پیامِ یه عضو، اونو از اتاق
    بیرون می‌کنه. منطقِ عضویت/auto-delete/آزادشدنِ جا دقیقاً مثلِ
    membership.leave_room_button است، فقط لازم نیست اینجا دوباره
    importش کنیم چون خودش این ماژول رو ایمپورت می‌کنه (نه برعکس)."""
    from db import kick_room_member

    owner_id = update.effective_user.id
    msg = update.message
    replied_id = msg.reply_to_message.message_id

    origin = await rc.get_room_msg_origin(room.id, owner_id, replied_id)
    if origin is None:
        await update.effective_message.reply_text("این پیام قابلِ شناسایی نیست.")
        return

    target_id, _ = origin
    if target_id == owner_id:
        await update.effective_message.reply_text("نمی‌تونی خودتو اخراج کنی؛ برای این کار اتاق رو ببند یا حذفش کن.")
        return

    result, error = await kick_room_member(owner_id, target_id)

    if error == "not_a_member":
        await update.effective_message.reply_text("این کاربر دیگه عضوِ این اتاق نیست.")
        return
    if error is not None:
        await update.effective_message.reply_text("مشکلی پیش اومد، دوباره تلاش کن.")
        return

    await rc.clear_active_room(target_id)
    display_name = await get_display_name(target_id) or "یه نفر"
    try:
        await context.bot.send_message(target_id, "🚫 توسطِ owner از اتاق اخراج شدی.", reply_markup=main_reply_keyboard())
    except TelegramError:
        pass

    if result["auto_deleted"]:
        metrics.room_auto_deleted.inc()
        for uid in result["remaining_member_ids"]:  # این حالت یعنی فقط owner مونده
            await rc.clear_active_room(uid)
            try:
                await context.bot.send_message(
                    uid, "ℹ️ با اخراجِ آخرین عضو، اتاق خودکار حذف شد.", reply_markup=main_reply_keyboard()
                )
            except TelegramError:
                pass
    else:
        await broadcast_system_message(
            room.id, f"{display_name} اخراج شد.", context, member_ids=result["remaining_member_ids"]
        )
        from .matching import try_fill_room_from_queue

        await try_fill_room_from_queue(room.id, context)


async def _build_sender_label(room, user_id: int) -> str:
    display_name, referral_code = await get_display_name_and_referral_code(user_id)
    display_name = display_name or "کاربر"
    name_part = f"{display_name} (owner)" if user_id == room.owner_id else display_name
    if referral_code:
        return f"{name_part} (/user_{referral_code})"
    return name_part


async def _send_one(bot, chat_id: int, msg, label: str, reply_params, secure: bool) -> list[int]:
    """یه پیام رو برای یه گیرنده می‌فرسته و لیستِ message_idِ واقعاً
    ساخته‌شده رو برمی‌گردونه. استیکر/ویدیو-نوت caption ندارن (محدودیتِ
    خودِ Telegram)، پس براشون یه پیامِ برچسبِ اسمِ جدا قبل از خودِ
    مدیا می‌ره؛ برای همینه که این تابع یه *لیست* برمی‌گردونه، نه یه
    message_idِ تنها — تا هر دو تو نگاشت ثبت بشن و دستورِ حذف یتیم
    نذاره."""
    if msg.text:
        sent = await bot.send_message(
            chat_id, f"{label}: {msg.text}", reply_parameters=reply_params, protect_content=secure
        )
        return [sent.message_id]

    if msg.sticker or msg.video_note:
        label_msg = await bot.send_message(
            chat_id, f"{label}:", reply_parameters=reply_params, protect_content=secure
        )
        if msg.sticker:
            media_msg = await bot.send_sticker(chat_id, msg.sticker.file_id, protect_content=secure)
        else:
            media_msg = await bot.send_video_note(chat_id, msg.video_note.file_id, protect_content=secure)
        return [label_msg.message_id, media_msg.message_id]

    caption = f"{label}: {msg.caption}" if msg.caption else f"{label}:"
    if msg.photo:
        sent = await bot.send_photo(
            chat_id, msg.photo[-1].file_id, caption=caption, reply_parameters=reply_params, protect_content=secure
        )
    elif msg.voice:
        sent = await bot.send_voice(
            chat_id, msg.voice.file_id, caption=caption, reply_parameters=reply_params, protect_content=secure
        )
    elif msg.audio:
        sent = await bot.send_audio(
            chat_id, msg.audio.file_id, caption=caption, reply_parameters=reply_params, protect_content=secure
        )
    elif msg.video:
        sent = await bot.send_video(
            chat_id, msg.video.file_id, caption=caption, reply_parameters=reply_params, protect_content=secure
        )
    elif msg.document:
        sent = await bot.send_document(
            chat_id, msg.document.file_id, caption=caption, reply_parameters=reply_params, protect_content=secure
        )
    elif msg.animation:
        sent = await bot.send_animation(
            chat_id, msg.animation.file_id, caption=caption, reply_parameters=reply_params, protect_content=secure
        )
    else:
        raise ValueError(f"unsupported message type for room relay: {msg}")
    return [sent.message_id]
