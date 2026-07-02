"""
قضاوتِ AI برای گزارش‌های پروفایل (با Gemini Vision)
------------------------------------------------------
وقتی کاربری پروفایلِ یک نفر رو گزارش می‌ده (عکس/نام/بیو نامناسب)، این
ماژول تمام مشخصه‌های پروفایلِ گزارش‌شده — عکس، نام نمایشی، بیوگرافی —
رو به Gemini Vision می‌ده تا تشخیص بده واقعاً محتوای نامناسبی داره یا
نه.

چرا Gemini و نه DeepSeek؟ چون این قضاوت نیاز به تحلیلِ تصویر (عکس
پروفایل) داره و DeepSeek قابلیت vision نداره؛ برای بخش متنیِ گزارشِ
گفتگو (judge.py) از DeepSeek استفاده می‌کنیم چون توکنش ارزون‌تره، ولی
اینجا چاره‌ای جز Gemini نیست.

نتیجه:
    - guilty: پروفایل واقعاً محتوای نامناسب داره → کاربر بلافاصله بلاک
      می‌شه (is_banned=True، مستقل از شمارشِ ۵-تاییِ اخطار) و
      گزارش‌دهنده ۵ سکه پاداش می‌گیره.
    - dismissed: گزارش اشتباه بوده (پروفایل مشکلی نداره) → گزارش‌دهنده
      یک اخطار می‌گیره.

اصل احتیاط: در صورت هر خطایی (شبکه، پاسخ نامعتبر)، هیچ اقدامی (نه بن،
نه اخطار، نه پاداش) انجام نمی‌شه.
"""

import json
import logging
import os

from google import genai
from google.genai import types

from gemini_limiter import gemini_limiter
from db import (
    ProfileReport,
    ReportVerdict,
    add_warning,
    async_session,
    ban_user,
    grant_report_reward,
    update_profile_report_verdict,
)

logger = logging.getLogger(__name__)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = "gemini-2.5-flash"

PROFILE_REPORT_REWARD_COINS = 5

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        if not GEMINI_API_KEY:
            raise RuntimeError("متغیر محیطی GEMINI_API_KEY تنظیم نشده.")
        _client = genai.Client(api_key=GEMINI_API_KEY)
    return _client


PROFILE_JUDGE_PROMPT = """\
تو یک قاضیِ بی‌طرف برای بررسیِ گزارشِ پروفایلِ کاربران در یک ربات چت
ناشناس تلگرام هستی. یک کاربر، پروفایلِ کاربرِ دیگری رو گزارش داده.

مشخصاتِ پروفایلِ گزارش‌شده:
نام نمایشی: {display_name}
بیوگرافی: {bio}

بر اساسِ عکسِ پیوست‌شده (اگه وجود داشته باشه) و متنِ بالا، بررسی کن که
آیا این پروفایل واقعاً محتوای نامناسب داره؛ از جمله:
- عکسِ پروفایل: محتوای جنسی/برهنه، خشونت گرافیکی، نمادهای نفرت‌پراکنی،
  یا هر تصویرِ آزاردهنده‌ی دیگه.
- نام نمایشی یا بیوگرافی: کلماتِ توهین‌آمیز، تبلیغِ محتوای غیرقانونی،
  اطلاعاتِ تماسِ شخصی (که بر خلافِ سیاستِ ناشناس‌بودنِ رباته)، یا محتوای
  نامناسبِ دیگه.

اگه واقعاً پروفایل مشکل داره، verdict را "guilty" بذار. اگه پروفایل
عادی و بی‌خطره و گزارش بی‌اساس بوده، verdict را "dismissed" بذار. در
صورت تردید، "dismissed" بذار (اصل بر برائته، مگر مشکل واضح باشه).

فقط و فقط یک شیء JSON با این ساختار دقیق برگردون (بدون هیچ متن اضافه،
بدون Markdown، بدون بک‌تیک):
{{"verdict": "guilty" یا "dismissed", "reason_fa": "توضیح کوتاه و مشخص به فارسی (حداکثر ۲ جمله)"}}
"""


class ProfileJudgeResult:
    def __init__(self, verdict: str, reason_fa: str):
        self.verdict = verdict
        self.reason_fa = reason_fa


async def _run_gemini_profile_judge(
    display_name: str | None, bio: str | None, image_bytes: bytes | None
) -> ProfileJudgeResult | None:
    prompt = PROFILE_JUDGE_PROMPT.format(
        display_name=display_name or "(تنظیم‌نشده)", bio=bio or "(تنظیم‌نشده)"
    )

    contents: list = [prompt]
    if image_bytes:
        contents.insert(0, types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"))

    try:
        await gemini_limiter.acquire()
        client = _get_client()
        response = await client.aio.models.generate_content(
            model=GEMINI_MODEL,
            contents=contents,
            config=types.GenerateContentConfig(temperature=0, response_mime_type="application/json"),
        )
        raw_text = (response.text or "").strip()
        data = json.loads(raw_text)

        verdict = str(data.get("verdict", "")).strip()
        reason_fa = str(data.get("reason_fa", "")).strip()

        if verdict not in ("guilty", "dismissed") or not reason_fa:
            logger.warning("پاسخ نامعتبر از Gemini profile judge: %s", raw_text)
            return None

        return ProfileJudgeResult(verdict=verdict, reason_fa=reason_fa)

    except Exception:
        logger.exception("خطا در قضاوتِ AI برای گزارشِ پروفایل.")
        return None


async def judge_profile_report(
    profile_report_id: int,
    reporter_id: int,
    reported_id: int,
    profile_snapshot: dict,
    image_bytes: bytes | None,
) -> dict:
    """گزارشِ پروفایل رو بررسی می‌کنه: Gemini Vision رو صدا می‌زنه،
    نتیجه رو در ProfileReport ثبت می‌کنه، و بر اساس نتیجه یا بلاکِ
    فوری+پاداش سکه، یا اخطار به گزارش‌دهنده می‌ده."""
    result = await _run_gemini_profile_judge(
        profile_snapshot.get("display_name"), profile_snapshot.get("bio"), image_bytes
    )

    if result is None:
        return {"verdict": "pending"}

    if result.verdict == "guilty":
        await update_profile_report_verdict(profile_report_id, ReportVerdict.guilty, result.reason_fa)
        await ban_user(reported_id)
        new_balance = await grant_report_reward(reporter_id, PROFILE_REPORT_REWARD_COINS, None)
        return {
            "verdict": "guilty",
            "reason_fa": result.reason_fa,
            "reported_id": reported_id,
            "reporter_id": reporter_id,
            "reward_coins": PROFILE_REPORT_REWARD_COINS,
            "new_coin_balance": new_balance,
        }
    else:
        await update_profile_report_verdict(profile_report_id, ReportVerdict.dismissed, result.reason_fa)
        warning_number, auto_banned = await add_warning(
            reporter_id, "ثبت گزارشِ نادرست/بی‌اساس برای پروفایل", None
        )
        return {
            "verdict": "dismissed",
            "reason_fa": result.reason_fa,
            "warning_number": warning_number,
            "auto_banned": auto_banned,
            "reporter_id": reporter_id,
        }
