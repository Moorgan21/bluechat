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

"""پروفایلِ عمومی (/user_<code>): دیدنِ پروفایلِ هر کاربر بدونِ نیاز به چتِ
فعال، با دکمه‌های گزارشِ پروفایل، درخواستِ چت، بلاک، و ارسالِ واکنش اگه
صاحبِ پروفایل فعالش کرده باشه.

درخواستِ چت rc.CHAT_COIN_COST سکه از A کسر می‌کنه. A رو دکمه‌ی درخواستِ
چت زیرِ پروفایلِ B می‌زنه، سکه کسر می‌شه، و B فقط یه نوتیفِ کوتاه می‌گیره
بدونِ اینکه هویتِ A افشا بشه. وقتی B روی «مشاهده» بزنه، پروفایلِ کاملِ A
با دکمه‌های قبول/رد نشونش داده می‌شه و A هم خبردار می‌شه که دیده شده.
قبول‌کردن یه چتِ دوطرفه‌ی کامل باز می‌کنه (سکه برنمی‌گرده، چون چت واقعاً
برقرار شده)؛ رد‌کردن سکه رو به A برمی‌گردونه. اگه B تا ۲ دقیقه
(rc.CHAT_REQUEST_TIMEOUT_SECONDS) نه قبول کنه نه رد، یه jobِ پس‌زمینه
(main.py:_expire_chat_requests_job) خودش لغوش می‌کنه و سکه به A برمی‌گرده.

برای واکنش، هر کاربر تو تنظیماتِ پروفایلِ خودش می‌تونه دریافتِ واکنش رو
روشن/خاموش کنه و تگ‌های سفارشی (مثلِ #عصبانی) بسازه. اگه روشن باشه،
بازدیدکننده‌ها می‌تونن یه تگ انتخاب کنن، صاحبِ پروفایل نوتیفِ ناشناس
می‌گیره و شمارشگرِ اون تگ یکی زیاد می‌شه.
"""

import html
import logging

_FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")

def _to_fa(n: int) -> str:
    return str(n).translate(_FA_DIGITS)

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

import redis_client as rc
from db import (
    ChatSession,
    async_session,
    add_reaction_tag,
    block_sender,
    deduct_coins,
    delete_reaction_tag,
    get_reaction_counts,
    get_reaction_tag,
    get_user_by_referral_code,
    increment_total_chats,
    is_sender_blocked,
    list_reaction_tags,
    log_reaction,
    refund_coins,
    set_reactions_enabled,
    set_silent_mode,
    unblock_sender,
)
from handlers.chat import check_room_conflict
from keyboards import (
    chat_request_decision_keyboard,
    in_chat_reply_keyboard,
    public_profile_keyboard,
    reaction_settings_keyboard,
    reaction_tags_keyboard,
    reaction_tags_manage_keyboard,
    view_chat_request_keyboard,
)

logger = logging.getLogger(__name__)

AWAITING_NEW_TAG_KEY = "awaiting_new_reaction_tag"


# نمایشِ پروفایلِ عمومی با /user_<code>
async def _build_public_profile_text(code: str, target) -> str:
    """متنِ HTML-escape‌شده‌ی پروفایلِ عمومی رو می‌سازه؛ بینِ نمایشِ
    /user_<code> و نمایشِ پروفایل هنگامِ «مشاهده‌ی درخواستِ چت» مشترکه."""
    from handlers.profile import GENDER_LABELS

    last_seen_ts = await rc.get_last_seen(target.id)
    last_seen_text = rc.format_last_seen(last_seen_ts)
    location_line = ""
    if target.province or target.city:
        parts = [p for p in (target.province, target.city) if p]
        location_line = f"\n📍 موقعیت: {html.escape(' — '.join(parts))}"

    text = (
        f"👤 پروفایلِ کاربر /user_{code}\n\n"
        f"نام نمایشی: {html.escape(target.display_name or 'تنظیم‌نشده')}\n"
        f"بیوگرافی: {html.escape(target.bio or '—')}\n"
        f"جنسیت: {GENDER_LABELS.get(target.gender, 'تنظیم‌نشده')}\n"
        f"سن: {target.age or '—'}"
        f"{location_line}\n"
        f"آخرین بازدید: {last_seen_text}"
    )

    counts = await get_reaction_counts(target.id)
    if counts:
        text += "\n\n😠 واکنش‌های دریافتی:\n\n"
        text += "\n".join(
            f"<blockquote>‏#{html.escape(c['label'])}: {_to_fa(c['count'])}</blockquote>"
            for c in counts[:10]
        )

    return text


