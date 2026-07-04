"""تست‌واحد برای یه باگِ ظریفِ python-telegram-bot: MessageFilter.check_update
پارامترش رو از update.effective_message می‌سازه که برای edited_message هم
truthy‌ه. اگه هندلرهای پیامِ معمولی (text_router/media_router) با
filters.TEXT بدونِ قیدِ UpdateType.MESSAGE رجیستر بشن، همون‌ها (که قبل از
edited_message_router رجیستر شدن) خودِ آپدیتِ ادیت رو می‌قاپن و
edited_message_router هیچ‌وقت اجرا نمی‌شه — دقیقاً همون علتِ اینکه ادیتِ
پیام تو چتِ ۱به۱ و اتاق کار نمی‌کرد. این تست قفلِ فیکس (main.py) رو
می‌زنه تا این رگرسیون برنگرده."""

import datetime

import telegram.ext.filters as filters
from telegram import Chat, Message, Update, User


def _make_message_update(edited: bool) -> Update:
    chat = Chat(id=1, type="private")
    user = User(id=1, is_bot=False, first_name="a")
    msg = Message(message_id=5, date=datetime.datetime.now(), chat=chat, text="hi", from_user=user)
    kwargs = {"edited_message": msg} if edited else {"message": msg}
    return Update(update_id=1, **kwargs)


def test_new_message_filter_excludes_edited_updates():
    """فیکس: main.py دیگه باید filters.UpdateType.MESSAGE رو هم شرط کنه."""
    new_message_filter = filters.UpdateType.MESSAGE & filters.TEXT & ~filters.COMMAND

    assert new_message_filter.check_update(_make_message_update(edited=False))
    assert not new_message_filter.check_update(_make_message_update(edited=True))


def test_plain_text_filter_without_update_type_would_have_leaked_edits():
    """رگرسیونِ خودِ باگ: filters.TEXT به‌تنهایی (بدونِ UpdateType.MESSAGE)
    آپدیتِ edited_message رو هم قبول می‌کنه؛ برای همینه که ترتیبِ
    رجیستریشن قبلاً باعثِ قاپیده‌شدنِ ادیت‌ها توسطِ text_router می‌شد."""
    old_buggy_filter = filters.TEXT & ~filters.COMMAND
    assert old_buggy_filter.check_update(_make_message_update(edited=True))


def test_edited_message_filter_only_matches_edits():
    edited_filter = filters.UpdateType.EDITED_MESSAGE & filters.TEXT

    assert edited_filter.check_update(_make_message_update(edited=True))
    assert not edited_filter.check_update(_make_message_update(edited=False))
