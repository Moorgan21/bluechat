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

"""هندلرهای «جستجوی کاربران»: matching هدفمند با فیلترِ جنسیت/سن، برخلافِ
matching کاملاً تصادفیِ chat.py.

فیلترها توی context.user_data می‌مونن (per-session، برای این مقیاس
کافیه). matching هنوز از صفِ Redis استفاده می‌کنه، فقط قبل از جفت‌کردن
پروفایلِ کاندیدا از Postgres چک می‌شه.
"""

from sqlalchemy import select
from telegram import Update
from telegram.ext import ContextTypes

import redis_client as rc
from db import Gender, User, async_session, deduct_coins, get_or_create_user
from handlers.chat import try_match
from keyboards import search_users_keyboard

FILTERS_KEY = "search_filters"  # {"gender": "male"|"female"|None, "min_age":..,"max_age":..}


async def show_search_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    filters_ = context.user_data.get(FILTERS_KEY, {})
    gender_label = {"male": "مرد", "female": "زن", None: "بدون فیلتر"}.get(filters_.get("gender"))
    age_range = filters_.get("age_range", "بدون فیلتر")

    text = (
        "🔮 جستجوی هدفمند کاربران\n\n"
        f"فیلتر جنسیت: {gender_label}\n"
        f"فیلتر سن: {age_range}\n\n"
        "می‌تونی فیلترها رو تنظیم کنی و بعد جستجو رو شروع کنی."
    )
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=search_users_keyboard())
    else:
        await update.message.reply_text(text, reply_markup=search_users_keyboard())


async def search_callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    action = query.data.split(":", 1)[1]

    if action == "filter_gender":
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("👨 مرد", callback_data="searchgender:male"),
                    InlineKeyboardButton("👩 زن", callback_data="searchgender:female"),
                ],
                [InlineKeyboardButton("❌ حذف فیلتر", callback_data="searchgender:none")],
                [InlineKeyboardButton("🔙 بازگشت", callback_data="menu:search")],
            ]
        )
        await query.edit_message_text("جنسیت مدنظرت رو انتخاب کن:", reply_markup=keyboard)

    elif action == "filter_age":
        context.user_data["awaiting_search_age"] = True
        await query.edit_message_text(
            "بازه‌ی سنی رو به فرمت «حداقل-حداکثر» بفرست (مثلاً 20-30):"
        )

    elif action == "go":
        await run_search(update, context)


async def search_gender_callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    value = query.data.split(":", 1)[1]

    filters_ = context.user_data.setdefault(FILTERS_KEY, {})
    filters_["gender"] = None if value == "none" else value

    await show_search_menu(update, context)


async def handle_search_age_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not context.user_data.get("awaiting_search_age"):
        return False

    text = (update.message.text or "").strip()
    parts = text.split("-")
    if len(parts) != 2 or not all(p.strip().isdigit() for p in parts):
        await update.message.reply_text("فرمت درست نیست. مثال: 20-30 دوباره بفرست:")
        return True

    min_age, max_age = int(parts[0]), int(parts[1])
    if min_age > max_age or min_age < 10 or max_age > 99:
        await update.message.reply_text("بازه‌ی نامعتبره. یه بازه‌ی منطقی بین ۱۰ تا ۹۹ بفرست:")
        return True

    filters_ = context.user_data.setdefault(FILTERS_KEY, {})
    filters_["min_age"] = min_age
    filters_["max_age"] = max_age
    filters_["age_range"] = f"{min_age} تا {max_age} سال"
    context.user_data.pop("awaiting_search_age", None)

    await update.message.reply_text("✅ فیلتر سن ثبت شد.")
    await show_search_menu(update, context)
    return True


async def run_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """صف انتظار Redis رو با فیلترهای Postgres ترکیب می‌کنه: کاندیداهای
    صف رو می‌گیره، پروفایلشون رو در Postgres چک می‌کنه، و اولین match
    منطبق با فیلتر رو جفت می‌کنه. اگه کسی پیدا نشد، به‌صورت معمولی به
    صف عمومی اضافه می‌شه."""
    from handlers.profile import is_profile_complete, start_onboarding

    query = update.callback_query
    user_id = query.from_user.id
    filters_ = context.user_data.get(FILTERS_KEY, {})

    async with async_session() as session:
        me = await get_or_create_user(session, user_id)
        if not is_profile_complete(me):
            await query.answer()
            await start_onboarding(update, context)
            return

    if await rc.get_partner(user_id) is not None:
        await query.edit_message_text("الان توی یه گفتگو هستی.")
        return

    # کسر سکه اگه فیلتر جنسیت داشت
    gender_filter = filters_.get("gender")
    if gender_filter:
        new_balance = await deduct_coins(user_id, rc.CHAT_COIN_COST, "gender_search")
        if new_balance is None:
            await query.edit_message_text(
                f"🪙 سکه‌ی کافی نداری!\n"
                f"جستجو با فیلتر جنسیت {rc.CHAT_COIN_COST} سکه هزینه داره.\n"
                "برای جستجوی رایگان فیلتر جنسیت رو حذف کن."
            )
            return
        await rc.set_chat_payer(user_id)

    candidate_ids: list[int] = []
    for gender in rc.PARTNER_GENDERS:
        members = await rc.r.zrange(rc.KEY_WAITING_QUEUE_BY_GENDER.format(gender=gender), 0, -1)
        candidate_ids.extend(int(m) for m in members if int(m) != user_id)

    matched_id = None
    if candidate_ids and (filters_.get("gender") or filters_.get("min_age")):
        async with async_session() as session:
            stmt = select(User).where(User.id.in_(candidate_ids))
            if filters_.get("gender"):
                stmt = stmt.where(User.gender == Gender(filters_["gender"]))
            if filters_.get("min_age") and filters_.get("max_age"):
                stmt = stmt.where(User.age >= filters_["min_age"], User.age <= filters_["max_age"])
            result = await session.execute(stmt)
            matches = result.scalars().all()
            if matches:
                matched_id = matches[0].id

    if matched_id is not None:
        # dequeue با ZREM اتمیکه، فقط یه کوروتین True می‌گیره پس double-claim نمی‌شه
        if not await rc.dequeue(matched_id):
            matched_id = None
        elif await rc.get_partner(matched_id) is not None:
            matched_id = None

    if matched_id is not None:
        await rc.dequeue(user_id)
        await rc.set_partner(user_id, matched_id)
        await query.edit_message_text("✅ یک کاربر منطبق با فیلترهات پیدا شد! گفتگو شروع شد.")
        from db import ChatSession, increment_total_chats
        from keyboards import in_chat_reply_keyboard

        async with async_session() as session:
            chat_session = ChatSession(user_a_id=user_id, user_b_id=matched_id)
            session.add(chat_session)
            await session.commit()
            await session.refresh(chat_session)
            await rc.set_session_id(user_id, matched_id, chat_session.id)

        await increment_total_chats([user_id, matched_id])

        await context.bot.send_message(
            user_id, "گفتگو شروع شد. از دکمه‌های پایین استفاده کن.", reply_markup=in_chat_reply_keyboard()
        )
        await context.bot.send_message(
            matched_id,
            "✅ یک کاربر با فیلتر جستجو به شما وصل شد! گفتگو شروع شد.",
            reply_markup=in_chat_reply_keyboard(),
        )
    else:
        await query.edit_message_text(
            "الان کاربری با این فیلترها آنلاین نیست. تو رو به صف عمومی اضافه می‌کنم..."
        )
        await try_match(user_id, context, filters_.get("gender"))
