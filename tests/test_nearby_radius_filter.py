"""تست‌های واحد برای فیلترهای شعاعیِ «افراد نزدیک» (۵/۱۰/۲۰/۵۰ کیلومتر
و «نزدیک‌ترین آدم ممکن»). فاصله‌ها با جابه‌جاییِ خالصِ عرضِ جغرافیایی
(بدونِ تغییرِ طول) ساخته می‌شن، چون در این حالت فاصله‌ی هر درجه تقریباً
همیشه ۱۱۱.۳۲ کیلومتره، صرف‌نظر از عرضِ جغرافیاییِ پایه."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import db
from db import async_session, make_point
from handlers import nearby

KM_PER_DEGREE_LAT = 111.32
BASE_LAT, BASE_LON = 35.7, 51.4


def _lat_offset_for_km(km: float) -> float:
    return km / KM_PER_DEGREE_LAT


async def _set_location(user_id: int, km_away: float, display_name: str) -> None:
    async with async_session() as session:
        u = await session.get(db.User, user_id)
        u.display_name = display_name
        u.location = make_point(BASE_LAT + _lat_offset_for_km(km_away), BASE_LON)
        u.location_updated_at = datetime.utcnow()
        await session.commit()


def _make_update(user_id: int):
    query = MagicMock()
    query.from_user.id = user_id
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    update = MagicMock()
    update.callback_query = query
    update.effective_user.id = user_id
    return update


async def test_radius_filter_excludes_candidates_outside_selected_km(make_user):
    me = await make_user()
    await _set_location(me.id, 0, "من")

    near = await make_user()
    await _set_location(near.id, 3, "نزدیک")
    far = await make_user()
    await _set_location(far.id, 15, "دور")

    update = _make_update(me.id)
    await nearby.show_nearby_users(update, MagicMock(), radius_km=5)

    text = update.callback_query.edit_message_text.await_args.args[0]
    assert "نزدیک" in text
    assert "دور" not in text


async def test_radius_filter_20km_includes_15km_candidate(make_user):
    me = await make_user()
    await _set_location(me.id, 0, "من")

    candidate = await make_user()
    await _set_location(candidate.id, 15, "کاندیدا")

    update = _make_update(me.id)
    await nearby.show_nearby_users(update, MagicMock(), radius_km=20)

    text = update.callback_query.edit_message_text.await_args.args[0]
    assert "کاندیدا" in text


async def test_radius_filter_no_one_in_range_shows_explicit_message(make_user):
    me = await make_user()
    await _set_location(me.id, 0, "من")

    far = await make_user()
    await _set_location(far.id, 35, "خیلی دور")

    update = _make_update(me.id)
    await nearby.show_nearby_users(update, MagicMock(), radius_km=5)

    text = update.callback_query.edit_message_text.await_args.args[0]
    assert "۵ کیلومتری‌ت پیدا نشد" in text


async def test_closest_possible_ignores_radius_and_returns_single_nearest(make_user):
    me = await make_user()
    await _set_location(me.id, 0, "من")

    close = await make_user()
    await _set_location(close.id, 8, "نزدیک‌تر")
    far = await make_user()
    await _set_location(far.id, 900, "خیلی دورترِ")

    update = _make_update(me.id)
    await nearby.show_nearby_users(update, MagicMock(), radius_km=None)

    text = update.callback_query.edit_message_text.await_args.args[0]
    assert "نزدیک‌تر" in text
    assert "خیلی دورترِ" not in text
    assert "نزدیک‌ترین کاربرِ ممکن" in text


async def test_closest_possible_finds_someone_even_far_beyond_50km(make_user):
    me = await make_user()
    await _set_location(me.id, 0, "من")

    only_candidate = await make_user()
    await _set_location(only_candidate.id, 900, "دوردست")

    update = _make_update(me.id)
    await nearby.show_nearby_users(update, MagicMock(), radius_km=None)

    text = update.callback_query.edit_message_text.await_args.args[0]
    assert "دوردست" in text


async def test_nearby_callback_router_parses_radius_and_closest(make_user):
    import main

    me = await make_user()
    await _set_location(me.id, 0, "من")
    candidate = await make_user()
    await _set_location(candidate.id, 3, "کاندیدا")

    update = _make_update(me.id)
    context = MagicMock()

    await main.nearby_callback_router(update, context, "nearby:radius:10")
    text = update.callback_query.edit_message_text.await_args.args[0]
    assert "کاندیدا" in text

    update2 = _make_update(me.id)
    await main.nearby_callback_router(update2, context, "nearby:radius:closest")
    text2 = update2.callback_query.edit_message_text.await_args.args[0]
    assert "نزدیک‌ترین کاربرِ ممکن" in text2