async def show_public_profile_by_code(update: Update, context: ContextTypes.DEFAULT_TYPE, code: str) -> None:
    viewer_id = update.effective_user.id
    target = await get_user_by_referral_code(code)

    if target is None:
        await update.message.reply_text("همچین کاربری پیدا نشد. لینک/شناسه رو دوباره چک کن.")
        return

    if target.id == viewer_id:
        await update.message.reply_text("این پروفایلِ خودته 🙂 از منوی «👤 پروفایل» می‌تونی ببینیش.")
        return

    text = await _build_public_profile_text(code, target)
    is_blocked = await is_sender_blocked(viewer_id, target.id)
    keyboard = public_profile_keyboard(target.id, target.reactions_enabled, is_blocked=is_blocked)

    if target.photo_file_id:
        try:
            await context.bot.send_photo(
                update.effective_chat.id, target.photo_file_id, caption=text,
                reply_markup=keyboard, parse_mode="HTML",
            )
        except TelegramError:
            from db import clear_photo_file_id
            await clear_photo_file_id(target.id)
            await update.message.reply_text(text, reply_markup=keyboard, parse_mode="HTML")
    else:
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode="HTML")


async def _refresh_block_button(query, owner_id: int, target_id: int) -> None:
    """بعد از بلاک/آنبلاک، دکمه‌ی زیرِ همون پیام رو فوراً با وضعیتِ
    جدید بازمی‌سازه (بدونِ نیاز به بازکردنِ دوباره‌ی پروفایل)."""
    target = await async_session_get_user(target_id)
    if target is None:
        return
    is_blocked = await is_sender_blocked(owner_id, target_id)
    keyboard = public_profile_keyboard(target_id, target.reactions_enabled, is_blocked=is_blocked)
    try:
        await query.message.edit_reply_markup(reply_markup=keyboard)
    except TelegramError:
        pass


# بلاک از طریق پروفایلِ عمومی
async def handle_public_block_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    target_id = int(query.data.split(":", 1)[1])
    owner_id = query.from_user.id

    if target_id == owner_id:
        await query.message.reply_text("نمی‌تونی خودت رو بلاک کنی 🙂")
        return

    await block_sender(owner_id, target_id)
    await query.message.reply_text("🚫 این کاربر بلاک شد و دیگه نمی‌تونه از طریق لینک/پروفایلت پیام یا درخواست چت بفرسته.")
    await _refresh_block_button(query, owner_id, target_id)


async def handle_public_unblock_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    target_id = int(query.data.split(":", 1)[1])
    owner_id = query.from_user.id

    await unblock_sender(owner_id, target_id)
    await query.message.reply_text("✅ این کاربر آنبلاک شد و دوباره می‌تونه برات پیام/درخواست چت بفرسته.")
    await _refresh_block_button(query, owner_id, target_id)


# درخواستِ چت
async def handle_chat_request_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """هندلر دکمه‌ی «💬 درخواست چت» زیرِ پروفایلِ عمومی."""
    query = update.callback_query
    await query.answer()

    target_id = int(query.data.split(":", 1)[1])
    requester_id = query.from_user.id

    if target_id == requester_id:
        await query.message.reply_text("نمی‌تونی برای خودت درخواستِ چت بفرستی 🙂")
        return

    if await rc.get_partner(requester_id) is not None:
        await query.message.reply_text(
            "⚠️ الان توی یه چت فعال هستی. برای ارسال درخواست چت، اول چت فعالت رو پایان بده."
        )
        return

    room_conflict = await check_room_conflict(requester_id)
    if room_conflict:
        await query.message.reply_text(room_conflict)
        return

    if await rc.get_partner(target_id) is not None:
        await query.message.reply_text("⚠️ این کاربر الان در یه چت فعال هست. بعداً تلاش کن.")
        return

    # چکِ بلاک همیشه باید قبل از چکِ سایلنت باشه؛ وگرنه به کسی که واقعاً
    # بلاک شده، پیامِ نامربوطِ «سایلنته» نشون داده می‌شه.
    if await is_sender_blocked(target_id, requester_id):
        await query.message.reply_text("🚫 این کاربر بلاکت کرده و امکانِ ارسالِ درخواستِ چت یا پیامِ دایرکت بهش نداری.")
        return

    target = await async_session_get_user(target_id)
    if target is None:
        await query.message.reply_text("این کاربر در دسترس نیست.")
        return

    if target.is_silent:
        await query.message.reply_text(
            "این کاربر حالتِ سایلنت رو فعال کرده و فعلاً امکانِ ارسالِ درخواستِ چت به ایشون وجود نداره."
        )
        return

    new_balance = await deduct_coins(requester_id, rc.CHAT_COIN_COST, "chat_request_cost")
    if new_balance is None:
        await query.message.reply_text(
            f"🪙 سکه‌ی کافی نداری! ارسالِ درخواستِ چت {rc.CHAT_COIN_COST} سکه هزینه داره."
        )
        return

    request_id = await rc.create_chat_request(requester_id, target_id)

    await query.message.reply_text("✅ درخواستِ چتت ارسال شد.")

    try:
        await context.bot.send_message(
            target_id,
            "🔔 یه درخواستِ چت داری. برای مشاهده روی دکمه‌ی زیر بزن:",
            reply_markup=view_chat_request_keyboard(request_id),
        )
    except TelegramError:
        logger.warning("امکان ارسالِ نوتیفِ درخواستِ چت به target_id=%s وجود نداشت.", target_id)


