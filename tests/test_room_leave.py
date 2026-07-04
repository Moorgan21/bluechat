"""تست‌های واحد برای فازِ ۶ (ترکِ اتاق): خروجِ اتمیک، قفلِ owner،
حذفِ خودکار در تک‌نفره‌شدن، پیامِ سیستمی، و آزادشدنِ جا برای صفِ عضویت."""

from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import delete, select

import db
import redis_client as rc
from handlers.chatroom import membership


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


def _make_context():
    context = MagicMock()
    context.bot = MagicMock()
    context.bot.send_message = AsyncMock()
    context.job_queue = MagicMock()
    context.job_queue.run_once = MagicMock()
    context.job_queue.get_jobs_by_name = MagicMock(return_value=[])
    return context


def _make_update(user_id):
    update = MagicMock()
    update.effective_user.id = user_id
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()
    return update


# ---------------------------------------------------------------------
# leave_chat_room (لایه‌ی دیتابیس)
# ---------------------------------------------------------------------

async def test_leave_chat_room_normal_member_leaves_room_stays_open(make_user):
    room, (owner, member_b, member_c) = await _make_room(make_user, member_count=2)

    result, error = await db.leave_chat_room(member_b.id)

    assert error is None
    assert result["auto_deleted"] is False
    assert result["room_id"] == room.id
    assert sorted(result["remaining_member_ids"]) == sorted([owner.id, member_c.id])

    async with db.async_session() as session:
        leaver = await session.get(db.User, member_b.id)
        assert leaver.active_room_id is None
        still_owner = await session.get(db.User, owner.id)
        assert still_owner.active_room_id == room.id
        r = await session.get(db.ChatRoom, room.id)
        assert r.status == db.RoomStatus.open

    await _cleanup_room(room.id)


async def test_leave_chat_room_owner_cannot_leave(make_user):
    room, (owner, member) = await _make_room(make_user, member_count=1)

    result, error = await db.leave_chat_room(owner.id)

    assert result is None
    assert error == "is_owner"

    async with db.async_session() as session:
        still_owner = await session.get(db.User, owner.id)
        assert still_owner.active_room_id == room.id  # دست‌نخورده

    await _cleanup_room(room.id)


async def test_leave_chat_room_user_with_no_room_returns_not_found(make_user):
    user = await make_user(coins=10)
    result, error = await db.leave_chat_room(user.id)
    assert result is None
    assert error == "not_found"


async def test_leave_chat_room_auto_deletes_when_only_owner_remains(make_user):
    room, (owner, member) = await _make_room(make_user, member_count=1)

    result, error = await db.leave_chat_room(member.id)

    assert error is None
    assert result["auto_deleted"] is True
    assert result["remaining_member_ids"] == [owner.id]

    async with db.async_session() as session:
        r = await session.get(db.ChatRoom, room.id)
        assert r.status == db.RoomStatus.deleted
        still_owner = await session.get(db.User, owner.id)
        assert still_owner.active_room_id is None  # owner هم آزاد شد
        leaver = await session.get(db.User, member.id)
        assert leaver.active_room_id is None

        member_count = len(
            (await session.execute(select(db.ChatRoomMember).where(db.ChatRoomMember.room_id == room.id)))
            .scalars()
            .all()
        )
        assert member_count == 0

    await _cleanup_room(room.id)


# ---------------------------------------------------------------------
# leave_room_button (هندلر)
# ---------------------------------------------------------------------

async def test_leave_room_button_broadcasts_system_message(make_user):
    room, (owner, member_b, member_c) = await _make_room(make_user, member_count=2)
    async with db.async_session() as session:
        u = await session.get(db.User, member_b.id)
        u.display_name = "سارا"
        await session.commit()

    context = _make_context()
    update = _make_update(member_b.id)

    await membership.leave_room_button(update, context)

    assert await rc.get_active_room(member_b.id) is None
    update.message.reply_text.assert_awaited()
    assert "خارج شدی" in update.message.reply_text.await_args.args[0]

    broadcast_calls = {c.args[0] for c in context.bot.send_message.await_args_list}
    assert owner.id in broadcast_calls
    assert member_c.id in broadcast_calls
    assert member_b.id not in broadcast_calls  # به خودش پیامِ سیستمی نمی‌ره

    for call in context.bot.send_message.await_args_list:
        if call.args[0] == owner.id:
            assert call.args[1] == "ℹ️ سارا ترک کرد اتاق رو."

    await _cleanup_room(room.id)


async def test_leave_room_button_owner_gets_rejected(make_user):
    room, (owner, member) = await _make_room(make_user, member_count=1)
    context = _make_context()
    update = _make_update(owner.id)

    await membership.leave_room_button(update, context)

    update.message.reply_text.assert_awaited_once()
    assert "نمی‌تونی ترکش کنی" in update.message.reply_text.await_args.args[0]
    assert await rc.get_active_room(owner.id) == room.id  # دست‌نخورده

    await _cleanup_room(room.id)


