"""
لایه‌ی Redis — برای صف matching، جفت‌های فعال، و کش سریع
------------------------------------------------------------
چرا Redis برای این بخش؟
    matching و جفت‌شدنِ کاربرها باید خیلی سریع و atomic باشه (چند
    کاربر ممکنه هم‌زمان /start بزنن). Postgres برای query سریع صف
    انتظار مناسب نیست، ولی Redis با ساختارهای in-memory (list/hash)
    برای این کار عالیه. اطلاعات دائمی (پروفایل، سکه، آمار) توی
    Postgres می‌مونه؛ Redis فقط state لحظه‌ایِ گفتگو رو نگه می‌داره.

نیازمندی‌ها:
    pip install redis[hiredis]

تنظیم اتصال:
    export REDIS_URL="redis://localhost:6379/0"
"""

import json
import os
import random
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
KEY_WAITING_QUEUE_BY_GENDER = "melogap:waiting_queue:{gender}"  # ZSET: user_id -> timestamp ورود
KEY_USER_DESIRED_GENDER = "melogap:desired_gender:{user_id}"    # user_id -> ترجیح جنسیتیِ این round
KEY_QUEUE_PIN_MSG = "melogap:queue_pin:{user_id}"               # user_id -> message_id پیامِ پین‌شده‌ی صف

PARTNER_GENDERS = ("male", "female")  # جنسیت‌های قابل‌جستجو در صف (unset وارد matching نمی‌شه)
QUEUE_TIMEOUT_SECONDS = 120  # ۲ دقیقه مهلت قبل از خروج خودکار از صف

KEY_PARTNER = "melogap:partner:{user_id}"            # user_id -> partner_id فعلی
KEY_MESSAGE_MAP = "melogap:msgmap:{user_id}:{msg_id}"  # (user,msg) -> "partner_id:partner_msg_id"
KEY_CHAT_HISTORY = "melogap:history:{user_id}"       # لیست message_id های یک گفتگو
KEY_OWN_SENT_MSGS = "melogap:own_sent:{user_id}"     # SET: message_id هایی که خودِ کاربر فرستاده (برای حذف)
KEY_SECURE_CHAT = "melogap:secure:{user_id}"         # چت امن per-user: پیام‌های خودِ کاربر protect می‌شن
KEY_PENDING_DELETE = "melogap:pending_delete:{pair_key}"  # set از user_id هایی که تایید کردن
KEY_SESSION_ID = "melogap:session_id:{user_id}"      # آی‌دی رکورد ChatSession جاری در Postgres
KEY_LAST_SEEN = "melogap:last_seen:{user_id}"        # timestamp آخرین فعالیت کاربر در ربات
KEY_CHAT_START = "melogap:chat_start:{user_id}"      # timestamp شروع چت جاری
KEY_CHAT_PAYER = "melogap:chat_payer:{user_id}"              # کاربر برای این چت سکه خرج کرده
KEY_CHAT_MSG_COUNT = "melogap:chat_msgs:{pair_key}"          # تعداد کل پیام‌های رد و بدل شده

MIN_CHAT_SECONDS = 10  # حداقل مدت حضور در چت قبل از اجازه‌ی بستن
CHAT_COIN_COST = 2              # هزینه‌ی جستجو با فیلتر جنسیت

# --- کلیدهای پیام‌های ناشناسِ نوتیفی (از طریق لینک ناشناس مستقیم) ---
# هر پیامی که از طریق لینک مستقیم ارسال می‌شه، یه note_id یکتا می‌گیره
# که فرستنده‌ی واقعیش رو نگه می‌داره (بدون اینکه سشن/جفت‌شدنِ دائمی
# ایجاد بشه). صاحب لینک زیر هر پیام یه دکمه‌ی «پاسخ» می‌بینه که با
# همین note_id شناسایی می‌شه.
KEY_NOTE_SENDER = "melogap:note:{note_id}"           # note_id -> sender_id
KEY_NOTE_MESSAGE = "melogap:note_msg:{note_id}"      # note_id -> "chat_id:message_id" پیام اصلی
KEY_AWAITING_REPLY = "melogap:awaiting_reply:{owner_id}"  # owner_id -> note_id ای که منتظر پاسخشه


TTL_LAST_SEEN = 60 * 60 * 24 * 30   # ۳۰ روز نگه می‌داره آخرین زمان آنلاین
TTL_MESSAGE_MAP = 60 * 60 * 24 * 2  # ۴۸ ساعت (هم‌راستا با محدودیت حذف پیام تلگرام)
TTL_PENDING_DELETE = 60 * 60 * 6    # ۶ ساعت فرصت برای تایید حذف دوطرفه
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


async def pop_matching_waiting(user_id: int, desired_gender: Optional[str]) -> Optional[int]:
    own_gender = await _user_gender_str(user_id)
    search_genders = [desired_gender] if desired_gender else list(PARTNER_GENDERS)

    candidates: list[int] = []
    for gender in search_genders:
        members = await r.zrange(KEY_WAITING_QUEUE_BY_GENDER.format(gender=gender), 0, -1)
        candidates.extend(int(m) for m in members if int(m) != user_id)

    if not candidates:
        return None

    random.shuffle(candidates)
    for candidate_id in candidates:
        candidate_desired = await get_desired_gender(candidate_id)
        if candidate_desired is None or candidate_desired == own_gender:
            if await dequeue(candidate_id):
                return candidate_id

    return None


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


