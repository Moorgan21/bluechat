"""تست‌های واحد برای فازِ ۵ (ابزارهای owner): حذفِ پیامِ دیگران، اخراج،
بستن/بازکردنِ اتاق، و پاک‌سازیِ تاریخچه بعدِ حذفِ اتاق."""

from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import delete, select

import db
import redis_client as rc
from handlers.chatroom import moderation, relay


async def _cleanup_room(room_id: int) -> None:
    async with db.async_session() as session:
        for user in (
            await session.execute(select(db.User).where(db.User.active_room_id == room_id))
        ).scalars().all():
            user.active_room_id = None
        await session.execute(delete(db.ChatRoomMember).where(db.ChatRoomMember.room_id == room_id))
        await session.execute(delete(db.ChatRoom).where(db.ChatRoom.id == room_id))
        await session.commit()


async def _make_room(make_user, capacity=5, member_count=1):
    owner = await make_user(coins=20)
    room, _ = await db.create_chat_room(owner.id, db.RoomGenderPref.any, capacity=capacity, cost=20)
    await rc.set_active_room(owner.id, room.id)
    members = [owner]
    for _ in range(member_count):
        m = await make_user(coins=10)
        await db.join_chat_room(m.id, room.id)
        await rc.set_active_room(m.id, room.id)
        members.append(m)
    return room, members


class _IdGen:
    def __init__(self, start=9000):
        self._next = start

    def __call__(self, *args, **kwargs):
        self._next += 1
        sent = MagicMock()
        sent.message_id = self._next
        return sent


def _make_bot():
    bot = MagicMock()
    bot.send_message = AsyncMock(side_effect=_IdGen())
    bot.delete_message = AsyncMock()
    return bot


def _make_context():
    context = MagicMock()
    context.bot = _make_bot()
    context.job_queue = MagicMock()
    context.job_queue.run_once = MagicMock()
    context.job_queue.get_jobs_by_name = MagicMock(return_value=[])
    return context


def _make_message(message_id, text=None, reply_to_message=None):
    msg = MagicMock()
    msg.message_id = message_id
    msg.text = text
    msg.caption = None
    msg.reply_to_message = reply_to_message
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


# ---------------------------------------------------------------------
# owner حذفِ پیامِ دیگران
# ---------------------------------------------------------------------

async def test_owner_can_delete_others_message(make_user):
    room, (owner, member) = await _make_room(make_user, member_count=1)
    context = _make_context()

    original = _make_message(100, text="سلام")
    await relay.relay_room_message(_make_update(original, member.id), context, room.id)
    owner_local_id = (await rc.get_room_msg_recipients(room.id, member.id, 100))[owner.id][0]

    reply_to = MagicMock()
    reply_to.message_id = owner_local_id
    del_msg = _make_message(101, text="حذف", reply_to_message=reply_to)
    await relay.relay_room_message(_make_update(del_msg, owner.id), context, room.id)

    deleted_calls = {c.args for c in context.bot.delete_message.await_args_list}
    assert (owner.id, owner_local_id) in deleted_calls
    assert (member.id, 100) in deleted_calls  # نسخه‌ی خودِ فرستنده هم پاک شد

    member_notified = [c.args[1] for c in context.bot.send_message.await_args_list if c.args[0] == member.id]
    assert any("owner یکی از پیام‌هات" in t for t in member_notified)

    await _cleanup_room(room.id)


# ---------------------------------------------------------------------
# اخراج
# ---------------------------------------------------------------------

