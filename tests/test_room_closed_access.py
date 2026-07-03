"""تست‌های واحد برای مکانیزمِ suppress_room_ui: وقتی owner اتاق رو
می‌بنده، هندلرِ اتاقِ عضوهای غیر-owner باید موقتاً غیرفعال بشه (نه
فقط کیبورد عوض بشه) تا بقیه‌ی امکاناتِ ربات — شاملِ ورودی‌های آزادِ
متنی مثلِ ویرایشِ پروفایل، نه فقط دکمه‌های منو — بدونِ استثنا کار کنن.
با /room یا «🏠 اتاق چت» دوباره فعال می‌شه. دکمه‌ی «🚪 خروج» همینو
دستی و بدونِ نیاز به بسته‌بودنِ اتاق فعال می‌کنه.

همین‌جا گپِ جانبی‌ای که موقعِ بررسی پیدا شد رو هم پوشش می‌دیم: فلوی
درخواستِ چتِ پروفایلِ عمومی (public_profile.py) اصلاً چکِ اتاق نداشت،
برخلافِ سه نقطه‌ی ورودِ دیگه‌ی چتِ ۱به۱."""

from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import delete, select

import db
import redis_client as rc
from handlers import public_profile
from handlers.chat import matching as chat_matching


async def _complete_profile(user):
    async with db.async_session() as session:
        u = await session.get(db.User, user.id)
        u.display_name = "کاربر تست"
        u.gender = db.Gender.male
        u.age = 25
        await session.commit()


async def _cleanup_room(room_id: int) -> None:
    async with db.async_session() as session:
        for user in (
            await session.execute(select(db.User).where(db.User.active_room_id == room_id))
        ).scalars().all():
            user.active_room_id = None
        await session.execute(delete(db.ChatRoomMember).where(db.ChatRoomMember.room_id == room_id))
        await session.execute(delete(db.ChatRoom).where(db.ChatRoom.id == room_id))
        await session.commit()


async def _make_room(make_user):
    owner = await make_user(coins=20)
    room, _ = await db.create_chat_room(owner.id, db.RoomGenderPref.any, capacity=5, cost=20)
    await rc.set_active_room(owner.id, room.id)
    member = await make_user(coins=10)
    await db.join_chat_room(member.id, room.id)
    await rc.set_active_room(member.id, room.id)
    return room, owner, member


async def _close_room(owner_id: int) -> None:
    """معادلِ واقعیِ کلیک‌کردنِ owner روی «🔒 بستن اتاق»، تا suppression
    هم مثلِ فلوی واقعی اعمال بشه (نه صرفاً تغییرِ status توی دیتابیس)."""
    from handlers.chatroom import close_room_button

    update = MagicMock()
    update.effective_user.id = owner_id
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    context.bot = MagicMock()
    context.bot.send_message = AsyncMock()
    await close_room_button(update, context)


# ---------------------------------------------------------------------
# بستنِ اتاق: suppress می‌شه، اعضا منویِ اصلیِ کامل می‌گیرن
# ---------------------------------------------------------------------

async def test_close_room_suppresses_non_owner_and_sends_plain_main_menu(make_user):
    room, owner, member = await _make_room(make_user)

    await _close_room(owner.id)

    assert await rc.is_room_ui_suppressed(member.id) is True
    assert await rc.is_room_ui_suppressed(owner.id) is False  # owner suppress نمی‌شه
    assert await rc.get_active_room(member.id) == room.id  # عضویت دست‌نخورده

    await _cleanup_room(room.id)


