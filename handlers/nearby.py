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
هندلرهای بخش «افراد نزدیک» — با PostGIS.

به‌جای محاسبه‌ی دستی فاصله در پایتون (Haversine)، از توابع مکانیِ
PostGIS استفاده می‌کنیم:
    - ST_DWithin(location, my_location, radius_meters)  → فیلتر شعاعی سریع با ایندکس GiST
    - ST_Distance(location, my_location)                → فاصله‌ی دقیق برای مرتب‌سازی/نمایش

این کوئری‌ها مستقیم توسط دیتابیس و با بهره از ایندکس مکانی انجام می‌شن،
پس حتی با ده‌ها هزار کاربر هم سریع می‌مونن.
"""

from datetime import datetime, timedelta

from geoalchemy2.functions import ST_DWithin, ST_Distance
from sqlalchemy import select
from telegram import KeyboardButton, ReplyKeyboardMarkup, Update
from telegram.ext import ContextTypes

from db import User, async_session, get_or_create_user, make_point
from keyboards import main_reply_keyboard, nearby_keyboard

LOCATION_MAX_AGE_DAYS = 30  # موقعیت‌های قدیمی‌تر از این، در جستجو نادیده گرفته می‌شن
NEARBY_RADIUS_METERS = 50_000  # شعاع ۵۰ کیلومتر
NEARBY_RESULT_LIMIT = 10


async def show_nearby_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    telegram_user = update.effective_user
    async with async_session() as session:
        user = await get_or_create_user(session, telegram_user.id)
        has_location = user.location is not None

    text = (
        "📍 افراد نزدیک\n\n"
        "برای استفاده از این بخش باید موقعیت مکانی‌ت رو (فقط برای پیدا کردن "
        "افراد اطراف، نه نمایش آدرس دقیق) به اشتراک بذاری.\n"
        "موقعیت تو هیچ‌وقت به کاربر دیگه‌ای مستقیماً نشون داده نمی‌شه؛ فقط فاصله‌ی "
        "تقریبی محاسبه می‌شه."
    )
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=nearby_keyboard(has_location))
    else:
        await update.message.reply_text(text, reply_markup=nearby_keyboard(has_location))


async def request_location_share(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton("📍 اشتراک‌گذاری موقعیت من", request_location=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await query.message.reply_text(
        "لطفاً با دکمه‌ی زیر موقعیتت رو بفرست:", reply_markup=keyboard
    )


async def handle_location_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    location = update.message.location
    telegram_user = update.effective_user

    async with async_session() as session:
        user = await get_or_create_user(session, telegram_user.id)
        user.location = make_point(location.latitude, location.longitude)
        user.location_updated_at = datetime.utcnow()
        await session.commit()

    await update.message.reply_text(
        "✅ موقعیتت ثبت شد. حالا می‌تونی افراد نزدیکت رو ببینی.",
        reply_markup=main_reply_keyboard(),
    )
    await show_nearby_users(update, context)


async def delete_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    telegram_user = query.from_user

    async with async_session() as session:
        user = await get_or_create_user(session, telegram_user.id)
        user.location = None
        user.location_updated_at = None
        await session.commit()

    await query.edit_message_text("موقعیت تو حذف شد.", reply_markup=nearby_keyboard(has_location=False))


async def show_nearby_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    telegram_user = update.effective_user
    cutoff = datetime.utcnow() - timedelta(days=LOCATION_MAX_AGE_DAYS)

    async with async_session() as session:
        me = await get_or_create_user(session, telegram_user.id)
        if me.location is None:
            text = "اول باید موقعیتت رو ثبت کنی."
            if update.callback_query:
                await update.callback_query.answer()
                await update.callback_query.edit_message_text(text, reply_markup=nearby_keyboard(False))
            else:
                await update.message.reply_text(text, reply_markup=nearby_keyboard(False))
            return

        distance_col = ST_Distance(User.location, me.location).label("distance_m")
        stmt = (
            select(User, distance_col)
            .where(
                User.id != me.id,
                User.is_banned.is_(False),
                User.location.isnot(None),
                User.location_updated_at >= cutoff,
                ST_DWithin(User.location, me.location, NEARBY_RADIUS_METERS),
            )
            .order_by(distance_col.asc())
            .limit(NEARBY_RESULT_LIMIT)
        )
        result = await session.execute(stmt)
        nearby = result.all()  # list of (User, distance_m)

    if not nearby:
        text = "فعلاً کسی توی ۵۰ کیلومتری‌ت پیدا نشد. بعداً دوباره امتحان کن."
    else:
        lines = ["📍 نزدیک‌ترین کاربران به تو:\n"]
        for candidate, distance_m in nearby:
            name = candidate.display_name or "کاربر ناشناس"
            lines.append(f"• {name} — تقریباً {distance_m / 1000:.1f} کیلومتر")
        text = "\n".join(lines)

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, reply_markup=nearby_keyboard(True))
    else:
        await update.message.reply_text(text, reply_markup=nearby_keyboard(True))
