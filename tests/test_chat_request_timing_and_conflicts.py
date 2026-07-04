"""تست‌های واحد برای مهلتِ ۵دقیقه‌ایِ درخواستِ چت و چکِ لحظه‌ی-accept:
هم اعلامِ صریحِ ۵ دقیقه، هم اینکه اگه درخواست‌دهنده (نه فقط پذیرنده)
بینِ ارسال و قبولِ درخواست وارد چتِ ۱به۱ (چه جفت‌شده چه فقط توی صفِ
matchingِ تصادفی) یا اتاق شده باشه، درخواست دیگه قابلِ قبول نیست؛
برعکسش هم درست کار کنه: اگه قبلاً درگیر بوده ولی الان آزاده، قابلِ
قبول باشه."""

from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import delete, select

import db
import redis_client as rc
from handlers import public_profile


async def _complete_profile(user, gender=db.Gender.male):
    async with db.async_session() as session:
        u = await session.get(db.User, user.id)
        u.display_name = "کاربر تست"
        u.gender = gender
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


def _make_send_request_update(user_id: int, target_id: int):
    query = MagicMock()
    query.data = f"chatreq:{target_id}"
    query.from_user.id = user_id
    query.answer = AsyncMock()
    query.message.reply_text = AsyncMock()
    update = MagicMock()
    update.callback_query = query
    return update


def _make_accept_update(request_id: str, acceptor_id: int):
    query = MagicMock()
    query.data = f"chatreqaccept:{request_id}"
    query.from_user.id = acceptor_id
    query.answer = AsyncMock()
    query.message.photo = None
    query.edit_message_text = AsyncMock()
    update = MagicMock()
    update.callback_query = query
    return update


def test_chat_request_timeout_is_five_minutes():
    assert rc.CHAT_REQUEST_TIMEOUT_SECONDS == 300


async def test_send_request_explicitly_mentions_five_minutes(make_user):
    requester = await make_user(coins=10)
    target = await make_user(coins=10)

    update = _make_send_request_update(requester.id, target.id)
    context = MagicMock()
    context.bot.send_message = AsyncMock()

    await public_profile.handle_chat_request_button(update, context)

    confirm_text = update.callback_query.message.reply_text.await_args.args[0]
    assert "۵ دقیقه" in confirm_text

    notify_text = context.bot.send_message.await_args.args[1]
    assert "۵ دقیقه" in notify_text


async def test_accept_fails_when_requester_joined_1to1_queue_after_request(make_user):
    requester = await make_user(coins=10)
    acceptor = await make_user(coins=10)
    await _complete_profile(requester, gender=db.Gender.female)

    request_id = await rc.create_chat_request(requester.id, acceptor.id)
    await rc.enqueue(requester.id)  # درخواست‌دهنده بعداً وارد صفِ چتِ تصادفی شده

    update = _make_accept_update(request_id, acceptor.id)
    await public_profile.handle_chat_request_accept(update, MagicMock())

    text = update.callback_query.edit_message_text.await_args.args[0]
    assert "قابلِ قبول نیست" in text
    assert await rc.get_chat_request(request_id) is None  # درخواست لغو شده
    assert await rc.get_partner(acceptor.id) is None

    await rc.dequeue(requester.id)


async def test_accept_fails_when_acceptor_is_in_1to1_queue(make_user):
    requester = await make_user(coins=10)
    acceptor = await make_user(coins=10)
    await _complete_profile(acceptor, gender=db.Gender.male)

    request_id = await rc.create_chat_request(requester.id, acceptor.id)
    await rc.enqueue(acceptor.id)

    update = _make_accept_update(request_id, acceptor.id)
    await public_profile.handle_chat_request_accept(update, MagicMock())

    text = update.callback_query.edit_message_text.await_args.args[0]
    assert "قابلِ قبول نیست" in text

    await rc.dequeue(acceptor.id)


