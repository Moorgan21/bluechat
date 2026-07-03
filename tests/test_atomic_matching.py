"""تست‌های واحد برای redis_client.atomic_match_or_enqueue: جایگزینِ
atomic برای الگوی قدیمیِ «pop_matching_waiting بعد enqueue + یه
sleep(0.8) برای پوشوندنِ race»، که واقعاً می‌تونست باعثِ double-match
(دو تا ChatSession جدا برای یه جفت) بشه اگه دو کاربر هم‌زمان می‌رسیدن.
این تست‌ها هم رفتارِ عادیِ matching رو چک می‌کنن، هم مستقیماً تضمینِ
atomicity رو زیرِ فراخوانیِ هم‌زمان می‌سنجن."""

import asyncio

import db
import redis_client as rc


async def _set_gender(user_id: int, gender: db.Gender) -> None:
    async with db.async_session() as session:
        u = await session.get(db.User, user_id)
        u.gender = gender
        await session.commit()


async def test_unset_gender_does_not_enter_queue(make_user):
    user = await make_user()
    entered, partner_id = await rc.atomic_match_or_enqueue(user.id, None)
    assert entered is False
    assert partner_id is None
    assert not await rc.is_waiting(user.id)


async def test_second_caller_matches_first_atomically(make_user):
    a = await make_user()
    await _set_gender(a.id, db.Gender.male)
    b = await make_user()
    await _set_gender(b.id, db.Gender.female)

    entered_a, partner_a = await rc.atomic_match_or_enqueue(a.id, None)
    assert entered_a is True
    assert partner_a is None
    assert await rc.is_waiting(a.id)

    entered_b, partner_b = await rc.atomic_match_or_enqueue(b.id, None)
    assert entered_b is True
    assert partner_b == a.id
    assert not await rc.is_waiting(a.id)
    assert not await rc.is_waiting(b.id)


async def test_incompatible_desired_gender_stays_queued(make_user):
    """A فقط دنبالِ یه partnerِ مرده؛ وقتی B (که خودش زنه و دنبالِ
    هرکسیه) می‌رسه، نباید با A match بشه چون A اصلاً دنبالِ زن نیست."""
    a = await make_user()
    await _set_gender(a.id, db.Gender.male)
    entered_a, partner_a = await rc.atomic_match_or_enqueue(a.id, "male")
    assert entered_a is True
    assert partner_a is None

    b = await make_user()
    await _set_gender(b.id, db.Gender.female)
    entered_b, partner_b = await rc.atomic_match_or_enqueue(b.id, None)
    assert entered_b is True
    assert partner_b is None
    assert await rc.is_waiting(a.id)
    assert await rc.is_waiting(b.id)


async def test_compatible_desired_gender_matches(make_user):
    """A (مرد) فقط دنبالِ یه partnerِ زنه؛ وقتی B (زن، دنبالِ هرکسی)
    می‌رسه، باید بلافاصله با A match بشه."""
    a = await make_user()
    await _set_gender(a.id, db.Gender.male)
    await rc.atomic_match_or_enqueue(a.id, "female")

    b = await make_user()
    await _set_gender(b.id, db.Gender.female)
    entered_b, partner_b = await rc.atomic_match_or_enqueue(b.id, None)
    assert entered_b is True
    assert partner_b == a.id


async def test_concurrent_calls_never_double_claim_a_partner(make_user):
    """قبلاً بینِ چکِ اول و enqueue یه فاصله‌ی TOCTOU بود که تئوریاً
    می‌تونست باعثِ claim‌شدنِ یه partner توسطِ دو نفرِ مختلف بشه. این
    تست چندین کاربر رو با asyncio.gather کاملاً هم‌زمان صدا می‌زنه و
    مطمئن می‌شه هیچ partner_id ای دوبار توی نتیجه‌ها ظاهر نمی‌شه."""
    users = []
    for i in range(10):
        u = await make_user()
        await _set_gender(u.id, db.Gender.male if i % 2 == 0 else db.Gender.female)
        users.append(u)

    results = await asyncio.gather(*[rc.atomic_match_or_enqueue(u.id, None) for u in users])

    matched_partner_ids = [partner_id for _, partner_id in results if partner_id is not None]
    assert len(matched_partner_ids) == len(set(matched_partner_ids))

    valid_ids = {u.id for u in users}
    assert all(pid in valid_ids for pid in matched_partner_ids)

    for u, (entered, partner_id) in zip(users, results):
        assert entered is True
        assert partner_id != u.id