async def test_owner_kicks_member_frees_slot_and_broadcasts(make_user):
    room, (owner, stay_member, target) = await _make_room(make_user, capacity=5, member_count=2)
    async with db.async_session() as session:
        u = await session.get(db.User, target.id)
        u.display_name = "سارا"
        await session.commit()

    context = _make_context()
    msg = _make_message(200, text="سلام")
    await relay.relay_room_message(_make_update(msg, target.id), context, room.id)
    owner_local_id = (await rc.get_room_msg_recipients(room.id, target.id, 200))[owner.id][0]

    waiter = await make_user(coins=10)
    await rc.enqueue_room_join(waiter.id, "any")

    reply_to = MagicMock()
    reply_to.message_id = owner_local_id
    kick_msg = _make_message(201, text="اخراج", reply_to_message=reply_to)
    await relay.relay_room_message(_make_update(kick_msg, owner.id), context, room.id)

    async with db.async_session() as session:
        refreshed_target = await session.get(db.User, target.id)
        assert refreshed_target.active_room_id is None
        refreshed_waiter = await session.get(db.User, waiter.id)
        assert refreshed_waiter.active_room_id == room.id  # جای آزادشده رو گرفت

    assert await rc.get_active_room(target.id) is None

    target_msgs = [c.args[1] for c in context.bot.send_message.await_args_list if c.args[0] == target.id]
    assert any("اخراج شدی" in t for t in target_msgs)
    stay_msgs = [c.args[1] for c in context.bot.send_message.await_args_list if c.args[0] == stay_member.id]
    assert any("سارا اخراج شد" in t for t in stay_msgs)

    await _cleanup_room(room.id)


async def test_owner_cannot_kick_self(make_user):
    room, (owner, member) = await _make_room(make_user, member_count=1)
    context = _make_context()

    msg = _make_message(300, text="سلام")
    await relay.relay_room_message(_make_update(msg, owner.id), context, room.id)

    reply_to = MagicMock()
    reply_to.message_id = 300  # نسخه‌ی خودِ owner از پیامِ خودش
    kick_msg = _make_message(301, text="اخراج", reply_to_message=reply_to)
    await relay.relay_room_message(_make_update(kick_msg, owner.id), context, room.id)

    kick_msg.reply_text.assert_awaited()
    assert "نمی‌تونی خودتو اخراج کنی" in kick_msg.reply_text.await_args.args[0]

    async with db.async_session() as session:
        still_owner = await session.get(db.User, owner.id)
        assert still_owner.active_room_id == room.id

    await _cleanup_room(room.id)


async def test_kick_auto_deletes_when_only_owner_remains(make_user):
    room, (owner, member) = await _make_room(make_user, member_count=1)
    context = _make_context()

    msg = _make_message(400, text="سلام")
    await relay.relay_room_message(_make_update(msg, member.id), context, room.id)
    owner_local_id = (await rc.get_room_msg_recipients(room.id, member.id, 400))[owner.id][0]

    reply_to = MagicMock()
    reply_to.message_id = owner_local_id
    kick_msg = _make_message(401, text="اخراج", reply_to_message=reply_to)
    await relay.relay_room_message(_make_update(kick_msg, owner.id), context, room.id)

    assert await rc.get_active_room(owner.id) is None
    async with db.async_session() as session:
        r = await session.get(db.ChatRoom, room.id)
        assert r.status == db.RoomStatus.deleted

    owner_msgs = [c.args[1] for c in context.bot.send_message.await_args_list if c.args[0] == owner.id]
    assert any("خودکار حذف شد" in t for t in owner_msgs)

    await _cleanup_room(room.id)


async def test_non_owner_kick_reply_is_treated_as_normal_message(make_user):
    """چون چکِ «user_id == room.owner_id» توی relay.py قبل از فراخوانیِ
    _handle_kick_command هست، پیامِ «اخراج» از یه عضوِ عادی باید مثلِ
    یه پیامِ معمولی رله بشه، نه اینکه به‌عنوانِ فرمان تفسیر بشه."""
    room, (owner, member) = await _make_room(make_user, member_count=1)
    context = _make_context()

    msg = _make_message(500, text="سلام")
    await relay.relay_room_message(_make_update(msg, owner.id), context, room.id)
    member_local_id = (await rc.get_room_msg_recipients(room.id, owner.id, 500))[member.id][0]

    reply_to = MagicMock()
    reply_to.message_id = member_local_id
    fake_kick = _make_message(501, text="اخراج", reply_to_message=reply_to)
    await relay.relay_room_message(_make_update(fake_kick, member.id), context, room.id)

    # owner نباید اخراج شده باشه؛ پیام باید عادی رله شده باشه
    async with db.async_session() as session:
        still_owner = await session.get(db.User, owner.id)
        assert still_owner.active_room_id == room.id

    owner_texts = [c.args[1] for c in context.bot.send_message.await_args_list if c.args[0] == owner.id]
    assert any("اخراج" in t and ":" in t for t in owner_texts)  # به‌صورتِ «نام: اخراج» رله شده

    await _cleanup_room(room.id)