async def record_message(user_id: int, message_id: int) -> None:
    key = KEY_CHAT_HISTORY.format(user_id=user_id)
    await r.rpush(key, message_id)
    await r.expire(key, TTL_MESSAGE_MAP)


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


async def pop_history(user_id: int) -> list[int]:
    key = KEY_CHAT_HISTORY.format(user_id=user_id)
    ids = await r.lrange(key, 0, -1)
    await r.delete(key)
    return [int(i) for i in ids]


def pair_key(user_a: int, user_b: int) -> str:
    lo, hi = sorted((user_a, user_b))
    return f"{lo}:{hi}"


async def start_pending_delete(user_a: int, user_b: int) -> None:
    """یه کلید «فعال‌بودن درخواست» با TTL می‌سازه، و ست تاییدکننده‌ها رو
    خالی می‌کنه. وجودِ کلید active معیار معتبربودنِ درخواسته."""
    key = KEY_PENDING_DELETE.format(pair_key=pair_key(user_a, user_b))
    await r.delete(key)
    await r.set(f"{key}:active", "1", ex=TTL_PENDING_DELETE)


async def confirm_pending_delete(user_a: int, user_b: int, confirmer_id: int) -> Optional[set[int]]:
    """confirmer رو به ست تاییدکننده‌ها اضافه می‌کنه. اگه کلید active وجود
    نداشته باشه (منقضی شده یا هیچ‌وقت start نشده) None برمی‌گردونه."""
    key = KEY_PENDING_DELETE.format(pair_key=pair_key(user_a, user_b))
    if not await r.exists(f"{key}:active"):
        return None
    await r.sadd(key, confirmer_id)
    await r.expire(key, TTL_PENDING_DELETE)
    members = await r.smembers(key)
    return {int(m) for m in members}


async def get_pending_delete_set(user_a: int, user_b: int) -> Optional[set[int]]:
    key = KEY_PENDING_DELETE.format(pair_key=pair_key(user_a, user_b))
    members = await r.smembers(key)
    return {int(m) for m in members} if members else None


async def clear_pending_delete(user_a: int, user_b: int) -> None:
    key = KEY_PENDING_DELETE.format(pair_key=pair_key(user_a, user_b))
    await r.delete(key)
    await r.delete(f"{key}:active")


async def set_session_id(user_a: int, user_b: int, session_id: int) -> None:
    await r.set(KEY_SESSION_ID.format(user_id=user_a), session_id)
    await r.set(KEY_SESSION_ID.format(user_id=user_b), session_id)


async def get_session_id(user_id: int) -> Optional[int]:
    val = await r.get(KEY_SESSION_ID.format(user_id=user_id))
    return int(val) if val else None


async def clear_session_id(user_id: int) -> None:
    await r.delete(KEY_SESSION_ID.format(user_id=user_id))


# ---------------------------------------------------------------------------
# وضعیت آنلاین / آخرین بازدید
# ---------------------------------------------------------------------------
async def update_last_seen(user_id: int) -> None:
    import time
    await r.set(KEY_LAST_SEEN.format(user_id=user_id), str(time.time()), ex=TTL_LAST_SEEN)


async def get_last_seen(user_id: int) -> Optional[float]:
    val = await r.get(KEY_LAST_SEEN.format(user_id=user_id))
    return float(val) if val else None


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


# ---------------------------------------------------------------------------
# پیام‌های ناشناسِ نوتیفی (لینک ناشناس مستقیم بدون ورود به یک چت کامل)
# ---------------------------------------------------------------------------
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




# ---------------------------------------------------------------------------
# درخواستِ چت از طریق پروفایلِ عمومی (/user_<code>)
# ---------------------------------------------------------------------------
# جریان: کاربر A روی «💬 درخواست چت» زیر پروفایلِ B (باز شده با
# /user_<code>) می‌زنه → B فقط یه نوتیفِ کوتاه می‌گیره («درخواست چت از
# طرف /user_<code_A>») با دکمه‌ی «مشاهده درخواست چت» → وقتی B اون
# دکمه رو می‌زنه، پیامِ کامل با دکمه‌های قبول/رد نمایش داده می‌شه. این
# دو-مرحله‌ای‌بودن باعث می‌شه هویتِ درخواست‌کننده تا وقتی خودِ B
# نخواد، فاش نشه (فقط شناسه‌ی عمومیش دیده می‌شه، نه محتوای پیام یا
# چیزِ دیگه).
KEY_CHAT_REQUEST = "melogap:chatreq:{request_id}"  # request_id -> requester_id
TTL_CHAT_REQUEST = 60 * 60 * 24  # یک روز اعتبار برای یک درخواستِ چت


async def create_chat_request(requester_id: int) -> str:
    import uuid

    request_id = uuid.uuid4().hex[:12]
    await r.set(KEY_CHAT_REQUEST.format(request_id=request_id), requester_id, ex=TTL_CHAT_REQUEST)
    return request_id


async def get_chat_request_requester(request_id: str) -> Optional[int]:
    val = await r.get(KEY_CHAT_REQUEST.format(request_id=request_id))
    return int(val) if val else None


async def clear_chat_request(request_id: str) -> None:
    await r.delete(KEY_CHAT_REQUEST.format(request_id=request_id))
