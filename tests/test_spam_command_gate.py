"""تست‌های واحد برای گیتِ سراسریِ main.reject_spam_blocked_commands:
قبل از این، وقتی کسی به‌خاطرِ اسپم ۶۰ ثانیه بلاک می‌شد، پیام‌های
معمولیش drop می‌شد ولی کامندهاش (/start و بقیه) همچنان جواب می‌گرفتن،
چون CommandHandlerها هیچ چکِ اسپمی نداشتن."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.ext import ApplicationHandlerStop

import main
import spam_guard


def _make_command_update(user_id: int):
    update = MagicMock()
    update.effective_user.id = user_id
    update.effective_message = MagicMock()
    update.effective_message.reply_text = AsyncMock()
    return update


async def test_reject_spam_blocked_commands_stops_when_already_blocked(make_user):
    user = await make_user()
    await spam_guard._block_user(user.id)

    update = _make_command_update(user.id)
    with pytest.raises(ApplicationHandlerStop):
        await main.reject_spam_blocked_commands(update, MagicMock())

    update.effective_message.reply_text.assert_not_awaited()


async def test_reject_spam_blocked_commands_notifies_on_fresh_block(make_user):
    user = await make_user()
    for _ in range(spam_guard.CMD_LIMIT):
        result = await spam_guard.check_command(user.id)
    assert result == spam_guard.SpamResult.ALLOWED

    update = _make_command_update(user.id)
    with pytest.raises(ApplicationHandlerStop):
        await main.reject_spam_blocked_commands(update, MagicMock())

    update.effective_message.reply_text.assert_awaited_once()
    assert "ثانیه صبر کن" in update.effective_message.reply_text.await_args.args[0]

    assert await spam_guard.is_blocked(user.id) is True


async def test_reject_spam_blocked_commands_noop_for_normal_user(make_user):
    user = await make_user()
    update = _make_command_update(user.id)

    result = await main.reject_spam_blocked_commands(update, MagicMock())

    assert result is None
    update.effective_message.reply_text.assert_not_awaited()
