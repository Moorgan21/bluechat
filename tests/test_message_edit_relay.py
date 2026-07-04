"""تست‌های رگرسیون برای این باگ: هم توی چتِ ۱به۱ (relay_edit) هم توی
اتاق (relay_room_edit)، ادیتِ پیام با یه TypeError خاموش fail می‌شد چون
protect_content به context.bot.edit_message_text پاس داده می‌شد که این
متد اصلاً همچین پارامتری نداره (فقط موقعِ ارسالِ اولیه معتبره).
create_autospec(telegram.Bot) عمداً به‌جای MagicMockِ ساده استفاده شده
چون یه AsyncMockِ معمولی هر kwargی رو بی‌سروصدا قبول می‌کنه و این باگ رو
اصلاً نمی‌گرفت (دقیقاً همون دلیلی که تستِ قبلیِ room_relay این مشکل رو
لو نداده بود)."""

import re
from unittest.mock import MagicMock, create_autospec

import telegram
from sqlalchemy import delete, select

import db
import redis_client as rc
from handlers.chat import relay as chat_relay
from handlers.chatroom import relay as room_relay


def _make_autospec_bot():
    return create_autospec(telegram.Bot, instance=True)


def _make_edited_update(message_id: int, text: str, user_id: int):
    edited = MagicMock()
    edited.message_id = message_id
    edited.text = text
    edited.from_user.id = user_id
    update = MagicMock()
    update.edited_message = edited
    return update


async def test_relay_edit_1to1_does_not_pass_protect_content(make_user):
    a = await make_user()
    b = await make_user()
    await rc.set_partner(a.id, b.id)
    await rc.link_messages(a.id, 10, b.id, 20)

    bot = _make_autospec_bot()
    context = MagicMock()
    context.bot = bot

    update = _make_edited_update(10, "متنِ ویرایش‌شده", a.id)
    await chat_relay.relay_edit(update, context)

    bot.edit_message_text.assert_awaited_once()
    call = bot.edit_message_text.await_args
    assert "protect_content" not in call.kwargs
    assert call.kwargs["chat_id"] == b.id
    assert call.kwargs["message_id"] == 20
    assert "✏️ ویرایش شده" in call.kwargs["text"]
    assert "متنِ ویرایش‌شده" in call.kwargs["text"]
    # کاربر خواسته زیرِ پیامِ ویرایش‌شده تاریخ *و* ساعت بیاد، نه فقط ساعت
    assert re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", call.kwargs["text"])


async def _cleanup_room(room_id: int) -> None:
    async with db.async_session() as session:
        for user in (
            await session.execute(select(db.User).where(db.User.active_room_id == room_id))
        ).scalars().all():
            user.active_room_id = None
        await session.execute(delete(db.ChatRoomMember).where(db.ChatRoomMember.room_id == room_id))
        await session.execute(delete(db.ChatRoom).where(db.ChatRoom.id == room_id))
        await session.commit()


async def test_relay_room_edit_does_not_pass_protect_content(make_user):
    owner = await make_user(coins=20)
    room, _ = await db.create_chat_room(owner.id, db.RoomGenderPref.any, capacity=4, cost=20)
    await rc.set_active_room(owner.id, room.id)
    member = await make_user(coins=10)
    await db.join_chat_room(member.id, room.id)
    await rc.set_active_room(member.id, room.id)

    bot = _make_autospec_bot()

    async def _fake_send_message(chat_id, text, **kwargs):
        sent = MagicMock()
        sent.message_id = 999
        return sent

    bot.send_message.side_effect = _fake_send_message
    context = MagicMock()
    context.bot = bot

    msg = MagicMock()
    msg.message_id = 300
    msg.text = "سلام"
    msg.caption = None
    msg.reply_to_message = None
    for field in ("photo", "sticker", "voice", "audio", "video", "video_note", "document", "animation"):
        setattr(msg, field, None)

    update = MagicMock()
    update.message = msg
    update.effective_message = msg
    update.effective_user.id = owner.id

    await room_relay.relay_room_message(update, context, room.id)
    member_local_id = (await rc.get_room_msg_recipients(room.id, owner.id, 300))[member.id][0]

    edit_update = _make_edited_update(300, "سلام ویرایش‌شده", owner.id)
    await room_relay.relay_room_edit(edit_update, context)

    bot.edit_message_text.assert_awaited_once()
    call = bot.edit_message_text.await_args
    assert "protect_content" not in call.kwargs
    assert call.kwargs["chat_id"] == member.id
    assert call.kwargs["message_id"] == member_local_id
    assert "✏️ ویرایش شده" in call.kwargs["text"]
    assert "سلام ویرایش‌شده" in call.kwargs["text"]
    assert re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", call.kwargs["text"])

    await _cleanup_room(room.id)
