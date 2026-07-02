"""
سیستم آنتی‌اسپم — rate limiting لغزنده (sliding window) با Redis
"""

import os
import time
import logging
from enum import Enum, auto

import redis.asyncio as redis
import metrics

logger = logging.getLogger(__name__)

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
r = redis.from_url(REDIS_URL, decode_responses=True)

# --- تنظیمات ---
MSG_LIMIT  = int(os.environ.get("SPAM_MSG_LIMIT",  "12"))
MSG_WINDOW = int(os.environ.get("SPAM_MSG_WINDOW", "5"))

CMD_LIMIT  = int(os.environ.get("SPAM_CMD_LIMIT",  "8"))
CMD_WINDOW = int(os.environ.get("SPAM_CMD_WINDOW", "10"))

FLOOD_LIMIT  = int(os.environ.get("SPAM_FLOOD_LIMIT",  "30"))
FLOOD_WINDOW = int(os.environ.get("SPAM_FLOOD_WINDOW", "30"))

BLOCK_DURATION = int(os.environ.get("SPAM_BLOCK_DURATION", "60"))

_KEY_BLOCK = "bluechat:spam_block:{user_id}"
_KEY_RATE  = "bluechat:spam_rate:{kind}:{user_id}"
_KEY_FLOOD = "bluechat:spam_flood:{user_id}"


class SpamResult(Enum):
    ALLOWED        = auto()   # مجاز
    JUST_BLOCKED   = auto()   # همین الان بلاک شد — یه پیام هشدار بفرست
    ALREADY_BLOCKED = auto()  # قبلاً بلاک بود — سکوت کامل (silent drop)


async def _sliding_window(key: str, limit: int, window: int) -> bool:
    """
    sliding window rate limiter.
    هر بار ZREMRANGEBYSCORE فراخوانی می‌شه تا timestamp های خارج
    از پنجره پاک بشن و Redis رم بیهوده نگه نداره.
    True = مجاز، False = تجاوز از سقف.
    """
    now = time.time()
    cutoff = now - window
    async with r.pipeline(transaction=True) as pipe:
        pipe.zremrangebyscore(key, "-inf", cutoff)   # پاک‌سازی قدیمی‌ها
        pipe.zadd(key, {str(now): now})               # ثبت timestamp جدید
        pipe.zcard(key)                               # شمارش پنجره فعلی
        pipe.expire(key, window + 1)                  # TTL خودکار برای کلید
        _, _, count, _ = await pipe.execute()
    return count <= limit


async def is_blocked(user_id: int) -> bool:
    return bool(await r.exists(_KEY_BLOCK.format(user_id=user_id)))


async def _block_user(user_id: int) -> None:
    await r.setex(_KEY_BLOCK.format(user_id=user_id), BLOCK_DURATION, "1")
    logger.warning("spam_guard: user %s blocked for %ds", user_id, BLOCK_DURATION)


async def check_message(user_id: int) -> SpamResult:
    """
    بررسی پیام متنی/مدیا.
    ALLOWED        → ادامه بده
    JUST_BLOCKED   → یه هشدار بفرست، بعد drop کن
    ALREADY_BLOCKED → بی‌صدا drop کن (silent drop)
    """
    if await is_blocked(user_id):
        return SpamResult.ALREADY_BLOCKED

    flood_key = _KEY_FLOOD.format(user_id=user_id)
    msg_key   = _KEY_RATE.format(kind="msg", user_id=user_id)

    flood_ok = await _sliding_window(flood_key, FLOOD_LIMIT, FLOOD_WINDOW)
    msg_ok   = await _sliding_window(msg_key,   MSG_LIMIT,   MSG_WINDOW)

    if not flood_ok or not msg_ok:
        await _block_user(user_id)
        metrics.spam_blocks.labels(kind="message").inc()
        return SpamResult.JUST_BLOCKED

    return SpamResult.ALLOWED


async def check_command(user_id: int) -> SpamResult:
    """
    بررسی دستور یا callback.
    همان سه حالت بالا.
    """
    if await is_blocked(user_id):
        return SpamResult.ALREADY_BLOCKED

    cmd_key = _KEY_RATE.format(kind="cmd", user_id=user_id)
    ok = await _sliding_window(cmd_key, CMD_LIMIT, CMD_WINDOW)

    if not ok:
        await _block_user(user_id)
        metrics.spam_blocks.labels(kind="command").inc()
        return SpamResult.JUST_BLOCKED

    return SpamResult.ALLOWED


async def remaining_block(user_id: int) -> int:
    ttl = await r.ttl(_KEY_BLOCK.format(user_id=user_id))
    return max(0, ttl)
