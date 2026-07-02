"""
تنظیماتِ مشترکِ تست‌ها
----------------------
نکته‌ی حیاتی: db.py و redis_client.py موقعِ import، اتصال به
DATABASE_URL/REDIS_URL رو از env می‌سازن. برای اینکه تست‌ها هرگز به‌طور
اتفاقی دیتای واقعیِ پروداکشن رو دستکاری نکنن، این فایل قبل از هر import
دیگه‌ای بررسی می‌کنه که env روی یه دیتابیس/Redis کاملاً ایزوله‌شده‌ی
تستی تنظیم شده باشه؛ در غیرِ این‌صورت کلِ session تست بلافاصله fail
می‌شه. برای اجرا:

    docker compose exec -T bot sh -c \
      "pip install -q -r requirements-dev.txt && \
       DATABASE_URL=postgresql+asyncpg://bluechat:bluechat@postgres:5432/bluechat_test \
       REDIS_URL=redis://redis:6379/15 \
       python -m pytest tests/ -v"

user_idِ کاربرهای تستی همیشه از TEST_USER_ID_BASE به بالا انتخاب می‌شه
(خارج از بازه‌ی واقعیِ آی‌دیِ کاربرهای تلگرام) تا هرگز با کاربرِ واقعی
برخورد نکنه، و بعد از هر تست پاک‌سازی می‌شه.
"""

import os

if "_test" not in os.environ.get("DATABASE_URL", ""):
    raise RuntimeError(
        "DATABASE_URL باید به یه دیتابیسِ تستیِ ایزوله اشاره کنه (شاملِ '_test' در نامش)، "
        "نه دیتابیسِ پروداکشن! تست‌ها اجرا نمی‌شن تا این تضمین برقرار نشه."
    )
if not os.environ.get("REDIS_URL", "").rstrip("/").endswith("/15"):
    raise RuntimeError(
        "REDIS_URL باید به دیتابیسِ ایندکسِ ۱۵ (رزروشده برای تست) اشاره کنه، نه دیتابیسِ "
        "۰ی پروداکشن! تست‌ها اجرا نمی‌شن تا این تضمین برقرار نشه."
    )

import pytest

import db
import redis_client as rc

TEST_USER_ID_BASE = 900_000_000_000  # خارج از بازه‌ی واقعیِ آی‌دیِ کاربرهای تلگرام
_next_test_id = [TEST_USER_ID_BASE]


def fresh_user_id() -> int:
    """هر بار یه user_idِ تستیِ یکتا و امن (غیرقابل‌برخورد با کاربرِ واقعی) برمی‌گردونه."""
    _next_test_id[0] += 1
    return _next_test_id[0]


@pytest.fixture
async def make_user():
    """یه کاربرِ تستی با موجودیِ سکه‌ی دلخواه می‌سازه و بعد از تست پاکش می‌کنه."""
    created_ids = []

    async def _make(coins: int = 10) -> db.User:
        user_id = fresh_user_id()
        async with db.async_session() as session:
            user = await db.get_or_create_user(session, user_id, username=f"test_{user_id}")
            user.coins = coins
            await session.commit()
            await session.refresh(user)
        created_ids.append(user_id)
        return user

    yield _make

    async with db.async_session() as session:
        from sqlalchemy import delete

        if created_ids:
            await session.execute(delete(db.CoinTransaction).where(db.CoinTransaction.user_id.in_(created_ids)))
            await session.execute(delete(db.User).where(db.User.id.in_(created_ids)))
            await session.commit()


@pytest.fixture(autouse=True)
async def _clean_test_redis():
    """قبل و بعد از هر تست، دیتابیسِ Redisِ تستی (index ۱۵) رو کاملاً
    خالی می‌کنه — چون این DB منحصراً برای تسته و چیزِ دیگه‌ای توش
    نگه‌داری نمی‌شه، flushdb اینجا کاملاً امنه (برخلافِ DB ۰ی پروداکشن).
    disconnect بعد از هر تست هم برای همون دلیلِ event-loop-per-test
    لازمه (نگاه کن به _dispose_db_engine_after_test)."""
    await rc.r.flushdb()
    yield
    await rc.r.flushdb()
    await rc.r.connection_pool.disconnect()


@pytest.fixture(autouse=True)
async def _dispose_db_engine_after_test():
    """pytest-asyncio (در حالتِ auto) برای هر تست یه event loopِ جدید
    می‌سازه، ولی db.engine یه singletonِ سطح-ماژوله که pool اتصالش به
    اولین loopی که ازش استفاده کرده گره می‌خوره. بدونِ این dispose،
    تستِ دوم به بعد با خطای «cannot perform operation: another operation
    is in progress» / «Event loop is closed» کرش می‌کنه، چون می‌خواد از
    اتصالِ loopِ قبلی (که بسته شده) استفاده کنه. dispose بعد از هر تست
    یعنی تستِ بعدی مجبوره اتصالِ تازه بسازه، بسته به loopِ خودش."""
    yield
    await db.engine.dispose()
    if db._read_engine is not db.engine:
        await db._read_engine.dispose()
