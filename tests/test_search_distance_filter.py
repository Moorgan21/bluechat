"""تست‌های واحد برای فیلترِ فاصله در «جستجوی کاربران» (همون گزینه‌های
شعاعیِ ۵/۱۰/۲۰/۵۰ کیلومتر و «نزدیک‌ترین آدم ممکن» که تو «افراد نزدیک»
هست، حالا به‌عنوانِ یه فیلترِ matching هم اضافه شده): بدونِ موقعیتِ
مکانیِ ثبت‌شده قابلِ‌انتخاب نیست، و matching باید واقعاً بر اساسِ فاصله
انجام بشه."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import db
import redis_client as rc
from db import async_session, make_point
from handlers import search

KM_PER_DEGREE_LAT = 111.32
BASE_LAT, BASE_LON = 35.7, 51.4


def _lat_offset_for_km(km: float) -> float:
    return km / KM_PER_DEGREE_LAT


async def _complete_profile(user, gender=db.Gender.male, age=25, km_away: float | None = None):
    async with async_session() as session:
        u = await session.get(db.User, user.id)
        u.display_name = "کاربر تست"
        u.gender = gender
        u.age = age
        if km_away is not None:
            u.location = make_point(BASE_LAT + _lat_offset_for_km(km_away), BASE_LON)
            u.location_updated_at = datetime.utcnow()
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
    update = MagicMock()
    update.callback_query = query
    return update


async def test_filter_distance_without_location_shows_warning(make_user):
    user = await make_user()
    update = _make_callback_update(user.id, "search:filter_distance")
    context = _make_context()

    await search.search_callback_router(update, context)

    text = update.callback_query.edit_message_text.await_args.args[0]
    assert "موقعیتِ مکانی نیاز داره" in text


async def test_filter_distance_with_location_shows_distance_keyboard(make_user):
    user = await make_user()
    await _complete_profile(user, km_away=0)
    update = _make_callback_update(user.id, "search:filter_distance")
    context = _make_context()

    await search.search_callback_router(update, context)

    text = update.callback_query.edit_message_text.await_args.args[0]
    assert "محدوده‌ی فاصله" in text


async def test_selecting_distance_sets_filter(make_user):
    user = await make_user()
    update = _make_callback_update(user.id, "searchdist:10")
    context = _make_context()

    await search.search_distance_callback_router(update, context)

    assert context.user_data[search.FILTERS_KEY]["distance"] == 10


async def test_selecting_closest_sets_filter(make_user):
    user = await make_user()
    update = _make_callback_update(user.id, "searchdist:closest")
    context = _make_context()

    await search.search_distance_callback_router(update, context)

    assert context.user_data[search.FILTERS_KEY]["distance"] == "closest"


async def test_clearing_distance_filter(make_user):
    user = await make_user()
    context = _make_context()
    context.user_data[search.FILTERS_KEY] = {"distance": 20}

    update = _make_callback_update(user.id, "searchdist:none")
    await search.search_distance_callback_router(update, context)

    assert "distance" not in context.user_data[search.FILTERS_KEY]


async def test_run_search_matches_only_within_selected_distance(make_user):
    searcher = await make_user(coins=10)
    await _complete_profile(searcher, gender=db.Gender.female, km_away=0)

    near = await make_user(coins=10)
    await _complete_profile(near, km_away=3)
    far = await make_user(coins=10)
    await _complete_profile(far, km_away=30)

    await rc.enqueue(near.id)
    await rc.enqueue(far.id)

    update = _make_callback_update(searcher.id, "search:go")
    context = _make_context()
    context.user_data[search.FILTERS_KEY] = {"distance": 5}

    try:
        await search.run_search(update, context)

        assert await rc.get_partner(searcher.id) == near.id
        assert await rc.is_waiting(far.id) is True
    finally:
        await rc.clear_partner(searcher.id)
        await rc.dequeue(far.id)


async def test_run_search_closest_ignores_radius(make_user):
    searcher = await make_user(coins=10)
    await _complete_profile(searcher, gender=db.Gender.female, km_away=0)

    close_candidate = await make_user(coins=10)
    await _complete_profile(close_candidate, km_away=8)
    far_candidate = await make_user(coins=10)
    await _complete_profile(far_candidate, km_away=900)

    await rc.enqueue(close_candidate.id)
    await rc.enqueue(far_candidate.id)

    update = _make_callback_update(searcher.id, "search:go")
    context = _make_context()
    context.user_data[search.FILTERS_KEY] = {"distance": "closest"}

    try:
        await search.run_search(update, context)

        assert await rc.get_partner(searcher.id) == close_candidate.id
        assert await rc.is_waiting(far_candidate.id) is True
    finally:
        await rc.clear_partner(searcher.id)
        await rc.dequeue(far_candidate.id)


async def test_run_search_closest_finds_someone_beyond_50km(make_user):
    """«نزدیک‌ترین آدم ممکن» باید حتی اگه تنها کاندیدا خیلی دور باشه،
    بدونِ محدودیتِ شعاع matching کنه."""
    searcher = await make_user(coins=10)
    await _complete_profile(searcher, gender=db.Gender.female, km_away=0)

    only_candidate = await make_user(coins=10)
    await _complete_profile(only_candidate, km_away=900)
    await rc.enqueue(only_candidate.id)

    update = _make_callback_update(searcher.id, "search:go")
    context = _make_context()
    context.user_data[search.FILTERS_KEY] = {"distance": "closest"}

    try:
        await search.run_search(update, context)

        assert await rc.get_partner(searcher.id) == only_candidate.id
    finally:
        await rc.clear_partner(searcher.id)
