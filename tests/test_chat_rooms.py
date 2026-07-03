"""تست‌های واحد برای create_chat_room (فازِ ۱-۲ از اتاقِ چت):
اقتصادِ سکه، قفلِ یک-اتاقِ-فعال، و کلمپِ ظرفیت. تستِ آخر هم کلِ فلوی
هندلرِ تلگرام (roommenu:create → roomgender → roomcap) رو با موکِ
callback_query شبیه‌سازی می‌کنه."""

from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import delete, select

import db
from handlers import chatroom


async def _cleanup_room(room_id: int) -> None:
    """users.active_room_id به chat_rooms.id رفرنس داره (بدونِ CASCADE)،
    پس قبل از حذفِ اتاق باید اول این رفرنس رو صفر کنیم، وگرنه FK رد
    می‌شه."""
    async with db.async_session() as session:
        for user in (
            await session.execute(select(db.User).where(db.User.active_room_id == room_id))
        ).scalars().all():
            user.active_room_id = None
        await session.execute(delete(db.ChatRoomMember).where(db.ChatRoomMember.room_id == room_id))
        await session.execute(delete(db.ChatRoom).where(db.ChatRoom.id == room_id))
        await session.commit()


async def test_create_chat_room_success_deducts_coins_and_sets_active_room(make_user):
    owner = await make_user(coins=25)

    room, error = await db.create_chat_room(owner.id, db.RoomGenderPref.any, capacity=3, cost=20)

    assert error is None
    assert room is not None
    assert room.owner_id == owner.id
    assert room.capacity == 3
    assert room.status == db.RoomStatus.open

    async with db.async_session() as session:
        refreshed = await session.get(db.User, owner.id)
        assert refreshed.coins == 5
        assert refreshed.active_room_id == room.id

        member = (
            await session.execute(
                select(db.ChatRoomMember).where(db.ChatRoomMember.room_id == room.id)
            )
        ).scalar_one()
        assert member.user_id == owner.id

    await _cleanup_room(room.id)


async def test_create_chat_room_insufficient_coins_creates_nothing(make_user):
    owner = await make_user(coins=5)

    room, error = await db.create_chat_room(owner.id, db.RoomGenderPref.male, capacity=2, cost=20)

    assert room is None
    assert error == "insufficient_coins"

    async with db.async_session() as session:
        refreshed = await session.get(db.User, owner.id)
        assert refreshed.coins == 5  # دست‌نخورده
        assert refreshed.active_room_id is None


async def test_create_chat_room_blocked_when_owner_already_has_active_room(make_user):
    owner = await make_user(coins=100)

    first_room, first_error = await db.create_chat_room(owner.id, db.RoomGenderPref.any, capacity=5, cost=20)
    assert first_error is None

    second_room, second_error = await db.create_chat_room(owner.id, db.RoomGenderPref.any, capacity=5, cost=20)

    assert second_room is None
    assert second_error == "has_active_room"

    async with db.async_session() as session:
        refreshed = await session.get(db.User, owner.id)
        assert refreshed.coins == 80  # فقط یه بار کسر شده، نه دوبار
        assert refreshed.active_room_id == first_room.id

    await _cleanup_room(first_room.id)


async def test_create_chat_room_unknown_user_returns_not_found():
    room, error = await db.create_chat_room(999_999_999_999, db.RoomGenderPref.any, capacity=5, cost=20)
    assert room is None
    assert error == "not_found"


async def test_create_chat_room_clamps_capacity_to_valid_range(make_user):
    owner = await make_user(coins=20)

    room, error = await db.create_chat_room(owner.id, db.RoomGenderPref.female, capacity=99, cost=20)

    assert error is None
    assert room.capacity == 5  # کلمپ‌شده به سقفِ مجاز

    await _cleanup_room(room.id)


def _make_query(user_id: int, data: str):
    query = MagicMock()
    query.data = data
    query.from_user.id = user_id
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    return query


def _make_update(query):
    update = MagicMock()
    update.callback_query = query
    update.effective_user.id = query.from_user.id
    return update


async def test_room_creation_handler_flow_end_to_end(make_user):
    """کلِ فلوی دکمه‌ها رو شبیه‌سازی می‌کنه: منو → جنسیت → ظرفیت، و چک
    می‌کنه اتاق واقعاً با تنظیماتِ درست ساخته شده و سکه کسر شده."""
    owner = await make_user(coins=25)
    context = MagicMock()
    context.user_data = {}
    context.bot = MagicMock()
    context.bot.send_message = AsyncMock()
    context.job_queue = MagicMock()
    context.job_queue.run_once = MagicMock()
    context.job_queue.get_jobs_by_name = MagicMock(return_value=[])

    query1 = _make_query(owner.id, "roommenu:create")
    await chatroom.room_menu_callback_router(_make_update(query1), context)
    query1.edit_message_text.assert_awaited()

    query2 = _make_query(owner.id, "roomgender:female")
    await chatroom.room_menu_callback_router(_make_update(query2), context)
    assert context.user_data["room_create_gender"] == "female"

    query3 = _make_query(owner.id, "roomcap:3")
    await chatroom.room_menu_callback_router(_make_update(query3), context)

    final_text = query3.edit_message_text.await_args.args[0]
    assert "اتاقت ساخته شد" in final_text

    async with db.async_session() as session:
        refreshed = await session.get(db.User, owner.id)
        assert refreshed.coins == 5
        room = await session.get(db.ChatRoom, refreshed.active_room_id)
        assert room.gender_pref == db.RoomGenderPref.female
        assert room.capacity == 3

    await _cleanup_room(room.id)
