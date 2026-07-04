"""تست‌های واحد برای این باگ: بلاک‌کردنِ فرستنده در پیام‌های ناشناس باید
هم جلوی پیامِ ناشناسِ جدید رو بگیره هم جلوی زنجیره‌ی پاسخ‌ها
(handle_pending_reply_input) رو، وگرنه طرفِ بلاک‌شده می‌تونه از طریقِ
دکمه‌ی «↩️ پاسخ دادن» دوباره برای صاحبِ لینک پیام بفرسته. فرستنده‌ی
بلاک‌شده باید صراحتاً باخبر بشه (نه یه موفقیتِ ساختگی)."""

from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import delete

import db
import redis_client as rc
from handlers import anon_note


def _make_update(user_id: int):
    update = MagicMock()
    update.effective_user.id = user_id
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()
    update.message.chat_id = 12345
    update.message.message_id = 1
    return update


def _make_context():
    context = MagicMock()
    context.bot = MagicMock()
    context.bot.send_message = AsyncMock()
    context.bot.copy_message = AsyncMock()
    return context


async def _cleanup_block(owner_id: int) -> None:
    async with db.async_session() as session:
        await session.execute(delete(db.BlockedSender).where(db.BlockedSender.owner_id == owner_id))
        await session.commit()


async def test_send_anon_note_blocked_sender_gets_explicit_notice(make_user):
    sender = await make_user()
    owner = await make_user()
    await db.block_sender(owner.id, sender.id)

    update, context = _make_update(sender.id), _make_context()
    try:
        await anon_note.send_anon_note(owner.id, sender.id, update, context)

        text = update.message.reply_text.await_args.args[0]
        assert "بلاک" in text
        context.bot.send_message.assert_not_awaited()  # owner هیچ نوتیفی نمی‌گیره
    finally:
        await _cleanup_block(owner.id)


async def test_reply_chain_blocks_previously_blocked_sender(make_user):
    """B قبلاً برای A یه پاسخ فرستاده (نوتی که sender=B داره). حالا B،
    A رو بلاک می‌کنه. اگه A از دکمه‌ی «پاسخ دادن» زیر همون پاسخ استفاده
    کنه، نباید چیزی به B تحویل داده بشه."""
    a = await make_user()
    b = await make_user()

    note_id = await rc.create_note(b.id)
    await rc.set_awaiting_reply(a.id, note_id)
    await db.block_sender(b.id, a.id)

    update, context = _make_update(a.id), _make_context()
    try:
        consumed = await anon_note.handle_pending_reply_input(update, context)

        assert consumed is True
        context.bot.copy_message.assert_not_awaited()
        text = update.message.reply_text.await_args.args[0]
        assert "بلاک" in text
    finally:
        await _cleanup_block(b.id)


async def test_reply_chain_delivers_when_not_blocked(make_user):
    """رگرسیون: وقتی بلاکی در کار نیست، پاسخ باید عادی تحویل داده بشه."""
    a = await make_user()
    b = await make_user()

    note_id = await rc.create_note(b.id)
    await rc.set_awaiting_reply(a.id, note_id)

    update, context = _make_update(a.id), _make_context()
    await anon_note.handle_pending_reply_input(update, context)

    context.bot.copy_message.assert_awaited_once()
    assert context.bot.copy_message.await_args.kwargs["chat_id"] == b.id
