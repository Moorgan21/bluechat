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

"""هندلرهای «گزارش کاربر»، در دو مسیرِ کاملاً جدا:

۱. گزارشِ تک‌پیام (handle_message_report_reply/message_report_reason_callback):
   کاربر توی یه گفتگوی فعال روی پیامِ طرفِ مقابل ریپلای می‌کنه و می‌نویسه
   «گزارش»/«report»؛ فقط همون یک پیام گزارش می‌شه.
۲. گزارشِ کلِ گفتگو (start_report_after_chat/report_reason_callback):
   فقط بعد از پایانِ چت، کنارِ دکمه‌ی پاک‌کردنِ تاریخچه، و فقط تا ۲ دقیقه
   معتبره (redis_client.is_post_chat_window_active).

هر دو مسیر در نهایت یه Report تو Postgres ثبت می‌کنن و judge.py (با
DeepSeek) بر اساسِ تاریخچه‌ی متنیِ گفتگو قضاوتش می‌کنه.

اگه گزارش‌شده واقعاً مقصر باشه یه اخطار می‌گیره (با دلیل و شماره‌ی
اخطار، مثلاً «۱ از ۵»؛ اخطار پنجم خودکار بن می‌شه). اگه گزارش نادرست
باشه، این‌بار خودِ گزارش‌دهنده اخطار می‌گیره. اگه تاریخچه‌ی گفتگو قبلاً
پاک شده باشه، اصلاً قضاوتی ممکن نیست و کسی اخطار نمی‌گیره.
"""

import base64
import logging

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

import redis_client as rc
from db import Report, ReportReason, async_session
from keyboards import message_report_reason_keyboard, report_reason_keyboard
from verdict_notify import notify_chat_verdict, notify_profile_verdict

logger = logging.getLogger(__name__)


async def handle_message_report_reply(
    update: Update, context: ContextTypes.DEFAULT_TYPE, partner_id: int, replied_message
) -> None:
    """وقتی کاربر با ریپلای‌کردنِ «گزارش»/«report» روی پیامِ طرفِ مقابل
    (نه پیامِ خودش)، می‌خواد فقط همون یک پیام رو گزارش بده. گزارشِ کلِ
    گفتگو دیگه از اینجا ممکن نیست؛ فقط از دکمه‌ی «🚫 گزارش این گفتگو»
    بعدِ پایانِ چت (start_report_after_chat)."""
    reporter_id = update.effective_user.id

    if await rc.is_own_message(reporter_id, replied_message.message_id):
        await update.message.reply_text("فقط می‌تونی پیامِ طرفِ مقابل رو گزارش بدی، نه پیامِ خودتو.")
        return

    if replied_message.text:
        excerpt = replied_message.text
    elif replied_message.caption:
        excerpt = replied_message.caption
    else:
        excerpt = "(پیامِ غیرمتنی: عکس/ویس/ویدیو و امثالش)"

    session_id = await rc.get_session_id(reporter_id)
    token = await rc.store_message_report_context(reporter_id, partner_id, session_id, excerpt)
    keyboard = message_report_reason_keyboard(token)
    await update.message.reply_text("دلیلِ گزارشِ این پیام رو انتخاب کن:", reply_markup=keyboard)


async def message_report_reason_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    if query.data == "msgreport:cancel":
        await query.edit_message_text("گزارش لغو شد.")
        return

    # format: msgreport:reason:<code>:<token>
    _, _, reason_code, token = query.data.split(":")
    report_context = await rc.pop_message_report_context(token)
    if report_context is None:
        await query.edit_message_text("⚠️ این درخواستِ گزارش دیگه معتبر نیست (منقضی شده).")
        return

    reporter_id = query.from_user.id
    reported_id = report_context["reported_id"]
    session_id = report_context["session_id"]
    message_excerpt = report_context["message_excerpt"]

    async with async_session() as session:
        report = Report(
            session_id=session_id,
            reporter_id=reporter_id,
            reported_id=reported_id,
            reason=ReportReason(reason_code),
        )
        session.add(report)
        await session.commit()
        await session.refresh(report)
        report_id = report.id

    await query.edit_message_text(
        "✅ گزارشِ این پیام ثبت شد و در حال بررسی توسطِ سیستمِ قضاوتِ ماست... ⏳"
    )

    await rc.push_ai_job({
        "type": "chat_report",
        "report_id": report_id,
        "session_id": session_id,
        "reporter_id": reporter_id,
        "reported_id": reported_id,
        "reason": reason_code,
        "details": f"کاربر مشخصاً همین پیام رو گزارش کرده: {message_excerpt}",
    })


