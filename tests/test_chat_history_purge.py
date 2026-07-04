"""تست‌های واحد برای فلوی جدیدِ پاکسازیِ تاریخچه‌ی چتِ ۱به۱:

۱. کلیدهای Redis (تاریخچه‌ی پیام و pending_delete) باید با session_id
   اسکوپ بشن تا اگه دو نفر چندبار پشتِ سرِ هم چت کنن، تاییدها/تاریخچه‌ی
   یه گفتگوی قدیمی با گفتگوی جدیدشون قاطی نشه.
۲. دکمه‌های پاکسازی/گزارش فقط ۲ دقیقه معتبرن (TTL_PENDING_DELETE).
۳. jobِ خودکارِ بعد از ۲ دقیقه فقط سرور رو پاک می‌کنه، نه پیام‌های
   خودِ تلگرام."""

from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import delete

import db
import redis_client as rc
from handlers import report
from handlers.chat import extras as chat_extras


async def _make_session(user_a: int, user_b: int) -> int:
    async with db.async_session() as session:
        chat_session = db.ChatSession(user_a_id=user_a, user_b_id=user_b)
        session.add(chat_session)
        await session.commit()
        await session.refresh(chat_session)
        return chat_session.id


async def _cleanup_session(session_id: int) -> None:
    async with db.async_session() as session:
        await session.execute(delete(db.ChatMessage).where(db.ChatMessage.session_id == session_id))
        await session.execute(delete(db.ChatSession).where(db.ChatSession.id == session_id))
        await session.commit()


def _make_context():
    context = MagicMock()
    context.bot = MagicMock()
    context.bot.send_message = AsyncMock()
    context.bot.delete_message = AsyncMock()
    context.job_queue = MagicMock()
    return context


def _make_callback_update(user_id: int, data: str):
    update = MagicMock()
    update.callback_query.from_user.id = user_id
    update.callback_query.data = data
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    return update


async def test_history_keys_scoped_by_session_dont_leak_across_sessions(make_user):
    a = await make_user()
    b = await make_user()
    session_1 = await _make_session(a.id, b.id)
    session_2 = await _make_session(a.id, b.id)

    await rc.record_message(a.id, 111, session_1)
    await rc.record_message(a.id, 222, session_2)

    ids_session_1 = await rc.pop_history(a.id, session_1)
    assert ids_session_1 == [111]

    # پیامِ سشنِ دوم هنوز دست‌نخورده‌ست، pop سشنِ اول نباید بهش دست بزنه
    ids_session_2 = await rc.pop_history(a.id, session_2)
    assert ids_session_2 == [222]

    await _cleanup_session(session_1)
    await _cleanup_session(session_2)


async def test_pending_delete_quorum_scoped_by_session(make_user):
    a = await make_user()
    b = await make_user()
    session_1 = await _make_session(a.id, b.id)
    session_2 = await _make_session(a.id, b.id)

    await rc.start_pending_delete(a.id, b.id, session_1)
    await rc.start_pending_delete(a.id, b.id, session_2)

    confirmed_1 = await rc.confirm_pending_delete(a.id, b.id, a.id, session_1)
    assert confirmed_1 == {a.id}

    # تاییدِ سشنِ اول نباید توی quorumِ سشنِ دوم حساب بشه
    confirmed_2 = await rc.get_pending_delete_set(a.id, b.id, session_2)
    assert confirmed_2 is None or a.id not in confirmed_2

    await _cleanup_session(session_1)
    await _cleanup_session(session_2)


async def test_delete_history_button_expired_after_window_closes(make_user):
    a = await make_user()
    b = await make_user()
    session_id = await _make_session(a.id, b.id)
    # start_pending_delete صدا زده نشده => کلیدِ active اصلاً وجود نداره،
    # دقیقاً معادلِ حالتی که مهلتِ ۲دقیقه‌ای گذشته.

    update = _make_callback_update(a.id, f"delhist:{a.id}:{b.id}:{session_id}")
    await chat_extras.handle_delete_history_callback(update, _make_context())

    text = update.callback_query.edit_message_text.await_args.args[0]
    assert "معتبر نیست" in text

    await _cleanup_session(session_id)


async def test_report_after_chat_expired_after_window_closes(make_user):
    a = await make_user()
    b = await make_user()
    session_id = await _make_session(a.id, b.id)

    update = _make_callback_update(a.id, f"reportsession:{b.id}:{session_id}")
    update.callback_query.message.reply_text = AsyncMock()
    await report.start_report_after_chat(update, _make_context())

    text = update.callback_query.message.reply_text.await_args.args[0]
    assert "معتبر نیست" in text

    await _cleanup_session(session_id)


async def test_report_after_chat_allowed_within_window(make_user):
    a = await make_user()
    b = await make_user()
    session_id = await _make_session(a.id, b.id)
    await rc.start_pending_delete(a.id, b.id, session_id)

    update = _make_callback_update(a.id, f"reportsession:{b.id}:{session_id}")
    update.callback_query.message.reply_text = AsyncMock()
    await report.start_report_after_chat(update, _make_context())

    text = update.callback_query.message.reply_text.await_args.args[0]
    assert "دلیل گزارش" in text

    await _cleanup_session(session_id)


async def test_auto_purge_job_clears_server_history_without_touching_telegram(make_user):
    a = await make_user()
    b = await make_user()
    session_id = await _make_session(a.id, b.id)

    await rc.start_pending_delete(a.id, b.id, session_id)
    await rc.record_message(a.id, 111, session_id)
    await rc.record_message(b.id, 222, session_id)

    context = _make_context()
    context.job.data = {"user_a": a.id, "user_b": b.id, "session_id": session_id}
    await chat_extras._auto_purge_session_history_job(context)

    # سرور پاک شده: هم Redis (تاریخچه/pending) هم Postgres (history_deleted)
    assert await rc.pop_history(a.id, session_id) == []
    assert await rc.get_pending_delete_set(a.id, b.id, session_id) is None
    async with db.async_session() as session:
        refreshed = await session.get(db.ChatSession, session_id)
        assert refreshed.history_deleted is True

    # ولی به هیچ‌کدوم پیامِ تلگرامی برای حذفِ خودِ پیام‌ها فرستاده نشده
    context.bot.delete_message.assert_not_awaited()

    await _cleanup_session(session_id)
