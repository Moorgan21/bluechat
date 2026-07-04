"""تست‌های واحد برای چرخه‌ی عمرِ درخواستِ چت در redis_client.py:
ساخت/خواندن، پاک‌سازی، سازگاری با فرمتِ قدیمی، و انقضای خودکار."""

import time

import redis_client as rc


async def test_create_and_get_chat_request_roundtrip():
    request_id = await rc.create_chat_request(requester_id=111, target_id=222)

    data = await rc.get_chat_request(request_id)

    assert data == {"requester_id": 111, "target_id": 222}


async def test_get_chat_request_returns_none_for_unknown_id():
    assert await rc.get_chat_request("does-not-exist") is None


async def test_clear_chat_request_removes_from_storage_and_pending_set():
    request_id = await rc.create_chat_request(requester_id=111, target_id=222)

    await rc.clear_chat_request(request_id)

    assert await rc.get_chat_request(request_id) is None
    score = await rc.r.zscore(rc.KEY_CHAT_REQUEST_PENDING, request_id)
    assert score is None


async def test_legacy_plain_int_payload_is_treated_as_expired_not_crash():
    """کلیدهای فرمتِ قدیمی (قبل از افزودنِ target_id) نباید کرش کنن."""
    legacy_id = "legacy01"
    await rc.r.set(rc.KEY_CHAT_REQUEST.format(request_id=legacy_id), 8598375148, ex=60)

    assert await rc.get_chat_request(legacy_id) is None


async def test_pop_expired_chat_requests_only_returns_entries_past_timeout():
    fresh_id = await rc.create_chat_request(requester_id=1, target_id=2)
    stale_id = await rc.create_chat_request(requester_id=3, target_id=4)

    # fresh_id همین الان ساخته شده (تازه‌ست)؛ stale_id رو به عمد قدیمی می‌کنیم.
    await rc.r.zadd(rc.KEY_CHAT_REQUEST_PENDING, {stale_id: time.time() - 200})

    expired = await rc.pop_expired_chat_requests(timeout_seconds=120)

    expired_ids = {item["request_id"] for item in expired}
    assert stale_id in expired_ids
    assert fresh_id not in expired_ids

    # منقضی‌شده پاک شده باشه، تازه دست‌نخورده بمونه
    assert await rc.get_chat_request(stale_id) is None
    assert await rc.get_chat_request(fresh_id) is not None


async def test_pop_expired_chat_requests_returns_correct_requester_and_target():
    stale_id = await rc.create_chat_request(requester_id=555, target_id=666)
    await rc.r.zadd(rc.KEY_CHAT_REQUEST_PENDING, {stale_id: time.time() - 200})

    expired = await rc.pop_expired_chat_requests(timeout_seconds=120)

    match = next(item for item in expired if item["request_id"] == stale_id)
    assert match["requester_id"] == 555
    assert match["target_id"] == 666


async def test_already_resolved_request_is_not_double_refunded_by_timeout_job():
    """شبیه‌سازیِ race condition: درخواست قبل از رسیدنِ jobِ انقضا با
    accept/reject پاک شده (clear_chat_request صدا زده شده)؛ jobِ انقضا
    نباید دوباره سکه برگردونه چون دیگه داده‌ای براش نیست."""
    request_id = await rc.create_chat_request(requester_id=777, target_id=888)
    await rc.clear_chat_request(request_id)

    # اگه به‌هردلیلی هنوز توی صفِ pending مونده باشه (نباید بمونه، ولی
    # این تست همون safety net رو می‌سنجه):
    await rc.r.zadd(rc.KEY_CHAT_REQUEST_PENDING, {request_id: time.time() - 200})

    expired = await rc.pop_expired_chat_requests(timeout_seconds=120)

    assert all(item["request_id"] != request_id for item in expired)


async def test_chat_request_ttl_is_set():
    request_id = await rc.create_chat_request(requester_id=1, target_id=2)

    ttl = await rc.r.ttl(rc.KEY_CHAT_REQUEST.format(request_id=request_id))

    assert 0 < ttl <= rc.TTL_CHAT_REQUEST


async def test_count_active_chat_requests_tracks_creation_and_clear():
    assert await rc.count_active_chat_requests(999) == 0

    id_a = await rc.create_chat_request(requester_id=999, target_id=1)
    id_b = await rc.create_chat_request(requester_id=999, target_id=2)
    assert await rc.count_active_chat_requests(999) == 2

    await rc.clear_chat_request(id_a)
    assert await rc.count_active_chat_requests(999) == 1

    await rc.clear_chat_request(id_b)
    assert await rc.count_active_chat_requests(999) == 0


async def test_count_active_chat_requests_decreases_on_expiry():
    request_id = await rc.create_chat_request(requester_id=888, target_id=1)
    await rc.r.zadd(rc.KEY_CHAT_REQUEST_PENDING, {request_id: time.time() - 400})

    assert await rc.count_active_chat_requests(888) == 1
    await rc.pop_expired_chat_requests(timeout_seconds=300)
    assert await rc.count_active_chat_requests(888) == 0


async def test_count_active_chat_requests_only_counts_that_requester():
    await rc.create_chat_request(requester_id=1, target_id=2)
    await rc.create_chat_request(requester_id=2, target_id=1)

    assert await rc.count_active_chat_requests(1) == 1
    assert await rc.count_active_chat_requests(2) == 1
