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

"""کیبوردهای شیشه‌ای (inline) و پایین‌صفحه (reply) رباتِ بلو چت."""

import json
import os

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

BOT_USERNAME = os.environ.get("BOT_USERNAME", "")

_CITIES_PER_PAGE = 24
try:
    _cities_path = os.path.join(os.path.dirname(__file__), "iran_cities.json")
    with open(_cities_path, encoding="utf-8") as _f:
        IRAN_CITIES: dict[str, list[str]] = json.load(_f)
except Exception:
    IRAN_CITIES = {}


def main_reply_keyboard() -> ReplyKeyboardMarkup:
    """کیبورد پایین صفحه؛ فقط وقتی نشون داده می‌شه که کاربر توی گفتگوی فعال نباشه."""
    keyboard = [
        [KeyboardButton("💬 وصل کن به یه ناشناس!")],
        [KeyboardButton("💬 جستجوی کاربران 🔮"), KeyboardButton("📍 افراد نزدیک 🛰")],
        [KeyboardButton("💰 سکه"), KeyboardButton("👤 پروفایل"), KeyboardButton("⚙️ تنظیمات"), KeyboardButton("🤔 راهنما")],
        [KeyboardButton("🔗 معرفی به دوستان (سکه رایگان)")],
        [KeyboardButton("🥷 لینک ناشناس من")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def in_chat_reply_keyboard(secure: bool = False) -> ReplyKeyboardMarkup:
    """کیبورد پایین صفحه حین یک گفتگوی فعال."""
    secure_label = "🔒 چت امن (فعال)" if secure else "🔒 چت امن (غیرفعال)"
    keyboard = [
        [KeyboardButton("👤 مشاهده پروفایل طرف مقابل"), KeyboardButton("⛔️ پایان چت")],
        [KeyboardButton(secure_label)],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def profile_inline_keyboard(is_own_profile: bool = True, reported_id: int | None = None) -> InlineKeyboardMarkup:
    if is_own_profile:
        rows = [
            [InlineKeyboardButton("🖼 عکس پروفایل", callback_data="profile:edit_photo")],
            [
                InlineKeyboardButton("✏️ نام نمایشی", callback_data="profile:edit_name"),
                InlineKeyboardButton("📝 بیوگرافی", callback_data="profile:edit_bio"),
            ],
            [
                InlineKeyboardButton("⚧ جنسیت", callback_data="profile:edit_gender"),
                InlineKeyboardButton("🎂 سن", callback_data="profile:edit_age"),
            ],
            [
                InlineKeyboardButton("🗺 استان", callback_data="profile:edit_province"),
                InlineKeyboardButton("🏙 شهر", callback_data="profile:edit_city"),
            ],
            [InlineKeyboardButton("😠 تنظیماتِ واکنش", callback_data="reactsettings:open")],
            [InlineKeyboardButton("🔙 بازگشت به منو", callback_data="menu:main")],
        ]
    else:
        rows = [
            [
                InlineKeyboardButton("🚫 گزارش رفتار", callback_data="report:start"),
                InlineKeyboardButton("🚩 گزارش پروفایل", callback_data=f"profilereport:{reported_id}"),
            ],
        ]
    return InlineKeyboardMarkup(rows)


def settings_keyboard(next_gender_pref: str | None) -> InlineKeyboardMarkup:
    """کیبورد صفحه‌ی تنظیمات؛ فعلاً فقط ترجیحِ جنسیتِ matching."""
    def _btn(value: str, label: str) -> InlineKeyboardButton:
        active = next_gender_pref == value
        return InlineKeyboardButton(
            f"{'✅ ' if active else ''}{label}",
            callback_data=f"settings:next_gender:{value}",
        )
    return InlineKeyboardMarkup([
        [_btn("female", "👩 دختر"), _btn("male", "👨 پسر")],
        [_btn("any", "🤷 فرقی نمی‌کنه")],
    ])


def desired_gender_keyboard() -> InlineKeyboardMarkup:
    """قبل از ورود به صفِ «وصل کن به یه ناشناس!» ازِ کاربر می‌پرسه دنبال
    چه جنسیتی می‌گرده."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("👩 دختر", callback_data="matchgender:female"),
                InlineKeyboardButton("👨 پسر", callback_data="matchgender:male"),
            ],
            [InlineKeyboardButton("🤷 فرقی نمی‌کنه", callback_data="matchgender:any")],
        ]
    )


def cancel_queue_keyboard() -> InlineKeyboardMarkup:
    """زیرِ پیامِ «شما در صف هستید...» قرار می‌گیره تا کاربر بتونه هر
    وقت خواست، بدون نیاز به تایپِ /stop، از جستجو منصرف بشه."""
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("❌ لغو جستجو", callback_data="cancelqueue")]]
    )


def gender_selection_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("👨 مرد", callback_data="gender:male"),
                InlineKeyboardButton("👩 زن", callback_data="gender:female"),
            ],
            [InlineKeyboardButton("🔙 بازگشت", callback_data="menu:profile")],
        ]
    )


def report_reason_keyboard(reported_id: int, session_id: int | None = None) -> InlineKeyboardMarkup:
    session_part = session_id if session_id is not None else "none"
    reasons = [
        ("اسپم / تبلیغات", "spam"),
        ("کلاهبرداری", "scam"),
        ("توهین / آزار", "abuse"),
        ("محتوای جنسی", "sexual"),
        ("پروفایل جعلی", "fake_profile"),
        ("سایر موارد", "other"),
    ]
    rows = [
        [InlineKeyboardButton(label, callback_data=f"report:reason:{code}:{reported_id}:{session_part}")]
        for label, code in reasons
    ]
    rows.append([InlineKeyboardButton("انصراف", callback_data="report:cancel")])
    return InlineKeyboardMarkup(rows)


def delete_history_keyboard(user_a: int, user_b: int, session_id: int | None = None) -> InlineKeyboardMarkup:
    session_part = session_id if session_id is not None else "none"
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🗑 پاک کردن تاریخچه چت", callback_data=f"delhist:{user_a}:{user_b}:{session_part}")]]
    )


def end_chat_actions_keyboard(
    user_a: int, user_b: int, session_id: int | None, reported_id: int
) -> InlineKeyboardMarkup:
    """کیبوردِ بعد از پایانِ چت، با دکمه‌ی پاک‌کردنِ تاریخچه و گزارشِ همین
    گفتگو. reported_id یعنی طرفِ مقابلِ همون کاربری که این کیبورد رو
    می‌بینه، پس باید جدا برای هر دو نفر ساخته بشه، نه یه کیبوردِ مشترک."""
    session_part = session_id if session_id is not None else "none"
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🗑 پاک کردن تاریخچه چت", callback_data=f"delhist:{user_a}:{user_b}:{session_part}")],
            [InlineKeyboardButton("🚫 گزارش این گفتگو", callback_data=f"reportsession:{reported_id}:{session_part}")],
        ]
    )


def search_users_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("⚧ فیلتر بر اساس جنسیت", callback_data="search:filter_gender")],
            [InlineKeyboardButton("🎂 فیلتر بر اساس سن", callback_data="search:filter_age")],
            [InlineKeyboardButton("🔍 شروع جستجو", callback_data="search:go")],
            [InlineKeyboardButton("🔙 بازگشت به منو", callback_data="menu:main")],
        ]
    )


def nearby_keyboard(has_location: bool) -> InlineKeyboardMarkup:
    rows = []
    if has_location:
        rows.append([InlineKeyboardButton("🔄 به‌روزرسانی موقعیت", callback_data="nearby:update_location")])
        rows.append([InlineKeyboardButton("👀 نمایش افراد نزدیک", callback_data="nearby:show")])
        rows.append([InlineKeyboardButton("🗑 حذف موقعیت من", callback_data="nearby:delete_location")])
    else:
        rows.append([InlineKeyboardButton("📍 اشتراک‌گذاری موقعیت", callback_data="nearby:share_location")])
    rows.append([InlineKeyboardButton("🔙 بازگشت به منو", callback_data="menu:main")])
    return InlineKeyboardMarkup(rows)


def coins_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔗 دعوت دوستان (+۵ سکه هرکدوم)", callback_data="menu:invite")],
            [InlineKeyboardButton("📜 تاریخچه‌ی تراکنش‌ها", callback_data="coins:history")],
            [InlineKeyboardButton("🔙 بازگشت به منو", callback_data="menu:main")],
        ]
    )


def note_reply_keyboard(note_id: str, sender_id: int) -> InlineKeyboardMarkup:
    """زیر هر پیامِ ناشناسِ نوتیفی (از طریق لینک ناشناس مستقیم) قرار
    می‌گیره تا صاحب لینک بتونه مستقیم به همون فرستنده پاسخ بده یا
    فرستنده رو برای همیشه بلاک کنه."""
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("↩️ پاسخ دادن", callback_data=f"noterep:{note_id}")],
            [InlineKeyboardButton("🚫 بلاک کردن فرستنده", callback_data=f"noteblock:{sender_id}")],
        ]
    )


def end_chat_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ بله، بستن چت", callback_data="endchat:confirm"),
            InlineKeyboardButton("❌ نه، پشیمون شدم", callback_data="endchat:cancel"),
        ]
    ])


def cancel_keyboard(callback_data: str = "generic:cancel") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("انصراف", callback_data=callback_data)]])


_PROVINCES = [
    "آذربایجان شرقی", "آذربایجان غربی", "اردبیل", "اصفهان", "البرز",
    "ایلام", "بوشهر", "تهران", "چهارمحال و بختیاری", "خراسان جنوبی",
    "خراسان رضوی", "خراسان شمالی", "خوزستان", "زنجان", "سمنان",
    "سیستان و بلوچستان", "فارس", "قزوین", "قم", "کردستان",
    "کرمان", "کرمانشاه", "کهگیلویه و بویراحمد", "گلستان", "گیلان",
    "لرستان", "مازندران", "مرکزی", "هرمزگان", "همدان", "یزد",
]


def province_keyboard() -> InlineKeyboardMarkup:
    """کیبورد انتخاب استان، ۳ تا در هر ردیف."""
    rows = []
    for i in range(0, len(_PROVINCES), 3):
        row = [
            InlineKeyboardButton(p, callback_data=f"obprov:{p}")
            for p in _PROVINCES[i:i + 3]
        ]
        rows.append(row)
    rows.append([InlineKeyboardButton("❌ انصراف", callback_data="generic:cancel")])
    return InlineKeyboardMarkup(rows)


def city_keyboard(province: str, page: int = 0) -> InlineKeyboardMarkup:
    """کیبورد انتخاب شهر برای یک استان، با صفحه‌بندی ۲۴تایی."""
    cities = IRAN_CITIES.get(province, [])
    total = len(cities)
    start = page * _CITIES_PER_PAGE
    end = min(start + _CITIES_PER_PAGE, total)
    page_cities = cities[start:end]

    rows = []
    for i in range(0, len(page_cities), 3):
        row = [
            InlineKeyboardButton(c, callback_data=f"obcity:{c}")
            for c in page_cities[i:i + 3]
        ]
        rows.append(row)

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("→ قبلی", callback_data=f"citypg:{province}:{page - 1}"))
    if end < total:
        nav.append(InlineKeyboardButton("بعدی ←", callback_data=f"citypg:{province}:{page + 1}"))
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton("❌ انصراف", callback_data="generic:cancel")])
    return InlineKeyboardMarkup(rows)


# پروفایلِ عمومی (/user_<code>) و سیستمِ واکنش
def public_profile_keyboard(target_id: int, reactions_enabled: bool) -> InlineKeyboardMarkup:
    """زیرِ پروفایلی که با /user_<code> باز می‌شه؛ حتی وقتی دو نفر توی
    چتِ فعال نیستن هم دیده می‌شه."""
    rows = [
        [
            InlineKeyboardButton("🚩 گزارش پروفایل", callback_data=f"profilereport:{target_id}"),
            InlineKeyboardButton("🚫 بلاک", callback_data=f"pubblock:{target_id}"),
        ],
        [InlineKeyboardButton("💬 درخواست چت", callback_data=f"chatreq:{target_id}")],
        [InlineKeyboardButton("📩 پیام دایرکت", callback_data=f"directmsg:{target_id}")],
    ]
    if reactions_enabled:
        rows.append([InlineKeyboardButton("😠 ارسال واکنش", callback_data=f"reactopen:{target_id}")])
    return InlineKeyboardMarkup(rows)


def view_chat_request_keyboard(request_id: str) -> InlineKeyboardMarkup:
    """نوتیفِ اولیه‌ی درخواستِ چت؛ فقط یه دکمه‌ی «مشاهده» داره تا هویتِ
    درخواست‌کننده قبل از اینکه گیرنده عمداً بازش کنه، افشا نشه."""
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("👀 مشاهده درخواست چت", callback_data=f"chatreqview:{request_id}")]]
    )


def chat_request_decision_keyboard(request_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ قبول درخواست", callback_data=f"chatreqaccept:{request_id}"),
                InlineKeyboardButton("❌ رد درخواست", callback_data=f"chatreqreject:{request_id}"),
            ]
        ]
    )


def reaction_tags_keyboard(target_id: int, tags: list[dict]) -> InlineKeyboardMarkup:
    """لیستِ تگ‌های خودِ صاحبِ پروفایل رو برای انتخاب نشون می‌ده."""
    rows = [
        [InlineKeyboardButton(f"#{tag['label']}", callback_data=f"reactsend:{target_id}:{tag['id']}")]
        for tag in tags
    ]
    rows.append([InlineKeyboardButton("انصراف", callback_data="generic:cancel")])
    return InlineKeyboardMarkup(rows)


def reaction_settings_keyboard(reactions_enabled: bool) -> InlineKeyboardMarkup:
    toggle_label = "🔕 غیرفعال‌کردنِ دریافتِ واکنش" if reactions_enabled else "🔔 فعال‌کردنِ دریافتِ واکنش"
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(toggle_label, callback_data="reactsettings:toggle")],
            [InlineKeyboardButton("➕ افزودنِ تگِ جدید", callback_data="reactsettings:addtag")],
            [InlineKeyboardButton("📋 لیست و حذفِ تگ‌ها", callback_data="reactsettings:listtags")],
            [InlineKeyboardButton("🔙 بازگشت به پروفایل", callback_data="menu:profile")],
        ]
    )


def reaction_tags_manage_keyboard(tags: list[dict]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(f"🗑 حذفِ #{tag['label']}", callback_data=f"reactsettings:deltag:{tag['id']}")]
        for tag in tags
    ]
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="reactsettings:back")])
    return InlineKeyboardMarkup(rows)