# ---------------------------------------------------------------------
# بستن / بازکردنِ اتاق
# ---------------------------------------------------------------------

async def test_close_room_blocks_messages_and_broadcasts(make_user):
    room, (owner, member) = await _make_room(make_user, member_count=1)
    context = _make_context()
    update = MagicMock()
    update.effective_user.id = owner.id
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()

    await moderation.close_room_button(update, context)

    update.message.reply_text.assert_awaited_once()
    assert "اتاق بسته شد" in update.message.reply_text.await_args.args[0]

    member_msgs = [c.args[1] for c in context.bot.send_message.await_args_list if c.args[0] == member.id]
    assert any("موقتاً بسته شد" in t for t in member_msgs)

    async with db.async_session() as session:
        r = await session.get(db.ChatRoom, room.id)
        assert r.status == db.RoomStatus.closed

    # حالا پیامِ معمولی نباید رله بشه
    context2 = _make_context()
    msg = _make_message(600, text="سلام")
    await relay.relay_room_message(_make_update(msg, member.id), context2, room.id)
    context2.bot.send_message.assert_not_awaited()
    msg.reply_text.assert_awaited_once()
    assert "بسته" in msg.reply_text.await_args.args[0]

    await _cleanup_room(room.id)


async def test_reopen_room_allows_messages_again(make_user):
    room, (owner, member) = await _make_room(make_user, member_count=1)
    await db.set_room_open_status(owner.id, is_open=False)

    context = _make_context()
    update = MagicMock()
    update.effective_user.id = owner.id
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()

    await moderation.reopen_room_button(update, context)

    update.message.reply_text.assert_awaited_once()
    assert "اتاق باز شد" in update.message.reply_text.await_args.args[0]

    async with db.async_session() as session:
        r = await session.get(db.ChatRoom, room.id)
        assert r.status == db.RoomStatus.open

    await _cleanup_room(room.id)


async def test_non_owner_cannot_close_room(make_user):
    room, (owner, member) = await _make_room(make_user, member_count=1)
    context = _make_context()
    update = MagicMock()
    update.effective_user.id = member.id
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()

    await moderation.close_room_button(update, context)

    assert "فقط owner" in update.message.reply_text.await_args.args[0]
    async with db.async_session() as session:
        r = await session.get(db.ChatRoom, room.id)
        assert r.status == db.RoomStatus.open

    await _cleanup_room(room.id)


# ---------------------------------------------------------------------
# پاک‌سازیِ تاریخچه بعدِ حذفِ اتاق
# ---------------------------------------------------------------------

def _make_query(user_id: int, data: str):
    query = MagicMock()
    query.data = data
    query.from_user.id = user_id
    query.answer = AsyncMock()
    query.message = MagicMock()
    query.message.delete = AsyncMock()
    query.edit_message_text = AsyncMock()
    return query


def _make_callback_update(query):
    update = MagicMock()
    update.callback_query = query
    update.effective_user.id = query.from_user.id
    return update


async def test_purge_history_deletes_all_recorded_messages(make_user):
    room, (owner, member) = await _make_room(make_user, member_count=1)
    context = _make_context()

    msg = _make_message(700, text="سلام")
    await relay.relay_room_message(_make_update(msg, owner.id), context, room.id)

    result, error = await db.delete_chat_room(owner.id)
    assert error is None
    await rc.store_deleted_room_members(result["room_id"], result["member_ids"])

    context2 = _make_context()
    query = _make_query(owner.id, f"roompurge:{room.id}")
    await moderation.purge_history_callback(_make_callback_update(query), context2)

    assert context2.bot.delete_message.await_count >= 2  # نسخه‌ی owner و نسخه‌ی member
    summary = [c.args[1] for c in context2.bot.send_message.await_args_list if c.args[0] == owner.id]
    assert any("پاک شد" in t for t in summary)

    assert await rc.get_deleted_room_members(room.id) is not None  # کلید تا TTL می‌مونه، دوباره هم قابلِ استفاده‌ست


