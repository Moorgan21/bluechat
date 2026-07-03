"""تست‌های واحد برای فازِ ۳ (فلوی عضویتِ اتاقِ چت): claimِ اتمیک،
سازگاریِ جنسیت، فلوی صف/تایم‌اوت، و trigger پرکردنِ اتاق از صف."""

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
# join_chat_room (لایه‌ی دیتابیس)
# ---------------------------------------------------------------------

async def test_join_chat_room_success_adds_member_without_charging_coins(make_user):
    owner = await make_user(coins=20)
    joiner = await make_user(coins=10)
    room, _ = await db.create_chat_room(owner.id, db.RoomGenderPref.any, capacity=3, cost=20)

    joined_room, error = await db.join_chat_room(joiner.id, room.id)

    assert error is None
    assert joined_room.id == room.id

    async with db.async_session() as session:
        refreshed = await session.get(db.User, joiner.id)
        assert refreshed.active_room_id == room.id
        assert refreshed.coins == 10  # join_chat_room خودش سکه کم نمی‌کنه

        count = len(
            (await session.execute(select(db.CoinTransaction).where(db.CoinTransaction.user_id == joiner.id)))
            .scalars()
            .all()
        )
        assert count == 0

    await _cleanup_room(room.id)


async def test_join_chat_room_fails_when_full(make_user):
    owner = await make_user(coins=20)
    room, _ = await db.create_chat_room(owner.id, db.RoomGenderPref.any, capacity=2, cost=20)
    second = await make_user(coins=10)
    await db.join_chat_room(second.id, room.id)  # الان پره (owner + second == capacity=2)

    third = await make_user(coins=10)
    joined_room, error = await db.join_chat_room(third.id, room.id)

    assert joined_room is None
    assert error == "room_full"

    async with db.async_session() as session:
        refreshed = await session.get(db.User, third.id)
        assert refreshed.active_room_id is None

    await _cleanup_room(room.id)


async def test_join_chat_room_fails_when_room_closed(make_user):
    owner = await make_user(coins=20)
    room, _ = await db.create_chat_room(owner.id, db.RoomGenderPref.any, capacity=5, cost=20)
    async with db.async_session() as session:
        r = await session.get(db.ChatRoom, room.id)
        r.status = db.RoomStatus.closed
        await session.commit()

    joiner = await make_user(coins=10)
    joined_room, error = await db.join_chat_room(joiner.id, room.id)

    assert joined_room is None
    assert error == "room_not_open"

    await _cleanup_room(room.id)


async def test_join_chat_room_fails_when_user_already_has_active_room(make_user):
    owner_a = await make_user(coins=20)
    owner_b = await make_user(coins=20)
    room_a, _ = await db.create_chat_room(owner_a.id, db.RoomGenderPref.any, capacity=5, cost=20)
    room_b, _ = await db.create_chat_room(owner_b.id, db.RoomGenderPref.any, capacity=5, cost=20)

    joined_room, error = await db.join_chat_room(owner_a.id, room_b.id)

    assert joined_room is None
    assert error == "has_active_room"

    await _cleanup_room(room_a.id)
    await _cleanup_room(room_b.id)


# ---------------------------------------------------------------------
# find_open_room_for_join (سازگاریِ جنسیتِ دوطرفه + ظرفیت)
# ---------------------------------------------------------------------

async def test_find_open_room_matches_exact_gender(make_user):
    owner = await make_user(coins=20)
    room, _ = await db.create_chat_room(owner.id, db.RoomGenderPref.female, capacity=5, cost=20)

    assert (await db.find_open_room_for_join("female")).id == room.id
    assert await db.find_open_room_for_join("male") is None

    await _cleanup_room(room.id)


async def test_find_open_room_any_room_matches_any_search(make_user):
    owner = await make_user(coins=20)
    room, _ = await db.create_chat_room(owner.id, db.RoomGenderPref.any, capacity=5, cost=20)

    assert (await db.find_open_room_for_join("male")).id == room.id
    assert (await db.find_open_room_for_join("female")).id == room.id
    assert (await db.find_open_room_for_join("any")).id == room.id

    await _cleanup_room(room.id)


async def test_find_open_room_any_search_matches_specific_room(make_user):
    owner = await make_user(coins=20)
    room, _ = await db.create_chat_room(owner.id, db.RoomGenderPref.male, capacity=5, cost=20)

    assert (await db.find_open_room_for_join("any")).id == room.id

    await _cleanup_room(room.id)


async def test_find_open_room_excludes_full_rooms(make_user):
    owner = await make_user(coins=20)
    room, _ = await db.create_chat_room(owner.id, db.RoomGenderPref.any, capacity=2, cost=20)
    second = await make_user(coins=10)
    await db.join_chat_room(second.id, room.id)  # پر شد

    assert await db.find_open_room_for_join("any") is None

    await _cleanup_room(room.id)


# ---------------------------------------------------------------------
# try_join_room: مسیرِ فوری در برابرِ مسیرِ صف
# ---------------------------------------------------------------------

