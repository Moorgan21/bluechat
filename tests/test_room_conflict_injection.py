"""تست‌های واحد برای بستنِ گپِ cross-system فازِ ۲: پارامترِ
conflict_check که داخلِ خودِ تراکنشِ create_chat_room/join_chat_room
تزریق می‌شه، بدونِ اینکه db/queries.py مستقیماً از redis_client چیزی
بدونه."""

from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import delete, select

import db
import redis_client as rc
from handlers.chatroom import matching


async def _cleanup_room(room_id: int) -> None:
    async with db.async_session() as session:
        for user in (
            await session.execute(select(db.User).where(db.User.active_room_id == room_id))
        ).scalars().all():
            user.active_room_id = None
        await session.execute(delete(db.ChatRoomMember).where(db.ChatRoomMember.room_id == room_id))
        await session.execute(delete(db.ChatRoom).where(db.ChatRoom.id == room_id))
        await session.commit()


def _make_context():
    context = MagicMock()
    context.bot = MagicMock()
    context.bot.send_message = AsyncMock()
    context.job_queue = MagicMock()
    context.job_queue.run_once = MagicMock()
    context.job_queue.get_jobs_by_name = MagicMock(return_value=[])
    return context


# ---------------------------------------------------------------------
# دو تستِ اصلی: closure رو مستقیم mock می‌کنن
# ---------------------------------------------------------------------

async def test_create_chat_room_rejects_when_conflict_check_reports_conflict(make_user):
    owner = await make_user(coins=25)

    room, error = await db.create_chat_room(
        owner.id,
        db.RoomGenderPref.any,
        capacity=3,
        cost=20,
        conflict_check=AsyncMock(return_value="in_1to1"),
    )

    assert room is None
    assert error == "in_1to1"

    async with db.async_session() as session:
        refreshed = await session.get(db.User, owner.id)
        assert refreshed.coins == 25  # دست‌نخورده، سکه کسر نشد
        assert refreshed.active_room_id is None  # هیچ اتاقی هم ساخته نشد


async def test_join_chat_room_rejects_when_conflict_check_reports_conflict(make_user):
    owner = await make_user(coins=20)
    room, _ = await db.create_chat_room(owner.id, db.RoomGenderPref.any, capacity=5, cost=20)
    joiner = await make_user(coins=10)

    joined_room, error = await db.join_chat_room(
        joiner.id, room.id, conflict_check=AsyncMock(return_value="in_queue")
    )

    assert joined_room is None
    assert error == "in_queue"

    async with db.async_session() as session:
        refreshed = await session.get(db.User, joiner.id)
        assert refreshed.active_room_id is None
        member_count = len(
            (await session.execute(select(db.ChatRoomMember).where(db.ChatRoomMember.room_id == room.id)))
            .scalars()
            .all()
        )
        assert member_count == 1  # فقط owner، joiner اضافه نشد

    await _cleanup_room(room.id)


# ---------------------------------------------------------------------
# سطحِ هندلر: رفتارِ بازگشتِ سکه وقتی conflict_check واقعی (نه mock)
# با ورودِ همون‌لحظه‌ای به چتِ ۱به۱ فعال می‌شه
# ---------------------------------------------------------------------

async def test_try_join_room_refunds_coins_on_real_conflict(make_user):
    owner = await make_user(coins=20)
    room, _ = await db.create_chat_room(owner.id, db.RoomGenderPref.any, capacity=5, cost=20)
    joiner = await make_user(coins=10)
    await rc.set_partner(joiner.id, 999_999_999)  # انگار همون‌لحظه وارد یه چتِ ۱به۱ شده

    context = _make_context()
    await matching.try_join_room(joiner.id, "any", context)

    async with db.async_session() as session:
        refreshed = await session.get(db.User, joiner.id)
        assert refreshed.active_room_id is None

    context.job_queue.run_once.assert_not_called()  # نباید وارد صفِ اتاق شده باشه
    assert "همون لحظه وارد یه چتِ دیگه شدی" in context.bot.send_message.await_args.args[1]

    await rc.clear_partner(joiner.id)
    await _cleanup_room(room.id)


async def test_try_fill_room_from_queue_refunds_stale_candidate_with_real_conflict(make_user):
    waiter = await make_user(coins=10)
    await rc.enqueue_room_join(waiter.id, "any")
    await rc.set_partner(waiter.id, 999_999_999)  # وقتی تو صف بود، وارد یه چتِ ۱به۱ شد

    owner = await make_user(coins=20)
    room, _ = await db.create_chat_room(owner.id, db.RoomGenderPref.any, capacity=5, cost=20)

    context = _make_context()
    await matching.try_fill_room_from_queue(room.id, context)

    async with db.async_session() as session:
        refreshed = await session.get(db.User, waiter.id)
        assert refreshed.active_room_id is None  # عضوِ این اتاق نشد

    assert await rc.is_waiting_room_join(waiter.id) is None  # از صف هم درومد

    waiter_msgs = [c.args[1] for c in context.bot.send_message.await_args_list if c.args[0] == waiter.id]
    assert any("همون لحظه وارد یه چتِ دیگه شدی" in t for t in waiter_msgs)

    await rc.clear_partner(waiter.id)
    await _cleanup_room(room.id)
