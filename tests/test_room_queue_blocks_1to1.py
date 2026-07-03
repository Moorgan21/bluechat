"""تست‌های واحد برای گپی که کاربر گزارش کرد: کسی که تو صفِ عضویتِ اتاق
منتظره (rc.is_waiting_room_join) قبلاً هیچ‌جا چک نمی‌شد، پس می‌تونست
هم‌زمان وارد چتِ ۱به۱ هم بشه. سه نقطه‌ی ورودِ ۱به۱ (start_chat،
handle_desired_gender_callback، run_search) الان این حالت رو رد
می‌کنن."""

from unittest.mock import AsyncMock, MagicMock

import db
import redis_client as rc
from handlers import search
from handlers.chat import matching


def _make_context():
    context = MagicMock()
    context.user_data = {}
    return context


async def test_start_chat_without_saved_pref_shows_gender_menu_while_waiting_for_room(make_user):
    """بدونِ next_gender_pref ذخیره‌شده، start_chat فقط کیبوردِ
    اینلاینِ انتخابِ جنسیت رو نشون می‌ده، بدونِ هیچ اکشنِ واقعی؛ پس
    نباید همین‌جا با پیامِ اتاق رد بشه — چکِ واقعی جای دیگه‌ایه
    (handle_desired_gender_callback)."""
    user = await make_user(coins=10)
    async with db.async_session() as session:
        u = await session.get(db.User, user.id)
        u.display_name = "علی"
        u.gender = db.Gender.male
        u.age = 25
        await session.commit()

    await rc.enqueue_room_join(user.id, "any")

    update = MagicMock()
    update.effective_user.id = user.id
    update.effective_user.username = "test"
    update.effective_user.first_name = "علی"
    update.effective_message = MagicMock()
    update.effective_message.reply_text = AsyncMock()

    await matching.start_chat(update, _make_context())

    update.effective_message.reply_text.assert_awaited_once()
    text = update.effective_message.reply_text.await_args.args[0]
    assert "چه جنسیتی" in text
    assert "منتظرِ پیدا شدنِ یه اتاقی" not in text

    assert await rc.get_partner(user.id) is None
    assert await rc.is_waiting(user.id) is False

    await rc.dequeue_room_join(user.id, "any")


async def test_start_chat_with_saved_pref_blocked_while_waiting_for_room(make_user):
    """با next_gender_pref ذخیره‌شده، start_chat مستقیم می‌ره سراغِ
    try_match بدونِ نمایشِ کیبورد؛ پس چکِ اتاق همین‌جا لازمه."""
    user = await make_user(coins=10)
    async with db.async_session() as session:
        u = await session.get(db.User, user.id)
        u.display_name = "علی"
        u.gender = db.Gender.male
        u.age = 25
        u.next_gender_pref = "any"
        await session.commit()

    await rc.enqueue_room_join(user.id, "any")

    update = MagicMock()
    update.effective_user.id = user.id
    update.effective_user.username = "test"
    update.effective_user.first_name = "علی"
    update.effective_message = MagicMock()
    update.effective_message.reply_text = AsyncMock()

    await matching.start_chat(update, _make_context())

    update.effective_message.reply_text.assert_awaited_once()
    assert "منتظرِ پیدا شدنِ یه اتاقی" in update.effective_message.reply_text.await_args.args[0]

    assert await rc.get_partner(user.id) is None
    assert await rc.is_waiting(user.id) is False

    await rc.dequeue_room_join(user.id, "any")


async def test_desired_gender_callback_blocked_while_waiting_for_room(make_user):
    user = await make_user(coins=10)
    await rc.enqueue_room_join(user.id, "any")

    query = MagicMock()
    query.data = "matchgender:any"
    query.from_user.id = user.id
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    update = MagicMock()
    update.callback_query = query

    await matching.handle_desired_gender_callback(update, _make_context())

    query.edit_message_text.assert_awaited_once()
    assert "منتظرِ پیدا شدنِ یه اتاقی" in query.edit_message_text.await_args.args[0]
    assert await rc.is_waiting(user.id) is False

    await rc.dequeue_room_join(user.id, "any")


async def test_run_search_blocked_while_waiting_for_room(make_user):
    user = await make_user(coins=10)
    async with db.async_session() as session:
        u = await session.get(db.User, user.id)
        u.display_name = "سارا"
        u.gender = db.Gender.female
        u.age = 22
        await session.commit()

    await rc.enqueue_room_join(user.id, "any")

    query = MagicMock()
    query.from_user.id = user.id
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    update = MagicMock()
    update.callback_query = query

    await search.run_search(update, _make_context())

    query.edit_message_text.assert_awaited_once()
    assert "منتظرِ پیدا شدنِ یه اتاقی" in query.edit_message_text.await_args.args[0]

    await rc.dequeue_room_join(user.id, "any")
