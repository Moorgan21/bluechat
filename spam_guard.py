"""
سیستم آنتی‌اسپم — rate limiting لغزنده (sliding window) با Redis
"""

import os
import time
import logging

import redis.asyncio as redis

logger = logging.getLogger(__name__)

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
r = redis.from_url(REDIS_URL, decode_responses=True)

# --- تنظیمات ---
# پیام عادی
MSG_LIMIT  = int(os.environ.get("SPAM_MSG_LIMIT",  "12"))   # پیام
MSG_WINDOW = int(os.environ.get("SPAM_MSG_WINDOW", "5"))    # ثانیه

# دستور / callback
CMD_LIMIT  = int(os.environ.get("SPAM_CMD_LIMIT",  "8"))
CMD_WINDOW = int(os.environ.get("SPAM_CMD_WINDOW", "10"))

# flood سنگین (تشخیص bot/اسپمر)
FLOOD_LIMIT  = int(os.environ.get("SPAM_FLOOD_LIMIT",  "30"))
FLOOD_WINDOW = int(os.environ.get("SPAM_FLOOD_WINDOW", "30"))

# مدت بلاک موقت (ثانیه)
BLOCK_DURATION = int(os.environ.get("SPAM_BLOCK_DURATION", "60"))

_KEY_BLOCK  = "bluechat:spam_block:{user_id}"
_KEY_RATE   = "bluechat:spam_rate:{kind}:{user_id}"
_KEY_FLOOD  = "bluechat:spam_flood:{user_id}"


async def _sliding_window(key: str, limit: int, window: int) -> bool:
    """sliding window rate limiter — True یعنی مجاز، False یعنی تجاوز."""
    now = time.time()
    cutoff = now - window
    async with r.pipeline(transaction=True) as pipe:
        pipe.zremrangebyscore(key, "-inf", cutoff)
        pipe.zadd(key, {str(now): now})
        pipe.zcard(key)
        pipe.expire(key, window + 1)
        _, _, count, _ = await pipe.execute()
    return count <= limit


async def is_blocked(user_id: int) -> bool:
    """آیا کاربر در حال حاضر بلاک موقت هست؟"""
    return bool(await r.exists(_KEY_BLOCK.format(user_id=user_id)))


async def _block_user(user_id: int) -> None:
    await r.setex(_KEY_BLOCK.format(user_id=user_id), BLOCK_DURATION, "1")
    logger.warning("spam_guard: user %s blocked for %ds", user_id, BLOCK_DURATION)


async def check_message(user_id: int) -> bool:
    """
    بررسی پیام متنی/مدیا.
    True = مجاز به ادامه، False = باید مسدود بشه.
    """
    if await is_blocked(user_id):
        return False

    flood_key = _KEY_FLOOD.format(user_id=user_id)
    msg_key   = _KEY_RATE.format(kind="msg", user_id=user_id)

    flood_ok = await _sliding_window(flood_key, FLOOD_LIMIT, FLOOD_WINDOW)
    msg_ok   = await _sliding_window(msg_key,   MSG_LIMIT,   MSG_WINDOW)

    if not flood_ok or not msg_ok:
        await _block_user(user_id)
        return False

    return True


async def check_command(user_id: int) -> bool:
    """
    بررسی دستور یا callback.
    True = مجاز، False = مسدود.
    """
    if await is_blocked(user_id):
        return False

    cmd_key = _KEY_RATE.format(kind="cmd", user_id=user_id)
    ok = await _sliding_window(cmd_key, CMD_LIMIT, CMD_WINDOW)

    if not ok:
        await _block_user(user_id)
        return False

    return True


async def remaining_block(user_id: int) -> int:
    """تعداد ثانیه‌های باقیمانده از بلاک (0 اگه بلاک نباشه)."""
    ttl = await r.ttl(_KEY_BLOCK.format(user_id=user_id))
    return max(0, ttl)
