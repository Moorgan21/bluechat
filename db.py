"""
لایه‌ی دیتابیس (PostgreSQL + PostGIS با SQLAlchemy async)
--------------------------------------------------------------
نیازمندی‌ها:
    pip install sqlalchemy[asyncio] asyncpg geoalchemy2

⚠️ باید پسوند PostGIS روی دیتابیستون فعال باشه (یک‌بار، دستی):
    CREATE EXTENSION IF NOT EXISTS postgis;
این دستور نیاز به دسترسی superuser داره؛ init_db() این extension رو
خودکار فعال می‌کنه (اگه یوزر دیتابیس دسترسی کافی داشته باشه).

تنظیم اتصال با متغیر محیطی:
    export DATABASE_URL="postgresql+asyncpg://user:pass@host:5432/melogap"

اگه از ایمیج رسمی postgres:16 استفاده می‌کنی، برای داشتن PostGIS باید
ایمیج postgis/postgis رو به‌جاش بگیری:
    docker run -d --name melogap-pg -e POSTGRES_USER=melogap \
      -e POSTGRES_PASSWORD=melogap -e POSTGRES_DB=melogap \
      -p 5432:5432 postgis/postgis:16-3.4

اگه روی همون سروری که Aptic رو داری (188.40.209.105) یه کانتینر Postgres
جدا بالا بیاری، کافیه همون رشته‌ی اتصال رو با این فرمت بدی. اگه از
Postgres مشترک با پروژه‌ی دیگه استفاده می‌کنی، فقط یه دیتابیس/اسکیمای
جدا (مثلاً `melogap`) براش بساز که تداخلی با جداول Aptic نداشته باشه.
"""

import enum
import os
from datetime import datetime

from geoalchemy2 import Geography
from geoalchemy2.functions import ST_DWithin, ST_Distance, ST_MakePoint, ST_SetSRID
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
    select,
    text,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql+asyncpg://melogap:melogap@localhost:5432/melogap"
)

engine = create_async_engine(DATABASE_URL, echo=False, pool_size=10, max_overflow=20)
async_session = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


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
    """متن پیام‌های هر گفتگو — برای قضاوتِ AI هنگام گزارش، و برای پاک‌سازی
    خودکارِ دوره‌ای (هر ۲۴ ساعت) یا پاک‌سازیِ دستی با تایید دوطرفه.
    فقط پیام‌های متنی ذخیره می‌شن (مدیا صرفاً با یک برچسب نوع، بدون
    محتوای واقعی، تا هم داده کم بمونه هم حریم خصوصی رعایت بشه)."""

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
    """گزارشِ محتوای پروفایل (عکس/نام/بیو) یک کاربر — جدا از گزارشِ
    رفتار در گفتگو. قضاوتش با Gemini Vision انجام می‌شه چون نیاز به
    تحلیل تصویر داره."""

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
    """هر بار که کسی به یه کاربر واکنش (با یکی از تگ‌های خودش) می‌فرسته،
    یه رکورد اینجا ثبت می‌شه — هم برای شمارشِ هر تگ، هم برای جلوگیری از
    اسپم/محدودیتِ نرخ در آینده."""

    __tablename__ = "reaction_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(BigInteger)  # صاحبِ پروفایل (گیرنده‌ی واکنش)
    sender_id: Mapped[int] = mapped_column(BigInteger)  # کسی که واکنش رو فرستاده (ناشناس برای owner)
    tag_id: Mapped[int] = mapped_column(ForeignKey("reaction_tags.id"))
    tag_label: Mapped[str] = mapped_column(String(32))  # کپیِ برچسب، برای اینکه اگه تگ بعداً حذف شد، تاریخچه بمونه
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        await conn.run_sync(Base.metadata.create_all)
        # ایندکس مکانی GiST برای جستجوی سریع «نزدیک‌ترین‌ها»
        await conn.execute(
            text("CREATE INDEX IF NOT EXISTS idx_users_location ON users USING GIST (location)")
        )


def make_point(latitude: float, longitude: float):
    """یه object نقطه‌ی جغرافیایی برای ذخیره در ستون location می‌سازه."""
    return ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)


async def get_or_create_user(
    session: AsyncSession,
    telegram_id: int,
    username: str | None = None,
    first_name: str | None = None,
    invited_by: int | None = None,
) -> User:
    result = await session.execute(select(User).where(User.id == telegram_id))
    user = result.scalar_one_or_none()
    if user is not None:
        # به‌روزرسانی سبک اطلاعات پایه (username ممکنه عوض شده باشه)
        changed = False
        if username and user.username != username:
            user.username = username
            changed = True
        if first_name and user.first_name != first_name:
            user.first_name = first_name
            changed = True
        if changed:
            await session.commit()
        return user

    import secrets
    import string

    referral_code = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(8))

    user = User(
        id=telegram_id,
        username=username,
        first_name=first_name,
        referral_code=referral_code,
        invited_by=invited_by,
        coins=10,
    )
    session.add(user)
    await session.commit()

    return user


