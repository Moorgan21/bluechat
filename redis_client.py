# Copyright (C) 2026 Dariush Lashani
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""لایه‌ی Redis، برای صف matching، جفت‌های فعال و کش سریع.

matching باید خیلی سریع و atomic باشه چون چند کاربر ممکنه هم‌زمان
/start بزنن، و Postgres برای این حجم query سریع مناسب نیست. اطلاعات
دائمی (پروفایل، سکه، آمار) همچنان توی Postgres می‌مونه؛ Redis فقط
state لحظه‌ایِ گفتگو رو نگه می‌داره.

نیاز به `pip install redis[hiredis]` و env var به اسم REDIS_URL.
"""

import json
import os
from typing import Optional

import redis.asyncio as redis

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

r = redis.from_url(REDIS_URL, decode_responses=True)

# --- کلیدهای Redis ---
# صف انتظار حالا بر اساس جنسیتِ خودِ کاربرِ منتظر جدا نگه داشته می‌شه
# (نه بر اساس ترجیحش)، چون وقتی کاربر B دنبال «فقط دختر» می‌گرده، باید
# بگرده توی صفِ کسانی که خودشون دخترن. هر آیتمِ صف با یه score (زمانِ
# ورود) نگه داشته می‌شه تا هم امکان محاسبه‌ی «چقدر منتظر مونده» باشه
# هم امکان حذفِ خودکار بعد از انقضای مهلت (۲ دقیقه).
KEY_WAITING_QUEUE_BY_GENDER = "bluechat:waiting_queue:{gender}"  # ZSET: user_id -> timestamp ورود
KEY_USER_DESIRED_GENDER = "bluechat:desired_gender:{user_id}"    # user_id -> ترجیح جنسیتیِ این round
KEY_QUEUE_PIN_MSG = "bluechat:queue_pin:{user_id}"               # user_id -> message_id پیامِ پین‌شده‌ی صف

PARTNER_GENDERS = ("male", "female")  # جنسیت‌های قابل‌جستجو در صف (unset وارد matching نمی‌شه)
QUEUE_TIMEOUT_SECONDS = 120  # ۲ دقیقه مهلت قبل از خروج خودکار از صف

KEY_PARTNER = "bluechat:partner:{user_id}"            # user_id -> partner_id فعلی
KEY_MESSAGE_MAP = "bluechat:msgmap:{user_id}:{msg_id}"  # (user,msg) -> "partner_id:partner_msg_id"
# session_key = session_id (وقتی موجوده) وگرنه pair_key، تا وقتی دو نفر
# چندبار پشتِ سرِ هم با هم چت می‌کنن، تاریخچه/تاییدِ حذفِ یه گفتگوی
# قدیمی با گفتگوی جدیدترشون قاطی نشه.
KEY_CHAT_HISTORY = "bluechat:history:{session_key}:{user_id}"  # لیست message_id های یک گفتگوی مشخص
KEY_OWN_SENT_MSGS = "bluechat:own_sent:{user_id}"     # SET: message_id هایی که خودِ کاربر فرستاده (برای حذف)
KEY_SECURE_CHAT = "bluechat:secure:{user_id}"         # چت امن per-user: پیام‌های خودِ کاربر protect می‌شن
KEY_PENDING_DELETE = "bluechat:pending_delete:{session_key}"  # set از user_id هایی که تایید کردن
KEY_SESSION_ID = "bluechat:session_id:{user_id}"      # آی‌دی رکورد ChatSession جاری در Postgres
KEY_LAST_SEEN = "bluechat:last_seen:{user_id}"        # timestamp آخرین فعالیت کاربر در ربات
KEY_BAN_NOTICE_SENT = "bluechat:ban_notice:{user_id}" # جلوگیری از اسپمِ پیامِ «حسابت مسدوده» با هر پیام
KEY_CHAT_START = "bluechat:chat_start:{user_id}"      # timestamp شروع چت جاری
KEY_CHAT_PAYER = "bluechat:chat_payer:{user_id}"              # کاربر برای این چت سکه خرج کرده
KEY_CHAT_MSG_COUNT = "bluechat:chat_msgs:{pair_key}"          # تعداد کل پیام‌های رد و بدل شده

MIN_CHAT_SECONDS = 10  # حداقل مدت حضور در چت قبل از اجازه‌ی بستن
CHAT_COIN_COST = 2              # هزینه‌ی جستجو با فیلتر جنسیت / درخواست چت
DIRECT_MSG_COIN_COST = 1        # هزینه‌ی هر پیامِ دایرکت (نه پیامِ ناشناسِ لینک مستقیم)

# --- کلیدهای پیام‌های ناشناسِ نوتیفی (از طریق لینک ناشناس مستقیم) ---
# هر پیامی که از طریق لینک مستقیم ارسال می‌شه، یه note_id یکتا می‌گیره
# که فرستنده‌ی واقعیش رو نگه می‌داره (بدون اینکه سشن/جفت‌شدنِ دائمی
# ایجاد بشه). صاحب لینک زیر هر پیام یه دکمه‌ی «پاسخ» می‌بینه که با
# همین note_id شناسایی می‌شه.
KEY_NOTE_SENDER = "bluechat:note:{note_id}"           # note_id -> sender_id
KEY_NOTE_MESSAGE = "bluechat:note_msg:{note_id}"      # note_id -> "chat_id:message_id" پیام اصلی
KEY_AWAITING_REPLY = "bluechat:awaiting_reply:{owner_id}"  # owner_id -> note_id ای که منتظر پاسخشه

# گزارشِ تک‌پیام (ریپلای‌کردنِ «گزارش» روی پیامِ طرفِ مقابل): چون
# callback_data محدودیتِ اندازه داره، متنِ پیامِ گزارش‌شده رو نمی‌شه
# مستقیم توش گذاشت؛ پس با یه tokenِ کوتاه توی Redis نگه می‌داریم تا وقتِ
# انتخابِ دلیل ازش بخونیم.
KEY_MSG_REPORT = "bluechat:msg_report:{token}"        # token -> JSON زمینه‌ی گزارش
TTL_MSG_REPORT = 60 * 10  # ۱۰ دقیقه فرصت برای انتخابِ دلیلِ گزارش


TTL_LAST_SEEN = 60 * 60 * 24 * 30   # ۳۰ روز نگه می‌داره آخرین زمان آنلاین
TTL_MESSAGE_MAP = 60 * 60 * 24 * 2  # ۴۸ ساعت (هم‌راستا با محدودیت حذف پیام تلگرام)
TTL_PENDING_DELETE = 120            # ۲ دقیقه: هم مهلتِ تاییدِ دوطرفه‌ی پاکسازیِ تاریخچه، هم مهلتِ گزارشِ گفتگو بعد از پایانش
TTL_NOTE = 60 * 60 * 24 * 7         # یک هفته اعتبار برای پاسخ به یک پیام ناشناس
TTL_AWAITING_REPLY = 60 * 60 * 6    # ۶ ساعت فرصت برای نوشتن متن پاسخ بعد از زدن دکمه



async def _user_gender_str(user_id: int) -> Optional[str]:
    """جنسیتِ خودِ کاربر رو از Postgres می‌خونه (male/female). برای این
    ماژول، وابستگی به db.py رو lazy نگه می‌داریم تا import چرخه‌ای
    ایجاد نشه."""
    from db import Gender, async_session
    from sqlalchemy import select
    from db import User

    async with async_session() as session:
        result = await session.execute(select(User.gender).where(User.id == user_id))
        gender = result.scalar_one_or_none()
        if gender is None or gender == Gender.unset:
            return None
        return gender.value


async def enqueue(user_id: int, desired_gender: Optional[str] = None) -> bool:
    """کاربر رو وارد صفِ مخصوصِ جنسیتِ خودش می‌کنه (با timestamp فعلی
    به‌عنوان score، برای پشتیبانی از timeout بعدی). desired_gender
    ("male"/"female"/None برای فرقی‌نمی‌کنه) رو هم ذخیره می‌کنه تا موقع
    جستجو بدونیم این کاربر باید توی کدوم صف(ها) بگرده. اگه جنسیتِ خودِ
    کاربر تنظیم نشده باشه، وارد صف نمی‌شه و False برمی‌گردونه."""
    import time

    own_gender = await _user_gender_str(user_id)
    if own_gender is None:
        return False

    key = KEY_WAITING_QUEUE_BY_GENDER.format(gender=own_gender)
    await r.zadd(key, {str(user_id): time.time()})

    if desired_gender:
        await r.set(KEY_USER_DESIRED_GENDER.format(user_id=user_id), desired_gender)
    else:
        await r.delete(KEY_USER_DESIRED_GENDER.format(user_id=user_id))

    return True


async def dequeue(user_id: int) -> bool:
    """کاربر رو از صف خارج می‌کنه. خروجی True یعنی واقعاً حذف شد (کلیم موفق)."""
    total = 0
    for gender in PARTNER_GENDERS:
        count = await r.zrem(KEY_WAITING_QUEUE_BY_GENDER.format(gender=gender), str(user_id))
        total += count
    await r.delete(KEY_USER_DESIRED_GENDER.format(user_id=user_id))
    return total > 0


async def is_waiting(user_id: int) -> bool:
    for gender in PARTNER_GENDERS:
        score = await r.zscore(KEY_WAITING_QUEUE_BY_GENDER.format(gender=gender), str(user_id))
        if score is not None:
            return True
    return False


async def get_desired_gender(user_id: int) -> Optional[str]:
    val = await r.get(KEY_USER_DESIRED_GENDER.format(user_id=user_id))
    return val



async def set_chat_payer(user_id: int) -> None:
    """علامت می‌زنه که این کاربر برای چت جاری سکه خرج کرده."""
    await r.set(KEY_CHAT_PAYER.format(user_id=user_id), "1", ex=60 * 60 * 12)


async def is_chat_payer(user_id: int) -> bool:
    return bool(await r.exists(KEY_CHAT_PAYER.format(user_id=user_id)))


async def increment_chat_msg_count(user_a: int, user_b: int) -> int:
    """بعد از هر پیام موفق، شمارنده رو یه واحد بالا می‌بره."""
    key = KEY_CHAT_MSG_COUNT.format(pair_key=pair_key(user_a, user_b))
    val = await r.incr(key)
    await r.expire(key, 60 * 60 * 24)
    return int(val)


async def get_chat_msg_count(user_a: int, user_b: int) -> int:
    key = KEY_CHAT_MSG_COUNT.format(pair_key=pair_key(user_a, user_b))
    val = await r.get(key)
    return int(val) if val else 0


# --- تطبیقِ atomic با اسکریپتِ Lua ---
# قبلاً «چک کن کسی هست، اگه نه enqueue کن» دو round-trip جدا بود
# (pop_matching_waiting بعد enqueue)، که یه فاصله‌ی TOCTOU واقعی
# می‌ذاشت: اگه دو کاربر همزمان می‌رسیدن، ممکن بود هیچ‌کدوم توی چکِ
# اولشون همدیگه رو نبینن و هر دو enqueue بشن، و بعد هر دو مستقلاً موفق
# به match‌کردنِ همدیگه بشن (دو تا ChatSession جدا برای یه جفت!). یه
# sleep(0.8) موقتاً این پنجره رو کوچیک می‌کرد ولی نمی‌بستش. حالا کلِ
# «جستجو یا enqueue» توی یه اسکریپتِ Lواحد و atomic اجرا می‌شه، پس
# اصلاً همچین پنجره‌ای وجود نداره.
_MATCH_OR_ENQUEUE_LUA = """
local self_id = ARGV[1]
local own_gender = ARGV[2]
local desired_gender = ARGV[3]
local now = ARGV[4]
local desired_key_prefix = ARGV[5]

