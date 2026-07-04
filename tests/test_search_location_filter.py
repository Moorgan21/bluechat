"""تست‌های واحد برای فیلترِ استان/شهر در «جستجوی کاربران»: انتخابِ استان
بدونِ شهر مشکلی نیست (فقط بر اساسِ استان جستجو می‌شه)، ولی شهر بدونِ
استان قابلِ انتخاب نیست و باید صراحتاً گفته بشه اول استان رو انتخاب کن."""

from unittest.mock import AsyncMock, MagicMock

import db
import redis_client as rc
from handlers import search


async def _complete_profile(user, gender=db.Gender.male, age=25, province=None, city=None):
    async with db.async_session() as session:
        u = await session.get(db.User, user.id)
        u.display_name = "کاربر تست"
        u.gender = gender
        u.age = age
        u.province = province
        u.city = city
        await session.commit()


def _make_context():
    context = MagicMock()
    context.user_data = {}
    context.bot = MagicMock()
    context.bot.send_message = AsyncMock()
    return context


def _make_callback_update(user_id: int, data: str):
    query = MagicMock()
    query.data = data
    query.from_user.id = user_id
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.edit_message_reply_markup = AsyncMock()
    update = MagicMock()
    update.callback_query = query
    return update


# --- دکمه‌های فیلتر (search_callback_router) ---

async def test_filter_city_without_province_shows_warning_not_city_keyboard():
    update = _make_callback_update(1, "search:filter_city")
    context = _make_context()

    await search.search_callback_router(update, context)

    text = update.callback_query.edit_message_text.await_args.args[0]
    assert "اول باید یه استان انتخاب کنی" in text
    keyboard = update.callback_query.edit_message_text.await_args.kwargs["reply_markup"]
    button_texts = [b.text for row in keyboard.inline_keyboard for b in row]
    assert "🎂 فیلتر بر اساس سن" in button_texts  # همون منوی اصلیِ جستجو، نه کیبوردِ شهر


async def test_filter_city_with_province_shows_city_keyboard():
    update = _make_callback_update(1, "search:filter_city")
    context = _make_context()
    context.user_data[search.FILTERS_KEY] = {"province": "تهران"}

    await search.search_callback_router(update, context)

    text = update.callback_query.edit_message_text.await_args.args[0]
    assert "شهر مدنظرت" in text


async def test_filter_province_shows_province_keyboard():
    update = _make_callback_update(1, "search:filter_province")
    context = _make_context()

    await search.search_callback_router(update, context)

    text = update.callback_query.edit_message_text.await_args.args[0]
    assert "استان مدنظرت" in text


# --- ست‌کردنِ فیلتر (search_province_callback_router / search_city_callback_router) ---

async def test_selecting_province_sets_filter(make_user):
    user = await make_user()
    update = _make_callback_update(user.id, "searchprov:تهران")
    context = _make_context()

    await search.search_province_callback_router(update, context)

    assert context.user_data[search.FILTERS_KEY]["province"] == "تهران"


async def test_selecting_new_province_clears_previous_city(make_user):
    user = await make_user()
    context = _make_context()
    context.user_data[search.FILTERS_KEY] = {"province": "تهران", "city": "تهران"}

    update = _make_callback_update(user.id, "searchprov:اصفهان")
    await search.search_province_callback_router(update, context)

    filters_ = context.user_data[search.FILTERS_KEY]
    assert filters_["province"] == "اصفهان"
    assert "city" not in filters_


async def test_clearing_province_also_clears_city(make_user):
    user = await make_user()
    context = _make_context()
    context.user_data[search.FILTERS_KEY] = {"province": "تهران", "city": "تهران"}

    update = _make_callback_update(user.id, "searchprov:none")
    await search.search_province_callback_router(update, context)

    filters_ = context.user_data[search.FILTERS_KEY]
    assert "province" not in filters_
    assert "city" not in filters_


async def test_selecting_city_sets_filter(make_user):
    user = await make_user()
    context = _make_context()
    context.user_data[search.FILTERS_KEY] = {"province": "تهران"}

    update = _make_callback_update(user.id, "searchcity:تهران")
    await search.search_city_callback_router(update, context)

    assert context.user_data[search.FILTERS_KEY]["city"] == "تهران"
    assert context.user_data[search.FILTERS_KEY]["province"] == "تهران"  # دست‌نخورده


# --- run_search با فیلترِ مکانی ---

async def test_run_search_matches_only_within_selected_province(make_user):
    searcher = await make_user(coins=10)
    await _complete_profile(searcher, gender=db.Gender.female)

    tehran_candidate = await make_user(coins=10)
    await _complete_profile(tehran_candidate, gender=db.Gender.male, province="تهران", city="تهران")
    isfahan_candidate = await make_user(coins=10)
    await _complete_profile(isfahan_candidate, gender=db.Gender.male, province="اصفهان", city="اصفهان")

    await rc.enqueue(tehran_candidate.id)
    await rc.enqueue(isfahan_candidate.id)

    update = _make_callback_update(searcher.id, "search:go")
    context = _make_context()
    context.user_data[search.FILTERS_KEY] = {"province": "تهران"}

    try:
        await search.run_search(update, context)

        assert await rc.get_partner(searcher.id) == tehran_candidate.id
        assert await rc.get_partner(tehran_candidate.id) == searcher.id
        assert await rc.is_waiting(isfahan_candidate.id) is True  # دست‌نخورده موند
    finally:
        await rc.clear_partner(searcher.id)
        await rc.dequeue(isfahan_candidate.id)


async def test_run_search_with_province_only_ignores_city(make_user):
    """انتخابِ استان بدونِ شهر مشکلی نیست: جستجو باید بر اساسِ فقط
    استان انجام بشه و کاندیدای هر شهری از همون استان رو پیدا کنه."""
    searcher = await make_user(coins=10)
    await _complete_profile(searcher, gender=db.Gender.female)

    candidate = await make_user(coins=10)
    await _complete_profile(candidate, gender=db.Gender.male, province="تهران", city="شمیرانات")
    await rc.enqueue(candidate.id)

    update = _make_callback_update(searcher.id, "search:go")
    context = _make_context()
    context.user_data[search.FILTERS_KEY] = {"province": "تهران"}  # بدونِ فیلترِ شهر

    try:
        await search.run_search(update, context)

        assert await rc.get_partner(searcher.id) == candidate.id
    finally:
        await rc.clear_partner(searcher.id)


async def test_run_search_with_province_and_city_requires_exact_city(make_user):
    searcher = await make_user(coins=10)
    await _complete_profile(searcher, gender=db.Gender.female)

    same_province_other_city = await make_user(coins=10)
    await _complete_profile(same_province_other_city, gender=db.Gender.male, province="تهران", city="شمیرانات")
    exact_match = await make_user(coins=10)
    await _complete_profile(exact_match, gender=db.Gender.male, province="تهران", city="تهران")

    await rc.enqueue(same_province_other_city.id)
    await rc.enqueue(exact_match.id)

    update = _make_callback_update(searcher.id, "search:go")
    context = _make_context()
    context.user_data[search.FILTERS_KEY] = {"province": "تهران", "city": "تهران"}

    try:
        await search.run_search(update, context)

        assert await rc.get_partner(searcher.id) == exact_match.id
        assert await rc.is_waiting(same_province_other_city.id) is True
    finally:
        await rc.clear_partner(searcher.id)
        await rc.dequeue(same_province_other_city.id)
