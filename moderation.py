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
بررسی محتوای تصویر با Gemini Vision API
-----------------------------------------
برای جلوگیری از ثبت عکس‌های نامناسب/مستهجن به‌عنوان عکس پروفایل.

نیازمندی‌ها:
    pip install google-genai

متغیر محیطی لازم:
    export GEMINI_API_KEY="کلید API از https://aistudio.google.com/apikey"

نکته‌ی مهم درباره‌ی دقت:
    این بررسی یک لایه‌ی کمکی‌ست، نه یک سیستم ۱۰۰٪ خطاناپذیر. مدل ممکنه
    گاهی false positive (رد کردن عکس سالم) یا false negative (تایید
    عکس نامناسب) بده. برای یک پلتفرم واقعی با ترافیک بالا، بهتره این
    لایه رو با گزارش‌دهی کاربران (که از قبل در handlers/report.py
    داری) و بازبینی انسانی دوره‌ای تکمیل کنی، نه صرفاً به آن تکیه کنی.

نحوه‌ی کار:
    عکس به‌صورت بایت به مدل multimodal Gemini داده می‌شه و از مدل
    خواسته می‌شه فقط یک خروجی JSON با ساختار مشخص برگردونه (safe/unsafe
    + دلیل). اگه به هر دلیلی (خطای شبکه، پاسخ نامعتبر، و...) ارزیابی
    شکست بخوره، به‌صورت محافظه‌کارانه عکس رد می‌شه (fail-closed) تا
    محتوای بررسی‌نشده منتشر نشه.
"""

import json
import logging
import os

from google import genai
from google.genai import types

from gemini_limiter import gemini_limiter

logger = logging.getLogger(__name__)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = "gemini-2.5-flash"

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        if not GEMINI_API_KEY:
            raise RuntimeError("متغیر محیطی GEMINI_API_KEY تنظیم نشده.")
        _client = genai.Client(api_key=GEMINI_API_KEY)
    return _client


MODERATION_PROMPT = """\
تو یک سیستم بررسی محتوای تصویر پروفایل برای یک اپلیکیشن چت هستی.
این تصویر قراره به‌عنوان عکس پروفایل عمومی یک کاربر در یک ربات چت
ناشناس نمایش داده بشه (کاربران بزرگسال، پلتفرم عمومی).

تصویر رو از نظر موارد زیر بررسی کن:
- محتوای برهنه یا جنسی صریح (نیمه‌برهنه، برهنه، ژست جنسی)
- خشونت گرافیکی یا محتوای آزاردهنده
- تصاویر کودکان در ژست‌های نامناسب یا هر محتوای مرتبط با سوءاستفاده از کودکان
- نمادهای نفرت‌پراکنی یا افراطی‌گری

فقط و فقط یک شیء JSON با این ساختار دقیق برگردون (بدون هیچ متن اضافه،
بدون Markdown، بدون بک‌تیک):
{"safe": true یا false, "category": "none" یا یکی از "sexual", "violence", "csam", "hate", "other", "reason": "توضیح خیلی کوتاه یک‌خطی به فارسی"}

اگه تصویر یک عکس عادی و بی‌خطر (چهره، منظره، حیوان، کارتون معمولی و
غیره) است، safe باید true باشه.
"""


class ModerationResult:
    def __init__(self, safe: bool, category: str, reason: str):
        self.safe = safe
        self.category = category
        self.reason = reason


async def check_image_safety(image_bytes: bytes, mime_type: str = "image/jpeg") -> ModerationResult:
    """عکس رو به Gemini Vision می‌فرسته و نتیجه‌ی ایمن/نامناسب‌بودن رو برمی‌گردونه.
    در صورت هر خطایی، fail-closed عمل می‌کنه (یعنی عکس را نامناسب فرض می‌کنه)."""
    try:
        await gemini_limiter.acquire()
        client = _get_client()
        response = await client.aio.models.generate_content(
            model=GEMINI_MODEL,
            contents=[
                types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                MODERATION_PROMPT,
            ],
            config=types.GenerateContentConfig(
                temperature=0,
                response_mime_type="application/json",
            ),
        )
        raw_text = (response.text or "").strip()
        data = json.loads(raw_text)

        safe = bool(data.get("safe", False))
        category = str(data.get("category", "other"))
        reason = str(data.get("reason", ""))
        return ModerationResult(safe=safe, category=category, reason=reason)

    except Exception:
        logger.exception("خطا در بررسی محتوای تصویر با Gemini Vision؛ به‌صورت محافظه‌کارانه رد می‌شه.")
        return ModerationResult(
            safe=False, category="other", reason="خطا در بررسی خودکار؛ عکس به‌صورت احتیاطی رد شد."
        )
