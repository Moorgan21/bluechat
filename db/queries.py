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
from typing import Awaitable, Callable

from geoalchemy2.functions import ST_MakePoint, ST_SetSRID
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .connections import async_session
from .models import (
    BlockedSender,
    ChatMessage,
    ChatRoom,
    ChatRoomMember,
    ChatSession,
    CoinTransaction,
    ProfileReport,
    ReactionLog,
    ReactionTag,
    Report,
    ReportVerdict,
    RoomGenderPref,
    RoomStatus,
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


# -------------------------------------------------------------------------
# اتاق‌های چت
# -------------------------------------------------------------------------

async def create_chat_room(
    owner_id: int,
    gender_pref: RoomGenderPref,
    capacity: int,
    cost: int,
    conflict_check: Callable[[], Awaitable[str | None]] | None = None,
) -> tuple[ChatRoom | None, str | None]:
    """اتاقِ چتِ جدید می‌سازه. خروجی: (room, None) در موفقیت، یا
    (None, error_code) که error_code یکی از "not_found",
    "has_active_room", "insufficient_coins"، یا هرچی conflict_check
    برگردونه (مثلاً "in_1to1"/"in_queue") است.

    قفلِ ردیفِ owner (همون الگوی grant_referral_bonus) جلوی دوبار-کلیکِ
    خودِ همین کاربر رو هم می‌گیره: اگه دو تا درخواستِ هم‌زمان برسه، یکی
    پشتِ لاک منتظر می‌مونه تا اولی active_room_id رو ست کنه، بعد با
    همون دیدِ تازه می‌بینه دیگه آزاد نیست.

    conflict_check یه callback اختیاریه که *داخلِ* همین قفل صدا زده
    می‌شه، بعدِ چکِ active_room_id و قبلِ کسرِ سکه؛ برای اینه که این
    لایه (Postgres) هیچ importی از redis_client نداشته باشه ولی
    caller (لایه‌ی هندلر) بتونه یه چکِ Redis-محورِ اتمیک (مثلاً «الان
    توی چتِ ۱به۱ نیستی؟») رو دقیقاً همون لحظه‌ای که قفل گرفته شده
    تزریق کنه، نه فقط قبل از این تراکنش. چون خودِ callback هیچ قفلِ
    جدیدی رو Postgres نمی‌گیره (فقط Redis می‌خونه)، deadlock‌خطری
    نداره، فقط قفلِ owner رو چند میلی‌ثانیه بیشتر نگه می‌داره."""
    capacity = max(2, min(5, capacity))

    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.id == owner_id).with_for_update()
        )
        owner = result.scalar_one_or_none()
        if owner is None:
            return None, "not_found"
        if owner.active_room_id is not None:
            return None, "has_active_room"
        if conflict_check is not None:
            conflict_reason = await conflict_check()
            if conflict_reason is not None:
                return None, conflict_reason
        if owner.coins < cost:
            return None, "insufficient_coins"

        owner.coins -= cost
        session.add(CoinTransaction(user_id=owner_id, amount=-cost, reason="chat_room_create"))

        room = ChatRoom(owner_id=owner_id, gender_pref=gender_pref, capacity=capacity)
        session.add(room)
        await session.flush()  # room.id لازمه قبل از commit

        session.add(ChatRoomMember(room_id=room.id, user_id=owner_id))
        owner.active_room_id = room.id

        await session.commit()
        await session.refresh(room)
        return room, None


