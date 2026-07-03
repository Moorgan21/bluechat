"""تست‌های واحد برای فازِ ۸ (متریک): rooms_created، room_joins،
room_messages، و room_auto_deleted باید درست افزایش پیدا کنن.

چون Counterهای Prometheus سطح-پروسه و مشترک بینِ کلِ سشنِ تستن، همه‌جا
با delta (قبل/بعد) چک می‌کنیم، نه مقدارِ مطلق، وگرنه به ترتیبِ اجرای
بقیه‌ی تست‌ها وابسته می‌شد."""

from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import delete, select

import db
import metrics
import redis_client as rc
from handlers.chatroom import matching, membership, relay


async def _cleanup_room(room_id: int) -> None:
    async with db.async_session() as session:
        for user in (
            await session.execute(select(db.User).where(db.User.active_room_id == room_id))
        ).scalars().all():
            user.active_room_id = None
        await session.execute(delete(db.ChatRoomMember).where(db.ChatRoomMember.room_id == room_id))
        await session.execute(delete(db.ChatRoom).where(db.ChatRoom.id == room_id))
        await session.commit()


def _counter_value(counter) -> float:
    return counter._value.get()


class _IdGen:
    def __init__(self, start=8000):
        self._next = start

    def __call__(self, *args, **kwargs):
        self._next += 1
        sent = MagicMock()
        sent.message_id = self._next
        return sent


def _make_context():
    context = MagicMock()
    context.bot = MagicMock()
    context.bot.send_message = AsyncMock(side_effect=_IdGen())
    context.job_queue = MagicMock()
    context.job_queue.run_once = MagicMock()
    context.job_queue.get_jobs_by_name = MagicMock(return_value=[])
    return context


def _make_message(message_id, text=None):
    msg = MagicMock()
    msg.message_id = message_id
    msg.text = text
    msg.caption = None
    msg.reply_to_message = None
    for field in ("photo", "sticker", "voice", "audio", "video", "video_note", "document", "animation"):
        setattr(msg, field, None)
    msg.delete = AsyncMock()
    msg.reply_text = AsyncMock()
    return msg


def _make_update(msg, user_id):
    update = MagicMock()
    update.message = msg
    update.effective_message = msg
    update.effective_user.id = user_id
    return update


async def test_create_chat_room_increments_rooms_created_metric(make_user):
    """rooms_created توی هندلر افزایش پیدا می‌کنه، نه توی db.create_chat_room
    (که فقط دیتابیسه)؛ اینجا مستقیم از هندلرِ create صدا می‌زنیم که
    incrementِ واقعی هم اجرا بشه."""
    from handlers.chatroom import creation

    owner = await make_user(coins=25)
    before = _counter_value(metrics.rooms_created)

    context = _make_context()
    context.user_data = {creation._CREATE_GENDER_KEY: "any"}
    query = MagicMock()
    query.data = "roomcap:3"
    query.from_user.id = owner.id
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    update = MagicMock()
    update.callback_query = query
    update.effective_user.id = owner.id

    await creation.room_menu_callback_router(update, context)

    assert _counter_value(metrics.rooms_created) == before + 1

    async with db.async_session() as session:
        refreshed = await session.get(db.User, owner.id)
        room_id = refreshed.active_room_id
    await _cleanup_room(room_id)


async def test_room_join_increments_room_joins_metric(make_user):
    owner = await make_user(coins=20)
    room, _ = await db.create_chat_room(owner.id, db.RoomGenderPref.any, capacity=5, cost=20)
    joiner = await make_user(coins=10)
    context = _make_context()

    before = _counter_value(metrics.room_joins)
    await matching.try_join_room(joiner.id, "any", context)
    assert _counter_value(metrics.room_joins) == before + 1

    await _cleanup_room(room.id)


async def test_room_message_relay_increments_room_messages_metric(make_user):
    owner = await make_user(coins=20)
    room, _ = await db.create_chat_room(owner.id, db.RoomGenderPref.any, capacity=5, cost=20)
    await rc.set_active_room(owner.id, room.id)
    member = await make_user(coins=10)
    await db.join_chat_room(member.id, room.id)
    await rc.set_active_room(member.id, room.id)

    context = _make_context()
    before = _counter_value(metrics.room_messages)

    msg = _make_message(100, text="سلام")
    await relay.relay_room_message(_make_update(msg, member.id), context, room.id)

    assert _counter_value(metrics.room_messages) == before + 1

    await _cleanup_room(room.id)


async def test_room_message_relay_does_not_touch_1to1_metric(make_user):
    """مطمئن می‌شیم پیامِ اتاق دیگه به‌جای متریکِ عمومی messages_relayed،
    فقط room_messagesِ اختصاصیِ خودشو افزایش می‌ده."""
    owner = await make_user(coins=20)
    room, _ = await db.create_chat_room(owner.id, db.RoomGenderPref.any, capacity=5, cost=20)
    await rc.set_active_room(owner.id, room.id)
    member = await make_user(coins=10)
    await db.join_chat_room(member.id, room.id)
    await rc.set_active_room(member.id, room.id)

    context = _make_context()
    before = _counter_value(metrics.messages_relayed)

    msg = _make_message(200, text="سلام")
    await relay.relay_room_message(_make_update(msg, member.id), context, room.id)

    assert _counter_value(metrics.messages_relayed) == before

    await _cleanup_room(room.id)


async def test_leave_auto_delete_increments_room_auto_deleted_metric(make_user):
    owner = await make_user(coins=20)
    room, _ = await db.create_chat_room(owner.id, db.RoomGenderPref.any, capacity=5, cost=20)
    await rc.set_active_room(owner.id, room.id)
    member = await make_user(coins=10)
    await db.join_chat_room(member.id, room.id)
    await rc.set_active_room(member.id, room.id)

    context = _make_context()
    update = MagicMock()
    update.effective_user.id = member.id
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()

    before = _counter_value(metrics.room_auto_deleted)
    await membership.leave_room_button(update, context)
    assert _counter_value(metrics.room_auto_deleted) == before + 1

    await _cleanup_room(room.id)
