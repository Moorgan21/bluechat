"""تست‌های واحد برای فازِ ۷ (UI): show_room_menu باید بینِ منوی
ایجاد/عضویت و نمایشِ وضعیتِ اتاقِ فعلی درست سوییچ کنه، و کیبورد رو
با وضعیتِ واقعی sync نگه داره."""

from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import delete, select

import db
import redis_client as rc
from handlers import chatroom


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
    room, _ = await db.create_chat_room(owner.id, db.RoomGenderPref.female, capacity=4, cost=20)
    await rc.set_active_room(owner.id, room.id)
    members = [owner]
    for _ in range(member_count):
        m = await make_user(coins=10)
        await db.join_chat_room(m.id, room.id)
        await rc.set_active_room(m.id, room.id)
        members.append(m)
    return room, members


def _make_message_update(user_id: int):
    update = MagicMock()
    update.callback_query = None
    update.effective_user.id = user_id
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()
    return update


async def test_show_room_menu_without_active_room_shows_create_join_menu(make_user):
    user = await make_user(coins=10)
    update = _make_message_update(user.id)
    context = MagicMock()

    await chatroom.show_room_menu(update, context)

    update.message.reply_text.assert_awaited_once()
    text = update.message.reply_text.await_args.args[0]
    assert "می‌تونی یه اتاقِ گروهیِ دائمی بسازی" in text


async def test_show_room_menu_shows_owner_status(make_user):
    room, (owner, member) = await _make_room(make_user, member_count=1)
    update = _make_message_update(owner.id)
    context = MagicMock()

    await chatroom.show_room_menu(update, context)

    text = update.message.reply_text.await_args.args[0]
    assert "اتاقِ فعلیِ تو" in text
    assert "دخترونه" in text
    assert "2 از 4 نفر" in text
    assert "باز" in text
    assert "owner" in text

    await _cleanup_room(room.id)


async def test_show_room_menu_shows_member_status(make_user):
    room, (owner, member) = await _make_room(make_user, member_count=1)
    update = _make_message_update(member.id)
    context = MagicMock()

    await chatroom.show_room_menu(update, context)

    text = update.message.reply_text.await_args.args[0]
    assert "نقشِ تو: عضو" in text

    await _cleanup_room(room.id)


async def test_show_room_menu_reflects_closed_status(make_user):
    room, (owner, member) = await _make_room(make_user, member_count=1)
    await db.set_room_open_status(owner.id, is_open=False)

    update = _make_message_update(owner.id)
    context = MagicMock()
    await chatroom.show_room_menu(update, context)

    text = update.message.reply_text.await_args.args[0]
    assert "بسته" in text

    await _cleanup_room(room.id)


async def test_show_room_menu_self_heals_stale_redis(make_user):
    user = await make_user(coins=10)
    await rc.set_active_room(user.id, 999_999)

    update = _make_message_update(user.id)
    context = MagicMock()
    await chatroom.show_room_menu(update, context)

    assert await rc.get_active_room(user.id) is None
    text = update.message.reply_text.await_args.args[0]
    assert "می‌تونی یه اتاقِ گروهیِ دائمی بسازی" in text  # به منوی معمولی برگشت