async def join_chat_room(
    user_id: int,
    room_id: int,
    conflict_check: Callable[[], Awaitable[str | None]] | None = None,
) -> tuple[ChatRoom | None, str | None]:
    """کاربر رو به یه اتاقِ مشخص ملحق می‌کنه. خروجی: (room, None) در
    موفقیت، یا (None, error_code) که یکی از "not_found",
    "room_not_open", "room_full", "has_active_room"، یا هرچی
    conflict_check برگردونه است.

    برخلافِ create_chat_room، اینجا سکه کسر نمی‌شه؛ چرخه‌ی
    پرداخت/بازگشتِ سکه‌ی جستجو کاملاً توسطِ لایه‌ی هندلر (که هم مسیرِ
    claimِ فوری هم مسیرِ صف/تایم‌اوت رو می‌بینه) مدیریت می‌شه، نه اینجا.

    قفل اول روی ردیفِ room گرفته می‌شه (نه user)، چون رقابتِ اصلی سرِ
    ظرفیتِ همون اتاقه؛ دو تا claim هم‌زمان برای یه اتاق پشتِ سرِ هم صف
    می‌کشن و هرکدوم با دیدِ تازه (شمارشِ اعضای به‌روز) تصمیم می‌گیرن.

    conflict_check همون قراردادِ create_chat_room رو داره: داخلِ قفل،
    بعدِ چکِ active_room_id، تزریق می‌شه — هم مسیرِ claimِ فوری هم
    مسیرِ trigger از صف (که هردو از همین تابع رد می‌شن) رو می‌پوشونه."""
    async with async_session() as session:
        room_result = await session.execute(
            select(ChatRoom).where(ChatRoom.id == room_id).with_for_update()
        )
        room = room_result.scalar_one_or_none()
        if room is None:
            return None, "not_found"
        if room.status != RoomStatus.open:
            return None, "room_not_open"

        user_result = await session.execute(
            select(User).where(User.id == user_id).with_for_update()
        )
        user = user_result.scalar_one_or_none()
        if user is None:
            return None, "not_found"
        if user.active_room_id is not None:
            return None, "has_active_room"
        if conflict_check is not None:
            conflict_reason = await conflict_check()
            if conflict_reason is not None:
                return None, conflict_reason

        member_count = (
            await session.execute(
                select(func.count()).select_from(ChatRoomMember).where(ChatRoomMember.room_id == room_id)
            )
        ).scalar_one()
        if member_count >= room.capacity:
            return None, "room_full"

        session.add(ChatRoomMember(room_id=room_id, user_id=user_id))
        user.active_room_id = room_id

        await session.commit()
        await session.refresh(room)
        return room, None


async def leave_chat_room(user_id: int) -> tuple[dict | None, str | None]:
    """یه عضوِ عادی (نه owner) رو از اتاقش خارج می‌کنه. اگه بعدِ خروج
    فقط owner بمونه، اتاق خودکار حذف می‌شه (status=deleted) و
    active_room_idِ owner هم پاک می‌شه.

    خروجی: (info, None) در موفقیت، یا (None, error_code) که یکی از
    "not_found" (اتاقِ فعالی نداره)، "is_owner" (owner نمی‌تونه ترک
    کنه، باید ببنده/حذفش کنه) است. info شاملِ room_id، owner_id،
    auto_deleted، و remaining_member_ids (برای broadcast) است.

    ترتیبِ قفل عمداً همون room-then-user ِ join_chat_room است: اول یه
    خوندنِ بدونِ قفل برای پیدا کردنِ room_id، بعد قفلِ room، بعد قفلِ
    user. اگه برعکس می‌شد (اول user)، دو تابع می‌تونستن رو دو ردیفِ
    مشترک قفلِ برعکسِ هم بگیرن و توریِ deadlock بسازن."""
    async with async_session() as session:
        user_peek = await session.get(User, user_id)
        if user_peek is None or user_peek.active_room_id is None:
            return None, "not_found"
        room_id = user_peek.active_room_id

        room_result = await session.execute(
            select(ChatRoom).where(ChatRoom.id == room_id).with_for_update()
        )
        room = room_result.scalar_one_or_none()
        if room is None:
            return None, "not_found"

        user_result = await session.execute(
            select(User).where(User.id == user_id).with_for_update()
        )
        user = user_result.scalar_one_or_none()
        if user is None or user.active_room_id != room_id:
            return None, "not_found"  # بینِ خوندنِ اول و قفل، وضعیت عوض شده

        if room.owner_id == user_id:
            return None, "is_owner"

        await session.execute(
            delete(ChatRoomMember).where(ChatRoomMember.room_id == room_id, ChatRoomMember.user_id == user_id)
        )
        user.active_room_id = None

        remaining_count = (
            await session.execute(
                select(func.count()).select_from(ChatRoomMember).where(ChatRoomMember.room_id == room_id)
            )
        ).scalar_one()

        remaining_ids_result = await session.execute(
            select(ChatRoomMember.user_id).where(ChatRoomMember.room_id == room_id)
        )
        remaining_member_ids = [row[0] for row in remaining_ids_result.all()]

        auto_deleted = remaining_count <= 1
        if auto_deleted:
            room.status = RoomStatus.deleted
            for uid in remaining_member_ids:
                remaining_user = await session.get(User, uid)
                if remaining_user is not None:
                    remaining_user.active_room_id = None
            await session.execute(delete(ChatRoomMember).where(ChatRoomMember.room_id == room_id))

        await session.commit()
        return {
            "room_id": room_id,
            "owner_id": room.owner_id,
            "auto_deleted": auto_deleted,
            "remaining_member_ids": remaining_member_ids,
        }, None


