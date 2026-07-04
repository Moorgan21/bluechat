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

"""ابزارهای owner: حذفِ اتاق (تنها راهِ خروجِ owner)، بستن/بازکردنِ
اتاق، و پاک‌سازیِ یک‌طرفه‌ی تاریخچه بعدِ حذف. حذفِ پیامِ دیگران و
اخراج دکمه ندارن؛ با ریپلای‌کردنِ «حذف»/«اخراج» تو relay.py هندل
می‌شن، همون‌جایی که پیام‌ها مدیریت می‌شن.
"""

import logging

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

import redis_client as rc
from db import delete_chat_room, set_room_open_status
from keyboards import (
    in_room_reply_keyboard,
    main_reply_keyboard,
    purge_history_keyboard,
    room_delete_confirm_keyboard,
)

logger = logging.getLogger(__name__)


async def delete_room_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """دکمه‌ی «🗑 حذف اتاق»؛ چون غیرقابلِ بازگشته، اول تاییدِ صریح
    می‌گیره (همون الگوی end_chat_confirm_keyboard برای پایانِ چتِ ۱به۱)."""
    await update.message.reply_text(
        "⚠️ حذفِ اتاق غیرقابلِ بازگشته و همه‌ی اعضا ازش بیرون میان. مطمئنی؟",
        reply_markup=room_delete_confirm_keyboard(),
    )


async def delete_room_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    action = query.data.split(":", 1)[1]

    if action == "cancel":
        try:
            await query.message.delete()
        except TelegramError:
            pass
        return

    user_id = query.from_user.id
    result, error = await delete_chat_room(user_id)

    try:
        await query.message.delete()
    except TelegramError:
        pass

    if error == "not_found":
        await rc.clear_active_room(user_id)
        await context.bot.send_message(user_id, "این اتاق دیگه فعال نیست.", reply_markup=main_reply_keyboard())
        return
    if error == "not_owner":
        await context.bot.send_message(user_id, "⚠️ فقط owner می‌تونه اتاق رو حذف کنه.")
        return

    # برخلافِ ترکِ معمولی (که فقط خودِ leaver کیبوردش عوض می‌شه، بقیه
    # همچنان تو اتاقن)، اینجا اتاق برای *همه* تموم می‌شه، پس همه باید
    # کیبوردِ منو رو پس بگیرن؛ برای همین یه پیامِ per-recipient مستقیم
    # می‌فرستیم، نه broadcast_system_messageِ عمومی (که reply_markup نداره).
    for uid in result["member_ids"]:
        await rc.clear_active_room(uid)
        text = "🗑 اتاقت حذف شد." if uid == user_id else "ℹ️ این اتاق توسطِ owner حذف شد."
        try:
            await context.bot.send_message(uid, text, reply_markup=main_reply_keyboard())
        except TelegramError:
            logger.warning("امکانِ اطلاع‌رسانیِ حذفِ اتاق به user_id=%s وجود نداشت.", uid)

    # ChatRoomMemberِ Postgres همین الان پاک شد، پس اگه بعداً بخوایم
    # تاریخچه رو پاک کنیم دیگه نمی‌تونیم بفهمیم اعضا کیا بودن؛ برای
    # همین لیست رو تو Redis نگه می‌داریم. result["member_ids"] فقط
    # عضوهای *فعلیِ* لحظه‌ی حذفه؛ برای اینکه واقعاً «همه‌ی اعضای سابق»
    # پاک بشه (کسی که قبل‌تر ترک کرده/اخراج شده/بن شده هم شامل بشه)،
    # offer_room_history_purge با تاریخچه‌ی ثبت‌شده‌ی خودِ Redis (که
    # مستقل از عضویتِ زنده‌ست) یکی‌شون می‌کنه.
    await offer_room_history_purge(context, result["room_id"], user_id, result["member_ids"])


async def offer_room_history_purge(
    context: ContextTypes.DEFAULT_TYPE, room_id: int, owner_id: int, member_ids: list[int]
) -> None:
    """بعد از حذفِ اتاق (چه با دکمه‌ی «🗑 حذف اتاق»، چه خودکار به‌خاطرِ
    ترک/اخراجِ آخرین عضوِ غیرِ owner در membership.py/relay.py)، اگه
    این اتاق واقعاً تاریخچه‌ای داشته، پیشنهادِ پاک‌سازیِ کامل رو به
    owner می‌ده — فقط تا ۲ دقیقه معتبر (TTL_ROOM_PURGE_OFFER)، هم‌راستا
    با پنجره‌ی پاکسازی/گزارشِ بعدِ پایانِ چتِ ۱به۱. اگه اصلاً پیامی تو
    این اتاق رد و بدل نشده بود، به‌جای پیشنهادِ الکی، صراحتاً می‌گه
    تاریخچه‌ای برای پاک‌سازی وجود نداره."""
    history_user_ids = await rc.get_room_history_user_ids(room_id)
    if not history_user_ids:
        try:
            await context.bot.send_message(owner_id, "ℹ️ این اتاق هیچ پیامی نداشت؛ تاریخچه‌ای برای پاک‌سازی وجود نداره.")
        except TelegramError:
            logger.warning("امکانِ اطلاع‌رسانیِ نبودِ تاریخچه به owner_id=%s وجود نداشت.", owner_id)
        return

    all_time_member_ids = sorted(set(member_ids) | history_user_ids)
    await rc.store_deleted_room_members(room_id, all_time_member_ids)
    try:
        await context.bot.send_message(
            owner_id,
            "🧹 می‌خوای کاملِ تاریخچه‌ی پیام‌های این اتاق رو (برای همه‌ی اعضای سابق) پاک کنم؟\n"
            "⏳ این پیشنهاد فقط تا ۲ دقیقه معتبره.",
            reply_markup=purge_history_keyboard(room_id),
        )
    except TelegramError:
        logger.warning("امکانِ ارسالِ پیشنهادِ پاک‌سازیِ تاریخچه به owner_id=%s وجود نداشت.", owner_id)


