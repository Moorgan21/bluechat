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
هندلرهای بخش سکه، دعوت دوستان (referral)، و لینک ناشناس اختصاصی.
"""

import os

from sqlalchemy import select
from telegram import Update
from telegram.ext import ContextTypes

from db import CoinTransaction, async_session, get_or_create_user
from keyboards import coins_keyboard

BOT_USERNAME = os.environ.get("BOT_USERNAME", "YourBotUsername")


async def show_coins(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    telegram_user = update.effective_user
    async with async_session() as session:
        user = await get_or_create_user(
            session, telegram_user.id, telegram_user.username, telegram_user.first_name
        )
        coins = user.coins

    text = (
        f"💰 موجودی سکه‌ی شما: {coins}\n\n"
        "سکه چیه؟\n"
        "با دعوت دوستات به ازای هر نفر ۵ سکه هدیه می‌گیری. سکه‌ها در آینده "
        "برای فیلترهای پیشرفته‌ی جستجو و قابلیت‌های ویژه قابل استفاده می‌شن."
    )
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=coins_keyboard())
    else:
        await update.message.reply_text(text, reply_markup=coins_keyboard())


async def show_coin_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    telegram_user = query.from_user

    async with async_session() as session:
        result = await session.execute(
            select(CoinTransaction)
            .where(CoinTransaction.user_id == telegram_user.id)
            .order_by(CoinTransaction.created_at.desc())
            .limit(15)
        )
        transactions = result.scalars().all()

    if not transactions:
        text = "هنوز هیچ تراکنشی ثبت نشده."
    else:
        reason_labels = {
            "referral_bonus": "پاداش دعوت دوست",
            "failed_chat_refund": "بازگشت وجه (چت ناموفق)",
        }
        lines = ["📜 آخرین تراکنش‌های سکه:\n"]
        for tx in transactions:
            sign = "+" if tx.amount >= 0 else ""
            label = reason_labels.get(tx.reason, tx.reason)
            lines.append(f"{sign}{tx.amount} — {label} ({tx.created_at.strftime('%Y-%m-%d')})")
        text = "\n".join(lines)

    await query.edit_message_text(text, reply_markup=coins_keyboard())


async def show_invite_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    telegram_user = update.effective_user
    async with async_session() as session:
        user = await get_or_create_user(
            session, telegram_user.id, telegram_user.username, telegram_user.first_name
        )
        code = user.referral_code

    link = f"https://t.me/{BOT_USERNAME}?start=ref_{code}"
    text = (
        "🔗 لینک دعوت اختصاصی شما:\n"
        f"{link}\n\n"
        "به ازای هر دوستی که با این لینک وارد ربات بشه، ۵ سکه هدیه می‌گیری!"
    )
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(text)
    else:
        await update.message.reply_text(text)


async def show_anon_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """لینک ناشناس اختصاصی: هر کسی این لینک رو باز کنه می‌تونه یه پیامِ
    ناشناسِ نوتیفی برای صاحب لینک بفرسته (بدون ورود به یک چت باز و
    دائمی). چند نفر می‌تونن هم‌زمان پیام بفرستن؛ صاحب لینک زیر هر پیام
    یه دکمه‌ی «پاسخ دادن» می‌بینه و می‌تونه دقیقاً به همون فرستنده جواب
    بده. منطق کامل در handlers/anon_note.py پیاده شده."""
    telegram_user = update.effective_user
    async with async_session() as session:
        user = await get_or_create_user(
            session, telegram_user.id, telegram_user.username, telegram_user.first_name
        )
        code = user.referral_code

    link = f"https://t.me/{BOT_USERNAME}?start=direct_{code}"
    text = (
        "🥷 لینک ناشناس اختصاصی شما:\n"
        f"{link}\n\n"
        "هر کسی این لینک رو باز کنه می‌تونه برات پیام ناشناس بفرسته (مثل یه نوتیف)، "
        "بدون اینکه یک چت باز و مداوم شکل بگیره. چند نفر می‌تونن هم‌زمان پیام بفرستن؛ "
        "زیر هر پیام یه دکمه‌ی «↩️ پاسخ دادن» می‌بینی که می‌تونی باهاش دقیقاً به همون "
        "فرستنده جواب بدی.\n\n"
        f"🪪 شناسه‌ی پروفایلِ عمومیت هم همینه: /user_{code}\n"
        "هرکسی این رو بفرسته، می‌تونه پروفایلت رو ببینه و درخواستِ چت بده (مگر اینکه "
        "حالتِ سایلنت رو با /silent فعال کرده باشی)."
    )
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(text)
    else:
        await update.message.reply_text(text)
