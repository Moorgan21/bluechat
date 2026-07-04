"""تست‌های واحد برای این باگ: وقتی درخواست‌کننده‌ی چت عکسِ پروفایل داشت،
پیامِ نمایشِ پروفایل (با دکمه‌های قبول/رد) به‌صورتِ عکس با caption
فرستاده می‌شد (handle_view_chat_request)، ولی handle_chat_request_accept/
reject همیشه edit_message_text صدا می‌زدن که روی پیامِ عکس‌دار با
TelegramError fail می‌کنه؛ چون catch نمی‌شد، کلِ هندلر همون‌جا می‌ترکید
و نه اطلاع‌رسانی به دو طرف انجام می‌شد نه (در ردِ درخواست) بازگشتِ سکه."""

from unittest.mock import AsyncMock, MagicMock

import db
import redis_client as rc
from handlers import public_profile


def _make_photo_query_update(request_id: str, callback_data_prefix: str, from_user_id: int):
    query = MagicMock()
    query.data = f"{callback_data_prefix}:{request_id}"
    query.from_user.id = from_user_id
    query.answer = AsyncMock()
    query.message.photo = [MagicMock()]  # پیامِ عکس‌دار، مثلِ خروجیِ send_photo
    query.edit_message_text = AsyncMock()
    query.edit_message_caption = AsyncMock()
    update = MagicMock()
    update.callback_query = query
    return update


async def test_accept_on_photo_message_uses_caption_and_still_notifies_both_sides(make_user):
    acceptor = await make_user(coins=10)
    requester = await make_user(coins=10)
    request_id = await rc.create_chat_request(requester.id, acceptor.id)

    update = _make_photo_query_update(request_id, "chatreqaccept", acceptor.id)
    context = MagicMock()
    context.bot.send_message = AsyncMock()

    await public_profile.handle_chat_request_accept(update, context)

    # نباید edit_message_text صدا زده بشه (پیام عکس‌داره)، باید caption ادیت بشه
    update.callback_query.edit_message_text.assert_not_awaited()
    update.callback_query.edit_message_caption.assert_awaited_once()
    assert "قبول شد" in update.callback_query.edit_message_caption.await_args.kwargs["caption"]

    # منطقِ اصلی (که قبلاً به‌خاطرِ کرشِ زودهنگام هیچ‌وقت اجرا نمی‌شد) باید کامل انجام بشه
    assert await rc.get_partner(acceptor.id) == requester.id
    assert await rc.get_partner(requester.id) == acceptor.id
    assert context.bot.send_message.await_count == 2
    notified_ids = {c.args[0] for c in context.bot.send_message.await_args_list}
    assert notified_ids == {acceptor.id, requester.id}

    await rc.clear_partner(acceptor.id)


async def test_reject_on_photo_message_uses_caption_and_still_refunds(make_user):
    rejector = await make_user(coins=10)
    requester = await make_user(coins=8)
    request_id = await rc.create_chat_request(requester.id, rejector.id)

    update = _make_photo_query_update(request_id, "chatreqreject", rejector.id)
    context = MagicMock()
    context.bot.send_message = AsyncMock()

    await public_profile.handle_chat_request_reject(update, context)

    update.callback_query.edit_message_text.assert_not_awaited()
    update.callback_query.edit_message_caption.assert_awaited_once()
    assert "رد شد" in update.callback_query.edit_message_caption.await_args.kwargs["caption"]

    # قبلاً هیچ‌وقت به اینجا نمی‌رسید: بازگشتِ سکه + اطلاع به درخواست‌کننده
    async with db.async_session() as session:
        refreshed = await session.get(db.User, requester.id)
        assert refreshed.coins == 8 + rc.CHAT_COIN_COST

    context.bot.send_message.assert_awaited_once()
    assert context.bot.send_message.await_args.args[0] == requester.id


async def test_accept_on_text_message_still_uses_edit_message_text(make_user):
    acceptor = await make_user(coins=10)
    requester = await make_user(coins=10)
    request_id = await rc.create_chat_request(requester.id, acceptor.id)

    query = MagicMock()
    query.data = f"chatreqaccept:{request_id}"
    query.from_user.id = acceptor.id
    query.answer = AsyncMock()
    query.message.photo = None
    query.edit_message_text = AsyncMock()
    query.edit_message_caption = AsyncMock()
    update = MagicMock()
    update.callback_query = query
    context = MagicMock()
    context.bot.send_message = AsyncMock()

    await public_profile.handle_chat_request_accept(update, context)

    query.edit_message_caption.assert_not_awaited()
    query.edit_message_text.assert_awaited_once()

    await rc.clear_partner(acceptor.id)