async def increment_total_chats(user_ids: list[int]) -> None:
    """آمارِ «تعداد گفتگوها» رو برای هر کدوم از کاربرهای داده‌شده یکی
    زیاد می‌کنه. موقعِ شروعِ موفقِ یک گفتگوی جدید (matching انجام‌شده،
    نه صرفاً ورود به صف) صدا زده می‌شه."""
    async with async_session() as session:
        for user_id in user_ids:
            user = await session.get(User, user_id)
            if user is not None:
                user.total_chats += 1
        await session.commit()


async def block_sender(owner_id: int, sender_id: int) -> None:
    """owner_id فرستنده‌ی sender_id رو برای لینک ناشناسِ خودش بلاک
    می‌کنه. اگه قبلاً بلاک شده باشه، کاری انجام نمی‌ده (idempotent)."""
    async with async_session() as session:
        result = await session.execute(
            select(BlockedSender).where(
                BlockedSender.owner_id == owner_id, BlockedSender.sender_id == sender_id
            )
        )
        if result.scalar_one_or_none() is not None:
            return
        from sqlalchemy.exc import IntegrityError
        session.add(BlockedSender(owner_id=owner_id, sender_id=sender_id))
        try:
            await session.commit()
        except IntegrityError:
            pass


async def unblock_sender(owner_id: int, sender_id: int) -> bool:
    """بلاک رو برمی‌داره. اگه بلاکی وجود نداشت، False برمی‌گردونه."""
    async with async_session() as session:
        result = await session.execute(
            select(BlockedSender).where(
                BlockedSender.owner_id == owner_id, BlockedSender.sender_id == sender_id
            )
        )
        blocked = result.scalar_one_or_none()
        if blocked is None:
            return False
        await session.delete(blocked)
        await session.commit()
        return True


async def is_sender_blocked(owner_id: int, sender_id: int) -> bool:
    async with async_session() as session:
        result = await session.execute(
            select(BlockedSender).where(
                BlockedSender.owner_id == owner_id, BlockedSender.sender_id == sender_id
            )
        )
        return result.scalar_one_or_none() is not None


# ---------------------------------------------------------------------------
# تاریخچه‌ی متنیِ چت (برای قضاوت AI) و سیستم اخطار
# ---------------------------------------------------------------------------
async def store_chat_message(
    session_id: int, sender_id: int, content: str | None, content_type: str = "text"
) -> None:
    """یه پیام از یک گفتگوی فعال رو در Postgres ذخیره می‌کنه. فقط برای
    پیام‌های متنی محتوای واقعی نگه داشته می‌شه؛ برای مدیا فقط نوعش."""
    async with async_session() as session:
        session.add(
            ChatMessage(
                session_id=session_id,
                sender_id=sender_id,
                content=content,
                content_type=content_type,
            )
        )
        await session.commit()


async def get_session_transcript(session_id: int) -> list[dict] | None:
    """تاریخچه‌ی یک گفتگو رو برای قضاوت AI برمی‌گردونه. اگه تاریخچه با
    تایید دوطرفه پاک شده باشه، None برمی‌گردونه (یعنی دیگه چیزی برای
    بررسی نیست)."""
    async with async_session() as session:
        chat_session = await session.get(ChatSession, session_id)
        if chat_session is None or chat_session.history_deleted:
            return None

        result = await session.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.created_at.asc())
        )
        messages = result.scalars().all()
        return [
            {"sender_id": m.sender_id, "content": m.content, "content_type": m.content_type}
            for m in messages
        ]


async def mark_session_history_deleted(session_id: int) -> None:
    """وقتی هر دو طرف پاک‌کردن تاریخچه رو تایید کردن، متنِ پیام‌ها واقعاً
    از Postgres حذف می‌شه و سشن به‌عنوان «تاریخچه‌ی پاک‌شده» علامت
    می‌خوره (تا دیگه قابل قضاوت نباشه)."""
    async with async_session() as session:
        await session.execute(
            ChatMessage.__table__.delete().where(ChatMessage.session_id == session_id)
        )
        chat_session = await session.get(ChatSession, session_id)
        if chat_session is not None:
            chat_session.history_deleted = True
        await session.commit()


