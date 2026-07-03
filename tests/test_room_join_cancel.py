"""تست‌های واحد برای دو درخواستِ کاربر:
۱) دکمه‌ی «❌ لغو جستجوی اتاق» زیرِ پیامِ صفِ انتظار — دقیقاً معادلِ
   cancel_queue_keyboardِ ۱به۱، سکه برمی‌گردونه و از صف درمیاره.
۲) اقدامِ دوباره برای ایجاد/عضویتِ اتاق درحالی‌که از قبل تو صفِ
   عضویتِ اتاق منتظری، باید رد بشه (نه دوباره enqueue)."""

from unittest.mock import AsyncMock, MagicMock

import db
import redis_client as rc
from handlers.chatroom import creation, matching


def _make_context():
    context = MagicMock()
    context.bot = MagicMock()
    context.bot.send_message = AsyncMock()
    context.job_queue = MagicMock()
    context.job_queue.run_once = MagicMock()
    context.job_queue.get_jobs_by_name = MagicMock(return_value=[])
    return context


def _make_cancel_query(user_id: int):
    query = MagicMock()
    query.from_user.id = user_id
    query.answer = AsyncMock()
    query.message = MagicMock()
    query.message.delete = AsyncMock()
    update = MagicMock()
    update.callback_query = query
    return update, query


async def test_cancel_button_refunds_and_dequeues(make_user):
    user = await make_user(coins=7)
    await rc.enqueue_room_join(user.id, "any")

    update, query = _make_cancel_query(user.id)
    context = _make_context()

    await matching.handle_cancel_room_join_button(update, context)

    query.message.delete.assert_awaited_once()
    assert await rc.is_waiting_room_join(user.id) is None

    async with db.async_session() as session:
        refreshed = await session.get(db.User, user.id)
        assert refreshed.coins == 10  # ۷ + بازگشتِ ۳ سکه‌ی جستجو

    context.bot.send_message.assert_awaited_once()
    assert "خارج شدی" in context.bot.send_message.await_args.args[1]


async def test_cancel_button_is_noop_if_already_resolved(make_user):
    user = await make_user(coins=10)
    # عمداً enqueue نمی‌کنیم؛ یعنی انگار قبلاً claim/timeout شده

    update, query = _make_cancel_query(user.id)
    context = _make_context()

    await matching.handle_cancel_room_join_button(update, context)

    query.message.delete.assert_awaited_once()
    context.bot.send_message.assert_not_called()

    async with db.async_session() as session:
        refreshed = await session.get(db.User, user.id)
        assert refreshed.coins == 10  # دست‌نخورده، بازگشتِ دوباره‌ای رخ نداد


async def test_retry_join_while_already_waiting_is_blocked(make_user):
    user = await make_user(coins=10)
    await rc.enqueue_room_join(user.id, "any")

    query = MagicMock()
    query.from_user.id = user.id
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    update = MagicMock()
    update.callback_query = query

    await matching.start_join_flow(update, MagicMock())

    query.edit_message_text.assert_awaited_once()
    assert "منتظرِ پیدا شدنِ یه اتاقی" in query.edit_message_text.await_args.args[0]

    async with db.async_session() as session:
        refreshed = await session.get(db.User, user.id)
        assert refreshed.coins == 10  # سکه‌ای کسر نشد

    await rc.dequeue_room_join(user.id, "any")


async def test_retry_create_while_already_waiting_to_join_is_blocked(make_user):
    user = await make_user(coins=30)
    await rc.enqueue_room_join(user.id, "any")

    query = MagicMock()
    query.from_user.id = user.id
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    update = MagicMock()
    update.callback_query = query
    update.effective_user.id = user.id

    await creation._start_create_flow(update, MagicMock())

    query.edit_message_text.assert_awaited_once()
    assert "منتظرِ پیدا شدنِ یه اتاقی" in query.edit_message_text.await_args.args[0]

    async with db.async_session() as session:
        refreshed = await session.get(db.User, user.id)
        assert refreshed.coins == 30  # هیچ اتاقی ساخته/پرداخت نشد
        assert refreshed.active_room_id is None

    await rc.dequeue_room_join(user.id, "any")
