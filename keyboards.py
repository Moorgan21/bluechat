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
        [KeyboardButton("💬 وصل کن به یه ناشناس!"), KeyboardButton("🏠 اتاق چت")],
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


def in_room_reply_keyboard(secure: bool = False, is_owner: bool = False, room_open: bool = True) -> ReplyKeyboardMarkup:
    """کیبورد پایین صفحه حینِ حضور در یه اتاقِ چت. owner دکمه‌ی «ترک»
    نداره (نمی‌تونه ترک کنه)، به‌جاش «حذفِ اتاق» (تنها راهِ خروجِ
    عضویتِ owner) و بستن/بازکردنِ اتاق می‌بینه. حذفِ پیامِ دیگران و
    اخراج دکمه‌ای ندارن، با ریپلای‌کردنِ «حذف»/«اخراج» انجام می‌شن
    (مثلِ فرمانِ حذفِ خودِ کاربر). چتِ امن per-userه، پس همون فلگِ
    ۱به۱ رو به اشتراک می‌ذاره، نه چیزِ جدا.

    «🚪 خروج» با «🚪 ترک اتاق» فرق داره: خروج فقط هندلرِ اتاق رو موقتاً
    غیرفعال می‌کنه (بدونِ از دست‌دادنِ عضویت) تا بشه از بقیه‌ی
    امکاناتِ ربات استفاده کرد؛ با /room یا «🏠 اتاق چت» دوباره فعال
    می‌شه. برای owner هم هست، چون owner راهِ دیگه‌ای برای موقتاً
    کنار کشیدن از مدیریتِ اتاق نداره."""
    secure_label = "🔒 چت امن (فعال)" if secure else "🔒 چت امن (غیرفعال)"
    rows = [[KeyboardButton("👥 وضعیت اتاق")], [KeyboardButton(secure_label), KeyboardButton("🚪 خروج")]]
    if is_owner:
        close_label = "🔒 بستن اتاق" if room_open else "🔓 بازکردن اتاق"
        rows.append([KeyboardButton(close_label)])
        rows.append([KeyboardButton("🗑 حذف اتاق")])
    else:
        rows.append([KeyboardButton("🚪 ترک اتاق")])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def room_delete_confirm_keyboard() -> InlineKeyboardMarkup:
    """قبل از حذفِ قطعیِ اتاق (غیرقابلِ بازگشت) از owner تاییدِ صریح
    می‌گیره، هم‌راستا با end_chat_confirm_keyboard برای پایانِ چتِ ۱به۱."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ بله، حذف کن", callback_data="roomdelete:confirm"),
                InlineKeyboardButton("❌ نه، پشیمون شدم", callback_data="roomdelete:cancel"),
            ]
        ]
    )


def purge_history_keyboard(room_id: int) -> InlineKeyboardMarkup:
    """بعد از حذفِ اتاق، فقط زیرِ پیامِ owner نشون داده می‌شه — تنها
    کسی که اجازه‌ی پاک‌کردنِ یک‌طرفه‌ی کاملِ تاریخچه رو داره."""
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🧹 پاک‌کردنِ کاملِ تاریخچه", callback_data=f"roompurge:{room_id}")]]
    )


def profile_inline_keyboard() -> InlineKeyboardMarkup:
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


def room_menu_keyboard() -> InlineKeyboardMarkup:
    """زیرِ «🏠 اتاق چت» توی منوی اصلی."""
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➕ ایجاد اتاق چت", callback_data="roommenu:create")],
            [InlineKeyboardButton("🔍 عضویت در اتاق چت", callback_data="roommenu:join")],
            [InlineKeyboardButton("🔙 بازگشت به منو", callback_data="menu:main")],
        ]
    )


def room_gender_keyboard() -> InlineKeyboardMarkup:
    """قدمِ اول از ساختِ اتاق: نوعِ اتاق."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("👩 دخترونه", callback_data="roomgender:female"),
                InlineKeyboardButton("👨 پسرونه", callback_data="roomgender:male"),
            ],
            [InlineKeyboardButton("🤷 فرقی نداره", callback_data="roomgender:any")],
        ]
    )