async def delete_chat_room(owner_id: int) -> tuple[dict | None, str | None]:
    """owner اتاقِ خودش رو کامل حذف می‌کنه: همون منطقِ مسیرِ auto-delete
    در leave_chat_room (status→deleted، پاک‌کردنِ active_room_id همه‌ی
    اعضا، حذفِ ردیف‌های ChatRoomMember، همه تو یه تراکنش)، فقط
    trigger‌ش دکمه‌ی owner‌ه نه شمارشِ اعضا. قفلِ روی ردیفِ room همون
    محافظتی رو می‌ده که join_chat_room/leave_chat_room دارن: هر
    عملیاتِ هم‌زمانِ دیگه روی همین اتاق (join/leave) پشتِ این قفل صف
    می‌کشه، پس لیستِ اعضایی که می‌خونیم قطعاً به‌روزه.

    خروجی: (info, None) در موفقیت، یا (None, error_code) که یکی از
    "not_found" (اتاقِ فعالی نداره)، "not_owner" (اتاقِ خودش نیست) است.
    info شاملِ room_id و member_ids (همه‌ی کسانی که باید آینه‌ی Redis
    و پیامِ سیستمی بگیرن، شاملِ خودِ owner) است."""
    async with async_session() as session:
        owner_peek = await session.get(User, owner_id)
        if owner_peek is None or owner_peek.active_room_id is None:
            return None, "not_found"
        room_id = owner_peek.active_room_id

        room_result = await session.execute(
            select(ChatRoom).where(ChatRoom.id == room_id).with_for_update()
        )
        room = room_result.scalar_one_or_none()
        if room is None:
            return None, "not_found"
        if room.owner_id != owner_id:
            return None, "not_owner"

        member_ids_result = await session.execute(
            select(ChatRoomMember.user_id).where(ChatRoomMember.room_id == room_id)
        )
        member_ids = [row[0] for row in member_ids_result.all()]

        for uid in member_ids:
            member_user = await session.get(User, uid)
            if member_user is not None:
                member_user.active_room_id = None

        room.status = RoomStatus.deleted
        await session.execute(delete(ChatRoomMember).where(ChatRoomMember.room_id == room_id))

        await session.commit()
        return {"room_id": room_id, "member_ids": member_ids}, None


