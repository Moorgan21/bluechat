"""تست‌های واحد برای گیتِ سراسریِ main.reject_banned_users: تا قبل از
این، is_banned هیچ‌جا واقعاً چک نمی‌شد و کاربرِ بن‌شده می‌تونست بدونِ
محدودیت از ربات استفاده کنه (فقط از نتایجِ جستجوی نزدیک حذف می‌شد)."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.ext import ApplicationHandlerStop

import db
import main
import redis_client as rc


def _make_message_update(user_id: int):
    update = MagicMock()
    update.callback_query = None
    update.effective_user.id = user_id
    update.effective_message = MagicMock()
    update.effective_message.reply_text = AsyncMock()
    return update


def _make_callback_update(user_id: int):
    update = MagicMock()
    update.effective_user.id = user_id
    update.callback_query.answer = AsyncMock()
    return update


async def test_is_user_banned_reflects_db_state(make_user):
    user = await make_user()
    assert await db.is_user_banned(user.id) is False

    await db.ban_user(user.id)
    assert await db.is_user_banned(user.id) is True


async def test_is_user_banned_false_for_unknown_user():
    assert await db.is_user_banned(999_999_999_999) is False


async def test_reject_banned_users_stops_message_and_notifies_once(make_user):
    user = await make_user()
    await db.ban_user(user.id)

    update = _make_message_update(user.id)
    with pytest.raises(ApplicationHandlerStop):
        await main.reject_banned_users(update, MagicMock())

    update.effective_message.reply_text.assert_awaited_once()
    assert "مسدود" in update.effective_message.reply_text.await_args.args[0]

    # دومین پیام تو همون پنجره‌ی زمانی نباید دوباره نوتیف بده (ضدِاسپم)
    update2 = _make_message_update(user.id)
    with pytest.raises(ApplicationHandlerStop):
        await main.reject_banned_users(update2, MagicMock())
    update2.effective_message.reply_text.assert_not_awaited()


async def test_reject_banned_users_answers_callback_with_alert(make_user):
    user = await make_user()
    await db.ban_user(user.id)

    update = _make_callback_update(user.id)
    with pytest.raises(ApplicationHandlerStop):
        await main.reject_banned_users(update, MagicMock())

    update.callback_query.answer.assert_awaited_once()
    assert update.callback_query.answer.await_args.kwargs.get("show_alert") is True


async def test_reject_banned_users_noop_for_normal_user(make_user):
    user = await make_user()
    update = _make_message_update(user.id)

    result = await main.reject_banned_users(update, MagicMock())

    assert result is None
    update.effective_message.reply_text.assert_not_awaited()


async def test_should_send_ban_notice_rate_limited(make_user):
    user = await make_user()
    assert await rc.should_send_ban_notice(user.id) is True
    assert await rc.should_send_ban_notice(user.id) is False
