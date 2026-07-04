"""تست‌های واحد برای دکمه‌ی «👥 وضعیت اتاق»: برخلافِ show_room_menu (که
فقط خلاصه‌ی کلی می‌ده)، این دکمه باید تک‌تکِ اعضا رو با نقش، لینکِ
پروفایلِ عمومی، و وضعیتِ حضورِ فعلی‌شون (حاضر / ترکِ موقت) لیست کنه."""

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


async def test_room_status_button_lists_members_with_roles_and_profile_links(make_user):
    room, (owner, member) = await _make_room(make_user, member_count=1)
    update = _make_message_update(owner.id)
    context = MagicMock()

    await chatroom.show_room_status_button(update, context)

    text = update.message.reply_text.await_args.args[0]
    assert f"#{room.id}" in text
    assert "تعدادِ اعضا: 2 از 4 نفر" in text
    assert "(owner)" in text
    assert f"/user_{owner.referral_code}" in text
    assert f"/user_{member.referral_code}" in text
    # هر دو عضو تازه‌join کردن، هیچ‌کدوم ترکِ موقت نکردن
    assert text.count("🟢 حاضر") == 2

    await _cleanup_room(room.id)


async def test_room_status_button_shows_temporary_leave(make_user):
    room, (owner, member) = await _make_room(make_user, member_count=1)
    await rc.suppress_room_ui(member.id)

    update = _make_message_update(owner.id)
    context = MagicMock()
    await chatroom.show_room_status_button(update, context)

    text = update.message.reply_text.await_args.args[0]
    assert "🌙 ترکِ موقتِ اتاق" in text
    assert text.count("🟢 حاضر") == 1

    await _cleanup_room(room.id)


async def test_room_status_button_without_active_room(make_user):
    user = await make_user(coins=10)
    update = _make_message_update(user.id)
    context = MagicMock()

    await chatroom.show_room_status_button(update, context)

    text = update.message.reply_text.await_args.args[0]
    assert "توی اتاقی نیستی" in text
