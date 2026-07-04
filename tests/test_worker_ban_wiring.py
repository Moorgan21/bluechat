"""تست‌های واحد برای اتصالِ worker.py._process_job به ban_enforcement:
بعد از هر قضاوتی که auto_banned=True برگردونه (چه برای گزارش‌شده، چه
گزارش‌دهنده، چه هر دو)، باید enforce_ban برای همون user_id(های) صدا
زده بشه. judge_report/judge_profile_report واقعی (که به AI واقعی وصلن)
mock می‌شن تا فقط منطقِ سیمکشیِ worker.py تست بشه."""

from unittest.mock import AsyncMock, MagicMock, patch

import worker


async def test_chat_report_guilty_and_reporter_also_guilty_both_banned():
    fake_result = {
        "verdict": "guilty",
        "reporter_id": 1,
        "reported_id": 2,
        "reported_auto_banned": True,
        "reporter_also_guilty": True,
        "reporter_also_guilty_auto_banned": True,
    }
    job = {"type": "chat_report", "report_id": 1, "session_id": 1, "reporter_id": 1, "reported_id": 2, "reason": "abuse"}

    with patch("worker.judge_report", new=AsyncMock(return_value=fake_result)), \
         patch("worker.notify_chat_verdict", new=AsyncMock()), \
         patch("worker.enforce_ban", new=AsyncMock()) as mock_enforce:
        await worker._process_job(MagicMock(), job)

    banned_ids = [call.args[1] for call in mock_enforce.await_args_list]
    assert banned_ids.count(2) == 1  # reported_id, از reported_auto_banned
    assert banned_ids.count(1) == 1  # reporter_id، از reporter_also_guilty_auto_banned


async def test_chat_report_no_ban_does_not_call_enforce_ban():
    fake_result = {"verdict": "guilty", "reporter_id": 1, "reported_id": 2, "reported_auto_banned": False}
    job = {"type": "chat_report", "report_id": 1, "session_id": 1, "reporter_id": 1, "reported_id": 2, "reason": "abuse"}

    with patch("worker.judge_report", new=AsyncMock(return_value=fake_result)), \
         patch("worker.notify_chat_verdict", new=AsyncMock()), \
         patch("worker.enforce_ban", new=AsyncMock()) as mock_enforce:
        await worker._process_job(MagicMock(), job)

    mock_enforce.assert_not_awaited()


async def test_profile_report_guilty_bans_immediately():
    fake_result = {"verdict": "guilty", "reporter_id": 1, "reported_id": 2}
    job = {"type": "profile_report", "profile_report_id": 1, "reporter_id": 1, "reported_id": 2, "snapshot": {}}

    with patch("worker.judge_profile_report", new=AsyncMock(return_value=fake_result)), \
         patch("worker.notify_profile_verdict", new=AsyncMock()), \
         patch("worker.enforce_ban", new=AsyncMock()) as mock_enforce:
        await worker._process_job(MagicMock(), job)

    mock_enforce.assert_awaited_once()
    assert mock_enforce.await_args.args[1] == 2


async def test_profile_report_dismissed_with_reporter_auto_banned():
    fake_result = {"verdict": "dismissed", "reporter_id": 1, "reported_id": 2, "auto_banned": True}
    job = {"type": "profile_report", "profile_report_id": 1, "reporter_id": 1, "reported_id": 2, "snapshot": {}}

    with patch("worker.judge_profile_report", new=AsyncMock(return_value=fake_result)), \
         patch("worker.notify_profile_verdict", new=AsyncMock()), \
         patch("worker.enforce_ban", new=AsyncMock()) as mock_enforce:
        await worker._process_job(MagicMock(), job)

    mock_enforce.assert_awaited_once()
    assert mock_enforce.await_args.args[1] == 1