def room_join_gender_keyboard() -> InlineKeyboardMarkup:
    """قبل از عضویت می‌پرسه دنبالِ چه نوع اتاقی می‌گرده. پیشوندش
    (roomjoingender) عمداً از roomgender جداست تا با قدمِ اولِ فلوی
    ساختِ اتاق قاطی نشه."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("👩 دخترونه", callback_data="roomjoingender:female"),
                InlineKeyboardButton("👨 پسرونه", callback_data="roomjoingender:male"),
            ],
            [InlineKeyboardButton("🤷 فرقی نداره", callback_data="roomjoingender:any")],
        ]
    )


def room_capacity_keyboard() -> InlineKeyboardMarkup:
    """قدمِ دوم از ساختِ اتاق: ظرفیت. دکمه‌ی «آزاد» هم فنیاً همون ۵
    نفره، فقط برای کسی که نمی‌خواد رو یه عدد فکر کنه."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("۲ نفر", callback_data="roomcap:2"),
                InlineKeyboardButton("۳ نفر", callback_data="roomcap:3"),
            ],
            [
                InlineKeyboardButton("۴ نفر", callback_data="roomcap:4"),
                InlineKeyboardButton("۵ نفر", callback_data="roomcap:5"),
            ],
            [InlineKeyboardButton("🔓 آزاد (پیش‌فرض، تا ۵ نفر)", callback_data="roomcap:5")],
        ]
    )


def cancel_queue_keyboard() -> InlineKeyboardMarkup:
    """زیرِ پیامِ «شما در صف هستید...» قرار می‌گیره تا کاربر بتونه هر
    وقت خواست، بدون نیاز به تایپِ /stop، از جستجو منصرف بشه."""
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("❌ لغو جستجو", callback_data="cancelqueue")]]
    )


def cancel_room_join_keyboard() -> InlineKeyboardMarkup:
    """زیرِ پیامِ «⏳ فعلاً اتاقِ خالی‌ای پیدا نشد...» — دقیقاً معادلِ
    cancel_queue_keyboard برای صفِ عضویتِ اتاق."""
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("❌ لغو جستجوی اتاق", callback_data="roomcanceljoin")]]
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


REPORT_REASONS = [
    ("اسپم / تبلیغات", "spam"),
    ("کلاهبرداری", "scam"),
    ("توهین / آزار", "abuse"),
    ("محتوای جنسی", "sexual"),
    ("پروفایل جعلی", "fake_profile"),
    ("سایر موارد", "other"),
]


def report_reason_keyboard(reported_id: int, session_id: int | None = None) -> InlineKeyboardMarkup:
    """دلیلِ گزارشِ کلِ گفتگو، فقط بعد از پایانِ چت (کنارِ دکمه‌ی
    پاک‌کردنِ تاریخچه) قابل‌دسترسه."""
    session_part = session_id if session_id is not None else "none"
    rows = [
        [InlineKeyboardButton(label, callback_data=f"report:reason:{code}:{reported_id}:{session_part}")]
        for label, code in REPORT_REASONS
    ]
    rows.append([InlineKeyboardButton("انصراف", callback_data="report:cancel")])
    return InlineKeyboardMarkup(rows)


def message_report_reason_keyboard(token: str) -> InlineKeyboardMarkup:
    """دلیلِ گزارشِ یک پیامِ مشخص (با ریپلای‌کردنِ «گزارش» روی پیامِ
    طرفِ مقابل)؛ برخلافِ report_reason_keyboard، بجای reported_id/session_id
    خامِ توی callback_data، یه tokenِ کوتاه داره که متنِ پیامِ گزارش‌شده
    رو (که توی callback_data جا نمی‌شه) از Redis resolve می‌کنه."""
    rows = [
        [InlineKeyboardButton(label, callback_data=f"msgreport:reason:{code}:{token}")]
        for label, code in REPORT_REASONS
    ]
    rows.append([InlineKeyboardButton("انصراف", callback_data="msgreport:cancel")])
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
def public_profile_keyboard(target_id: int, reactions_enabled: bool, is_blocked: bool = False) -> InlineKeyboardMarkup:
    """زیرِ پروفایلی که با /user_<code> باز می‌شه؛ حتی وقتی دو نفر توی
    چتِ فعال نیستن هم دیده می‌شه. is_blocked یعنی بیننده قبلاً همین
    target_id رو بلاک کرده؛ توی این حالت دکمه به «✅ آنبلاک» عوض می‌شه."""
    block_button = (
        InlineKeyboardButton("✅ آنبلاک", callback_data=f"pubunblock:{target_id}")
        if is_blocked
        else InlineKeyboardButton("🚫 بلاک", callback_data=f"pubblock:{target_id}")
    )
    rows = [
        [
            InlineKeyboardButton("🚩 گزارش پروفایل", callback_data=f"profilereport:{target_id}"),
            block_button,
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