async def test_text_router_lets_closed_room_member_reach_other_features(make_user):
    """حتی ورودیِ آزادِ متنی (نه فقط دکمه‌ی منو) باید کار کنه، چون
    suppress یعنی دقیقاً مثلِ نداشتنِ اتاق رفتار کن — این دقیقاً همون
    گزارشی بود که با روشِ قبلیِ ROOM_CLOSED_ALLOWED_ROUTES کار نمی‌کرد
    (ویرایشِ نامِ نمایشی که آزاد تایپ می‌شه، نه یه دکمه)."""
    import main

    room, owner, member = await _make_room(make_user)
    await _complete_profile(member)
    await _close_room(owner.id)

    update = MagicMock()
    update.effective_user.id = member.id
    update.effective_user.username = "test"
    update.effective_user.first_name = "کاربر"
    update.message = MagicMock()
    update.message.text = "👤 پروفایل"
    update.message.reply_text = AsyncMock()
    update.callback_query = None
    context = MagicMock()
    context.user_data = {}

    await main.text_router(update, context)

    update.message.reply_text.assert_awaited_once()
    text = update.message.reply_text.await_args.args[0]
    assert "بسته‌ست" not in text
    assert "کاربر تست" in text

    await _cleanup_room(room.id)


async def test_text_router_lets_closed_room_member_open_1to1_gender_menu(make_user):
    """کلیک روی «وصل کن به یه ناشناس» وقتی suppress شده نباید فوراً رد
    بشه؛ باید کیبوردِ اینلاینِ انتخابِ جنسیت رو نشون بده."""
    import main

    room, owner, member = await _make_room(make_user)
    await _complete_profile(member)
    await _close_room(owner.id)

    update = MagicMock()
    update.effective_user.id = member.id
    update.effective_user.username = "test"
    update.effective_user.first_name = "کاربر"
    update.effective_message = MagicMock()
    update.effective_message.reply_text = AsyncMock()
    update.message = update.effective_message
    update.message.text = "💬 وصل کن به یه ناشناس!"
    update.callback_query = None
    context = MagicMock()
    context.user_data = {}

    await main.text_router(update, context)

    update.effective_message.reply_text.assert_awaited_once()
    text = update.effective_message.reply_text.await_args.args[0]
    assert "چه جنسیتی" in text
    assert "اتاقِ چتِ فعال" not in text

    await _cleanup_room(room.id)


# ---------------------------------------------------------------------
# چک‌کردنِ وضعیت (/room یا «🏠 اتاق چت») دوباره فعال می‌کنه
# ---------------------------------------------------------------------

async def test_show_active_room_status_reactivates_suppressed_handler(make_user):
    from handlers import chatroom

    room, owner, member = await _make_room(make_user)
    await _close_room(owner.id)
    assert await rc.is_room_ui_suppressed(member.id) is True

    update = MagicMock()
    update.callback_query = None
    update.effective_user.id = member.id
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()

    await chatroom.show_room_menu(update, MagicMock())

    assert await rc.is_room_ui_suppressed(member.id) is False

    update.message.reply_text.assert_awaited_once()
    text = update.message.reply_text.await_args.args[0]
    assert f"شماره‌ی اتاق: {room.id}" in text
    markup = update.message.reply_text.await_args.kwargs["reply_markup"]
    sent_labels = {btn.text for row in markup.keyboard for btn in row}
    assert "🚪 ترک اتاق" in sent_labels
    assert "👤 پروفایل" not in sent_labels

    await _cleanup_room(room.id)


async def test_relay_rejects_message_after_reactivating_closed_room(make_user):
    """بعدِ چک‌کردنِ وضعیت (suppress برداشته می‌شه)، اگه عضو بازم سعی کنه
    پیام بده، باید رد بشه (چون واقعاً بسته‌ست)، ولی کیبوردش همون
    کیبوردِ داخلِ‌اتاق بمونه، نه منوی اصلی."""
    from handlers import chatroom

    room, owner, member = await _make_room(make_user)
    await _close_room(owner.id)
    await rc.unsuppress_room_ui(member.id)  # معادلِ چک‌کردنِ /room

    update = MagicMock()
    update.effective_user.id = member.id
    update.effective_message = MagicMock()
    update.effective_message.reply_text = AsyncMock()
    update.message = update.effective_message
    update.message.text = "سلام"
    update.message.reply_to_message = None

    await chatroom.relay_room_message(update, MagicMock(), room.id)

    update.effective_message.reply_text.assert_awaited_once()
    assert "بسته‌ست" in update.effective_message.reply_text.await_args.args[0]
    markup = update.effective_message.reply_text.await_args.kwargs["reply_markup"]
    sent_labels = {btn.text for row in markup.keyboard for btn in row}
    assert "🚪 ترک اتاق" in sent_labels

    await _cleanup_room(room.id)