async def purge_old_chat_messages(older_than_hours: int = 24) -> int:
    """تمام پیام‌های متنیِ قدیمی‌تر از X ساعت رو حذف می‌کنه (پاک‌سازیِ
    دوره‌ای خودکار، مستقل از پاک‌کردن دستیِ دوطرفه). خروجی: تعداد رکورد
    حذف‌شده."""
    from datetime import timedelta

    cutoff = datetime.utcnow() - timedelta(hours=older_than_hours)
    async with async_session() as session:
        result = await session.execute(
            select(ChatMessage.id).where(ChatMessage.created_at < cutoff)
        )
        ids = [row[0] for row in result.all()]
        if not ids:
            return 0
        await session.execute(ChatMessage.__table__.delete().where(ChatMessage.id.in_(ids)))
        await session.commit()
        return len(ids)


async def add_warning(user_id: int, reason: str, report_id: int | None = None) -> tuple[int, bool]:
    """یه اخطار برای user_id ثبت می‌کنه. خروجی: (شماره‌ی اخطار، آیا بن
    خودکار شد). به ۵ که برسه، is_banned=True می‌شه."""
    async with async_session() as session:
        user = await session.get(User, user_id)
        if user is None:
            return (0, False)

        user.warning_count += 1
        warning_number = user.warning_count
        auto_banned = warning_number >= 5
        if auto_banned:
            user.is_banned = True

        session.add(
            Warning(
                user_id=user_id,
                report_id=report_id,
                warning_number=warning_number,
                reason=reason,
            )
        )
        await session.commit()
        return (warning_number, auto_banned)


async def update_report_verdict(report_id: int, verdict: "ReportVerdict", verdict_reason: str) -> None:
    async with async_session() as session:
        report = await session.get(Report, report_id)
        if report is not None:
            report.verdict = verdict
            report.verdict_reason = verdict_reason
            report.verdict_at = datetime.utcnow()
            await session.commit()


async def grant_report_reward(reporter_id: int, amount: int, report_id: int | None = None) -> int | None:
    """به گزارش‌دهنده‌ای که گزارشش توسط AI تاییدِ صحت شده، سکه پاداش
    می‌ده. خروجی: موجودیِ جدیدِ سکه (یا None اگه کاربر پیدا نشد)."""
    async with async_session() as session:
        user = await session.get(User, reporter_id)
        if user is None:
            return None
        user.coins += amount
        session.add(
            CoinTransaction(
                user_id=reporter_id,
                amount=amount,
                reason=f"report_reward:{report_id}" if report_id else "report_reward",
            )
        )
        await session.commit()
        return user.coins


async def grant_referral_bonus(inviter_id: int, invitee_id: int) -> int | None:
    """بعد از تکمیل پروفایل دعوت‌شده، به دعوت‌کننده ۵ سکه می‌ده — فقط
    یک‌بار (idempotent). خروجی: موجودیِ جدید دعوت‌کننده یا None اگه قبلاً
    داده شده یا کاربر پیدا نشد."""
    from sqlalchemy.exc import IntegrityError

    reason = f"referral_bonus:{invitee_id}"
    async with async_session() as session:
        # SELECT FOR UPDATE قفل ردیف inviter رو می‌گیره تا دو فراخوانی همزمان هر دو سکه ندن
        result = await session.execute(
            select(User).where(User.id == inviter_id).with_for_update()
        )
        inviter = result.scalar_one_or_none()
        if inviter is None:
            return None
        existing = await session.execute(
            select(CoinTransaction).where(
                CoinTransaction.user_id == inviter_id,
                CoinTransaction.reason == reason,
            )
        )
        if existing.scalar_one_or_none() is not None:
            return None
        inviter.coins += 5
        session.add(CoinTransaction(user_id=inviter_id, amount=5, reason=reason))
        try:
            await session.commit()
        except IntegrityError:
            return None
        return inviter.coins


async def ban_user(user_id: int) -> None:
    """کاربر رو بلافاصله بن می‌کنه (بدون نیاز به رسیدن به ۵ اخطار)."""
    async with async_session() as session:
        user = await session.get(User, user_id)
        if user is not None:
            user.is_banned = True
            await session.commit()


async def get_user_profile_snapshot(user_id: int) -> dict | None:
    """اطلاعات فعلی پروفایل یک کاربر (برای ارسال به AI موقع گزارشِ
    پروفایل) رو برمی‌گردونه."""
    async with async_session() as session:
        user = await session.get(User, user_id)
        if user is None:
            return None
        return {
            "display_name": user.display_name,
            "bio": user.bio,
            "gender": user.gender.value if user.gender else None,
            "age": user.age,
            "photo_file_id": user.photo_file_id,
        }


