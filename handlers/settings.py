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

from telegram import Update
from telegram.ext import ContextTypes

from db import async_session, User, update_next_gender_pref
from keyboards import settings_keyboard

_PREF_LABELS = {"female": "👩 دختر", "male": "👨 پسر", "any": "🤷 فرقی نمی‌کنه"}


def _settings_text(pref: str | None) -> str:
    current = _PREF_LABELS.get(pref or "", "تنظیم نشده")
    return (
        "⚙️ <b>تنظیمات</b>\n\n"
        "🔍 <b>جنسیت مورد نظر برای چت ناشناس:</b>\n"
        f"وضعیت فعلی: {current}\n\n"
        "با انتخاب یکی از گزینه‌ها، همه‌ی دفعات بعدی چت ناشناس بدون سؤال "
        "مستقیم با همین فیلتر شروع می‌شه."
    )


async def show_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    async with async_session() as session:
        user = await session.get(User, user_id)
    pref = user.next_gender_pref if user else None
    text = _settings_text(pref)
    markup = settings_keyboard(pref)
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
    else:
        await update.message.reply_text(text, reply_markup=markup, parse_mode="HTML")


async def handle_settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """هندلر کالبک‌های settings:next_gender:<value>"""
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":", 2)
    if len(parts) != 3 or parts[1] != "next_gender":
        return
    value = parts[2]  # "male" | "female" | "any" — "any" ذخیره می‌شه (نه None) تا از «هنوز تنظیم نشده» متمایز بشه
    await update_next_gender_pref(query.from_user.id, value)
    # نمایش مجدد با مقدار جدید
    async with async_session() as session:
        user = await session.get(User, query.from_user.id)
    new_pref = user.next_gender_pref if user else value
    await query.edit_message_text(
        _settings_text(new_pref), reply_markup=settings_keyboard(new_pref), parse_mode="HTML"
    )