async def test_purge_history_expired_shows_message(make_user):
    context = _make_context()
    query = _make_query(999_999_999, "roompurge:123456")
    await moderation.purge_history_callback(_make_callback_update(query), context)

    query.edit_message_text.assert_awaited_once()
    assert "معتبر نیست" in query.edit_message_text.await_args.args[0]


async def test_purge_history_includes_member_who_left_before_room_deletion(make_user):
    """رگرسیون: result["member_ids"]ِ delete_chat_room فقط عضوهای *فعلیِ*
    لحظه‌ی حذفه؛ کسی که قبل‌تر ترک کرده باید همچنان تو پاک‌سازیِ کامل
    باشه، چون متنِ خودِ دکمه صراحتاً «همه‌ی اعضای سابق» رو وعده می‌ده."""
    room, (owner, leaver, stayer) = await _make_room(make_user, member_count=2)
    context = _make_context()

    leaver_msg = _make_message(900, text="پیامِ عضوی که بعداً ترک می‌کنه")
    await relay.relay_room_message(_make_update(leaver_msg, leaver.id), context, room.id)

    # عضو قبل از حذفِ اتاق ترک می‌کنه؛ دیگه تو ChatRoomMember نیست
    leave_result, leave_error = await db.leave_chat_room(leaver.id)
    assert leave_error is None
    assert leave_result["auto_deleted"] is False  # هنوز owner + stayer موندن

    delete_query = _make_query(owner.id, "roomdelete:confirm")
    await moderation.delete_room_confirm_callback(_make_callback_update(delete_query), context)

    stored_members = await rc.get_deleted_room_members(room.id)
    assert leaver.id in stored_members  # با وجودِ ترکِ قبلی، همچنان تو لیستِ پاک‌سازیه
    assert owner.id in stored_members
    assert stayer.id in stored_members

    context2 = _make_context()
    purge_query = _make_query(owner.id, f"roompurge:{room.id}")
    await moderation.purge_history_callback(_make_callback_update(purge_query), context2)

    deleted_chat_ids = {c.args[0] for c in context2.bot.delete_message.await_args_list}
    assert leaver.id in deleted_chat_ids  # پیامِ عضوِ سابق هم واقعاً پاک شده

    # بعدِ پاک‌سازیِ کامل، ردِ تاریخچه‌ی اعضا هم جمع بشه
    assert await rc.get_room_history_user_ids(room.id) == set()


async def test_room_history_users_scoped_per_room(make_user):
    """هر room_id شناسه‌ی یکتای Postgresه (autoincrement)، پس هیچ‌وقت
    برای دو اتاقِ متفاوت تکرار نمی‌شه؛ این تست همون تضمین رو صراحتاً
    برای ردگیریِ اعضای سابق (KEY_ROOM_HISTORY_USERS) هم می‌سنجه تا
    پاک‌سازیِ یه اتاق هیچ‌وقت به اتاقِ دیگه‌ای نشت نکنه."""
    room_a, (owner_a, member_a) = await _make_room(make_user, member_count=1)
    room_b, (owner_b, member_b) = await _make_room(make_user, member_count=1)
    context = _make_context()

    await relay.relay_room_message(_make_update(_make_message(910, text="A"), member_a.id), context, room_a.id)
    await relay.relay_room_message(_make_update(_make_message(920, text="B"), member_b.id), context, room_b.id)

    assert await rc.get_room_history_user_ids(room_a.id) == {owner_a.id, member_a.id}
    assert await rc.get_room_history_user_ids(room_b.id) == {owner_b.id, member_b.id}