async def update_profile_report_verdict(
    profile_report_id: int, verdict: "ReportVerdict", verdict_reason: str
) -> None:
    async with async_session() as session:
        report = await session.get(ProfileReport, profile_report_id)
        if report is not None:
            report.verdict = verdict
            report.verdict_reason = verdict_reason
            report.verdict_at = datetime.utcnow()
            await session.commit()


# ---------------------------------------------------------------------------
# پروفایلِ عمومی (/user_<code>)، حالتِ سایلنت، و سیستمِ واکنش
# ---------------------------------------------------------------------------
async def get_user_by_referral_code(code: str) -> User | None:
    async with async_session() as session:
        result = await session.execute(select(User).where(User.referral_code == code))
        return result.scalar_one_or_none()


async def set_silent_mode(user_id: int, is_silent: bool) -> None:
    async with async_session() as session:
        user = await session.get(User, user_id)
        if user is not None:
            user.is_silent = is_silent
            await session.commit()


async def set_reactions_enabled(user_id: int, enabled: bool) -> None:
    async with async_session() as session:
        user = await session.get(User, user_id)
        if user is not None:
            user.reactions_enabled = enabled
            await session.commit()


async def list_reaction_tags(owner_id: int) -> list[dict]:
    async with async_session() as session:
        result = await session.execute(
            select(ReactionTag).where(ReactionTag.owner_id == owner_id).order_by(ReactionTag.created_at.asc())
        )
        tags = result.scalars().all()
        return [{"id": t.id, "label": t.label} for t in tags]


async def add_reaction_tag(owner_id: int, label: str) -> bool:
    """یه تگِ جدید اضافه می‌کنه. خروجی False یعنی همین برچسب از قبل
    وجود داشته (idempotent، بدون خطا)."""
    label = label.strip().lstrip("#").strip()
    async with async_session() as session:
        result = await session.execute(
            select(ReactionTag).where(ReactionTag.owner_id == owner_id, ReactionTag.label == label)
        )
        if result.scalar_one_or_none() is not None:
            return False
        from sqlalchemy.exc import IntegrityError
        session.add(ReactionTag(owner_id=owner_id, label=label))
        try:
            await session.commit()
        except IntegrityError:
            return False
        return True


async def delete_reaction_tag(owner_id: int, tag_id: int) -> bool:
    async with async_session() as session:
        tag = await session.get(ReactionTag, tag_id)
        if tag is None or tag.owner_id != owner_id:
            return False
        await session.delete(tag)
        await session.commit()
        return True


async def get_reaction_tag(tag_id: int) -> dict | None:
    async with async_session() as session:
        tag = await session.get(ReactionTag, tag_id)
        if tag is None:
            return None
        return {"id": tag.id, "owner_id": tag.owner_id, "label": tag.label}


async def log_reaction(owner_id: int, sender_id: int, tag_id: int, tag_label: str) -> None:
    async with async_session() as session:
        session.add(
            ReactionLog(owner_id=owner_id, sender_id=sender_id, tag_id=tag_id, tag_label=tag_label)
        )
        await session.commit()


async def get_reaction_counts(owner_id: int) -> list[dict]:
    """تعدادِ دریافتیِ هر تگ رو برای نمایش در پروفایل برمی‌گردونه (بر
    اساسِ tag_label، پس حتی اگه تگ بعداً حذف بشه، شمارش تاریخی باقی
    می‌مونه)."""
    from sqlalchemy import func as sa_func

    async with async_session() as session:
        result = await session.execute(
            select(ReactionLog.tag_label, sa_func.count(ReactionLog.id))
            .where(ReactionLog.owner_id == owner_id)
            .group_by(ReactionLog.tag_label)
            .order_by(sa_func.count(ReactionLog.id).desc())
        )
        return [{"label": label, "count": count} for label, count in result.all()]


async def update_next_gender_pref(user_id: int, pref: str | None) -> None:
    """ذخیره‌ی ترجیح جنسیت برای matching: 'male'/'female'/'any' یا None برای پاک‌کردن."""
    async with async_session() as session:
        user = await session.get(User, user_id)
        if user:
            user.next_gender_pref = pref
            await session.commit()


async def clear_photo_file_id(user_id: int) -> None:
    """photo_file_id نامعتبر رو از دیتابیس پاک می‌کنه — وقتی تلگرام BadRequest می‌ده."""
    async with async_session() as session:
        user = await session.get(User, user_id)
        if user:
            user.photo_file_id = None
            await session.commit()
