"""تست‌های واحد برای فازِ ۴ (رله‌ی پیام داخلِ اتاق): فن‌اوتِ یک‌به‌چند،
فرمتِ نامِ فرستنده، resolveِ ریپلای برای هر گیرنده، ویرایش/حذفِ پیامِ
خود، چتِ امن، استیکر/ویدیو-نوتِ دو-پیامی، و خودتصحیحیِ آینه‌ی Redis."""

from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import delete, select

import db
import redis_client as rc
from handlers.chatroom import relay


async def _cleanup_room(room_id: int) -> None:
    async with db.async_session() as session:
        for user in (
            await session.execute(select(db.User).where(db.User.active_room_id == room_id))
        ).scalars().all():
            user.active_room_id = None
        await session.execute(delete(db.ChatRoomMember).where(db.ChatRoomMember.room_id == room_id))
        await session.execute(delete(db.ChatRoom).where(db.ChatRoom.id == room_id))
        await session.commit()


async def _set_display_name(user_id: int, name: str) -> None:
    async with db.async_session() as session:
        user = await session.get(db.User, user_id)
        user.display_name = name
        await session.commit()


class _IdGen:
    def __init__(self, start=5000):
        self._next = start

    def __call__(self, *args, **kwargs):
        self._next += 1
        sent = MagicMock()
        sent.message_id = self._next
        return sent


def _make_bot():
    bot = MagicMock()
    gen = _IdGen()
    for method in (
        "send_message", "send_photo", "send_sticker", "send_voice", "send_audio",
        "send_video", "send_video_note", "send_document", "send_animation",
    ):
        setattr(bot, method, AsyncMock(side_effect=gen))
    bot.edit_message_text = AsyncMock()
    bot.delete_message = AsyncMock()
    return bot


def _make_message(message_id, text=None, reply_to_message=None, **media):
    msg = MagicMock()
    msg.message_id = message_id
    msg.text = text
    msg.caption = media.get("caption")
    msg.reply_to_message = reply_to_message
    for field in ("photo", "sticker", "voice", "audio", "video", "video_note", "document", "animation"):
        setattr(msg, field, media.get(field))
    msg.delete = AsyncMock()
    msg.reply_text = AsyncMock()
    return msg


def _make_update(msg, user_id):
    update = MagicMock()
    update.message = msg
    update.effective_message = msg
    update.effective_user.id = user_id
    return update


async def _make_room(make_user, capacity=5, member_count=1):
    """owner + (member_count) عضوِ عادی رو می‌سازه، لیستِ userهاشون رو
    برمی‌گردونه. آینه‌ی Redisِ active_room رو هم دستی ست می‌کنه، چون
    اینجا مستقیم از db.create_chat_room/join_chat_room استفاده می‌کنیم
    (نه فلوی واقعیِ هندلر که این کارو خودکار انجام می‌ده) و
    relay_room_edit برای پیدا کردنِ room_id به همین آینه نیاز داره."""
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


# ---------------------------------------------------------------------
# فن‌اوت و فرمتِ برچسبِ نام
# ---------------------------------------------------------------------

async def test_relay_text_message_fanout_with_sender_label(make_user):
    room, (owner, member) = await _make_room(make_user, member_count=1)
    await _set_display_name(member.id, "سارا")

    bot = _make_bot()
    context = MagicMock(); context.bot = bot

    msg = _make_message(100, text="سلام بچه‌ها")
    await relay.relay_room_message(_make_update(msg, member.id), context, room.id)

    bot.send_message.assert_awaited_once()
    call = bot.send_message.await_args
    assert call.args[0] == owner.id
    assert call.args[1] == "سارا: سلام بچه‌ها"

    await _cleanup_room(room.id)


async def test_relay_text_message_owner_gets_tag(make_user):
    room, (owner, member) = await _make_room(make_user, member_count=1)
    await _set_display_name(owner.id, "علی")

    bot = _make_bot()
    context = MagicMock(); context.bot = bot

    msg = _make_message(200, text="سلام")
    await relay.relay_room_message(_make_update(msg, owner.id), context, room.id)

    call = bot.send_message.await_args
    assert call.args[0] == member.id
    assert call.args[1] == "علی (owner): سلام"

    await _cleanup_room(room.id)


