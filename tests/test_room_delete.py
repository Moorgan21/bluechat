"""تست‌های واحد برای فازِ ۶.۵ (حذفِ اتاق توسطِ owner): تنها راهِ
خروجِ owner، چون owner نمی‌تونه مثلِ عضوِ عادی ترک کنه."""

from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import delete, select

import db
import redis_client as rc
from handlers.chatroom import moderation


async def _cleanup_room(room_id: int) -> None:
    async with db.async_session() as session:
        for user in (
            await session.execute(select(db.User).where(db.User.active_room_id == room_id))
        ).scalars().all():
            user.active_room_id = None
        await session.execute(delete(db.ChatRoomMember).where(db.ChatRoomMember.room_id == room_id))
        await session.execute(delete(db.ChatRoom).where(db.ChatRoom.id == room_id))
        await session.commit()


async def _make_room(make_user, member_count=1):
    owner = await make_user(coins=20)
    room, _ = await db.create_chat_room(owner.id, db.RoomGenderPref.any, capacity=5, cost=20)
    await rc.set_active_room(owner.id, room.id)
    members = [owner]
    for _ in range(member_count):
        m = await make_user(coins=10)
        await db.join_chat_room(m.id, room.id)
        await rc.set_active_room(m.id, room.id)
        members.append(m)
    return room, members


def _make_query(user_id: int, data: str):
    query = MagicMock()
    query.data = data
    query.from_user.id = user_id
    query.answer = AsyncMock()
    query.message = MagicMock()
    query.message.delete = AsyncMock()
    return query


def _make_update(query):
    update = MagicMock()
    update.callback_query = query
    update.effective_user.id = query.from_user.id
    return update


def _make_context():
    context = MagicMock()
    context.bot = MagicMock()
    context.bot.send_message = AsyncMock()
    return context


# ---------------------------------------------------------------------
# delete_chat_room (لایه‌ی دیتابیس)
# ---------------------------------------------------------------------

async def test_delete_chat_room_success_clears_everyone(make_user):
    room, (owner, member_b, member_c) = await _make_room(make_user, member_count=2)

    result, error = await db.delete_chat_room(owner.id)

    assert error is None
    assert result["room_id"] == room.id
    assert sorted(result["member_ids"]) == sorted([owner.id, member_b.id, member_c.id])

    async with db.async_session() as session:
        r = await session.get(db.ChatRoom, room.id)
        assert r.status == db.RoomStatus.deleted
        for uid in (owner.id, member_b.id, member_c.id):
            u = await session.get(db.User, uid)
            assert u.active_room_id is None

        remaining = (
            await session.execute(select(db.ChatRoomMember).where(db.ChatRoomMember.room_id == room.id))
        ).scalars().all()
        assert remaining == []

    await _cleanup_room(room.id)


async def test_delete_chat_room_non_owner_rejected(make_user):
    room, (owner, member) = await _make_room(make_user, member_count=1)

    result, error = await db.delete_chat_room(member.id)

    assert result is None
    assert error == "not_owner"

    async with db.async_session() as session:
        r = await session.get(db.ChatRoom, room.id)
        assert r.status == db.RoomStatus.open  # دست‌نخورده
        still_member = await session.get(db.User, member.id)
        assert still_member.active_room_id == room.id

    await _cleanup_room(room.id)


async def test_delete_chat_room_no_active_room_returns_not_found(make_user):
    user = await make_user(coins=10)
    result, error = await db.delete_chat_room(user.id)
    assert result is None
    assert error == "not_found"


# ---------------------------------------------------------------------
# delete_room_button / delete_room_confirm_callback (هندلر)
# ---------------------------------------------------------------------

async def test_delete_room_button_shows_confirmation(make_user):
    room, (owner,) = await _make_room(make_user, member_count=0)
    update = MagicMock()
    update.effective_user.id = owner.id
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()
    context = _make_context()

    await moderation.delete_room_button(update, context)

    update.message.reply_text.assert_awaited_once()
    assert "غیرقابلِ بازگشت" in update.message.reply_text.await_args.args[0]

    await _cleanup_room(room.id)


async def test_delete_room_confirm_cancel_does_nothing(make_user):
    room, (owner, member) = await _make_room(make_user, member_count=1)
    query = _make_query(owner.id, "roomdelete:cancel")
    context = _make_context()

    await moderation.delete_room_confirm_callback(_make_update(query), context)

    query.message.delete.assert_awaited()
    context.bot.send_message.assert_not_awaited()

    async with db.async_session() as session:
        r = await session.get(db.ChatRoom, room.id)
        assert r.status == db.RoomStatus.open  # حذف نشد

    await _cleanup_room(room.id)


async def test_delete_room_confirm_removes_room_and_notifies_everyone(make_user):
    room, (owner, member) = await _make_room(make_user, member_count=1)
    async with db.async_session() as session:
        u = await session.get(db.User, owner.id)
        u.display_name = "علی"
        await session.commit()

    # این اتاق باید تاریخچه داشته باشه، وگرنه پیشنهادِ پاک‌سازی نمیاد
    # (به‌جاش پیامِ «هیچ پیامی نداشت» می‌ره) و این تست شکست می‌خوره.
    await rc.record_room_message(room.id, owner.id, 1001)
    await rc.record_room_message(room.id, member.id, 1002)

    query = _make_query(owner.id, "roomdelete:confirm")
    context = _make_context()

    await moderation.delete_room_confirm_callback(_make_update(query), context)

    assert await rc.get_active_room(owner.id) is None
    assert await rc.get_active_room(member.id) is None

    calls = context.bot.send_message.await_args_list
    owner_texts = [c.args[1] for c in calls if c.args[0] == owner.id]
    member_texts = [c.args[1] for c in calls if c.args[0] == member.id]

    assert any("اتاقت حذف شد" in t for t in owner_texts)
    assert any("توسطِ owner حذف شد" in t for t in member_texts)
    assert any("پاک کنم؟" in t for t in owner_texts)  # پیشنهادِ پاک‌سازیِ تاریخچه فقط به owner

    for call in calls:
        assert call.kwargs.get("reply_markup") is not None  # همه پیام‌ها یه کیبورد دارن

    stored_members = await rc.get_deleted_room_members(room.id)
    assert sorted(stored_members) == sorted([owner.id, member.id])

    async with db.async_session() as session:
        r = await session.get(db.ChatRoom, room.id)
        assert r.status == db.RoomStatus.deleted


async def test_delete_room_confirm_stale_redis_self_heals(make_user):
    user = await make_user(coins=10)
    await rc.set_active_room(user.id, 999_999)
    query = _make_query(user.id, "roomdelete:confirm")
    context = _make_context()

    await moderation.delete_room_confirm_callback(_make_update(query), context)

    assert await rc.get_active_room(user.id) is None
    context.bot.send_message.assert_awaited_once()
    assert "دیگه فعال نیست" in context.bot.send_message.await_args.args[1]
