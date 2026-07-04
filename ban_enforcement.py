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

"""وقتی judge.py/profile_judge.py یه کاربر رو بن می‌کنه (چه با رسیدن به
۵ اخطار، چه بلافاصله برای گزارشِ پروفایل)، این ماژول اثرِ فوریِ اون بن
رو روی گفتگوی ۱به۱ یا اتاقِ چتِ فعلیِ همون کاربر اعمال می‌کنه. جدا از
verdict_notify.py نگه داشته شده چون اون فقط پیامِ متنیِ نتیجه‌ی قضاوت
رو می‌فرسته، نه خروجِ عملی از گفتگو/اتاق.

عمداً هیچ importی از handlers/ نداره تا worker.py (که فقط یه Bot خام
داره، نه Application/job_queue) بتونه مستقیم صداش بزنه."""

import logging
from datetime import datetime

from telegram import Bot
from telegram.error import TelegramError

import redis_client as rc
from db import ChatSession, async_session, remove_banned_user_from_room
from keyboards import main_reply_keyboard

logger = logging.getLogger(__name__)


async def enforce_ban(bot: Bot, user_id: int) -> None:
    """هر دو اثرِ ممکنِ یه بن رو اعمال می‌کنه؛ هرکدوم که مصداق نداشته
    باشه (کاربر نه توی چتِ ۱به۱ بوده نه عضوِ اتاقی) خودش بی‌سروصدا
    هیچ‌کاری نمی‌کنه."""
    await _close_active_1to1_chat(bot, user_id)
    await _remove_from_room(bot, user_id)


async def _close_active_1to1_chat(bot: Bot, user_id: int) -> None:
    partner_id = await rc.clear_partner(user_id)
    if partner_id is None:
        return

    session_id = await rc.get_session_id(user_id)
    if session_id is not None:
        async with async_session() as session:
            chat_session = await session.get(ChatSession, session_id)
            if chat_session is not None and chat_session.ended_at is None:
                chat_session.ended_at = datetime.utcnow()
                chat_session.ended_by = user_id
                await session.commit()
        await rc.clear_session_id(user_id)
        await rc.clear_session_id(partner_id)

    try:
        await bot.send_message(
            partner_id,
            "🚫 طرفِ مقابل توسطِ سیستم بن شد و چت به‌صورتِ خودکار بسته شد.",
            reply_markup=main_reply_keyboard(),
        )
    except TelegramError:
        logger.warning("امکانِ اطلاع‌رسانیِ پایانِ چت به partner_id=%s وجود نداشت.", partner_id)


async def _remove_from_room(bot: Bot, user_id: int) -> None:
    result = await remove_banned_user_from_room(user_id)
    if result is None:
        return

    await rc.clear_active_room(user_id)
    outcome = result["outcome"]
    remaining_member_ids = result["remaining_member_ids"]

    if outcome == "owner_banned":
        text = "🚫 سازنده‌ی این اتاق توسطِ سیستم بن شد و اتاق بسته شد."
    elif outcome == "member_banned_last":
        text = "🚫 یه عضو توسطِ سیستم بن شد؛ چون آخرین عضوِ اتاق بود، اتاق بسته شد."
    else:
        text = "🚫 یه عضو به‌دلیلِ بن‌شدن، از اتاق اخراج شد."

    for member_id in remaining_member_ids:
        try:
            await bot.send_message(member_id, text)
        except TelegramError:
            continue
