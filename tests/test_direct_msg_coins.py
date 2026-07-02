"""تست‌های واحد برای کسرِ سکه در پیامِ دایرکت (send_direct_msg در
handlers/anon_note.py) — با موکِ سبکِ Update/Context تلگرام تا بدونِ
نیاز به شبکه یا توکنِ واقعیِ بات تست بشه."""

from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import delete

import db
from handlers import anon_note


def _make_update():
    update = MagicMock()
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()
    update.message.chat_id = 12345
    update.message.message_id = 1
    return update


def _make_context():
    context = MagicMock()
    context.bot = MagicMock()
    context.bot.send_message = AsyncMock()
    return context


async def test_send_direct_msg_deducts_exactly_one_coin(make_user):
    sender = await make_user(coins=5)
    owner = await make_user(coins=5)
    update, context = _make_update(), _make_context()

    await anon_note.send_direct_msg(owner.id, sender.id, update, context)

    async with db.async_session() as session:
        refreshed = await session.get(db.User, sender.id)
        assert refreshed.coins == 4

    update.message.reply_text.assert_awaited_with("✅ پیامت ارسال شد.")
    context.bot.send_message.assert_awaited()  # نوتیف به owner فرستاده شد


async def test_send_direct_msg_charges_regardless_of_owner_reading_it(make_user):
    """صریحاً همون رفتاری که خواسته شده: سکه در لحظه‌ی ارسال کسر می‌شه،
    نه لحظه‌ای که مقصد پیام رو «می‌بینه» — اینجا هیچ شبیه‌سازیِ دیدنی
    نیست و صرفاً با فرستادن، سکه باید کم بشه."""
    sender = await make_user(coins=3)
    owner = await make_user(coins=0)
    update, context = _make_update(), _make_context()

    await anon_note.send_direct_msg(owner.id, sender.id, update, context)

    async with db.async_session() as session:
        refreshed = await session.get(db.User, sender.id)
        assert refreshed.coins == 2


async def test_send_direct_msg_insufficient_balance_blocks_send_and_charges_nothing(make_user):
    sender = await make_user(coins=0)
    owner = await make_user(coins=5)
    update, context = _make_update(), _make_context()

    await anon_note.send_direct_msg(owner.id, sender.id, update, context)

    async with db.async_session() as session:
        refreshed = await session.get(db.User, sender.id)
        assert refreshed.coins == 0

    context.bot.send_message.assert_not_awaited()  # پیام هرگز به مقصد نرسید
    reply_text = update.message.reply_text.await_args.args[0]
    assert "سکه" in reply_text


async def test_send_direct_msg_blocked_sender_is_not_charged(make_user):
    sender = await make_user(coins=5)
    owner = await make_user(coins=5)
    await db.block_sender(owner.id, sender.id)
    update, context = _make_update(), _make_context()

    try:
        await anon_note.send_direct_msg(owner.id, sender.id, update, context)

        async with db.async_session() as session:
            refreshed = await session.get(db.User, sender.id)
            assert refreshed.coins == 5  # بلاک‌شده، پس اصلاً کسر نمی‌شه

        context.bot.send_message.assert_not_awaited()
    finally:
        async with db.async_session() as session:
            await session.execute(delete(db.BlockedSender).where(db.BlockedSender.owner_id == owner.id))
            await session.commit()
