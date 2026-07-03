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
کوئری‌ها و توابعِ کمکیِ سطح-بالا که روی مدل‌های ORM کار می‌کنن. هر کدوم
سشنِ خودشون رو باز/بسته می‌کنن (بدونِ نیاز به session بیرونی)، مگر
get_or_create_user که برای استفاده‌ی مشترک بینِ چند عملیات در یک
تراکنش، session رو از بیرون می‌گیره.
"""

from datetime import datetime

from geoalchemy2.functions import ST_MakePoint, ST_SetSRID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .connections import async_session
from .models import (
    BlockedSender,
    ChatMessage,
    ChatSession,
    CoinTransaction,
    ProfileReport,
    ReactionLog,
    ReactionTag,
    Report,
    ReportVerdict,
    User,
    Warning,
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


# تاریخچه‌ی متنیِ چت (برای قضاوت AI) و سیستم اخطار
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


async def deduct_coins(user_id: int, amount: int, reason: str) -> int | None:
    """سکه کسر می‌کنه. خروجی: موجودی جدید یا None اگه سکه کافی نباشه."""
    async with async_session() as session:
        user = await session.get(User, user_id)
        if user is None or user.coins < amount:
            return None
        user.coins -= amount
        session.add(CoinTransaction(user_id=user_id, amount=-amount, reason=reason))
        await session.commit()
        return user.coins


async def refund_coins(user_id: int, amount: int, reason: str) -> int | None:
    """سکه برمی‌گردونه. خروجی: موجودی جدید یا None اگه کاربر پیدا نشد."""
    async with async_session() as session:
        user = await session.get(User, user_id)
        if user is None:
            return None
        user.coins += amount
        session.add(CoinTransaction(user_id=user_id, amount=amount, reason=reason))
        await session.commit()
        return user.coins


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
    """بعد از تکمیل پروفایل دعوت‌شده، به دعوت‌کننده ۵ سکه می‌ده، فقط یه بار
    (idempotent). موجودیِ جدید دعوت‌کننده رو برمی‌گردونه، یا None اگه
    قبلاً داده شده یا کاربر پیدا نشد."""
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


# پروفایلِ عمومی (/user_<code>)، حالتِ سایلنت، و سیستمِ واکنش
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
    """وقتی تلگرام BadRequest می‌ده، photo_file_id نامعتبر رو از دیتابیس پاک می‌کنه."""
    async with async_session() as session:
        user = await session.get(User, user_id)
        if user:
            user.photo_file_id = None
            await session.commit()