async def test_leave_room_button_auto_delete_notifies_owner(make_user):
    room, (owner, member) = await _make_room(make_user, member_count=1)
    context = _make_context()
    update = _make_update(member.id)

    await membership.leave_room_button(update, context)

    assert await rc.get_active_room(owner.id) is None
    owner_calls = [c for c in context.bot.send_message.await_args_list if c.args[0] == owner.id]
    assert any("خودکار حذف شد" in c.args[1] for c in owner_calls)
    # هیچ پیامی تو این اتاق رد و بدل نشده بود، پس باید صراحتاً بگه تاریخچه‌ای نیست
    assert any("تاریخچه‌ای برای پاک‌سازی وجود نداره" in c.args[1] for c in owner_calls)
    assert await rc.get_deleted_room_members(room.id) is None  # پیشنهادِ پاک‌سازی ارائه نشده

    await _cleanup_room(room.id)


async def test_leave_room_button_auto_delete_offers_purge_when_history_exists(make_user):
    room, (owner, member) = await _make_room(make_user, member_count=1)
    from handlers.chatroom import relay

    relay_context = MagicMock()
    relay_context.bot = MagicMock()
    _next_id = [8000]

    async def _fake_send_message(*args, **kwargs):
        _next_id[0] += 1
        sent = MagicMock()
        sent.message_id = _next_id[0]
        return sent

    relay_context.bot.send_message = AsyncMock(side_effect=_fake_send_message)
    await relay.relay_room_message(
        _make_relay_update(_make_relay_message(500, text="سلام"), member.id), relay_context, room.id
    )

    context = _make_context()
    update = _make_update(member.id)
    await membership.leave_room_button(update, context)

    owner_calls = [c for c in context.bot.send_message.await_args_list if c.args[0] == owner.id]
    purge_offer = next((c for c in owner_calls if "پاک کنم" in c.args[1]), None)
    assert purge_offer is not None
    assert "۲ دقیقه" in purge_offer.args[1]
    assert purge_offer.kwargs["reply_markup"] is not None

    stored = await rc.get_deleted_room_members(room.id)
    assert stored is not None and owner.id in stored and member.id in stored

    await _cleanup_room(room.id)


def _make_relay_message(message_id, text=None):
    msg = MagicMock()
    msg.message_id = message_id
    msg.text = text
    msg.caption = None
    msg.reply_to_message = None
    for field in ("photo", "sticker", "voice", "audio", "video", "video_note", "document", "animation"):
        setattr(msg, field, None)
    return msg


def _make_relay_update(msg, user_id):
    update = MagicMock()
    update.message = msg
    update.effective_message = msg
    update.effective_user.id = user_id
    return update


async def test_leave_room_button_stale_redis_self_heals(make_user):
    user = await make_user(coins=10)
    await rc.set_active_room(user.id, 999_999)
    context = _make_context()
    update = _make_update(user.id)

    await membership.leave_room_button(update, context)

    assert await rc.get_active_room(user.id) is None
    assert "دیگه فعال نیست" in update.message.reply_text.await_args.args[0]


async def test_leave_room_button_frees_slot_for_waiting_searcher(make_user):
    """وقتی یه عضو اتاقی رو ترک می‌کنه که ظرفیتش پر بود، جای خالی باید
    فوراً به یه جستجوگرِ منتظر تو صف داده بشه (همون trigger که فازِ ۳
    بعدِ ساختِ اتاق صدا می‌زنه، اینجا بعدِ خروج)."""
    # ظرفیت ۳: owner + دو عضوِ عادی، پس با ترکِ یکی هنوز ۲ نفر می‌مونن
    # (بیشتر از ۱)، یعنی اتاق باز می‌مونه و فقط یه جا آزاد می‌شه —
    # برخلافِ سناریوی حذفِ خودکار که با owner+۱عضو تست شده.
    owner = await make_user(coins=20)
    room, _ = await db.create_chat_room(owner.id, db.RoomGenderPref.any, capacity=3, cost=20)
    await rc.set_active_room(owner.id, room.id)
    stay_member = await make_user(coins=10)
    await db.join_chat_room(stay_member.id, room.id)
    await rc.set_active_room(stay_member.id, room.id)
    leaving_member = await make_user(coins=10)
    await db.join_chat_room(leaving_member.id, room.id)
    await rc.set_active_room(leaving_member.id, room.id)  # الان پره: owner + stay_member + leaving_member

    waiter = await make_user(coins=10)
    await rc.enqueue_room_join(waiter.id, "any")

    context = _make_context()
    update = _make_update(leaving_member.id)

    await membership.leave_room_button(update, context)

    async with db.async_session() as session:
        refreshed_waiter = await session.get(db.User, waiter.id)
        assert refreshed_waiter.active_room_id == room.id
    assert await rc.is_waiting_room_join(waiter.id) is None

    await _cleanup_room(room.id)
