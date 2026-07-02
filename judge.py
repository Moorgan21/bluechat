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
قضاوتِ AI برای گزارش‌های کاربران (با DeepSeek، متنی)
------------------------------------------------------
وقتی کاربری گزارش می‌ده، این ماژول تاریخچه‌ی متنیِ همون گفتگو (اگه هنوز
در Postgres موجود باشه؛ یعنی هیچ‌کدوم از دو طرف تاریخچه رو پاک نکرده
باشن) رو به DeepSeek می‌ده و ازش می‌خواد بر اساس دلیلِ گزارش، قضاوت کنه.

چرا DeepSeek به‌جای Gemini؟ برای بررسیِ متنیِ گزارش‌ها (که حجم بالایی
داره)، هزینه‌ی توکنِ DeepSeek به‌مراتب پایین‌تره. Gemini همچنان برای
moderation تصویریِ عکس پروفایل (moderation.py) و قضاوتِ گزارشِ پروفایل
(profile_judge.py) استفاده می‌شه، چون اون‌ها نیاز به تحلیل تصویر دارن
و DeepSeek قابلیت vision نداره.

نیازمندی‌ها:
    pip install openai   (API دیپ‌سیک سازگار با فرمت OpenAI است)

متغیر محیطی لازم:
    export DEEPSEEK_API_KEY="کلید API از https://platform.deepseek.com"

قضاوت شاملِ دو تصمیمِ مستقله:
    ۱) verdict درباره‌ی REPORTED (کاربرِ گزارش‌شده):
       - guilty: واقعاً طبق تاریخچه مقصر بوده → اخطار می‌گیره، و
         گزارش‌دهنده ۵ سکه پاداش می‌گیره.
       - dismissed: گزارش بی‌اساس/نادرست بوده → گزارش‌دهنده اخطار می‌گیره.
    ۲) reporter_also_guilty درباره‌ی REPORTER (کاربرِ گزارش‌دهنده):
       اگه true باشه، یعنی خودِ گزارش‌دهنده هم در همون گفتگو رفتارِ
       نامناسبِ مشابه (یا مرتبط با همون دلیل) داشته — مثلاً هر دو طرف
       به هم فحش داده‌ن، یا گزارش‌دهنده داره تخلفِ خودش رو دروغ به طرفِ
       مقابل نسبت می‌ده. در این حالت، صرف‌نظر از verdict، به REPORTER هم
       جداگانه یه اخطار داده می‌شه (مستقل از اخطارِ dismissed).
    - no_history: تاریخچه پاک شده یا پیامی برای بررسی نبود → هیچ اخطار/
      پاداشی داده نمی‌شه (چون قابل بررسی نیست)

اصل احتیاط: اگه DeepSeek پاسخ نامعتبر بده یا خطای شبکه/API رخ بده، به
هیچ‌کدوم از دو طرف اخطار/پاداش داده نمی‌شه (fail-safe، چون تصمیمِ
اشتباه به یه کاربر بی‌گناه آسیب واقعی داره).
"""

import json
import logging
import os

from openai import AsyncOpenAI

from db import (
    ReportVerdict,
    add_warning,
    get_session_transcript,
    grant_report_reward,
    update_report_verdict,
)

logger = logging.getLogger(__name__)

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-chat"

REPORT_REWARD_COINS = 5

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        if not DEEPSEEK_API_KEY:
            raise RuntimeError("متغیر محیطی DEEPSEEK_API_KEY تنظیم نشده.")
        _client = AsyncOpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
    return _client


REASON_LABELS_FA = {
    "spam": "اسپم / تبلیغات",
    "scam": "کلاهبرداری",
    "abuse": "توهین / آزار",
    "sexual": "محتوای جنسی",
    "fake_profile": "پروفایل جعلی",
    "other": "سایر موارد",
}

JUDGE_SYSTEM_PROMPT = (
    "تو یک قاضیِ بی‌طرف برای یک ربات چت ناشناس تلگرام هستی. فقط و فقط با "
    "یک شیء JSON خالص پاسخ بده، بدون هیچ متن اضافه، بدون Markdown، بدون "
    "بک‌تیک."
)

JUDGE_PROMPT_TEMPLATE = """\
وظیفه‌ت اینه که بر اساس متنِ کامل یک گفتگو و دلیلِ گزارش، تصمیم بگیری
که آیا کاربرِ گزارش‌شده (REPORTED) واقعاً تخلف کرده یا نه، و همچنین
مستقل از اون، آیا خودِ کاربرِ گزارش‌دهنده (REPORTER) هم در همین گفتگو
رفتار نامناسبی داشته یا نه (مثلاً هر دو طرف به هم فحش داده‌ن، یا
REPORTER داره تخلفِ خودش رو به دروغ به REPORTED نسبت می‌ده).