async def test_try_join_room_immediate_match(make_user):
    owner = await make_user(coins=20)
    room, _ = await db.create_chat_room(owner.id, db.RoomGenderPref.any, capacity=5, cost=20)
    joiner = await make_user(coins=10)
    context = _make_context()

    await matching.try_join_room(joiner.id, "any", context)

    async with db.async_session() as session:
        refreshed = await session.get(db.User, joiner.id)
        assert refreshed.active_room_id == room.id

    context.job_queue.run_once.assert_not_called()  # نیازی به صف نبود
    context.bot.send_message.assert_awaited()
    assert "ملحق شدی" in context.bot.send_message.await_args.args[1]

    await _cleanup_room(room.id)


async def test_try_join_room_no_match_enqueues_and_schedules_timeout(make_user):
    joiner = await make_user(coins=10)
    context = _make_context()

    await matching.try_join_room(joiner.id, "female", context)

    assert await rc.is_waiting_room_join(joiner.id) == "female"
    context.job_queue.run_once.assert_called_once()
    kwargs = context.job_queue.run_once.call_args.kwargs
    assert kwargs["when"] == rc.ROOM_JOIN_TIMEOUT_SECONDS
    assert kwargs["name"] == f"room_join_timeout_{joiner.id}"

    await rc.dequeue_room_join(joiner.id, "female")


# ---------------------------------------------------------------------
# تایم‌اوت
# ---------------------------------------------------------------------

async def test_room_join_timeout_refunds_when_still_waiting(make_user):
    joiner = await make_user(coins=10)
    await db.deduct_coins(joiner.id, matching.ROOM_JOIN_COST, "test_setup")
    await rc.enqueue_room_join(joiner.id, "male")

    context = _make_context()
    context.job = MagicMock()
    context.job.data = {"user_id": joiner.id, "desired_gender": "male"}

    await matching._room_join_timeout_job(context)

    assert await rc.is_waiting_room_join(joiner.id) is None
    async with db.async_session() as session:
        refreshed = await session.get(db.User, joiner.id)
        assert refreshed.coins == 10  # برگشت کامل


async def test_room_join_timeout_noop_when_already_claimed(make_user):
    joiner = await make_user(coins=7)
    context = _make_context()
    context.job = MagicMock()
    context.job.data = {"user_id": joiner.id, "desired_gender": "male"}

    # عمداً enqueue نشده، یعنی انگار قبلاً claim شده
    await matching._room_join_timeout_job(context)

    async with db.async_session() as session:
        refreshed = await session.get(db.User, joiner.id)
        assert refreshed.coins == 7  # دست‌نخورده، بازگشتی صورت نگرفت
    context.bot.send_message.assert_not_awaited()


# ---------------------------------------------------------------------
# try_fill_room_from_queue (trigger سمتِ عرضه)
# ---------------------------------------------------------------------

async def test_try_fill_room_from_queue_admits_waiting_searcher(make_user):
    waiter = await make_user(coins=10)
    await rc.enqueue_room_join(waiter.id, "any")
    context = _make_context()

    owner = await make_user(coins=20)
    room, _ = await db.create_chat_room(owner.id, db.RoomGenderPref.female, capacity=5, cost=20)

    await matching.try_fill_room_from_queue(room.id, context)

    async with db.async_session() as session:
        refreshed = await session.get(db.User, waiter.id)
        assert refreshed.active_room_id == room.id

    assert await rc.is_waiting_room_join(waiter.id) is None
    context.job_queue.get_jobs_by_name.assert_called_with(f"room_join_timeout_{waiter.id}")
    context.bot.send_message.assert_awaited()

    await _cleanup_room(room.id)


async def test_try_fill_room_from_queue_admits_multiple_up_to_capacity(make_user):
    """اتاقِ تازه‌ساخته با ظرفیتِ ۳ باید چند جستجوگرِ سازگار رو یک‌جا
    جذب کنه، نه فقط یکی."""
    waiter1 = await make_user(coins=10)
    waiter2 = await make_user(coins=10)
    waiter3 = await make_user(coins=10)  # این یکی نباید جا داشته باشه
    await rc.enqueue_room_join(waiter1.id, "any")
    await rc.enqueue_room_join(waiter2.id, "any")
    await rc.enqueue_room_join(waiter3.id, "any")
    context = _make_context()

    owner = await make_user(coins=20)
    room, _ = await db.create_chat_room(owner.id, db.RoomGenderPref.any, capacity=3, cost=20)  # owner + 2 جا

    await matching.try_fill_room_from_queue(room.id, context)

    async with db.async_session() as session:
        w1 = await session.get(db.User, waiter1.id)
        w2 = await session.get(db.User, waiter2.id)
        w3 = await session.get(db.User, waiter3.id)
        assert w1.active_room_id == room.id
        assert w2.active_room_id == room.id
        assert w3.active_room_id is None  # جا نبود

    assert await rc.is_waiting_room_join(waiter3.id) == "any"  # هنوز تو صفه

    await rc.dequeue_room_join(waiter3.id, "any")
    await _cleanup_room(room.id)


async def test_try_fill_room_from_queue_noop_when_no_candidates(make_user):
    owner = await make_user(coins=20)
    room, _ = await db.create_chat_room(owner.id, db.RoomGenderPref.any, capacity=5, cost=20)
    context = _make_context()

    await matching.try_fill_room_from_queue(room.id, context)

    context.bot.send_message.assert_not_awaited()
    await _cleanup_room(room.id)