async def test_reopen_room_reactivates_all_suppressed_members(make_user):
    from handlers.chatroom import reopen_room_button

    room, owner, member = await _make_room(make_user)
    await _close_room(owner.id)
    assert await rc.is_room_ui_suppressed(member.id) is True

    update = MagicMock()
    update.effective_user.id = owner.id
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    context.bot = MagicMock()
    context.bot.send_message = AsyncMock()

    await reopen_room_button(update, context)

    assert await rc.is_room_ui_suppressed(member.id) is False

    await _cleanup_room(room.id)


# ---------------------------------------------------------------------
# دکمه‌ی «🚪 خروج»: دستی، مستقل از باز/بسته‌بودنِ اتاق
# ---------------------------------------------------------------------

async def test_exit_room_ui_button_suppresses_without_leaving_membership(make_user):
    from handlers.chatroom import exit_room_ui_button

    room, owner, member = await _make_room(make_user)
    # اتاق بازه؛ عضو خودش تصمیم می‌گیره موقتاً خارج بشه

    update = MagicMock()
    update.effective_user.id = member.id
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()

    await exit_room_ui_button(update, MagicMock())

    assert await rc.is_room_ui_suppressed(member.id) is True
    assert await rc.get_active_room(member.id) == room.id  # عضویت دست‌نخورده

    async with db.async_session() as session:
        refreshed = await session.get(db.User, member.id)
        assert refreshed.active_room_id == room.id

    update.message.reply_text.assert_awaited_once()
    text = update.message.reply_text.await_args.args[0]
    assert "/room" in text
    sent_labels = {btn.text for row in update.message.reply_text.await_args.kwargs["reply_markup"].keyboard for btn in row}
    assert "💬 وصل کن به یه ناشناس!" in sent_labels

    await _cleanup_room(room.id)


async def test_leaving_room_clears_suppression_flag(make_user):
    """clear_active_room باید suppress_room_ui رو هم پاک کنه تا کلیدِ
    یتیم توی Redis نمونه."""
    from handlers.chatroom import leave_room_button, exit_room_ui_button

    room, owner, member = await _make_room(make_user)

    update = MagicMock()
    update.effective_user.id = member.id
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()
    await exit_room_ui_button(update, MagicMock())
    assert await rc.is_room_ui_suppressed(member.id) is True

    context = MagicMock()
    context.bot = MagicMock()
    context.bot.send_message = AsyncMock()
    await leave_room_button(update, context)

    assert await rc.is_room_ui_suppressed(member.id) is False
    assert await rc.get_active_room(member.id) is None

    await _cleanup_room(room.id)


# ---------------------------------------------------------------------
# چکِ ۱به۱ باید همچنان کار کنه، چون این وابسته به suppression نیست
# ---------------------------------------------------------------------

async def test_desired_gender_callback_still_blocked_for_closed_room_member(make_user):
    room, owner, member = await _make_room(make_user)
    await _close_room(owner.id)

    query = MagicMock()
    query.data = "matchgender:any"
    query.from_user.id = member.id
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    update = MagicMock()
    update.callback_query = query

    await chat_matching.handle_desired_gender_callback(update, MagicMock())

    query.edit_message_text.assert_awaited_once()
    assert "اتاقِ چتِ فعال" in query.edit_message_text.await_args.args[0]
    assert await rc.get_partner(member.id) is None

    await _cleanup_room(room.id)


