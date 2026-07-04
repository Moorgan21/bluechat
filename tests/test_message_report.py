"""تست‌های واحد برای گزارشِ تک‌پیام (جایگزینِ /report که کاملاً حذف شده):
کاربر توی یه چتِ فعال روی پیامِ طرفِ مقابل ریپلای می‌کنه و می‌نویسه
«گزارش»/«report»؛ فقط همون یک پیام گزارش می‌شه، نه کلِ گفتگو. گزارشِ کلِ
گفتگو فقط از طریقِ دکمه‌ی «🚫 گزارش این گفتگو»یِ بعدِ پایانِ چت ممکنه
(که در tests/test_chat_history_purge.py پوشش داده شده)."""

from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import delete, select

import db
import main
import redis_client as rc
from handlers import report
from handlers.chat import relay as chat_relay


def _make_context():
    context = MagicMock()
    context.bot = MagicMock()
    context.bot.send_chat_action = AsyncMock()
    context.bot.send_message = AsyncMock()
    return context


def _make_reply_update(user_id: int, text: str, replied_message_id: int):
    replied = MagicMock()
    replied.message_id = replied_message_id
    replied.text = "پیامِ طرفِ مقابل"
    replied.caption = None

    msg = MagicMock()
    msg.text = text
    msg.reply_to_message = replied
    msg.message_id = 555
    msg.reply_text = AsyncMock()

    update = MagicMock()
    update.effective_user.id = user_id
    update.message = msg
    return update


async def _cleanup_report(report_id: int) -> None:
    async with db.async_session() as session:
        await session.execute(delete(db.Report).where(db.Report.id == report_id))
        await session.commit()


def test_report_command_and_start_report_fully_removed():
    assert not hasattr(report, "start_report")
    source = open(main.__file__, encoding="utf-8").read()
    assert 'CommandHandler("report"' not in source
    assert 'data == "report:start"' not in source


async def test_reply_report_on_own_message_is_rejected(make_user):
    a = await make_user()
    b = await make_user()
    await rc.set_partner(a.id, b.id)
    await rc.mark_own_message(a.id, 42)  # این پیام مالِ خودِ a ست

    update = _make_reply_update(a.id, "گزارش", 42)
    await chat_relay.relay_message(update, _make_context())

    update.message.reply_text.assert_awaited_once()
    text = update.message.reply_text.await_args.args[0]
    assert "پیامِ خودت" in text


async def test_reply_report_on_partner_message_shows_reason_keyboard(make_user):
    a = await make_user()
    b = await make_user()
    await rc.set_partner(a.id, b.id)
    # علامت‌گذاری نشده به‌عنوانِ پیامِ a، پس یعنی پیامِ رله‌شده‌ی partner است

    update = _make_reply_update(a.id, "گزارش", 42)
    await chat_relay.relay_message(update, _make_context())

    update.message.reply_text.assert_awaited_once()
    text = update.message.reply_text.await_args.args[0]
    assert "دلیل" in text
    keyboard = update.message.reply_text.await_args.kwargs["reply_markup"]
    button_data = [b.callback_data for row in keyboard.inline_keyboard for b in row]
    assert any(cb.startswith("msgreport:reason:spam:") for cb in button_data)
    assert "msgreport:cancel" in button_data


async def test_report_word_without_reply_falls_through_to_normal_relay(make_user):
    a = await make_user()
    b = await make_user()
    await rc.set_partner(a.id, b.id)

    msg = MagicMock()
    msg.text = "گزارش"
    msg.reply_to_message = None
    msg.message_id = 1
    msg.reply_text = AsyncMock()
    update = MagicMock()
    update.effective_user.id = a.id
    update.message = msg

    context = _make_context()
    sent = MagicMock()
    sent.message_id = 100
    context.bot.send_message = AsyncMock(return_value=sent)

    await chat_relay.relay_message(update, context)

    context.bot.send_message.assert_awaited_once_with(b.id, "گزارش", reply_parameters=None, protect_content=False)


async def test_selecting_reason_creates_report_and_pushes_ai_job(make_user):
    a = await make_user()
    b = await make_user()

    token = await rc.store_message_report_context(a.id, b.id, None, "متنِ توهین‌آمیز")

    query_update = MagicMock()
    query_update.callback_query.data = f"msgreport:reason:abuse:{token}"
    query_update.callback_query.from_user.id = a.id
    query_update.callback_query.answer = AsyncMock()
    query_update.callback_query.edit_message_text = AsyncMock()

    await report.message_report_reason_callback(query_update, MagicMock())

    query_update.callback_query.edit_message_text.assert_awaited_once()
    text = query_update.callback_query.edit_message_text.await_args.args[0]
    assert "ثبت شد" in text

    job = await rc.pop_ai_job(timeout=1)
    assert job is not None
    assert job["type"] == "chat_report"
    assert job["reporter_id"] == a.id
    assert job["reported_id"] == b.id
    assert job["reason"] == "abuse"
    assert "متنِ توهین‌آمیز" in job["details"]

    await _cleanup_report(job["report_id"])


async def test_cancel_message_report_does_not_create_report():
    query_update = MagicMock()
    query_update.callback_query.data = "msgreport:cancel"
    query_update.callback_query.answer = AsyncMock()
    query_update.callback_query.edit_message_text = AsyncMock()

    await report.message_report_reason_callback(query_update, MagicMock())

    query_update.callback_query.edit_message_text.assert_awaited_once_with("گزارش لغو شد.")
    assert await rc.pop_ai_job(timeout=1) is None


async def test_expired_message_report_token_is_rejected():
    query_update = MagicMock()
    query_update.callback_query.data = "msgreport:reason:spam:doesnotexist"
    query_update.callback_query.from_user.id = 1
    query_update.callback_query.answer = AsyncMock()
    query_update.callback_query.edit_message_text = AsyncMock()

    await report.message_report_reason_callback(query_update, MagicMock())

    text = query_update.callback_query.edit_message_text.await_args.args[0]
    assert "معتبر نیست" in text
