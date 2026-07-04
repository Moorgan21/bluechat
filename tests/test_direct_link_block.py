"""تست‌های واحد برای این باگ: قبل از دعوت‌کردنِ کاربر به نوشتنِ پیامِ
ناشناس (از طریقِ لینکِ مستقیم /start direct_<code>)، main._handle_direct_link
اصلاً چکِ بلاک نمی‌کرد؛ فقط لحظه‌ی ارسالِ واقعیِ پیام (send_anon_note)
متوجهِ بلاک‌بودنِ فرستنده می‌شد. حالا باید همین‌جا، قبل از نمایشِ
«✍️ پیامت رو بنویس»، چک بشه."""

from unittest.mock import AsyncMock, MagicMock

import db
import main
import redis_client as rc


async def _complete_profile(user):
    async with db.async_session() as session:
        u = await session.get(db.User, user.id)
        u.display_name = "کاربر تست"
        u.gender = db.Gender.male
        u.age = 25
        await session.commit()


def _make_update(user_id: int, username: str = "tester"):
    update = MagicMock()
    update.effective_user.id = user_id
    update.effective_user.username = username
    update.effective_user.first_name = "تست"
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()
    return update


async def test_direct_link_blocked_sender_gets_explicit_notice_before_prompt(make_user):
    owner = await make_user()
    sender = await make_user()
    await _complete_profile(sender)
    await db.block_sender(owner.id, sender.id)

    update = _make_update(sender.id)
    context = MagicMock()
    context.user_data = {}

    try:
        await main._handle_direct_link(update, context, owner.referral_code)

        text = update.message.reply_text.await_args.args[0]
        assert "بلاک" in text
        # نباید وارد state «در حال نوشتن پیام» بشه
        assert "awaiting_note_target" not in context.user_data
    finally:
        await db.unblock_sender(owner.id, sender.id)


async def test_direct_link_not_blocked_shows_write_prompt(make_user):
    owner = await make_user()
    sender = await make_user()
    await _complete_profile(sender)

    update = _make_update(sender.id)
    context = MagicMock()
    context.user_data = {}

    await main._handle_direct_link(update, context, owner.referral_code)

    text = update.message.reply_text.await_args.args[0]
    assert "پیامت رو بنویس" in text
    assert context.user_data["awaiting_note_target"] == owner.id


async def test_direct_link_block_checked_before_active_chat_check(make_user):
    """چکِ بلاک نباید توسطِ چکِ چتِ فعال (که قبل از این اضافه شده) قایم
    بشه؛ این تست فقط سناریوی معمولِ بلاک (بدونِ چتِ فعال) رو دوباره از
    مسیرِ عمومی‌تر تایید می‌کنه تا رگرسیونِ ترتیبِ چک‌ها زودتر لو بره."""
    owner = await make_user()
    sender = await make_user()
    await _complete_profile(sender)
    await db.block_sender(owner.id, sender.id)
    assert await rc.get_partner(sender.id) is None

    update = _make_update(sender.id)
    context = MagicMock()
    context.user_data = {}

    try:
        await main._handle_direct_link(update, context, owner.referral_code)

        text = update.message.reply_text.await_args.args[0]
        assert "بلاک" in text
    finally:
        await db.unblock_sender(owner.id, sender.id)
