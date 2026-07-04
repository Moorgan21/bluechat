"""تست‌های واحد برای بلاک/آنبلاکِ پروفایلِ عمومی: تاگل‌شدنِ دکمه بینِ
«بلاک»/«آنبلاک»، پیامِ صریحِ بلاک به‌جای موفقیتِ ساختگی، و اولویتِ چکِ
بلاک نسبت به چکِ سایلنت."""

from unittest.mock import AsyncMock, MagicMock

import db
import redis_client as rc
from handlers import public_profile
from keyboards import public_profile_keyboard


def _make_callback_update(user_id: int, data: str):
    update = MagicMock()
    update.callback_query.from_user.id = user_id
    update.callback_query.data = data
    update.callback_query.answer = AsyncMock()
    update.callback_query.message.reply_text = AsyncMock()
    update.callback_query.message.edit_reply_markup = AsyncMock()
    return update


def _button_texts(keyboard):
    return [b.text for row in keyboard.inline_keyboard for b in row]


async def test_public_profile_keyboard_toggles_block_button_label():
    not_blocked = public_profile_keyboard(123, reactions_enabled=False, is_blocked=False)
    blocked = public_profile_keyboard(123, reactions_enabled=False, is_blocked=True)

    not_blocked_texts = [b.text for row in not_blocked.inline_keyboard for b in row]
    blocked_texts = [b.text for row in blocked.inline_keyboard for b in row]

    assert "🚫 بلاک" in not_blocked_texts
    assert "✅ آنبلاک" in blocked_texts
    assert "🚫 بلاک" not in blocked_texts


async def test_block_then_unblock_roundtrip(make_user):
    owner = await make_user()
    target = await make_user()

    block_update = _make_callback_update(owner.id, f"pubblock:{target.id}")
    await public_profile.handle_public_block_button(block_update, MagicMock())
    assert await db.is_sender_blocked(owner.id, target.id) is True

    unblock_update = _make_callback_update(owner.id, f"pubunblock:{target.id}")
    await public_profile.handle_public_unblock_button(unblock_update, MagicMock())
    assert await db.is_sender_blocked(owner.id, target.id) is False


async def test_block_button_click_refreshes_keyboard_in_place(make_user):
    """کاربر خواسته با کلیک، خودِ دکمه‌ی زیرِ همون پیام فوراً عوض بشه، نه
    اینکه فقط یه پیامِ جدا بیاد و کاربر مجبور باشه پروفایل رو دوباره باز کنه."""
    owner = await make_user()
    target = await make_user()

    block_update = _make_callback_update(owner.id, f"pubblock:{target.id}")
    await public_profile.handle_public_block_button(block_update, MagicMock())

    block_update.callback_query.message.edit_reply_markup.assert_awaited_once()
    new_keyboard = block_update.callback_query.message.edit_reply_markup.await_args.kwargs["reply_markup"]
    assert "✅ آنبلاک" in _button_texts(new_keyboard)
    assert "🚫 بلاک" not in _button_texts(new_keyboard)

    unblock_update = _make_callback_update(owner.id, f"pubunblock:{target.id}")
    await public_profile.handle_public_unblock_button(unblock_update, MagicMock())

    unblock_update.callback_query.message.edit_reply_markup.assert_awaited_once()
    reverted_keyboard = unblock_update.callback_query.message.edit_reply_markup.await_args.kwargs["reply_markup"]
    assert "🚫 بلاک" in _button_texts(reverted_keyboard)
    assert "✅ آنبلاک" not in _button_texts(reverted_keyboard)


async def test_chat_request_explicit_block_message_not_fake_success(make_user):
    requester = await make_user(coins=10)
    target = await make_user(coins=10)
    await db.block_sender(target.id, requester.id)

    try:
        update = _make_callback_update(requester.id, f"chatreq:{target.id}")
        await public_profile.handle_chat_request_button(update, MagicMock())

        text = update.callback_query.message.reply_text.await_args.args[0]
        assert "بلاک" in text
        assert "ثبت شد" not in text

        async with db.async_session() as session:
            refreshed = await session.get(db.User, requester.id)
            assert refreshed.coins == 10  # سکه‌ای کسر نشده
    finally:
        # بدونِ این، ردیفِ blocked_senders برای همیشه تو دیتابیسِ تستی
        # می‌مونه (make_user فقط User/CoinTransaction رو پاک می‌کنه)، و
        # چون شناسه‌ی کاربرهای تستی هر اجرای جداگانه از همون پایه شروع
        # می‌شه، این ردیفِ باقی‌مونده می‌تونه با کاربرهای تازه‌ی یه اجرای
        # بعدی تصادفاً collision کنه و اون تست‌ها رو به‌اشتباه fail کنه.
        await db.unblock_sender(target.id, requester.id)


async def test_chat_request_block_takes_priority_over_silent(make_user):
    """طرفِ مقابل هم بلاکش کرده هم سایلنته؛ باید خطای بلاک بیاد نه سایلنت."""
    requester = await make_user(coins=10)
    target = await make_user(coins=10)
    await db.block_sender(target.id, requester.id)
    await db.set_silent_mode(target.id, True)

    try:
        update = _make_callback_update(requester.id, f"chatreq:{target.id}")
        await public_profile.handle_chat_request_button(update, MagicMock())

        text = update.callback_query.message.reply_text.await_args.args[0]
        assert "بلاک" in text
        assert "سایلنت" not in text
    finally:
        await db.unblock_sender(target.id, requester.id)


async def test_chat_request_silent_message_when_not_blocked(make_user):
    requester = await make_user(coins=10)
    target = await make_user(coins=10)
    await db.set_silent_mode(target.id, True)

    update = _make_callback_update(requester.id, f"chatreq:{target.id}")
    await public_profile.handle_chat_request_button(update, MagicMock())

    text = update.callback_query.message.reply_text.await_args.args[0]
    assert "سایلنت" in text