async def purge_history_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """فقط از طریقِ پیامِ پیشنهادِ offer_room_history_purge قابلِ‌دسترسیه
    (roomdelete نه خودِ اتاق، چون تا اونجا active_room_idِ owner دیگه
    معتبر نیست)، و فقط تا ۲ دقیقه (TTL_ROOM_PURGE_OFFER) بعدِ اون پیشنهاد."""
    query = update.callback_query
    await query.answer()
    room_id = int(query.data.split(":", 1)[1])
    user_id = query.from_user.id

    member_ids = await rc.get_deleted_room_members(room_id)
    if member_ids is None:
        await query.edit_message_text("⚠️ این درخواست دیگه معتبر نیست (مهلتِ ۲دقیقه‌ای گذشته یا قبلاً پاک شده).")
        return

    try:
        await query.message.delete()
    except TelegramError:
        pass

    deleted_count = 0
    skipped_count = 0
    for uid in member_ids:
        for mid in await rc.pop_room_history(room_id, uid):
            try:
                await context.bot.delete_message(uid, mid)
                deleted_count += 1
            except TelegramError:
                skipped_count += 1
    await rc.clear_room_history_users(room_id)

    if deleted_count == 0 and skipped_count == 0:
        await context.bot.send_message(user_id, "ℹ️ تاریخچه‌ای برای پاک‌سازی پیدا نشد (شاید قبلاً پاک شده بود).")
        return

    summary = f"✅ {deleted_count} پیام پاک شد."
    if skipped_count:
        summary += f" {skipped_count} پیامِ قدیمی‌تر از ۴۸ ساعت طبقِ محدودیتِ خودِ تلگرام قابلِ حذف نبودن."
    await context.bot.send_message(user_id, summary)


async def _set_room_status(update: Update, context: ContextTypes.DEFAULT_TYPE, is_open: bool) -> None:
    user_id = update.effective_user.id
    result, error = await set_room_open_status(user_id, is_open)

    if error == "not_found":
        await rc.clear_active_room(user_id)
        await update.message.reply_text("این اتاق دیگه فعال نیست.", reply_markup=main_reply_keyboard())
        return
    if error == "not_owner":
        await update.message.reply_text("⚠️ فقط owner می‌تونه اتاق رو ببنده یا باز کنه.")
        return

    from .relay import broadcast_system_message

    other_ids = [uid for uid in result["member_ids"] if uid != user_id]
    if is_open:
        # با بازشدنِ اتاق، هندلرِ اتاقِ اعضا دوباره فعال می‌شه (حتی اگه
        # قبلش با «🚪 خروج» خودشون غیرفعالش کرده بودن) و کیبوردِ
        # داخلِ اتاق برمی‌گرده، چون دوباره می‌شه پیام رد و بدل کرد.
        for uid in other_ids:
            await rc.unsuppress_room_ui(uid)
        await broadcast_system_message(
            result["room_id"],
            "اتاق دوباره باز شد؛ می‌تونید پیام بدید.",
            context,
            member_ids=other_ids,
            reply_markup=in_room_reply_keyboard(),
        )
        text = "🔓 اتاق باز شد."
    else:
        # با بسته‌شدنِ اتاق، هندلرِ اتاقِ اعضا (نه owner) غیرفعال می‌شه
        # و به منوی اصلیِ کامل و بدون‌تغییر هدایت می‌شن؛ عضویت
        # (active_room_id) دست‌نخورده می‌مونه، پس همچنان نمی‌تونن
        # وارد چتِ ۱به۱ بشن یا اتاقِ دیگه بسازن/بهش ملحق بشن، ولی با
        # /room هر وقت خواستن هندلرِ اتاق دوباره فعال می‌شه.
        for uid in other_ids:
            await rc.suppress_room_ui(uid)
        await broadcast_system_message(
            result["room_id"],
            "اتاقت موقتاً بسته شد و فعلاً پیامی رد و بدل نمی‌شه. با دستورِ /room "
            "می‌تونی وضعیتِ اتاقتو چک کنی؛ فعلاً می‌تونی از بقیه‌ی امکاناتِ ربات "
            "استفاده کنی.",
            context,
            member_ids=other_ids,
            reply_markup=main_reply_keyboard(),
        )
        text = "🔒 اتاق بسته شد."

    await update.message.reply_text(text, reply_markup=in_room_reply_keyboard(is_owner=True, room_open=is_open))


async def close_room_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _set_room_status(update, context, is_open=False)


async def reopen_room_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _set_room_status(update, context, is_open=True)