async def start_report_after_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """هندلر دکمه‌ی «🚫 گزارش این گفتگو» که بعد از پایانِ چت (کنارِ دکمه‌ی
    پاک‌کردنِ تاریخچه) نشون داده می‌شه. چون در این حالت دیگه rc.get_partner
    خالیه (چت تموم شده)، reported_id و session_id مستقیم از callback_data
    خونده می‌شن. این دکمه دقیقاً همون مهلتِ ۲دقیقه‌ایِ دکمه‌ی پاکسازیِ
    تاریخچه رو داره (redis_client.is_post_chat_window_active) — بعد از
    اون، تاریخچه‌ی متنیِ گفتگو خودکار پاک شده و دیگه چیزی برای قضاوتِ
    AI باقی نمونده."""
    query = update.callback_query
    await query.answer()

    _, reported_id_str, session_id_str = query.data.split(":")
    reported_id = int(reported_id_str)
    session_id = int(session_id_str) if session_id_str != "none" else None
    reporter_id = query.from_user.id

    if session_id is not None and not await rc.is_post_chat_window_active(session_id, reporter_id, reported_id):
        await query.message.reply_text("⚠️ این درخواست دیگه معتبر نیست (مهلتِ ۲دقیقه‌ای گذشته).")
        return

    keyboard = report_reason_keyboard(reported_id=reported_id, session_id=session_id)
    await query.message.reply_text("دلیل گزارش رو انتخاب کن:", reply_markup=keyboard)


async def report_reason_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    if query.data == "report:cancel":
        await query.edit_message_text("گزارش لغو شد.")
        return

    # format: report:reason:<code>:<reported_id>:<session_id|none>
    _, _, reason_code, reported_id_str, session_id_str = query.data.split(":")
    reporter_id = query.from_user.id
    reported_id = int(reported_id_str)
    session_id = int(session_id_str) if session_id_str != "none" else None

    async with async_session() as session:
        report = Report(
            session_id=session_id,
            reporter_id=reporter_id,
            reported_id=reported_id,
            reason=ReportReason(reason_code),
        )
        session.add(report)
        await session.commit()
        await session.refresh(report)
        report_id = report.id

    await query.edit_message_text(
        "✅ گزارش شما ثبت شد و در حال بررسی توسطِ سیستمِ قضاوتِ ماست... ⏳"
    )

    await rc.push_ai_job({
        "type": "chat_report",
        "report_id": report_id,
        "session_id": session_id,
        "reporter_id": reporter_id,
        "reported_id": reported_id,
        "reason": reason_code,
        "details": None,
    })


# گزارشِ پروفایل (عکس/نام/بیو)، جدا از گزارشِ رفتار در گفتگو
async def handle_profile_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """هندلر دکمه‌ی «🚩 گزارش پروفایل» زیر پروفایلِ طرف مقابل در چت.
    تمام مشخصاتِ پروفایل (عکس، نام، بیو) به Gemini Vision داده می‌شه؛
    اگه واقعاً محتوای نامناسب داشت، کاربر بلافاصله بلاک و گزارش‌دهنده
    ۵ سکه پاداش می‌گیره؛ اگه گزارش اشتباه بود، گزارش‌دهنده یک اخطار
    می‌گیره."""
    from db import ProfileReport, get_user_profile_snapshot
    from profile_judge import judge_profile_report

    query = update.callback_query
    await query.answer()

    reported_id = int(query.data.split(":", 1)[1])
    reporter_id = query.from_user.id

    if reported_id == reporter_id:
        await query.message.reply_text("نمی‌تونی پروفایلِ خودت رو گزارش بدی 🙂")
        return

    snapshot = await get_user_profile_snapshot(reported_id)
    if snapshot is None:
        await query.message.reply_text("پروفایلِ این کاربر در دسترس نیست.")
        return

    async with async_session() as session:
        profile_report = ProfileReport(reporter_id=reporter_id, reported_id=reported_id)
        session.add(profile_report)
        await session.commit()
        await session.refresh(profile_report)
        profile_report_id = profile_report.id

    await query.message.reply_text("🚩 گزارشِ پروفایل ثبت شد و در حال بررسی توسطِ سیستمِ قضاوتِ ماست... ⏳")

    image_b64 = None
    if snapshot.get("photo_file_id"):
        try:
            tg_file = await context.bot.get_file(snapshot["photo_file_id"])
            image_bytes = bytes(await tg_file.download_as_bytearray())
            image_b64 = base64.b64encode(image_bytes).decode()
        except TelegramError:
            logger.warning("امکانِ دانلودِ عکسِ پروفایل برای بررسیِ AI وجود نداشت.")

    await rc.push_ai_job({
        "type": "profile_report",
        "profile_report_id": profile_report_id,
        "reporter_id": reporter_id,
        "reported_id": reported_id,
        "snapshot": snapshot,
        "image_b64": image_b64,
    })


