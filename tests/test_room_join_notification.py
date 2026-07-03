"""تست‌های واحد برای رفعِ گزارشِ کاربر: وقتی یکی به یه اتاق ملحق می‌شه،
باید به بقیه‌ی اعضای *موجود* هم خبر بده، نه فقط به خودِ نفرِ تازه‌وارد."""

from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import delete, select

import db
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


async def _set_display_name(user_id: int, name: str) -> None:
    async with db.async_session() as session:
        u = await session.get(db.User, user_id)
        u.display_name = name
        await session.commit()


async def test_immediate_join_notifies_existing_members(make_user):
    owner = await make_user(coins=20)
    await _set_display_name(owner.id, "علی")
    room, _ = await db.create_chat_room(owner.id, db.RoomGenderPref.any, capacity=5, cost=20)

    joiner = await make_user(coins=10)
    await _set_display_name(joiner.id, "سارا")

    context = _make_context()
    await matching.try_join_room(joiner.id, "any", context)

    owner_calls = [c for c in context.bot.send_message.await_args_list if c.args[0] == owner.id]
    assert any("سارا" in c.args[1] and "ملحق شد" in c.args[1] for c in owner_calls)

    # خودِ جوینده هم پیامِ خوش‌آمد می‌گیره، نه پیامِ «ملحق شد» درباره‌ی خودش
    joiner_calls = [c for c in context.bot.send_message.await_args_list if c.args[0] == joiner.id]
    assert any("ملحق شدی" in c.args[1] for c in joiner_calls)
    assert not any("سارا به اتاق ملحق شد" in c.args[1] for c in joiner_calls)

    await _cleanup_room(room.id)


async def test_join_from_queue_notifies_all_existing_members(make_user):
    """وقتی یه اتاقِ تازه‌ساخته چند نفرو از صف یک‌جا جذب می‌کنه، هرکدوم
    باید بقیه‌ی اعضای *قبل از خودشون* رو باخبر کنه، نه فقط owner."""
    owner = await make_user(coins=20)
    room, _ = await db.create_chat_room(owner.id, db.RoomGenderPref.any, capacity=5, cost=20)

    waiter1 = await make_user(coins=10)
    await _set_display_name(waiter1.id, "نفرِ اول")
    waiter2 = await make_user(coins=10)
    await _set_display_name(waiter2.id, "نفرِ دوم")

    import redis_client as rc

    await rc.enqueue_room_join(waiter1.id, "any")
    await rc.enqueue_room_join(waiter2.id, "any")

    context = _make_context()
    await matching.try_fill_room_from_queue(room.id, context)

    # نفرِ دوم باید ببینه هم owner هم نفرِ اول از قبل تو اتاقن؛ پس پیامِ
    # ورودِ «نفرِ دوم» باید به owner *و* نفرِ اول رفته باشه.
    owner_texts = [c.args[1] for c in context.bot.send_message.await_args_list if c.args[0] == owner.id]
    waiter1_texts = [c.args[1] for c in context.bot.send_message.await_args_list if c.args[0] == waiter1.id]

    assert any("نفرِ دوم" in t and "ملحق شد" in t for t in owner_texts)
    assert any("نفرِ دوم" in t and "ملحق شد" in t for t in waiter1_texts)

    await _cleanup_room(room.id)
