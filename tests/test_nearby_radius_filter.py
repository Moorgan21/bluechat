"""تست‌های واحد برای فیلترهای شعاعیِ «افراد نزدیک» (۵/۱۰/۲۰/۵۰ کیلومتر)،
نمایشِ آیدیِ پروفایلِ عمومی و زمانِ آنلاینی، مرتب‌سازی بر اساسِ
زودترین آنلاینی، سقفِ ۵۰ نفر در هر شعاع، و صفحه‌بندیِ ۲۰تایی.

فاصله‌ها با جابه‌جاییِ خالصِ عرضِ جغرافیایی (بدونِ تغییرِ طول) ساخته
می‌شن، چون در این حالت فاصله‌ی هر درجه تقریباً همیشه ۱۱۱.۳۲ کیلومتره،
صرف‌نظر از عرضِ جغرافیاییِ پایه."""

import time
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import db
import redis_client as rc
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


async def test_result_shows_profile_id_and_online_time(make_user):
    me = await make_user()
    await _set_location(me.id, 0, "من")

    candidate = await make_user()
    await _set_location(candidate.id, 3, "کاندیدا")
    await rc.update_last_seen(candidate.id)

    update = _make_update(me.id)
    await nearby.show_nearby_users(update, MagicMock(), radius_km=10)

    text = update.callback_query.edit_message_text.await_args.args[0]
    assert f"/user_{candidate.referral_code}" in text
    assert "🟢 آنلاین" in text


async def test_results_sorted_by_most_recently_online_first(make_user):
    me = await make_user()
    await _set_location(me.id, 0, "من")

    # candB فیزیکاً نزدیک‌تره ولی candA اخیراً آنلاین بوده، پس candA باید اول بیاد
    cand_a = await make_user()
    await _set_location(cand_a.id, 8, "کاندالف")
    cand_b = await make_user()
    await _set_location(cand_b.id, 2, "کاندبی")

    await rc.update_last_seen(cand_a.id)
    await rc.r.set(rc.KEY_LAST_SEEN.format(user_id=cand_b.id), time.time() - 10_000)

    update = _make_update(me.id)
    await nearby.show_nearby_users(update, MagicMock(), radius_km=10)

    text = update.callback_query.edit_message_text.await_args.args[0]
    assert text.index("کاندالف") < text.index("کاندبی")


async def test_results_capped_at_50_and_paginated_20_per_page(make_user):
    me = await make_user()
    await _set_location(me.id, 0, "من")

    for i in range(55):
        candidate = await make_user()
        await _set_location(candidate.id, 1 + i * 0.05, f"کاربر{i}")
        await rc.update_last_seen(candidate.id)

    update = _make_update(me.id)
    await nearby.show_nearby_users(update, MagicMock(), radius_km=50, page=0)

    text = update.callback_query.edit_message_text.await_args.args[0]
    assert "۵۰ نفر" in text  # سقفِ نمایش‌داده‌شده، نه ۵۵ی واقعی
    keyboard = update.callback_query.edit_message_text.await_args.kwargs["reply_markup"]
    nav_texts = [b.text for row in keyboard.inline_keyboard for b in row]
    assert "بعدی ←" in nav_texts
    assert "→ قبلی" not in nav_texts  # صفحه‌ی اول، دکمه‌ی قبلی نداره

    # صفحه‌ی دوم (آخرین ۱۰ نفر از ۵۰تا)
    update_p2 = _make_update(me.id)
    await nearby.show_nearby_users(update_p2, MagicMock(), radius_km=50, page=2)
    keyboard_p2 = update_p2.callback_query.edit_message_text.await_args.kwargs["reply_markup"]
    nav_texts_p2 = [b.text for row in keyboard_p2.inline_keyboard for b in row]
    assert "→ قبلی" in nav_texts_p2
    assert "بعدی ←" not in nav_texts_p2  # صفحه‌ی آخر (۵۰ = ۳ صفحه‌ی ۲۰تایی)


async def test_nearby_callback_router_parses_radius(make_user):
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


async def test_nearby_callback_router_page_action(make_user):
    import main

    me = await make_user()
    await _set_location(me.id, 0, "من")
    for i in range(25):
        candidate = await make_user()
        await _set_location(candidate.id, 1 + i * 0.1, f"کاربر{i}")

    update = _make_update(me.id)
    context = MagicMock()

    await main.nearby_callback_router(update, context, "nearby:page:50:1")
    keyboard = update.callback_query.edit_message_text.await_args.kwargs["reply_markup"]
    nav_texts = [b.text for row in keyboard.inline_keyboard for b in row]
    assert "→ قبلی" in nav_texts


async def test_old_closest_button_falls_back_to_menu(make_user):
    """دکمه‌ی حذف‌شده‌ی «نزدیک‌ترین آدم ممکن» اگه از یه پیامِ قدیمیِ
    باقی‌مونده کلیک بشه، نباید کرش کنه؛ باید برگرده به منوی افرادِ نزدیک."""
    import main

    me = await make_user()
    update = _make_update(me.id)
    context = MagicMock()

    await main.nearby_callback_router(update, context, "nearby:radius:closest")

    text = update.callback_query.edit_message_text.await_args.args[0]
    assert "افراد نزدیک" in text