async def test_accept_fails_when_requester_has_since_joined_a_room(make_user):
    requester = await make_user(coins=20)
    acceptor = await make_user(coins=10)

    request_id = await rc.create_chat_request(requester.id, acceptor.id)

    room, _ = await db.create_chat_room(requester.id, db.RoomGenderPref.any, capacity=4, cost=20)
    await rc.set_active_room(requester.id, room.id)

    update = _make_accept_update(request_id, acceptor.id)
    await public_profile.handle_chat_request_accept(update, MagicMock())

    text = update.callback_query.edit_message_text.await_args.args[0]
    assert "قابلِ قبول نیست" in text
    assert await rc.get_partner(requester.id) is None

    await _cleanup_room(room.id)


async def test_accept_succeeds_after_requester_left_the_queue(make_user):
    """رفتِ اصلی: چک لحظه‌ی accept انجام می‌شه نه لحظه‌ی ارسال، پس اگه
    درخواست‌دهنده قبلاً درگیر بوده ولی الان آزاد شده، باید قابلِ قبول
    باشه."""
    requester = await make_user(coins=10)
    acceptor = await make_user(coins=10)
    await _complete_profile(requester, gender=db.Gender.female)

    request_id = await rc.create_chat_request(requester.id, acceptor.id)
    await rc.enqueue(requester.id)
    await rc.dequeue(requester.id)  # از صف اومده بیرون

    update = _make_accept_update(request_id, acceptor.id)
    context = MagicMock()
    context.bot.send_message = AsyncMock()
    await public_profile.handle_chat_request_accept(update, context)

    text = update.callback_query.edit_message_text.await_args.args[0]
    assert "گفتگو شروع شد" in text
    assert await rc.get_partner(acceptor.id) == requester.id
    assert await rc.get_partner(requester.id) == acceptor.id

    await rc.clear_partner(acceptor.id)


async def test_sixth_active_request_is_blocked(make_user):
    requester = await make_user(coins=20)
    targets = [await make_user(coins=10) for _ in range(6)]

    for t in targets[:5]:
        update = _make_send_request_update(requester.id, t.id)
        context = MagicMock()
        context.bot.send_message = AsyncMock()
        await public_profile.handle_chat_request_button(update, context)

    assert await rc.count_active_chat_requests(requester.id) == 5

    sixth_update = _make_send_request_update(requester.id, targets[5].id)
    context = MagicMock()
    context.bot.send_message = AsyncMock()
    await public_profile.handle_chat_request_button(sixth_update, context)

    text = sixth_update.callback_query.message.reply_text.await_args.args[0]
    assert "۵ درخواستِ چتِ فعال" in text
    context.bot.send_message.assert_not_awaited()  # درخواستِ ششم اصلاً ساخته نشد

    async with db.async_session() as session:
        refreshed = await session.get(db.User, requester.id)
        assert refreshed.coins == 20 - 5 * rc.CHAT_COIN_COST  # فقط بابتِ ۵تای اول کسر شده


async def test_sending_allowed_again_after_one_active_request_resolves(make_user):
    requester = await make_user(coins=20)
    targets = [await make_user(coins=10) for _ in range(6)]

    for t in targets[:5]:
        update = _make_send_request_update(requester.id, t.id)
        context = MagicMock()
        context.bot.send_message = AsyncMock()
        await public_profile.handle_chat_request_button(update, context)

    # به‌جای استخراجِ request_id از داخلِ هندلر، مستقیم یکی رو رد می‌کنیم
    active_ids = await rc.r.smembers(rc.KEY_CHAT_REQUEST_BY_REQUESTER.format(requester_id=requester.id))
    await rc.clear_chat_request(next(iter(active_ids)))

    assert await rc.count_active_chat_requests(requester.id) == 4

    sixth_update = _make_send_request_update(requester.id, targets[5].id)
    context = MagicMock()
    context.bot.send_message = AsyncMock()
    await public_profile.handle_chat_request_button(sixth_update, context)

    confirm_text = sixth_update.callback_query.message.reply_text.await_args.args[0]
    assert "درخواستِ چتت ارسال شد" in confirm_text
    assert await rc.count_active_chat_requests(requester.id) == 5