async def test_start_chat_with_saved_pref_still_blocked_for_room_member(make_user):
    """وقتی next_gender_pref از قبل ذخیره‌ست، start_chat مستقیم می‌ره
    سراغِ try_match بدونِ نمایشِ کیبوردِ اینلاین؛ پس چکِ اتاق همین‌جا
    باید انجام بشه، وگرنه مستقیم وارد صفِ ۱به۱ می‌شد."""
    room, owner, member = await _make_room(make_user)
    async with db.async_session() as session:
        u = await session.get(db.User, member.id)
        u.display_name = "کاربر تست"
        u.gender = db.Gender.male
        u.age = 25
        u.next_gender_pref = "any"
        await session.commit()

    update = MagicMock()
    update.effective_user.id = member.id
    update.effective_user.username = "test"
    update.effective_user.first_name = "کاربر تست"
    update.effective_message = MagicMock()
    update.effective_message.reply_text = AsyncMock()

    await chat_matching.start_chat(update, MagicMock())

    update.effective_message.reply_text.assert_awaited_once()
    assert "اتاقِ چتِ فعال" in update.effective_message.reply_text.await_args.args[0]
    assert await rc.is_waiting(member.id) is False

    await _cleanup_room(room.id)


async def test_text_router_still_relays_when_room_open(make_user):
    import main

    room, owner, member = await _make_room(make_user)
    await _complete_profile(member)
    # اتاق بازه (پیش‌فرض)، suppress هم نشده

    def _fake_send(*args, **kwargs):
        sent = MagicMock()
        sent.message_id = 9999
        return sent

    update = MagicMock()
    update.effective_user.id = member.id
    update.message = MagicMock()
    update.message.text = "سلام به همه"
    update.message.message_id = 1
    update.message.reply_text = AsyncMock()
    update.message.caption = None
    update.message.reply_to_message = None
    for field in ("photo", "sticker", "voice", "audio", "video", "video_note", "document", "animation"):
        setattr(update.message, field, None)
    update.effective_message = update.message
    update.callback_query = None
    context = MagicMock()
    context.user_data = {}
    context.bot = MagicMock()
    context.bot.send_message = AsyncMock(side_effect=_fake_send)

    await main.text_router(update, context)

    # پیام باید relay بشه (به owner)، نه به‌عنوانِ دستورِ منو رد بشه
    context.bot.send_message.assert_awaited()

    await _cleanup_room(room.id)


async def test_chat_request_send_blocked_when_requester_in_room(make_user):
    room, owner, member = await _make_room(make_user)
    target = await make_user(coins=10)

    query = MagicMock()
    query.data = f"chatrequest:{target.id}"
    query.from_user.id = member.id
    query.answer = AsyncMock()
    query.message = MagicMock()
    query.message.reply_text = AsyncMock()
    update = MagicMock()
    update.callback_query = query

    await public_profile.handle_chat_request_button(update, MagicMock())

    query.message.reply_text.assert_awaited_once()
    text = query.message.reply_text.await_args.args[0]
    assert "اتاقِ چتِ فعال" in text

    async with db.async_session() as session:
        refreshed = await session.get(db.User, member.id)
        assert refreshed.coins == 10  # سکه‌ای برای درخواست کسر نشد

    await _cleanup_room(room.id)


async def test_chat_request_accept_blocked_when_acceptor_in_room(make_user):
    room, owner, member = await _make_room(make_user)
    requester = await make_user(coins=10)

    request_id = await rc.create_chat_request(requester.id, member.id)

    query = MagicMock()
    query.data = f"chataccept:{request_id}"
    query.from_user.id = member.id
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    update = MagicMock()
    update.callback_query = query

    await public_profile.handle_chat_request_accept(update, MagicMock())

    query.edit_message_text.assert_awaited_once()
    assert "قابلِ قبول نیست" in query.edit_message_text.await_args.args[0]
    assert await rc.get_partner(member.id) is None
    assert await rc.get_partner(requester.id) is None

    await _cleanup_room(room.id)