async def kick_room_member(owner_id: int, target_user_id: int) -> tuple[dict | None, str | None]:
    """owner یه عضوِ عادیِ اتاقِ خودش رو اخراج می‌کنه. منطقش تقریباً
    عینِ leave_chat_room است (همون قفلِ room-then-user، همون چکِ
    auto-delete وقتی فقط owner بمونه)، با این تفاوت که اینجا owner
    تصمیم می‌گیره کی بره، نه خودِ همون فرد.

    خروجی: (info, None) در موفقیت، یا (None, error_code) که یکی از
    "not_found" (owner اتاقِ فعالی نداره)، "not_owner" (caller مالکِ
    این اتاق نیست)، "cannot_kick_self"، "not_a_member" (target واقعاً
    عضوِ همین اتاق نیست) است."""
    async with async_session() as session:
        owner_peek = await session.get(User, owner_id)
        if owner_peek is None or owner_peek.active_room_id is None:
            return None, "not_found"
        room_id = owner_peek.active_room_id

        room_result = await session.execute(
            select(ChatRoom).where(ChatRoom.id == room_id).with_for_update()
        )
        room = room_result.scalar_one_or_none()
        if room is None:
            return None, "not_found"
        if room.owner_id != owner_id:
            return None, "not_owner"
        if target_user_id == owner_id:
            return None, "cannot_kick_self"

        target_result = await session.execute(
            select(User).where(User.id == target_user_id).with_for_update()
        )
        target = target_result.scalar_one_or_none()
        if target is None or target.active_room_id != room_id:
            return None, "not_a_member"

        await session.execute(
            delete(ChatRoomMember).where(
                ChatRoomMember.room_id == room_id, ChatRoomMember.user_id == target_user_id
            )
        )
        target.active_room_id = None

        remaining_count = (
            await session.execute(
                select(func.count()).select_from(ChatRoomMember).where(ChatRoomMember.room_id == room_id)
            )
        ).scalar_one()
        remaining_ids_result = await session.execute(
            select(ChatRoomMember.user_id).where(ChatRoomMember.room_id == room_id)
        )
        remaining_member_ids = [row[0] for row in remaining_ids_result.all()]

        auto_deleted = remaining_count <= 1
        if auto_deleted:
            room.status = RoomStatus.deleted
            for uid in remaining_member_ids:
                remaining_user = await session.get(User, uid)
                if remaining_user is not None:
                    remaining_user.active_room_id = None
            await session.execute(delete(ChatRoomMember).where(ChatRoomMember.room_id == room_id))

        await session.commit()
        return {
            "room_id": room_id,
            "target_user_id": target_user_id,
            "auto_deleted": auto_deleted,
            "remaining_member_ids": remaining_member_ids,
        }, None


async def set_room_open_status(owner_id: int, is_open: bool) -> tuple[dict | None, str | None]:
    """owner اتاقشو می‌بنده یا دوباره باز می‌کنه. برخلافِ حذف/اخراج،
    اینجا هیچ عضویتی تغییر نمی‌کنه؛ فقط status عوض می‌شه (open<->closed)
    که relay.py قبل از رله‌ی هر پیام چکش می‌کنه.

    خروجی: (info, None) در موفقیت، یا (None, error_code) که یکی از
    "not_found"، "not_owner" است."""
    async with async_session() as session:
        owner_peek = await session.get(User, owner_id)
        if owner_peek is None or owner_peek.active_room_id is None:
            return None, "not_found"
        room_id = owner_peek.active_room_id

        room_result = await session.execute(
            select(ChatRoom).where(ChatRoom.id == room_id).with_for_update()
        )
        room = room_result.scalar_one_or_none()
        if room is None or room.status == RoomStatus.deleted:
            return None, "not_found"
        if room.owner_id != owner_id:
            return None, "not_owner"

        room.status = RoomStatus.open if is_open else RoomStatus.closed
        await session.commit()

        member_ids_result = await session.execute(
            select(ChatRoomMember.user_id).where(ChatRoomMember.room_id == room_id)
        )
        member_ids = [row[0] for row in member_ids_result.all()]
        return {"room_id": room_id, "member_ids": member_ids}, None


async def get_chat_room(room_id: int) -> ChatRoom | None:
    async with async_session() as session:
        return await session.get(ChatRoom, room_id)


