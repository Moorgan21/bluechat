"""تست‌های واحد برای گیتِ سراسریِ main.reject_spam_blocked_commands:
قبل از این، وقتی کسی به‌خاطرِ اسپم ۶۰ ثانیه بلاک می‌شد، پیام‌های
معمولیش drop می‌شد ولی کامندهاش (/start و بقیه) همچنان جواب می‌گرفتن،
چون CommandHandlerها هیچ چکِ اسپمی نداشتن."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import Update
from telegram.ext import Application, ApplicationHandlerStop, CommandHandler, MessageHandler, filters

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


def _make_start_update(user_id: int) -> Update:
    """آپدیتِ واقعیِ /start؛ برای تستِ integration که خودِ رجیستریِ
    هندلرها تو main.py رو (نه فقط تابع رو مجزا) چک می‌کنه، چون باگِ
    قبلی دقیقاً تو تداخلِ گروه‌بندیِ هندلرها بود، نه تو منطقِ خودِ تابع."""
    from datetime import datetime

    from telegram import Chat, Message, MessageEntity
    from telegram import User as TgUser

    message = Message(
        message_id=1,
        date=datetime.now(),
        chat=Chat(id=user_id, type="private"),
        from_user=TgUser(id=user_id, is_bot=False, first_name="Test"),
        text="/start",
        entities=[MessageEntity(type=MessageEntity.BOT_COMMAND, offset=0, length=6)],
    )
    return Update(update_id=1, message=message)


async def test_command_handler_actually_blocked_when_spam_blocked_via_real_app(make_user):
    """گیتِ اسپمِ کامندها باید واقعاً جلوی CommandHandlerِ ثبت‌شده تو
    main.py رو بگیره؛ این تست دقیقاً همون باگی رو گیر می‌ندازه که تستِ
    واحدِ بالا (که تابع رو مستقیم صدا می‌زنه) نمی‌تونه: توی PTB، تو هر
    گروه فقط اولین هندلرِ match‌شده اجرا می‌شه، پس گیتِ اسپمِ کامندها
    (filters.COMMAND) باید تو گروهِ جداگانه‌ای (-2) از گیتِ بن (که با
    filters.ALL همه‌چیز رو می‌قاپه و تو -1 هست) رجیستر بشه، وگرنه
    filters.ALL همیشه اول match می‌کنه و گیتِ اسپم هیچ‌وقت اجرا نمی‌شه."""
    user = await make_user()
    await spam_guard._block_user(user.id)

    app = Application.builder().token("123456:FAKE-TOKEN-FOR-TEST-XXXXXXXXXXXXXXX").build()

    async def fake_ban_gate(update, context):
        return None

    start_called = MagicMock()

    async def fake_start(update, context):
        start_called()

    # همون ترتیب و گروه‌بندیِ main.py: گیتِ اسپمِ کامندها تو -2 (قبل)،
    # گیتِ بن (filters.ALL) تو -1، بعد CommandHandler تو گروهِ پیش‌فرضِ ۰.
    app.add_handler(
        MessageHandler(filters.UpdateType.MESSAGE & filters.COMMAND, main.reject_spam_blocked_commands), group=-2
    )
    app.add_handler(MessageHandler(filters.ALL, fake_ban_gate), group=-1)
    app.add_handler(CommandHandler("start", fake_start))

    # از app.initialize()/bot.initialize() واقعی صرف‌نظر می‌کنیم چون
    # شبکه (getMe) لازم دارن؛ ولی CommandHandler.check_update برای
    # تطبیقِ نامِ کامند به message.get_bot().username نیاز داره، پس
    # دستی bot_user رو ست و bot رو به پیام bind می‌کنیم.
    app._initialized = True
    from telegram import User as TgBotUser

    app.bot._bot_user = TgBotUser(id=999999999, first_name="TestBot", is_bot=True, username="test_bot")

    update = _make_start_update(user.id)
    update.message.set_bot(app.bot)
    await app.process_update(update)

    start_called.assert_not_called()