دلیلِ گزارش: {reason_label}
{details_line}

کاربرِ گزارش‌دهنده در این متن به‌عنوان "REPORTER" و کاربرِ گزارش‌شده
به‌عنوان "REPORTED" مشخص شده.

متنِ کامل گفتگو:
{transcript}

بر اساس این متن، دو تصمیمِ کاملاً مستقل بگیر:

۱) verdict (درباره‌ی REPORTED):
- اگه واقعاً REPORTED طبق دلیلِ گزارش تخلف کرده، "guilty" بذار.
- اگه گفتگو نشون می‌ده REPORTED هیچ تخلفی نکرده (گزارش بی‌اساس یا
  کاملاً غلط بوده)، "dismissed" بذار.
- اگه متن ناکافیه، "dismissed" بذار (اصل بر برائته).

۲) reporter_also_guilty (درباره‌ی REPORTER — کاملاً مستقل از تصمیمِ اول):
- true بذار اگه در همین گفتگو، خودِ REPORTER هم رفتارِ نامناسبی داشته
  (مثلاً خودش هم توهین کرده، شروع‌کننده‌ی درگیری بوده، یا در حالِ
  گزارشِ دروغینِ رفتارِ خودش به‌نامِ REPORTED است). این می‌تونه true
  باشه حتی اگه verdict هم "guilty" باشه (یعنی هر دو طرف مقصر بودن).
- false بذار اگه REPORTER در این گفتگو رفتارِ نامناسبی نداشته.