async def get_room_member_ids(room_id: int) -> list[int]:
    async with async_session() as session:
        result = await session.execute(
            select(ChatRoomMember.user_id).where(ChatRoomMember.room_id == room_id)
        )
        return [row[0] for row in result.all()]


async def get_display_name(user_id: int) -> str | None:
    async with async_session() as session:
        result = await session.execute(select(User.display_name).where(User.id == user_id))
        return result.scalar_one_or_none()


async def list_users_with_active_room() -> list[tuple[int, int]]:
    """برای jobِ sync دوره‌ای: کاربرهایی که Postgres می‌گه اتاقِ فعال
    دارن، تا اگه آینه‌ی Redis (KEY_USER_ACTIVE_ROOM) عقب افتاده باشه
    (مثلاً بینِ commit و ست‌کردنِ Redis کرش شده باشه)، دوباره ست بشه."""
    async with async_session() as session:
        result = await session.execute(
            select(User.id, User.active_room_id).where(User.active_room_id.isnot(None))
        )
        return [(row[0], row[1]) for row in result.all()]


async def find_open_room_for_join(desired_gender: str) -> ChatRoom | None:
    """قدیمی‌ترین اتاقِ بازِ دارایِ ظرفیتِ خالی که با desired_gender
    سازگاره رو برمی‌گردونه (یا None). سازگاری دوطرفه‌ست: desired_gender
    == "any" هر نوع اتاقی رو می‌پذیره، و room.gender_pref == "any" هر
    جستجوگری رو می‌پذیره؛ وگرنه باید دقیقاً یکی باشن.

    این فقط یه پیشنهاده، نه ادعای قطعی — claimِ واقعی با join_chat_room
    و قفلِ FOR UPDATE انجام می‌شه؛ ممکنه بینِ این کوئری و claim یکی
    دیگه زودتر برسه، که در اون صورت لایه‌ی هندلر باید صف رو امتحان کنه."""
    if desired_gender == "any":
        compatible_prefs = [RoomGenderPref.male, RoomGenderPref.female, RoomGenderPref.any]
    else:
        compatible_prefs = [RoomGenderPref(desired_gender), RoomGenderPref.any]

    async with async_session() as session:
        member_counts = (
            select(ChatRoomMember.room_id, func.count().label("cnt"))
            .group_by(ChatRoomMember.room_id)
            .subquery()
        )
        result = await session.execute(
            select(ChatRoom)
            .outerjoin(member_counts, member_counts.c.room_id == ChatRoom.id)
            .where(ChatRoom.status == RoomStatus.open)
            .where(ChatRoom.gender_pref.in_(compatible_prefs))
            .where(func.coalesce(member_counts.c.cnt, 0) < ChatRoom.capacity)
            .order_by(ChatRoom.created_at.asc())
            .limit(1)
        )
        return result.scalar_one_or_none()


async def list_open_room_ids_with_spare_capacity() -> list[int]:
    """برای سیفتی‌نتِ دوره‌ای: id همه‌ی اتاق‌های بازی که هنوز جا دارن.
    شمارشِ اینجا صرفاً برای تصمیمِ «ارزششو داره دوباره تلاش کنیم یا نه»
    است، نه یه مرزِ تراکنشی؛ enforcementِ واقعیِ ظرفیت همون قفلِ
    join_chat_room است."""
    async with async_session() as session:
        member_counts = (
            select(ChatRoomMember.room_id, func.count().label("cnt"))
            .group_by(ChatRoomMember.room_id)
            .subquery()
        )
        result = await session.execute(
            select(ChatRoom.id)
            .outerjoin(member_counts, member_counts.c.room_id == ChatRoom.id)
            .where(ChatRoom.status == RoomStatus.open)
            .where(func.coalesce(member_counts.c.cnt, 0) < ChatRoom.capacity)
        )
        return [row[0] for row in result.all()]


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
