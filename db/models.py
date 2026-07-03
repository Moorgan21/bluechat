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
مدل‌های ORM (جداولِ دیتابیس) و enumهای مرتبط.
"""

import enum
from datetime import datetime

from geoalchemy2 import Geography
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from .connections import Base


class Gender(str, enum.Enum):
    male = "male"
    female = "female"
    unset = "unset"


class ReportReason(str, enum.Enum):
    spam = "spam"
    scam = "scam"
    abuse = "abuse"
    sexual = "sexual"
    fake_profile = "fake_profile"
    other = "other"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # telegram user_id
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # پروفایل عمومی داخل ربات (کاملاً مستقل از هویت واقعی)
    display_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    bio: Mapped[str | None] = mapped_column(String(512), nullable=True)
    gender: Mapped[Gender] = mapped_column(Enum(Gender), default=Gender.unset)
    age: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # موقعیت جغرافیایی متنی (انتخاب در onboarding)
    province: Mapped[str | None] = mapped_column(String(50), nullable=True)
    city: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # عکس پروفایل (فقط بعد از تایید ماژول moderation ذخیره می‌شه)
    photo_file_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    photo_approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # اقتصاد سکه
    coins: Mapped[int] = mapped_column(Integer, default=10)  # سکه‌ی هدیه‌ی اولیه

    # موقعیت مکانی برای «افراد نزدیک» (اختیاری، با رضایت کاربر)
    # با PostGIS به‌صورت geography(Point, 4326) ذخیره می‌شه تا کوئری‌های
    # مکانی (ST_DWithin, ST_Distance) مستقیم توسط دیتابیس و با ایندکس
    # GiST بهینه انجام بشن، نه با محاسبه‌ی دستی در پایتون.
    location: Mapped[object | None] = mapped_column(
        Geography(geometry_type="POINT", srid=4326), nullable=True
    )
    location_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # لینک ناشناس اختصاصی (deep-link): t.me/BotName?start=u_<referral_code>
    referral_code: Mapped[str] = mapped_column(String(16), unique=True)
    invited_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    is_banned: Mapped[bool] = mapped_column(Boolean, default=False)
    warning_count: Mapped[int] = mapped_column(Integer, default=0)  # اخطارهای فعال (۵ = بن خودکار)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # تنظیماتِ دریافتِ واکنش روی پروفایلِ عمومی (/user_<code>)
    reactions_enabled: Mapped[bool] = mapped_column(Boolean, default=False)

    # تنظیم جنسیت مورد نظر برای matching: "male"/"female"/"any" یا None (هنوز تنظیم نشده)
    next_gender_pref: Mapped[str | None] = mapped_column(String(8), nullable=True)

    # حالت سایلنت: وقتی فعاله، کسی نمی‌تونه از طریق /user_<code> درخواستِ
    # چت بفرسته (پروفایل و بقیه‌ی دکمه‌ها همچنان در دسترسن).
    is_silent: Mapped[bool] = mapped_column(Boolean, default=False)

    # آمار
    total_chats: Mapped[int] = mapped_column(Integer, default=0)
    total_reports_received: Mapped[int] = mapped_column(Integer, default=0)


class ChatSession(Base):
    """رکورد هر گفتگوی ناشناس، برای گزارش‌گیری/آمار و سیستم گزارش کاربر."""

    __tablename__ = "chat_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_a_id: Mapped[int] = mapped_column(BigInteger)
    user_b_id: Mapped[int] = mapped_column(BigInteger)
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ended_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    was_successful: Mapped[bool] = mapped_column(Boolean, default=False)  # حداقل چند پیام رد و بدل شده
    # اگه تاریخچه با تایید دوطرفه پاک شده باشه، دیگه پیام‌های این سشن
    # برای قضاوت AI در دسترس نیستن (متنشون واقعاً از Postgres حذف شده).
    history_deleted: Mapped[bool] = mapped_column(Boolean, default=False)


class ChatMessage(Base):
    """متن پیام‌های هر گفتگو، برای قضاوتِ AI هنگام گزارش و برای پاک‌سازیِ
    خودکارِ دوره‌ای (هر ۲۴ ساعت) یا پاک‌سازیِ دستی با تایید دوطرفه. فقط
    پیام‌های متنی ذخیره می‌شن، مدیا فقط با یه برچسبِ نوع بدون محتوای
    واقعی تا هم داده کمتر بمونه هم حریمِ خصوصی حفظ بشه."""

    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("chat_sessions.id"))
    sender_id: Mapped[int] = mapped_column(BigInteger)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)  # متن پیام (اگه متنی بود)
    content_type: Mapped[str] = mapped_column(String(32), default="text")  # text/photo/voice/...
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Warning(Base):
    """اخطارِ ثبت‌شده برای یک کاربر توسط سیستم قضاوتِ AI (بعد از بررسیِ
    یک گزارش). warning_number شماره‌ی ترتیبیِ این اخطار برای اون کاربره
    (۱ تا ۵)."""

    __tablename__ = "warnings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
    report_id: Mapped[int | None] = mapped_column(ForeignKey("reports.id"), nullable=True)
    warning_number: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ReportVerdict(str, enum.Enum):
    pending = "pending"        # هنوز قضاوت نشده
    guilty = "guilty"          # AI تشخیص داده گزارش‌شونده مقصره
    dismissed = "dismissed"    # AI تشخیص داده گزارش نادرست/بی‌اساس بوده
    no_history = "no_history"  # تاریخچه پاک شده بود یا پیامی برای بررسی نبود


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int | None] = mapped_column(ForeignKey("chat_sessions.id"), nullable=True)
    reporter_id: Mapped[int] = mapped_column(BigInteger)
    reported_id: Mapped[int] = mapped_column(BigInteger)
    reason: Mapped[ReportReason] = mapped_column(Enum(ReportReason))
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # نتیجه‌ی قضاوتِ AI
    verdict: Mapped[ReportVerdict] = mapped_column(Enum(ReportVerdict), default=ReportVerdict.pending)
    verdict_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    verdict_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ProfileReport(Base):
    """گزارشِ محتوای پروفایل (عکس/نام/بیو) یه کاربر، جدا از گزارشِ رفتار
    در گفتگو. قضاوتش با Gemini Vision انجام می‌شه چون نیاز به تحلیل
    تصویر داره."""

    __tablename__ = "profile_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    reporter_id: Mapped[int] = mapped_column(BigInteger)
    reported_id: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    verdict: Mapped[ReportVerdict] = mapped_column(Enum(ReportVerdict), default=ReportVerdict.pending)
    verdict_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    verdict_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class CoinTransaction(Base):
    """تاریخچه‌ی تراکنش‌های سکه (برای شفافیت و جلوگیری از سوءاستفاده)."""

    __tablename__ = "coin_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
    amount: Mapped[int] = mapped_column(Integer)  # مثبت = واریز، منفی = برداشت
    reason: Mapped[str] = mapped_column(String(64))  # e.g. "referral_bonus", "failed_chat_refund"
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class BlockedSender(Base):
    """بلاکِ دائمی: owner_id فرستنده‌ی sender_id رو برای لینک ناشناسِ
    خودش بلاک کرده. بعد از این، هر پیامی که sender_id از طریق لینکِ
    owner_id بفرسته، بدون تحویل رد می‌شه. این توی Postgres نگه داشته
    می‌شه (نه Redis) چون باید دائمی باشه، نه TTL-دار."""

    __tablename__ = "blocked_senders"
    __table_args__ = (UniqueConstraint("owner_id", "sender_id", name="uq_owner_sender_block"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(BigInteger)
    sender_id: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ReactionTag(Base):
    """تگ‌های سفارشیِ واکنش که هر کاربر برای پروفایلِ خودش تعریف کرده
    (مثلاً «#عصبانی»، «#دختر_بد»). این تگ‌ها موقعی که یکی روی «ارسال
    واکنش» می‌زنه، به‌عنوانِ گزینه نشونش داده می‌شن."""

    __tablename__ = "reaction_tags"
    __table_args__ = (UniqueConstraint("owner_id", "label", name="uq_owner_tag_label"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(BigInteger)
    label: Mapped[str] = mapped_column(String(32))  # بدون # ذخیره می‌شه، موقعِ نمایش # اضافه می‌شه
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ReactionLog(Base):
    """هر بار کسی به یه کاربر واکنش می‌فرسته (با یکی از تگ‌های خودش) یه
    رکورد اینجا ثبت می‌شه، هم برای شمارشِ هر تگ هم برای rate limit در
    آینده."""

    __tablename__ = "reaction_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(BigInteger)  # صاحبِ پروفایل (گیرنده‌ی واکنش)
    sender_id: Mapped[int] = mapped_column(BigInteger)  # کسی که واکنش رو فرستاده (ناشناس برای owner)
    tag_id: Mapped[int] = mapped_column(ForeignKey("reaction_tags.id"))
    tag_label: Mapped[str] = mapped_column(String(32))  # کپیِ برچسب، برای اینکه اگه تگ بعداً حذف شد، تاریخچه بمونه
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
