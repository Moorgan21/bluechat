"""تست‌های واحد برای ایج‌کیسِ بن‌شدن: وقتی judge.py/profile_judge.py یه
کاربر رو بن می‌کنه، باید بلافاصله از چتِ ۱به۱ یا اتاقِ چتِ فعلی‌ش هم
خارج بشه. سه حالتِ اتاق: owner بن شده (اتاق بسته می‌شه)، آخرین عضوِ
غیرِ owner بن شده (اتاق بسته می‌شه)، عضوِ عادیِ دیگه‌ای هم مونده
(فقط از عضویت خارج می‌شه، اتاق دست‌نخورده می‌مونه)."""

from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import delete, select

import ban_enforcement
import db
import redis_client as rc


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


async def _room_status(room_id: int):
    async with db.async_session() as session:
        room = await session.get(db.ChatRoom, room_id)
        return room.status


def _make_bot():
    bot = MagicMock()
    bot.send_message = AsyncMock()
    return bot


# --- db.remove_banned_user_from_room ---

async def test_remove_banned_owner_closes_room_without_deleting(make_user):
    room, (owner, member) = await _make_room(make_user, member_count=1)

    result = await db.remove_banned_user_from_room(owner.id)

    assert result["outcome"] == "owner_banned"
    assert result["remaining_member_ids"] == [member.id]
    assert await _room_status(room.id) == db.RoomStatus.closed
    # اتاق حذف نشده، عضویتِ بقیه دست‌نخورده مونده
    async with db.async_session() as session:
        remaining = (
            await session.execute(select(db.ChatRoomMember).where(db.ChatRoomMember.room_id == room.id))
        ).scalars().all()
        assert len(remaining) == 2

    await _cleanup_room(room.id)


async def test_remove_banned_last_regular_member_closes_room(make_user):
    room, (owner, member) = await _make_room(make_user, member_count=1)

    result = await db.remove_banned_user_from_room(member.id)

    assert result["outcome"] == "member_banned_last"
    assert result["remaining_member_ids"] == [owner.id]
    assert await _room_status(room.id) == db.RoomStatus.closed

    await _cleanup_room(room.id)


async def test_remove_banned_regular_member_with_others_left_does_not_close_room(make_user):
    room, (owner, member_a, member_b) = await _make_room(make_user, member_count=2)

    result = await db.remove_banned_user_from_room(member_a.id)

    assert result["outcome"] == "member_banned"
    assert set(result["remaining_member_ids"]) == {owner.id, member_b.id}
    assert await _room_status(room.id) == db.RoomStatus.open

    await _cleanup_room(room.id)


async def test_remove_banned_user_without_room_returns_none(make_user):
    user = await make_user()
    assert await db.remove_banned_user_from_room(user.id) is None


# --- ban_enforcement.enforce_ban ---

async def test_enforce_ban_closes_1to1_chat_and_notifies_partner(make_user):
    a = await make_user()
    b = await make_user()
    await rc.set_partner(a.id, b.id)

    bot = _make_bot()
    await ban_enforcement.enforce_ban(bot, a.id)

    assert await rc.get_partner(a.id) is None
    assert await rc.get_partner(b.id) is None
    bot.send_message.assert_awaited_once()
    call = bot.send_message.await_args
    assert call.args[0] == b.id
    assert "بن شد" in call.args[1]


async def test_enforce_ban_owner_closes_room_and_notifies_members(make_user):
    room, (owner, member) = await _make_room(make_user, member_count=1)

    bot = _make_bot()
    await ban_enforcement.enforce_ban(bot, owner.id)

    assert await _room_status(room.id) == db.RoomStatus.closed
    bot.send_message.assert_awaited_once_with(member.id, "🚫 سازنده‌ی این اتاق توسطِ سیستم بن شد و اتاق بسته شد.")
    assert await rc.get_active_room(owner.id) is None

    await _cleanup_room(room.id)


async def test_enforce_ban_regular_member_kicked_without_closing_room(make_user):
    room, (owner, member_a, member_b) = await _make_room(make_user, member_count=2)

    bot = _make_bot()
    await ban_enforcement.enforce_ban(bot, member_a.id)

    assert await _room_status(room.id) == db.RoomStatus.open
    sent_to = {c.args[0] for c in bot.send_message.await_args_list}
    assert sent_to == {owner.id, member_b.id}
    for c in bot.send_message.await_args_list:
        assert "اخراج شد" in c.args[1]

    await _cleanup_room(room.id)


async def test_enforce_ban_noop_when_no_active_chat_or_room(make_user):
    user = await make_user()
    bot = _make_bot()

    await ban_enforcement.enforce_ban(bot, user.id)

    bot.send_message.assert_not_awaited()