async def async_session_get_user(user_id: int):
    from db import User

    async with async_session() as session:
        return await session.get(User, user_id)


async def handle_view_chat_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """هندلر دکمه‌ی «👀 مشاهده درخواست چت»؛ پروفایلِ کاملِ درخواست‌کننده رو
    با دکمه‌های قبول/رد نشون می‌ده و به درخواست‌کننده هم خبر می‌ده که
    دیده شده."""
    query = update.callback_query
    await query.answer()

    request_id = query.data.split(":", 1)[1]
    request_data = await rc.get_chat_request(request_id)

    if request_data is None:
        await query.edit_message_text("⚠️ این درخواست دیگه معتبر نیست (منقضی شده یا قبلاً پاسخ داده شده).")
        return

    requester_id = request_data["requester_id"]
    requester = await async_session_get_user(requester_id)
    if requester is None:
        await query.edit_message_text("⚠️ این کاربر دیگه در دسترس نیست.")
        return

    requester_code = requester.referral_code or "نامشخص"
    text = await _build_public_profile_text(requester_code, requester)
    keyboard = chat_request_decision_keyboard(request_id)

    await query.edit_message_text(f"🔔 درخواست چت از طرف /user_{requester_code}\n\n👇 پروفایلش رو ببین و تصمیم بگیر:")

    chat_id = query.message.chat_id
    if requester.photo_file_id:
        try:
            await context.bot.send_photo(
                chat_id, requester.photo_file_id, caption=text,
                reply_markup=keyboard, parse_mode="HTML",
            )
        except TelegramError:
            from db import clear_photo_file_id
            await clear_photo_file_id(requester.id)
            await context.bot.send_message(chat_id, text, reply_markup=keyboard, parse_mode="HTML")
    else:
        await context.bot.send_message(chat_id, text, reply_markup=keyboard, parse_mode="HTML")

    try:
        await context.bot.send_message(requester_id, "👀 کاربر درخواستِ چتت رو دید.")
    except TelegramError:
        pass


async def _edit_decision_message(query, text: str) -> None:
    """پیامِ زیرِ دکمه‌های قبول/رد گاهی پیامِ متنیه، گاهی (اگه
    درخواست‌کننده عکسِ پروفایل داشته باشه) پیامِ عکس‌دار با caption
    (نگاه کن به handle_view_chat_request). زدنِ edit_message_text روی
    یه پیامِ عکس‌دار با TelegramError fail می‌کنه (نه بی‌سروصدا رد
    می‌شه)، و چون قبلاً هیچ‌جا catch نمی‌شد، کلِ هندلر همون‌جا می‌ترکید
    و منطقِ اصلیِ بعدش (اطلاع‌رسانی به دو طرف، بازگشتِ سکه در ردِ
    درخواست) اصلاً اجرا نمی‌شد."""
    try:
        if query.message.photo:
            await query.edit_message_caption(caption=text)
        else:
            await query.edit_message_text(text)
    except TelegramError:
        logger.warning("امکانِ ویرایشِ پیامِ تصمیمِ درخواستِ چت وجود نداشت.")