# ---------------------------------------------------------------------
# ریپلای: هر گیرنده باید نسخه‌ی محلیِ خودشو ببینه
# ---------------------------------------------------------------------

async def test_relay_reply_resolves_to_each_recipients_local_copy(make_user):
    room, (owner, member_b, member_c) = await _make_room(make_user, capacity=5, member_count=2)

    bot = _make_bot()
    context = MagicMock(); context.bot = bot

    original = _make_message(300, text="اول")
    await relay.relay_room_message(_make_update(original, owner.id), context, room.id)

    recipients_map = await rc.get_room_msg_recipients(room.id, owner.id, 300)
    b_local_id = recipients_map[member_b.id][0]
    c_local_id = recipients_map[member_c.id][0]
    assert b_local_id != c_local_id

    bot.send_message.reset_mock()
    reply_to = MagicMock()
    reply_to.message_id = b_local_id
    reply_msg = _make_message(301, text="جواب", reply_to_message=reply_to)
    await relay.relay_room_message(_make_update(reply_msg, member_b.id), context, room.id)

    seen = {}
    for call in bot.send_message.await_args_list:
        seen[call.args[0]] = call.kwargs.get("reply_parameters")

    assert seen[owner.id].message_id == 300  # نسخه‌ی خودِ owner از پیامِ اصلی، خودِ 300 است
    assert seen[member_c.id].message_id == c_local_id

    await _cleanup_room(room.id)


# ---------------------------------------------------------------------
# ویرایش
# ---------------------------------------------------------------------

async def test_relay_edit_own_message_updates_recipients(make_user):
    room, (owner, member) = await _make_room(make_user, member_count=1)
    bot = _make_bot()
    context = MagicMock(); context.bot = bot

    msg = _make_message(400, text="سلام")
    await relay.relay_room_message(_make_update(msg, owner.id), context, room.id)
    member_local_id = (await rc.get_room_msg_recipients(room.id, owner.id, 400))[member.id][0]

    edited = MagicMock()
    edited.message_id = 400
    edited.text = "سلام ویرایش‌شده"
    edited.from_user.id = owner.id
    edit_update = MagicMock()
    edit_update.edited_message = edited

    await relay.relay_room_edit(edit_update, context)

    bot.edit_message_text.assert_awaited_once()
    call = bot.edit_message_text.await_args
    assert call.kwargs["chat_id"] == member.id
    assert call.kwargs["message_id"] == member_local_id
    assert "سلام ویرایش‌شده" in call.kwargs["text"]

    await _cleanup_room(room.id)


async def test_relay_edit_ignores_non_owner_message(make_user):
    room, (owner, member) = await _make_room(make_user, member_count=1)
    bot = _make_bot()
    context = MagicMock(); context.bot = bot

    msg = _make_message(500, text="سلام")
    await relay.relay_room_message(_make_update(msg, owner.id), context, room.id)
    member_local_id = (await rc.get_room_msg_recipients(room.id, owner.id, 500))[member.id][0]

    edited = MagicMock()
    edited.message_id = member_local_id
    edited.text = "دستکاری"
    edited.from_user.id = member.id  # اون پیامِ owner نبوده، پس نباید بتونه ویرایشش کنه
    edit_update = MagicMock()
    edit_update.edited_message = edited

    await relay.relay_room_edit(edit_update, context)

    bot.edit_message_text.assert_not_awaited()

    await _cleanup_room(room.id)


# ---------------------------------------------------------------------
# حذف
# ---------------------------------------------------------------------

async def test_relay_delete_own_message_removes_all_copies(make_user):
    room, (owner, member) = await _make_room(make_user, member_count=1)
    bot = _make_bot()
    context = MagicMock(); context.bot = bot

    msg = _make_message(600, text="سلام")
    await relay.relay_room_message(_make_update(msg, owner.id), context, room.id)
    member_local_id = (await rc.get_room_msg_recipients(room.id, owner.id, 600))[member.id][0]

    reply_to = MagicMock()
    reply_to.message_id = 600
    del_msg = _make_message(601, text="حذف", reply_to_message=reply_to)
    await relay.relay_room_message(_make_update(del_msg, owner.id), context, room.id)

    delete_calls = {c.args for c in bot.delete_message.await_args_list}
    assert (member.id, member_local_id) in delete_calls
    assert (owner.id, 600) in delete_calls
    del_msg.delete.assert_awaited()

    await _cleanup_room(room.id)