local qkeys = {}
qkeys["male"] = KEYS[1]
qkeys["female"] = KEYS[2]

local candidate_genders = {}
if desired_gender ~= "" then
    candidate_genders[1] = desired_gender
else
    candidate_genders[1] = "male"
    candidate_genders[2] = "female"
end

for _, gender in ipairs(candidate_genders) do
    local members = redis.call("ZRANGE", qkeys[gender], 0, -1)
    for _, candidate_id in ipairs(members) do
        if candidate_id ~= self_id then
            local candidate_desired = redis.call("GET", desired_key_prefix .. candidate_id)
            if candidate_desired == false or candidate_desired == own_gender then
                redis.call("ZREM", KEYS[1], candidate_id)
                redis.call("ZREM", KEYS[2], candidate_id)
                redis.call("DEL", desired_key_prefix .. candidate_id)
                return candidate_id
            end
        end
    end
end

redis.call("ZADD", qkeys[own_gender], now, self_id)
if desired_gender ~= "" then
    redis.call("SET", desired_key_prefix .. self_id, desired_gender)
else
    redis.call("DEL", desired_key_prefix .. self_id)
end
return false
"""

_match_or_enqueue_script = r.register_script(_MATCH_OR_ENQUEUE_LUA)


async def atomic_match_or_enqueue(user_id: int, desired_gender: Optional[str]) -> tuple[bool, Optional[int]]:
    """جایگزینِ atomic برای «pop_matching_waiting بعد enqueue»؛ توی یه
    round-trip واحد یا یه partnerِ سازگار پیدا و claim می‌کنه، یا (اگه
    کسی نبود) خودِ کاربر رو توی صفِ جنسیتِ خودش enqueue می‌کنه.

    خروجی (entered, partner_id):
    - (False, None): جنسیتِ خودِ کاربر توی پروفایل تنظیم نشده، پس
      اصلاً وارد صف نشد.
    - (True, None): صف‌شد و منتظره؛ کسی این لحظه پیدا نشد.
    - (True, partner_id): بلافاصله matched شد؛ partner از قبل از صف
      حذف شده (خودِ user_id هیچ‌وقت enqueue نشد)."""
    import time

    own_gender = await _user_gender_str(user_id)
    if own_gender is None:
        return False, None

    result = await _match_or_enqueue_script(
        keys=[
            KEY_WAITING_QUEUE_BY_GENDER.format(gender="male"),
            KEY_WAITING_QUEUE_BY_GENDER.format(gender="female"),
        ],
        args=[
            str(user_id),
            own_gender,
            desired_gender or "",
            time.time(),
            KEY_USER_DESIRED_GENDER.format(user_id=""),
        ],
    )
    return True, (int(result) if result else None)


async def purge_stale_queue_entries() -> int:
    """ورودی‌های منقضی‌شده‌ی صف رو پاک می‌کنه (برای حفاظت در برابر ریستارت ربات)."""
    import time
    cutoff = time.time() - QUEUE_TIMEOUT_SECONDS
    total = 0
    for gender in PARTNER_GENDERS:
        removed = await r.zremrangebyscore(
            KEY_WAITING_QUEUE_BY_GENDER.format(gender=gender), "-inf", cutoff
        )
        total += removed
    return total


async def get_queue_wait_seconds(user_id: int) -> Optional[float]:
    """چند ثانیه‌ست که کاربر توی صفه (بر اساس score/timestamp ورود)."""
    import time

    for gender in PARTNER_GENDERS:
        score = await r.zscore(KEY_WAITING_QUEUE_BY_GENDER.format(gender=gender), str(user_id))
        if score is not None:
            return time.time() - score
    return None


async def set_queue_pin_message(user_id: int, message_id: int) -> None:
    await r.set(KEY_QUEUE_PIN_MSG.format(user_id=user_id), message_id, ex=QUEUE_TIMEOUT_SECONDS + 30)


async def pop_queue_pin_message(user_id: int) -> Optional[int]:
    key = KEY_QUEUE_PIN_MSG.format(user_id=user_id)
    val = await r.get(key)
    if val is None:
        return None
    await r.delete(key)
    return int(val)


async def set_partner(user_a: int, user_b: int) -> None:
    import time as _time
    now = _time.time()
    await r.set(KEY_PARTNER.format(user_id=user_a), user_b)
    await r.set(KEY_PARTNER.format(user_id=user_b), user_a)
    await r.set(KEY_CHAT_START.format(user_id=user_a), now, ex=60 * 60 * 24)
    await r.set(KEY_CHAT_START.format(user_id=user_b), now, ex=60 * 60 * 24)


async def get_chat_start(user_id: int) -> Optional[float]:
    val = await r.get(KEY_CHAT_START.format(user_id=user_id))
    return float(val) if val else None


async def get_partner(user_id: int) -> Optional[int]:
    val = await r.get(KEY_PARTNER.format(user_id=user_id))
    return int(val) if val else None


async def clear_partner(user_id: int) -> Optional[int]:
    partner_id = await get_partner(user_id)
    await r.delete(KEY_PARTNER.format(user_id=user_id))
    if partner_id is not None:
        await r.delete(KEY_PARTNER.format(user_id=partner_id))
        await r.delete(KEY_SECURE_CHAT.format(user_id=user_id))
        await r.delete(KEY_CHAT_START.format(user_id=user_id))
        await r.delete(KEY_CHAT_START.format(user_id=partner_id))
        await r.delete(KEY_CHAT_PAYER.format(user_id=user_id))
        await r.delete(KEY_CHAT_PAYER.format(user_id=partner_id))
        await r.delete(KEY_CHAT_MSG_COUNT.format(pair_key=pair_key(user_id, partner_id)))
    return partner_id


async def link_messages(user_a: int, msg_a: int, user_b: int, msg_b: int) -> None:
    await r.set(
        KEY_MESSAGE_MAP.format(user_id=user_a, msg_id=msg_a),
        f"{user_b}:{msg_b}",
        ex=TTL_MESSAGE_MAP,
    )
    await r.set(
        KEY_MESSAGE_MAP.format(user_id=user_b, msg_id=msg_b),
        f"{user_a}:{msg_a}",
        ex=TTL_MESSAGE_MAP,
    )


async def get_linked_message(user_id: int, message_id: int) -> Optional[tuple[int, int]]:
    val = await r.get(KEY_MESSAGE_MAP.format(user_id=user_id, msg_id=message_id))
    if not val:
        return None
    partner_id_str, partner_msg_str = val.split(":")
    return int(partner_id_str), int(partner_msg_str)


def _history_session_key(session_id: Optional[int], user_a: int, user_b: Optional[int] = None) -> str:
    """اگه session_id موجود باشه ازش استفاده می‌کنیم؛ وگرنه (حالتِ نادرِ
    نبودِ سشن) fallback به pair_key تا لااقل بینِ این دو نفر مشترک بمونه."""
    if session_id is not None:
        return str(session_id)
    return pair_key(user_a, user_b) if user_b is not None else str(user_a)


async def record_message(user_id: int, message_id: int, session_id: Optional[int] = None) -> None:
    key = KEY_CHAT_HISTORY.format(session_key=_history_session_key(session_id, user_id), user_id=user_id)
    await r.rpush(key, message_id)
    await r.expire(key, TTL_MESSAGE_MAP)


async def has_chat_history(user_id: int, session_id: Optional[int] = None) -> bool:
    """چکِ غیرمخربِ «آیا این گفتگو اصلاً پیامی داشته؟» (برخلافِ
    pop_history که لیست رو هم می‌خونه هم پاک می‌کنه). برای تصمیم‌گیریِ
    offer_history_deletion/start_report_after_chat قبل از پیشنهادِ
    پاک‌سازی/گزارش به یه گفتگوی کاملاً خالی لازمه."""
    key = KEY_CHAT_HISTORY.format(session_key=_history_session_key(session_id, user_id), user_id=user_id)
    return bool(await r.llen(key))


async def mark_own_message(user_id: int, message_id: int) -> None:
    """پیامی که خودِ کاربر فرستاده رو علامت می‌زنه تا بعداً قابل حذف باشه."""
    key = KEY_OWN_SENT_MSGS.format(user_id=user_id)
    await r.sadd(key, message_id)
    await r.expire(key, TTL_MESSAGE_MAP)


async def is_own_message(user_id: int, message_id: int) -> bool:
    """بررسی می‌کنه آیا این message_id توسط خودِ کاربر فرستاده شده یا دریافت شده."""
    return bool(await r.sismember(KEY_OWN_SENT_MSGS.format(user_id=user_id), message_id))


async def toggle_secure_chat(user_id: int) -> bool:
    """چت امن رو برای پیام‌های خودِ کاربر toggle می‌کنه.
    True = فعال شد، False = غیرفعال شد.
    """
    key = KEY_SECURE_CHAT.format(user_id=user_id)
    if await r.exists(key):
        await r.delete(key)
        return False
    await r.set(key, "1", ex=60 * 60 * 24)
    return True


async def is_secure_chat(user_id: int) -> bool:
    """آیا پیام‌های این کاربر باید با protect_content ارسال بشن؟"""
    return bool(await r.exists(KEY_SECURE_CHAT.format(user_id=user_id)))


async def pop_history(user_id: int, session_id: Optional[int] = None) -> list[int]:
    key = KEY_CHAT_HISTORY.format(session_key=_history_session_key(session_id, user_id), user_id=user_id)
    ids = await r.lrange(key, 0, -1)
    await r.delete(key)
    return [int(i) for i in ids]


def pair_key(user_a: int, user_b: int) -> str:
    lo, hi = sorted((user_a, user_b))
    return f"{lo}:{hi}"


async def start_pending_delete(user_a: int, user_b: int, session_id: Optional[int] = None) -> None:
    """یه کلید «فعال‌بودن درخواست» با TTL می‌سازه، و ست تاییدکننده‌ها رو
    خالی می‌کنه. وجودِ کلید active معیار معتبربودنِ درخواسته. کلید با
    session_id اسکوپ می‌شه (نه صرفاً جفتِ کاربر) که اگه همین دو نفر
    دوباره با هم چت کردن، تاییدهای گفتگوی قدیمی با گفتگوی جدید قاطی نشه."""
    key = KEY_PENDING_DELETE.format(session_key=_history_session_key(session_id, user_a, user_b))
    await r.delete(key)
    await r.set(f"{key}:active", "1", ex=TTL_PENDING_DELETE)


async def confirm_pending_delete(
    user_a: int, user_b: int, confirmer_id: int, session_id: Optional[int] = None
) -> Optional[set[int]]:
    """confirmer رو به ست تاییدکننده‌ها اضافه می‌کنه. اگه کلید active وجود
    نداشته باشه (منقضی شده یا هیچ‌وقت start نشده) None برمی‌گردونه."""
    key = KEY_PENDING_DELETE.format(session_key=_history_session_key(session_id, user_a, user_b))
    if not await r.exists(f"{key}:active"):
        return None
    await r.sadd(key, confirmer_id)
    await r.expire(key, TTL_PENDING_DELETE)
    members = await r.smembers(key)
    return {int(m) for m in members}


async def get_pending_delete_set(user_a: int, user_b: int, session_id: Optional[int] = None) -> Optional[set[int]]:
    key = KEY_PENDING_DELETE.format(session_key=_history_session_key(session_id, user_a, user_b))
    members = await r.smembers(key)
    return {int(m) for m in members} if members else None


async def clear_pending_delete(user_a: int, user_b: int, session_id: Optional[int] = None) -> None:
    key = KEY_PENDING_DELETE.format(session_key=_history_session_key(session_id, user_a, user_b))
    await r.delete(key)
    await r.delete(f"{key}:active")


async def is_post_chat_window_active(session_id: Optional[int], user_a: int, user_b: int) -> bool:
    """آیا هنوز توی مهلتِ ۲دقیقه‌ایِ بعدِ پایانِ گفتگوییم؟ همون کلیدِ
    «active»ِ pending_delete رو چک می‌کنه، چون دقیقاً همون لحظه که
    offer_history_deletion صدا زده می‌شه ست می‌شه؛ برای این که دکمه‌ی
    «🚫 گزارش این گفتگو»ی بعدِ پایانِ چت هم همون مهلتِ دکمه‌ی پاکسازیِ
    تاریخچه رو داشته باشه."""
    key = KEY_PENDING_DELETE.format(session_key=_history_session_key(session_id, user_a, user_b))
    return bool(await r.exists(f"{key}:active"))


async def set_session_id(user_a: int, user_b: int, session_id: int) -> None:
    await r.set(KEY_SESSION_ID.format(user_id=user_a), session_id)
    await r.set(KEY_SESSION_ID.format(user_id=user_b), session_id)


async def get_session_id(user_id: int) -> Optional[int]:
    val = await r.get(KEY_SESSION_ID.format(user_id=user_id))
    return int(val) if val else None


async def clear_session_id(user_id: int) -> None:
    await r.delete(KEY_SESSION_ID.format(user_id=user_id))


# وضعیت آنلاین / آخرین بازدید
async def update_last_seen(user_id: int) -> None:
    import time
    await r.set(KEY_LAST_SEEN.format(user_id=user_id), str(time.time()), ex=TTL_LAST_SEEN)


async def get_last_seen(user_id: int) -> Optional[float]:
    val = await r.get(KEY_LAST_SEEN.format(user_id=user_id))
    return float(val) if val else None


async def get_last_seen_many(user_ids: list[int]) -> dict[int, float]:
    """نسخه‌ی دسته‌ای برای وقتی باید last_seenِ چندین کاربر رو یکجا
    بخونیم (مثلاً برای مرتب‌سازیِ لیستِ افرادِ نزدیک بر اساسِ زودترین
    آنلاینی)؛ یه MGET به‌جای چندین GETِ جدا."""
    if not user_ids:
        return {}
    keys = [KEY_LAST_SEEN.format(user_id=uid) for uid in user_ids]
    values = await r.mget(keys)
    return {uid: float(val) for uid, val in zip(user_ids, values) if val is not None}


TTL_BAN_NOTICE = 60 * 60 * 6  # هر ۶ ساعت یه‌بار یادآوری، نه با هر پیام


async def should_send_ban_notice(user_id: int) -> bool:
    """SET NX: فقط اولین‌بار (توی این پنجره‌ی ۶ساعته) True برمی‌گردونه،
    تا کاربرِ بن‌شده که هنوز داره پیام می‌فرسته، با هر پیام یه پیامِ
    «حسابت مسدوده» جداگانه نگیره."""
    key = KEY_BAN_NOTICE_SENT.format(user_id=user_id)
    return bool(await r.set(key, "1", ex=TTL_BAN_NOTICE, nx=True))


def format_last_seen(ts: Optional[float]) -> str:
    """timestamp آخرین بازدید رو به متن فارسی تبدیل می‌کنه."""
    if ts is None:
        return "نامشخص"
    import time
    diff = time.time() - ts
    if diff < 5 * 60:
        return "🟢 آنلاین"
    if diff < 60 * 60:
        return f"🕐 {int(diff / 60)} دقیقه پیش"
    if diff < 24 * 60 * 60:
        return f"🕐 {int(diff / 3600)} ساعت پیش"
    if diff < 30 * 24 * 60 * 60:
        return f"🕐 {int(diff / 86400)} روز پیش"
    return "🕐 بیش از یک ماه پیش"


# پیام‌های ناشناسِ نوتیفی (لینک ناشناس مستقیم بدون ورود به یک چت کامل)
async def create_note(sender_id: int) -> str:
    """یه note_id یکتا برای یک پیامِ ورودیِ ناشناس می‌سازه و فرستنده‌ش رو
    ذخیره می‌کنه. این note_id توی callback_data دکمه‌ی «پاسخ» قرار می‌گیره."""
    import uuid

    note_id = uuid.uuid4().hex[:12]
    await r.set(KEY_NOTE_SENDER.format(note_id=note_id), sender_id, ex=TTL_NOTE)
    return note_id


async def get_note_sender(note_id: str) -> Optional[int]:
    val = await r.get(KEY_NOTE_SENDER.format(note_id=note_id))
    return int(val) if val else None


async def store_note_message(note_id: str, chat_id: int, message_id: int) -> None:
    await r.set(
        KEY_NOTE_MESSAGE.format(note_id=note_id),
        f"{chat_id}:{message_id}",
        ex=TTL_NOTE,
    )


async def get_note_message(note_id: str) -> Optional[tuple[int, int]]:
    val = await r.get(KEY_NOTE_MESSAGE.format(note_id=note_id))
    if not val:
        return None
    chat_id_str, message_id_str = val.split(":")
    return int(chat_id_str), int(message_id_str)


async def set_awaiting_reply(owner_id: int, note_id: str) -> None:
    """صاحب لینک روی دکمه‌ی «پاسخ» یک پیام زده؛ پیام متنیِ بعدیش باید
    برای فرستنده‌ی همون note_id ارسال بشه."""
    await r.set(KEY_AWAITING_REPLY.format(owner_id=owner_id), note_id, ex=TTL_AWAITING_REPLY)


async def pop_awaiting_reply(owner_id: int) -> Optional[str]:
    key = KEY_AWAITING_REPLY.format(owner_id=owner_id)
    note_id = await r.get(key)
    if note_id is None:
        return None
    await r.delete(key)
    return note_id


async def clear_awaiting_reply(owner_id: int) -> None:
    await r.delete(KEY_AWAITING_REPLY.format(owner_id=owner_id))


async def store_message_report_context(
    reporter_id: int, reported_id: int, session_id: Optional[int], message_excerpt: str
) -> str:
    """زمینه‌ی گزارشِ تک‌پیام رو تا وقتِ انتخابِ دلیل نگه می‌داره و یه
    tokenِ کوتاه برای گنجوندن توی callback_data برمی‌گردونه."""
    import uuid

    token = uuid.uuid4().hex[:12]
    payload = json.dumps(
        {
            "reporter_id": reporter_id,
            "reported_id": reported_id,
            "session_id": session_id,
            "message_excerpt": message_excerpt,
        }
    )
    await r.set(KEY_MSG_REPORT.format(token=token), payload, ex=TTL_MSG_REPORT)
    return token


async def pop_message_report_context(token: str) -> Optional[dict]:
    key = KEY_MSG_REPORT.format(token=token)
    raw = await r.get(key)
    if raw is None:
        return None
    await r.delete(key)
    return json.loads(raw)


# درخواستِ چت از طریق پروفایلِ عمومی (/user_<code>). کاربر A رو دکمه‌ی
# «درخواست چت» زیر پروفایلِ B می‌زنه، اول فقط یه نوتیفِ کوتاه به B
# می‌رسه (بدون هویتِ A)، و وقتی خودِ B روی «مشاهده» بزنه پیامِ کامل با
# دکمه‌های قبول/رد نشونش داده می‌شه. این دو مرحله باعث می‌شه هویتِ A
# فاش نشه تا وقتی خودِ B بخواد.
KEY_CHAT_REQUEST = "bluechat:chatreq:{request_id}"  # request_id -> {"requester_id":, "target_id":} (JSON)
KEY_CHAT_REQUEST_PENDING = "bluechat:chatreq_pending"  # ZSET: request_id -> timestampِ ایجاد (برای لغوِ خودکار)
KEY_CHAT_REQUEST_BY_REQUESTER = "bluechat:chatreq_by_requester:{requester_id}"  # SET: request_idهای فعالِ این requester
TTL_CHAT_REQUEST = 60 * 60 * 24  # یک روز اعتبار برای یک درخواستِ چت (سقفِ ایمنی؛ خودِ job زودتر لغوش می‌کنه)
CHAT_REQUEST_TIMEOUT_SECONDS = 60 * 5  # ۵ دقیقه مهلت قبل از لغوِ خودکار و بازگشتِ سکه
MAX_ACTIVE_CHAT_REQUESTS_PER_USER = 5  # هر کاربر حداکثر همزمان همین‌قدر درخواستِ چتِ بی‌پاسخ می‌تونه داشته باشه


async def create_chat_request(requester_id: int, target_id: int) -> str:
    import time
    import uuid

    request_id = uuid.uuid4().hex[:12]
    payload = json.dumps({"requester_id": requester_id, "target_id": target_id})
    await r.set(KEY_CHAT_REQUEST.format(request_id=request_id), payload, ex=TTL_CHAT_REQUEST)
    await r.zadd(KEY_CHAT_REQUEST_PENDING, {request_id: time.time()})
    by_requester_key = KEY_CHAT_REQUEST_BY_REQUESTER.format(requester_id=requester_id)
    await r.sadd(by_requester_key, request_id)
    await r.expire(by_requester_key, TTL_CHAT_REQUEST)
    return request_id


async def get_chat_request(request_id: str) -> Optional[dict]:
    val = await r.get(KEY_CHAT_REQUEST.format(request_id=request_id))
    if not val:
        return None
    try:
        data = json.loads(val)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or "requester_id" not in data or "target_id" not in data:
        # فرمتِ قدیمیِ قبل از این تغییر: فقط requester_id به‌تنهایی ذخیره
        # می‌شد (بدونِ target_id). چون این کلیدها دیگه قابلِ پردازشِ کامل
        # نیستن، به‌عنوانِ منقضی‌شده در نظر گرفته می‌شن (نه کرش).
        return None
    return {"requester_id": int(data["requester_id"]), "target_id": int(data["target_id"])}


async def count_active_chat_requests(requester_id: int) -> int:
    """چندتا درخواستِ چتِ بی‌پاسخِ فعلیِ این requester هنوز باز مونده؛
    handle_chat_request_button قبل از ساختِ درخواستِ جدید این رو با
    MAX_ACTIVE_CHAT_REQUESTS_PER_USER مقایسه می‌کنه."""
    return await r.scard(KEY_CHAT_REQUEST_BY_REQUESTER.format(requester_id=requester_id))


async def clear_chat_request(request_id: str) -> None:
    data = await get_chat_request(request_id)
    await r.delete(KEY_CHAT_REQUEST.format(request_id=request_id))
    await r.zrem(KEY_CHAT_REQUEST_PENDING, request_id)
    if data is not None:
        await r.srem(KEY_CHAT_REQUEST_BY_REQUESTER.format(requester_id=data["requester_id"]), request_id)


async def pop_expired_chat_requests(timeout_seconds: int = CHAT_REQUEST_TIMEOUT_SECONDS) -> list[dict]:
    """درخواست‌های چتی که بیشتر از timeout_seconds بدونِ پاسخ (نه قبول نه
    رد) موندن رو پیدا می‌کنه، از Redis پاکشون می‌کنه، و برای بازگشتِ سکه
    و اطلاع‌رسانی برمی‌گردونه. اگه درخواستی همزمان قبول/رد شده باشه (و
    دیگه توی KEY_CHAT_REQUEST نباشه)، بی‌سروصدا از صفِ pending حذف
    می‌شه، بدونِ بازگشتِ سکه (چون قبلاً توسطِ خودِ accept/reject
    پردازش شده)."""
    import time

    cutoff = time.time() - timeout_seconds
    expired_ids = await r.zrangebyscore(KEY_CHAT_REQUEST_PENDING, "-inf", cutoff)
    results = []
    for request_id in expired_ids:
        data = await get_chat_request(request_id)
        await r.delete(KEY_CHAT_REQUEST.format(request_id=request_id))
        await r.zrem(KEY_CHAT_REQUEST_PENDING, request_id)
        if data is not None:
            await r.srem(KEY_CHAT_REQUEST_BY_REQUESTER.format(requester_id=data["requester_id"]), request_id)
            results.append({"request_id": request_id, **data})
    return results


# صفِ عضویتِ اتاقِ چت. برخلافِ matchingِ ۱به۱ (که هر دو طرف دنبالِ همون
# کاری‌ن، پس چکِ لحظه‌ای موقعِ ورودِ نفرِ دوم کافیه)، اینجا «عرضه»
# (ساختِ اتاق، یا آزادشدنِ جا) از یه اکشنِ کاملاً متفاوت میاد. برای
# همین صفِ انتظار اینجا بر اساسِ فیلترِ خودِ جستجوگر باکت‌بندی می‌شه
# (نه یه صفتِ ذاتیِ خودش مثلِ جنسیت)، و «عرضه» باید فعالانه بیاد این
# صف رو چک کنه، نه اینکه جستجوگر منتظرِ notification بمونه.
KEY_ROOM_JOIN_QUEUE_BY_GENDER = "bluechat:room_join_queue:{gender}"  # ZSET: user_id -> timestamp ورود

ROOM_JOIN_TIMEOUT_SECONDS = 120  # ۲ دقیقه مهلت قبل از خروج خودکار و بازگشتِ سکه


async def enqueue_room_join(user_id: int, desired_gender: str) -> None:
    import time

    key = KEY_ROOM_JOIN_QUEUE_BY_GENDER.format(gender=desired_gender)
    await r.zadd(key, {str(user_id): time.time()})


async def dequeue_room_join(user_id: int, desired_gender: str) -> bool:
    key = KEY_ROOM_JOIN_QUEUE_BY_GENDER.format(gender=desired_gender)
    count = await r.zrem(key, str(user_id))
    return count > 0


async def is_waiting_room_join(user_id: int) -> Optional[str]:
    """اگه کاربر توی صفِ عضویتِ اتاقه، فیلترِ ذخیره‌شده‌ش رو برمی‌گردونه، وگرنه None."""
    for gender in ("male", "female", "any"):
        score = await r.zscore(KEY_ROOM_JOIN_QUEUE_BY_GENDER.format(gender=gender), str(user_id))
        if score is not None:
            return gender
    return None


def room_join_compatible_genders(room_gender_pref: str) -> list[str]:
    """اتاقِ «any» همه‌ی باکت‌ها رو می‌پذیره؛ اتاقِ male/female فقط
    همون باکت به‌علاوه‌ی باکتِ «any» (کسی که فرقی براش نداره)."""
    if room_gender_pref == "any":
        return ["male", "female", "any"]
    return [room_gender_pref, "any"]


async def peek_oldest_room_join_candidate(compatible_genders: list[str]) -> Optional[tuple[int, str]]:
    """قدیمی‌ترینِ کاندیدای سازگار رو (بر اساسِ زمانِ ورود، فارغ از
    اینکه تو کدوم باکته) برمی‌گردونه، بدونِ حذف از صف. حذفِ واقعی بعد
    از claim (موفق یا ناموفق) با dequeue_room_join انجام می‌شه."""
    best: tuple[int, str, float] | None = None
    for gender in compatible_genders:
        members = await r.zrange(
            KEY_ROOM_JOIN_QUEUE_BY_GENDER.format(gender=gender), 0, 0, withscores=True
        )
        if members:
            uid, score = members[0]
            if best is None or score < best[2]:
                best = (int(uid), gender, score)
    return (best[0], best[1]) if best else None


async def purge_stale_room_join_queue() -> int:
    """ورودی‌های منقضی‌شده‌ی صفِ عضویتِ اتاق رو پاک می‌کنه (حفاظت در
    برابرِ ریستارتِ ربات، مثلِ purge_stale_queue_entries برای صفِ ۱به۱)."""
    import time

    cutoff = time.time() - ROOM_JOIN_TIMEOUT_SECONDS
    total = 0
    for gender in ("male", "female", "any"):
        removed = await r.zremrangebyscore(
            KEY_ROOM_JOIN_QUEUE_BY_GENDER.format(gender=gender), "-inf", cutoff
        )
        total += removed
    return total


# آینه‌ی active_room_id در Redis. Postgres همچنان source-of-truthه؛
# این کلید فقط برای اینکه text_router/media_router به‌ازای هر پیام
# مجبور نباشن به Postgres سر بزنن (دقیقاً همون فلسفه‌ی KEY_PARTNER).
# چون آینه‌ست، ممکنه لحظه‌ای عقب یا جلوتر از Postgres باشه:
#   - عقب‌تر (کلید هنوز ست نشده): کاربر یه پیام رو گم می‌کنه که به منو
#     می‌افته؛ آزاردهنده ولی بی‌خطر، و با sync دوره‌ای (پایین) جبران می‌شه.
#   - جلوتر (کلید پاک نشده بعدِ حذفِ اتاق): relay وقتی از Postgres
#     تناقض ببینه (اتاق نیست/عضو نیست)، خودش همون‌جا کلید رو پاک
#     می‌کنه و کاربر رو به منو برمی‌گردونه — یعنی خودتصحیح‌شونده‌ست.
KEY_USER_ACTIVE_ROOM = "bluechat:active_room:{user_id}"


async def set_active_room(user_id: int, room_id: int) -> None:
    await r.set(KEY_USER_ACTIVE_ROOM.format(user_id=user_id), room_id)


async def get_active_room(user_id: int) -> Optional[int]:
    val = await r.get(KEY_USER_ACTIVE_ROOM.format(user_id=user_id))
    return int(val) if val else None


KEY_ROOM_UI_SUPPRESSED = "bluechat:room_ui_suppressed:{user_id}"


async def clear_active_room(user_id: int) -> None:
    """پاک‌کردنِ suppress_room_ui همیشه همراهِ اینه: تا وقتی active_room
    مقدار نداره، اون فلگ بی‌معنیه، پس نگه‌داشتنش فقط یه کلیدِ یتیمِ
    Redisه — بهتره جفتِ هم پاک بشن تا فراموش نشه توی جاهای جدیدی که
    عضویت واقعاً تموم می‌شه (ترک/اخراج/حذفِ اتاق)."""
    await r.delete(KEY_USER_ACTIVE_ROOM.format(user_id=user_id))
    await r.delete(KEY_ROOM_UI_SUPPRESSED.format(user_id=user_id))


async def suppress_room_ui(user_id: int) -> None:
    """کاربر همچنان عضوِ اتاقه (active_room_id دست‌نخورده)، ولی
    text_router/media_router باید موقتاً طوری رفتار کنن که انگار
    اصلاً اتاقی نداره — برای «🚪 خروج» (بدونِ ترکِ اتاق) و برای
    عضوهای غیر-owner وقتی owner اتاق رو می‌بنده. جدا از
    KEY_USER_ACTIVE_ROOM نگه‌داری می‌شه تا _active_room_mirror_sync_job
    (که فقط برای بازیابیِ crash بینِ commit و ست‌کردنِ آینه‌ست) این
    حالتِ عمدی رو با یه mirrorِ گم‌شده اشتباه نگیره و خودکار
    برنگردونتش."""
    await r.set(KEY_ROOM_UI_SUPPRESSED.format(user_id=user_id), "1")


async def unsuppress_room_ui(user_id: int) -> None:
    await r.delete(KEY_ROOM_UI_SUPPRESSED.format(user_id=user_id))


async def is_room_ui_suppressed(user_id: int) -> bool:
    return bool(await r.exists(KEY_ROOM_UI_SUPPRESSED.format(user_id=user_id)))


# نگاشتِ پیام‌های اتاق. برخلافِ KEY_MESSAGE_MAPِ زوجیِ ۱به۱، اینجا یه
# پیام باید برای چند نفر فن‌اوت بشه، پس دو تا کلید لازمه:
#   RECIPIENTS: (room, sender, sender_msg) -> {recipient_id: [local_msg_ids]}
#     برای فن‌اوتِ حذف/ویرایش به همه (لیست چون استیکر/ویدیو-نوت caption
#     ندارن و یه پیامِ برچسبِ اسمِ جدا قبلشون می‌ره؛ پس یه پیامِ منطقی
#     می‌تونه دو تا message_id واقعی داشته باشه).
#   ORIGIN: (room, viewer, viewer_local_msg) -> "sender_id:sender_msg_id"
#     تا هرکی (چه خودِ فرستنده چه هر گیرنده‌ای) بتونه نسخه‌ی محلیِ
#     خودشو به پیامِ اصلی resolve کنه؛ هم برای ریپلای هم برای تشخیصِ
#     «این پیام خودمه؟» (وقتی origin == (خودم, همون id)).
KEY_ROOM_MSG_RECIPIENTS = "bluechat:room_msg_recipients:{room_id}:{sender_id}:{sender_msg_id}"
KEY_ROOM_MSG_ORIGIN = "bluechat:room_msg_origin:{room_id}:{viewer_id}:{viewer_msg_id}"
KEY_ROOM_HISTORY = "bluechat:room_history:{room_id}:{user_id}"  # لیستِ message_idهای رسیده به این کاربر در این اتاق
# ChatRoomMemberِ Postgres فقط عضویتِ *فعلی* رو نشون می‌ده؛ کسی که قبل
# از حذفِ نهاییِ اتاق ترک کرده/اخراج شده/بن شده دیگه توش نیست. این SET
# مستقل از عضویتِ زنده، هرکسی که تا حالا تو این اتاق پیامی record شده
# (چه فرستاده چه گرفته) رو نگه می‌داره تا پاک‌سازیِ کاملِ تاریخچه واقعاً
# شاملِ «همه‌ی اعضای سابق» بشه، نه فقط اونایی که لحظه‌ی حذف هنوز عضو بودن.
KEY_ROOM_HISTORY_USERS = "bluechat:room_history_users:{room_id}"


async def set_room_msg_recipient_ids(
    room_id: int, sender_id: int, sender_msg_id: int, recipient_id: int, local_msg_ids: list[int]
) -> None:
    key = KEY_ROOM_MSG_RECIPIENTS.format(room_id=room_id, sender_id=sender_id, sender_msg_id=sender_msg_id)
    await r.hset(key, str(recipient_id), json.dumps(local_msg_ids))
    await r.expire(key, TTL_MESSAGE_MAP)


async def get_room_msg_recipients(room_id: int, sender_id: int, sender_msg_id: int) -> dict[int, list[int]]:
    key = KEY_ROOM_MSG_RECIPIENTS.format(room_id=room_id, sender_id=sender_id, sender_msg_id=sender_msg_id)
    raw = await r.hgetall(key)
    return {int(uid): json.loads(ids_json) for uid, ids_json in raw.items()}


async def set_room_msg_origin(
    room_id: int, viewer_id: int, viewer_msg_id: int, origin_sender_id: int, origin_msg_id: int
) -> None:
    await r.set(
        KEY_ROOM_MSG_ORIGIN.format(room_id=room_id, viewer_id=viewer_id, viewer_msg_id=viewer_msg_id),
        f"{origin_sender_id}:{origin_msg_id}",
        ex=TTL_MESSAGE_MAP,
    )


async def get_room_msg_origin(room_id: int, viewer_id: int, viewer_msg_id: int) -> Optional[tuple[int, int]]:
    val = await r.get(KEY_ROOM_MSG_ORIGIN.format(room_id=room_id, viewer_id=viewer_id, viewer_msg_id=viewer_msg_id))
    if not val:
        return None
    sender_str, msg_str = val.split(":")
    return int(sender_str), int(msg_str)


async def record_room_message(room_id: int, user_id: int, message_id: int) -> None:
    key = KEY_ROOM_HISTORY.format(room_id=room_id, user_id=user_id)
    await r.rpush(key, message_id)
    await r.expire(key, TTL_MESSAGE_MAP)

    users_key = KEY_ROOM_HISTORY_USERS.format(room_id=room_id)
    await r.sadd(users_key, user_id)
    await r.expire(users_key, TTL_MESSAGE_MAP)


async def pop_room_history(room_id: int, user_id: int) -> list[int]:
    key = KEY_ROOM_HISTORY.format(room_id=room_id, user_id=user_id)
    ids = await r.lrange(key, 0, -1)
    await r.delete(key)
    return [int(i) for i in ids]


async def get_room_history_user_ids(room_id: int) -> set[int]:
    """هرکسی که تا حالا تو این اتاق پیامی record شده، صرف‌نظر از اینکه
    الان هنوز عضوه یا نه (ترک کرده/اخراج شده/بن شده). برای اینکه
    پاک‌سازیِ کاملِ تاریخچه بعدِ حذفِ اتاق، اعضای سابق رو هم بگیره."""
    members = await r.smembers(KEY_ROOM_HISTORY_USERS.format(room_id=room_id))
    return {int(uid) for uid in members}


async def clear_room_history_users(room_id: int) -> None:
    await r.delete(KEY_ROOM_HISTORY_USERS.format(room_id=room_id))


# بعد از حذفِ اتاق، ChatRoomMemberِ Postgres پاک می‌شه، پس دیگه راهی
# نیست بفهمیم «کیا عضو بودن» تا تاریخچه‌شون رو پاک کنیم. این کلید
# لیستِ اعضا رو لحظه‌ی حذف نگه می‌داره تا دکمه‌ی «پاک‌کردنِ تاریخچه»
# (که به owner نشون داده می‌شه) بدونه کجا رو بگرده. برخلافِ خودِ
# پیام‌ها (که TTLشون تا سقفِ حذفِ تلگرام، ۴۸ ساعت، زنده‌ست)، این
# پیشنهاد فقط ۲ دقیقه معتبره — هم‌راستا با پنجره‌ی پاکسازی/گزارشِ
# بعدِ پایانِ چتِ ۱به۱.
KEY_DELETED_ROOM_MEMBERS = "bluechat:deleted_room_members:{room_id}"
TTL_ROOM_PURGE_OFFER = 60 * 2  # ۲ دقیقه مهلتِ معتبربودنِ پیشنهادِ پاک‌سازیِ تاریخچه‌ی اتاق


async def store_deleted_room_members(room_id: int, member_ids: list[int]) -> None:
    await r.set(
        KEY_DELETED_ROOM_MEMBERS.format(room_id=room_id),
        json.dumps(member_ids),
        ex=TTL_ROOM_PURGE_OFFER,
    )


async def get_deleted_room_members(room_id: int) -> Optional[list[int]]:
    val = await r.get(KEY_DELETED_ROOM_MEMBERS.format(room_id=room_id))
    return json.loads(val) if val else None


# --- صف جاب‌های AI (برای worker.py) ---
KEY_AI_JOBS = "bluechat:ai_jobs"


async def push_ai_job(job: dict) -> None:
    await r.lpush(KEY_AI_JOBS, json.dumps(job, ensure_ascii=False))


async def pop_ai_job(timeout: int = 5) -> Optional[dict]:
    result = await r.brpop(KEY_AI_JOBS, timeout=timeout)
    if result is None:
        return None
    _, data = result
    return json.loads(data)