فقط با این ساختار دقیق JSON پاسخ بده:
{{"verdict": "guilty" یا "dismissed", "reason_fa": "توضیح کوتاه درباره‌ی REPORTED (حداکثر ۲ جمله)", "reporter_also_guilty": true یا false, "reporter_reason_fa": "اگه reporter_also_guilty=true، دلیلِ کوتاه؛ وگرنه رشته‌ی خالی"}}
"""


class JudgeResult:
    def __init__(self, verdict: str, reason_fa: str, reporter_also_guilty: bool, reporter_reason_fa: str):
        self.verdict = verdict  # "guilty" | "dismissed"  (درباره‌ی REPORTED)
        self.reason_fa = reason_fa
        self.reporter_also_guilty = reporter_also_guilty  # آیا خودِ REPORTER هم مقصر بوده
        self.reporter_reason_fa = reporter_reason_fa


def _build_transcript_text(messages: list[dict], reporter_id: int, reported_id: int) -> str:
    lines = []
    for m in messages:
        role = "REPORTER" if m["sender_id"] == reporter_id else "REPORTED"
        if m["content_type"] == "text" and m["content"]:
            lines.append(f"[{role}]: {m['content']}")
        else:
            lines.append(f"[{role}]: (ارسال {m['content_type']})")
    return "\n".join(lines) if lines else "(هیچ پیام متنی‌ای در این گفتگو ثبت نشده)"


async def _run_deepseek_judge(reason_label: str, details: str | None, transcript: str) -> JudgeResult | None:
    """پرامپت رو به DeepSeek می‌ده و پاسخ رو parse می‌کنه. در صورت هر
    خطایی None برمی‌گردونه (یعنی قضاوت انجام نشد، نه اینکه مقصر تشخیص
    داده شد)."""
    details_line = f"توضیحاتِ اضافیِ گزارش‌دهنده: {details}" if details else ""
    user_prompt = JUDGE_PROMPT_TEMPLATE.format(
        reason_label=reason_label, details_line=details_line, transcript=transcript
    )

    try:
        client = _get_client()
        response = await client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
        raw_text = (response.choices[0].message.content or "").strip()
        data = json.loads(raw_text)

        verdict = str(data.get("verdict", "")).strip()
        reason_fa = str(data.get("reason_fa", "")).strip()
        reporter_also_guilty = bool(data.get("reporter_also_guilty", False))
        reporter_reason_fa = str(data.get("reporter_reason_fa", "")).strip()

        if verdict not in ("guilty", "dismissed") or not reason_fa:
            logger.warning("پاسخ نامعتبر از DeepSeek judge: %s", raw_text)
            return None

        return JudgeResult(
            verdict=verdict,
            reason_fa=reason_fa,
            reporter_also_guilty=reporter_also_guilty,
            reporter_reason_fa=reporter_reason_fa,
        )

    except Exception:
        logger.exception("خطا در قضاوتِ AI برای گزارش.")
        return None


async def judge_report(report_id: int, session_id: int | None, reporter_id: int, reported_id: int, reason: str, details: str | None) -> dict:
    """گزارش رو بررسی می‌کنه: تاریخچه رو می‌گیره، DeepSeek رو صدا می‌زنه،
    نتیجه رو در Report ثبت می‌کنه، و بر اساسِ دو تصمیمِ مستقل (verdict و
    reporter_also_guilty) اخطار/پاداشِ لازم رو می‌ده. خروجی یه dict شامل
    نتیجه‌ی نهایی برای اطلاع‌رسانی به کاربرهاست."""
    reason_label = REASON_LABELS_FA.get(reason, reason)

    if session_id is None:
        await update_report_verdict(report_id, ReportVerdict.no_history, "سشنِ این گزارش یافت نشد.")
        return {"verdict": "no_history"}

    messages = await get_session_transcript(session_id)
    if messages is None or not messages:
        await update_report_verdict(
            report_id, ReportVerdict.no_history,
            "تاریخچه‌ی این گفتگو پاک شده بود یا پیامی برای بررسی وجود نداشت."
        )
        return {"verdict": "no_history"}

    transcript = _build_transcript_text(messages, reporter_id, reported_id)
    result = await _run_deepseek_judge(reason_label, details, transcript)

    if result is None:
        # قضاوت به هر دلیلی ممکن نشد؛ به‌صورت محافظه‌کارانه هیچ اخطار/
        # پاداشی نمی‌دیم و گزارش رو در حالت pending نگه می‌داریم.
        return {"verdict": "pending"}

    await update_report_verdict(report_id, ReportVerdict(result.verdict), result.reason_fa)

    output: dict = {
        "verdict": result.verdict,
        "reason_fa": result.reason_fa,
        "reporter_id": reporter_id,
        "reported_id": reported_id,
    }

    if result.verdict == "guilty":
        # REPORTED واقعاً مقصر بوده → اخطار برای REPORTED + پاداش برای REPORTER
        warning_number, auto_banned = await add_warning(reported_id, result.reason_fa, report_id)
        new_coin_balance = await grant_report_reward(reporter_id, REPORT_REWARD_COINS, report_id)
        output.update(
            {
                "reported_warning_number": warning_number,
                "reported_auto_banned": auto_banned,
                "reward_coins": REPORT_REWARD_COINS,
                "new_coin_balance": new_coin_balance,
            }
        )
    else:
        # گزارش نادرست/بی‌اساس بوده → اخطار برای REPORTER (به‌خاطرِ گزارشِ غلط)
        warning_number, auto_banned = await add_warning(
            reporter_id, "ثبت گزارشِ نادرست/بی‌اساس", report_id
        )
        output.update(
            {
                "reporter_warning_number": warning_number,
                "reporter_auto_banned": auto_banned,
            }
        )

    # مستقل از verdict بالا: اگه AI تشخیص داده که خودِ REPORTER هم در
    # همین گفتگو رفتارِ نامناسبی داشته (مثلاً هر دو طرف فحش داده‌ن)،
    # یه اخطارِ جداگانه هم به REPORTER می‌دیم — این باعث می‌شه اگه
    # REPORTER خودش فحش داده و بعد REPORTED رو گزارش کرده، جدا از
    # نتیجه‌ی گزارش، بابتِ فحش‌دادنِ خودش هم مسئول شناخته بشه.
    if result.reporter_also_guilty and result.reporter_reason_fa:
        extra_warning_number, extra_auto_banned = await add_warning(
            reporter_id, result.reporter_reason_fa, report_id
        )
        output["reporter_also_guilty"] = True
        output["reporter_also_guilty_reason_fa"] = result.reporter_reason_fa
        output["reporter_also_guilty_warning_number"] = extra_warning_number
        output["reporter_also_guilty_auto_banned"] = extra_auto_banned
    else:
        output["reporter_also_guilty"] = False

    return output