async def test_relay_delete_command_blocks_non_owner(make_user):
    room, (owner, member) = await _make_room(make_user, member_count=1)
    bot = _make_bot()
    context = MagicMock(); context.bot = bot

    msg = _make_message(700, text="سلام")
    await relay.relay_room_message(_make_update(msg, owner.id), context, room.id)
    member_local_id = (await rc.get_room_msg_recipients(room.id, owner.id, 700))[member.id][0]

    reply_to = MagicMock()
    reply_to.message_id = member_local_id
    del_msg = _make_message(701, text="del", reply_to_message=reply_to)
    await relay.relay_room_message(_make_update(del_msg, member.id), context, room.id)  # سعیِ حذفِ پیامِ owner

    bot.delete_message.assert_not_awaited()
    del_msg.reply_text.assert_awaited()

    await _cleanup_room(room.id)


# ---------------------------------------------------------------------
# استیکر/ویدیو-نوت: دو پیامِ واقعی، هر دو باید tracked بشن
# ---------------------------------------------------------------------

async def test_relay_sticker_sends_label_then_media_both_tracked(make_user):
    room, (owner, member) = await _make_room(make_user, member_count=1)
    bot = _make_bot()
    context = MagicMock(); context.bot = bot

    fake_sticker = MagicMock()
    fake_sticker.file_id = "sticker123"
    msg = _make_message(1000, sticker=fake_sticker)
    await relay.relay_room_message(_make_update(msg, owner.id), context, room.id)

    bot.send_message.assert_awaited_once()  # فقط برچسبِ اسم، بدونِ caption
    bot.send_sticker.assert_awaited_once()

    recipients_map = await rc.get_room_msg_recipients(room.id, owner.id, 1000)
    assert len(recipients_map[member.id]) == 2  # هم پیامِ برچسب هم خودِ استیکر

    await _cleanup_room(room.id)


# ---------------------------------------------------------------------
# وضعیتِ اتاق: بسته، یا تناقضِ Redis/Postgres
# ---------------------------------------------------------------------

async def test_relay_rejects_message_when_room_closed(make_user):
    room, (owner,) = await _make_room(make_user, member_count=0)
    async with db.async_session() as session:
        r = await session.get(db.ChatRoom, room.id)
        r.status = db.RoomStatus.closed
        await session.commit()

    bot = _make_bot()
    context = MagicMock(); context.bot = bot
    msg = _make_message(900, text="سلام")

    await relay.relay_room_message(_make_update(msg, owner.id), context, room.id)

    bot.send_message.assert_not_awaited()
    msg.reply_text.assert_awaited_once()
    assert "بسته" in msg.reply_text.await_args.args[0]

    await _cleanup_room(room.id)


async def test_relay_self_heals_when_redis_and_postgres_disagree(make_user):
    user = await make_user(coins=10)
    await rc.set_active_room(user.id, 999_999)  # اتاقی که اصلاً وجود نداره

    bot = _make_bot()
    context = MagicMock(); context.bot = bot
    msg = _make_message(800, text="سلام")

    await relay.relay_room_message(_make_update(msg, user.id), context, 999_999)

    assert await rc.get_active_room(user.id) is None
    msg.reply_text.assert_awaited_once()
    assert "دیگه فعال نیست" in msg.reply_text.await_args.args[0]


# ---------------------------------------------------------------------
# چتِ امن
# ---------------------------------------------------------------------

async def test_toggle_secure_chat_button(make_user):
    user = await make_user(coins=10)
    update = MagicMock()
    update.effective_user.id = user.id
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()
    context = MagicMock()

    await relay.toggle_secure_chat_button(update, context)
    assert await rc.is_secure_chat(user.id) is True
    assert "فعال شد" in update.message.reply_text.await_args.args[0]

    await relay.toggle_secure_chat_button(update, context)
    assert await rc.is_secure_chat(user.id) is False