async def handle_chat_request_accept(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    request_id = query.data.split(":", 1)[1]
    request_data = await rc.get_chat_request(request_id)
    acceptor_id = query.from_user.id

    if request_data is None:
        await _edit_decision_message(query, "⚠️ این درخواست دیگه معتبر نیست.")
        return

    requester_id = request_data["requester_id"]

    if await rc.get_partner(acceptor_id) is not None or await rc.get_partner(requester_id) is not None:
        await _edit_decision_message(
            query, "یکی از دو طرف الان توی گفتگوی دیگه‌ای هست، پس این درخواست قابلِ قبول نیست."
        )
        await rc.clear_chat_request(request_id)
        return

    if await check_room_conflict(acceptor_id) or await check_room_conflict(requester_id):
        # پیامِ دقیقِ اتاق/صف اهمیتی نداره؛ نکته‌ی مهم برای این دو نفر
        # همینه که یکی‌شون آزادِ چتِ ۱به۱ نیست، صرف‌نظر از اینکه کدومه.
        await _edit_decision_message(
            query, "یکی از دو طرف الان توی یه اتاقِ چتِ فعاله یا منتظرِ عضویتِ یه اتاقه، پس این درخواست قابلِ قبول نیست."
        )
        await rc.clear_chat_request(request_id)
        return

    await rc.clear_chat_request(request_id)
    await rc.dequeue(acceptor_id)
    await rc.dequeue(requester_id)
    await rc.set_partner(acceptor_id, requester_id)

    async with async_session() as session:
        chat_session = ChatSession(user_a_id=acceptor_id, user_b_id=requester_id)
        session.add(chat_session)
        await session.commit()
        await session.refresh(chat_session)
        await rc.set_session_id(acceptor_id, requester_id, chat_session.id)

    await increment_total_chats([acceptor_id, requester_id])

    await _edit_decision_message(query, "✅ درخواست قبول شد. گفتگو شروع شد.")
    try:
        await context.bot.send_message(
            acceptor_id,
            "✅ درخواستِ چت رو قبول کردی. گفتگو شروع شد.",
            reply_markup=in_chat_reply_keyboard(),
        )
    except TelegramError:
        pass
    try:
        await context.bot.send_message(
            requester_id,
            "✅ درخواستِ چتت قبول شد! گفتگو شروع شد.",
            reply_markup=in_chat_reply_keyboard(),
        )
    except TelegramError:
        pass


async def handle_chat_request_reject(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    request_id = query.data.split(":", 1)[1]
    request_data = await rc.get_chat_request(request_id)
    rejector_id = query.from_user.id

    await rc.clear_chat_request(request_id)
    await _edit_decision_message(query, "❌ درخواست رد شد.")

    if request_data is not None:
        requester_id = request_data["requester_id"]
        await refund_coins(requester_id, rc.CHAT_COIN_COST, "chat_request_reject_refund")

        rejector = await async_session_get_user(rejector_id)
        rejector_code = rejector.referral_code if rejector else "نامشخص"
        try:
            await context.bot.send_message(
                requester_id,
                f"❌ درخواست چتِ شما به پروفایلِ عمومیِ /user_{rejector_code} رد شد و "
                f"{rc.CHAT_COIN_COST} سکه به حسابتون برگشت.",
            )
        except TelegramError:
            pass


# حالتِ سایلنت (/silent)
async def toggle_silent_mode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    user = await async_session_get_user(user_id)
    if user is None:
        return

    new_state = not user.is_silent
    await set_silent_mode(user_id, new_state)

    if new_state:
        await update.message.reply_text(
            "🔕 حالتِ سایلنت فعال شد. از این به بعد کسی نمی‌تونه برات درخواستِ چت بفرسته.\n"
            "برای غیرفعال‌کردنش دوباره /silent رو بزن."
        )
    else:
        await update.message.reply_text("🔔 حالتِ سایلنت غیرفعال شد. دوباره درخواستِ چت می‌تونی دریافت کنی.")


# تنظیماتِ واکنش (زیرِ پروفایلِ خودِ کاربر)
async def open_reaction_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    user = await async_session_get_user(user_id)
    if user is None:
        return

    status = "فعال ✅" if user.reactions_enabled else "غیرفعال ❌"
    text = (
        f"😠 تنظیماتِ واکنش\n\n"
        f"وضعیتِ فعلی: {status}\n\n"
        "وقتی فعال باشه، هرکسی که پروفایلت رو با /user_شناسه ببینه، می‌تونه با یکی از "
        "تگ‌های خودت (که پایین تعریف می‌کنی) بهت واکنشِ ناشناس بفرسته."
    )
    await query.message.reply_text(text, reply_markup=reaction_settings_keyboard(user.reactions_enabled))


async def reaction_settings_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    action = query.data.split(":", 1)[1]
    user_id = query.from_user.id

    if action == "toggle":
        user = await async_session_get_user(user_id)
        if user is None:
            return
        new_state = not user.reactions_enabled
        await set_reactions_enabled(user_id, new_state)
        status = "فعال ✅" if new_state else "غیرفعال ❌"
        await query.edit_message_text(
            f"😠 تنظیماتِ واکنش\n\nوضعیتِ فعلی: {status}",
            reply_markup=reaction_settings_keyboard(new_state),
        )

    elif action == "addtag":
        context.user_data[AWAITING_NEW_TAG_KEY] = True
        await query.message.reply_text(
            "برچسبِ تگِ جدید رو بفرست (بدون #، مثلاً: عصبانی). حداکثر ۲۰ کاراکتر:"
        )

    elif action == "listtags":
        tags = await list_reaction_tags(user_id)
        if not tags:
            await query.message.reply_text("هنوز هیچ تگی نساختی. از «➕ افزودنِ تگِ جدید» شروع کن.")
            return
        await query.message.reply_text(
            "تگ‌های فعلیت (برای حذف روشون بزن):",
            reply_markup=reaction_tags_manage_keyboard(tags),
        )

    elif action == "back":
        user = await async_session_get_user(user_id)
        if user is None:
            return
        await query.edit_message_text(
            "😠 تنظیماتِ واکنش", reply_markup=reaction_settings_keyboard(user.reactions_enabled)
        )

    elif action.startswith("deltag:"):
        tag_id = int(action.split(":", 1)[1])
        deleted = await delete_reaction_tag(user_id, tag_id)
        tags = await list_reaction_tags(user_id)
        if tags:
            await query.edit_message_text(
                "تگ‌های فعلیت (برای حذف روشون بزن):",
                reply_markup=reaction_tags_manage_keyboard(tags),
            )
        else:
            await query.edit_message_text("همه‌ی تگ‌هات حذف شدن.")


async def handle_new_tag_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """اگه کاربر منتظرِ نوشتنِ یه تگِ جدید بود، این پیامش رو پردازش
    می‌کنه. خروجی True یعنی مصرف شد."""
    if not context.user_data.get(AWAITING_NEW_TAG_KEY):
        return False

    from security import sanitize_tag
    raw = (update.message.text or "").strip().lstrip("#").strip()
    text = sanitize_tag(raw)
    user_id = update.effective_user.id

    if not text or len(text) > 20:
        await update.message.reply_text("برچسب باید بین ۱ تا ۲۰ کاراکتر باشه (بدون #). دوباره بفرست:")
        return True

    if any(ch.isspace() for ch in text):
        await update.message.reply_text("برچسب نباید فاصله داشته باشه (از _ استفاده کن). دوباره بفرست:")
        return True

    context.user_data.pop(AWAITING_NEW_TAG_KEY, None)
    added = await add_reaction_tag(user_id, text)

    if added:
        await update.message.reply_text(f"✅ تگِ #{text} اضافه شد.")
    else:
        await update.message.reply_text("این برچسب از قبل داشتی.")

    user = await async_session_get_user(user_id)
    if user is not None:
        await update.message.reply_text(
            "😠 تنظیماتِ واکنش", reply_markup=reaction_settings_keyboard(user.reactions_enabled)
        )
    return True


# ارسالِ واکنش (توسطِ بازدیدکننده‌ی پروفایلِ عمومی)
async def handle_open_reaction_picker(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    target_id = int(query.data.split(":", 1)[1])
    tags = await list_reaction_tags(target_id)

    if not tags:
        await query.message.reply_text("این کاربر هنوز هیچ تگِ واکنشی تعریف نکرده.")
        return

    await query.message.reply_text(
        "کدوم واکنش رو می‌خوای بفرستی؟", reply_markup=reaction_tags_keyboard(target_id, tags)
    )


async def handle_send_reaction(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """format: reactsend:<target_id>:<tag_id>"""
    query = update.callback_query
    await query.answer()

    _, target_id_str, tag_id_str = query.data.split(":")
    target_id = int(target_id_str)
    tag_id = int(tag_id_str)
    sender_id = query.from_user.id

    if target_id == sender_id:
        await query.edit_message_text("نمی‌تونی به خودت واکنش بفرستی 🙂")
        return

    tag = await get_reaction_tag(tag_id)
    if tag is None or tag["owner_id"] != target_id:
        await query.edit_message_text("⚠️ این تگ دیگه معتبر نیست.")
        return

    await log_reaction(target_id, sender_id, tag_id, tag["label"])
    await query.edit_message_text(f"✅ واکنش #{tag['label']} ارسال شد.")

    try:
        await context.bot.send_message(
            target_id, f"😠 یه نفر برات واکنشِ #{tag['label']} فرستاد."
        )
    except TelegramError:
        logger.warning("امکانِ اطلاع‌رسانیِ واکنش به target_id=%s وجود نداشت.", target_id)
