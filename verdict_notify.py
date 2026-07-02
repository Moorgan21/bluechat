"""
توابع اطلاع‌رسانی نتیجه‌ی قضاوت AI — مشترک بین report.py و worker.py
"""
import logging

from telegram import Bot
from telegram.error import TelegramError

logger = logging.getLogger(__name__)


async def notify_chat_verdict(bot: Bot, reporter_id: int, reported_id: int, result: dict) -> None:
    verdict = result.get("verdict")

    if verdict == "no_history":
        try:
            await bot.send_message(
                reporter_id,
                "⚠️ متاسفانه تاریخچه‌ی این گفتگو در دسترس نیست (پاک شده یا پیامی ثبت نشده بود)، "
                "پس امکانِ بررسیِ این گزارش وجود نداشت.",
            )
        except TelegramError:
            pass
        return

    if verdict == "pending":
        try:
            await bot.send_message(
                reporter_id,
                "⚠️ بررسیِ خودکارِ این گزارش با خطا مواجه شد. گزارشت ثبت شده و بعداً بررسی می‌شه.",
            )
        except TelegramError:
            pass
        return

    if verdict == "guilty":
        warning_number = result["reported_warning_number"]
        auto_banned = result["reported_auto_banned"]
        reason_fa = result["reason_fa"]
        reward_coins = result.get("reward_coins", 0)
        new_balance = result.get("new_coin_balance")

        guilty_text = (
            f"⚖️ طبق بررسیِ قاضی، شما اخطار گرفتید ({warning_number} از ۵).\n\n"
            f"دلیل: {reason_fa}"
        )
        if auto_banned:
            guilty_text += "\n\n🚫 به دلیل رسیدن به ۵ اخطار، حساب شما به‌صورت خودکار مسدود شد."
        try:
            await bot.send_message(reported_id, guilty_text)
        except TelegramError:
            logger.warning("امکان اطلاع‌رسانیِ اخطار به reported_id=%s وجود نداشت.", reported_id)

        reporter_text = "✅ گزارش شما بررسی و تاییدِ صحت شد."
        if reward_coins:
            reporter_text += f"\n💰 به‌عنوان پاداش، {reward_coins} سکه به حسابت اضافه شد."
            if new_balance is not None:
                reporter_text += f" (موجودی فعلی: {new_balance})"
        reporter_text += "\nاز کمکت به امنیتِ جامعه‌ی بلوچت ممنونیم 🙏"

        if result.get("reporter_also_guilty"):
            reporter_text += (
                f"\n\n⚠️ ضمناً طبق بررسی، خودت هم در این گفتگو رفتار نامناسبی داشتی و بابتش "
                f"اخطار گرفتی ({result['reporter_also_guilty_warning_number']} از ۵).\n"
                f"دلیل: {result['reporter_also_guilty_reason_fa']}"
            )
            if result.get("reporter_also_guilty_auto_banned"):
                reporter_text += "\n🚫 به دلیل رسیدن به ۵ اخطار، حساب شما به‌صورت خودکار مسدود شد."

        try:
            await bot.send_message(reporter_id, reporter_text)
        except TelegramError:
            pass
        return

    if verdict == "dismissed":
        warning_number = result["reporter_warning_number"]
        auto_banned = result["reporter_auto_banned"]
        reason_fa = result["reason_fa"]

        dismissed_text = (
            f"⚖️ طبق بررسیِ قاضی، گزارشِ شما نادرست/بی‌اساس تشخیص داده شد و یک اخطار گرفتید "
            f"({warning_number} از ۵).\n\nدلیل: {reason_fa}"
        )
        if auto_banned:
            dismissed_text += "\n\n🚫 به دلیل رسیدن به ۵ اخطار، حساب شما به‌صورت خودکار مسدود شد."

        if result.get("reporter_also_guilty"):
            dismissed_text += (
                f"\n\n⚠️ ضمناً طبق بررسی، بابتِ رفتارِ نامناسبِ خودت در همین گفتگو هم یک اخطارِ "
                f"دیگه گرفتی ({result['reporter_also_guilty_warning_number']} از ۵).\n"
                f"دلیل: {result['reporter_also_guilty_reason_fa']}"
            )
            if result.get("reporter_also_guilty_auto_banned"):
                dismissed_text += "\n🚫 به دلیل رسیدن به ۵ اخطار، حساب شما به‌صورت خودکار مسدود شد."

        try:
            await bot.send_message(reporter_id, dismissed_text)
        except TelegramError:
            logger.warning("امکان اطلاع‌رسانیِ اخطار به reporter_id=%s وجود نداشت.", reporter_id)


async def notify_profile_verdict(bot: Bot, reporter_id: int, reported_id: int, result: dict) -> None:
    verdict = result.get("verdict")

    if verdict == "pending":
        try:
            await bot.send_message(
                reporter_id,
                "⚠️ بررسیِ خودکارِ این گزارش با خطا مواجه شد. گزارشت ثبت شده و بعداً بررسی می‌شه.",
            )
        except TelegramError:
            pass
        return

    if verdict == "guilty":
        reason_fa = result["reason_fa"]
        reward_coins = result.get("reward_coins", 0)
        new_balance = result.get("new_coin_balance")

        try:
            await bot.send_message(
                reported_id,
                f"🚫 پروفایلِ شما بر اساسِ بررسیِ قاضی محتوای نامناسب داشت و حسابتون مسدود شد.\n\n"
                f"دلیل: {reason_fa}",
            )
        except TelegramError:
            logger.warning("امکان اطلاع‌رسانیِ بلاک به reported_id=%s وجود نداشت.", reported_id)

        reporter_text = "✅ گزارشِ پروفایلِ شما بررسی و تاییدِ صحت شد؛ کاربر بلاک شد."
        if reward_coins:
            reporter_text += f"\n💰 به‌عنوان پاداش، {reward_coins} سکه به حسابت اضافه شد."
            if new_balance is not None:
                reporter_text += f" (موجودی فعلی: {new_balance})"

        try:
            await bot.send_message(reporter_id, reporter_text)
        except TelegramError:
            pass
        return

    if verdict == "dismissed":
        warning_number = result["warning_number"]
        auto_banned = result["auto_banned"]
        reason_fa = result["reason_fa"]

        dismissed_text = (
            f"⚖️ طبق بررسیِ قاضی، گزارشِ پروفایلِ شما نادرست/بی‌اساس تشخیص داده شد و یک اخطار "
            f"گرفتید ({warning_number} از ۵).\n\nدلیل: {reason_fa}"
        )
        if auto_banned:
            dismissed_text += "\n\n🚫 به دلیل رسیدن به ۵ اخطار، حساب شما به‌صورت خودکار مسدود شد."

        try:
            await bot.send_message(reporter_id, dismissed_text)
        except TelegramError:
            logger.warning("امکان اطلاع‌رسانیِ اخطار به reporter_id=%s وجود نداشت.", reporter_id)
