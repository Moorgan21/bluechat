
# 💬 Blue Chat

![GitHub License](https://img.shields.io/github/license/Moorgan21/bluechat?color=007bff)
![Python Version](https://img.shields.io/badge/python-3.11+-blue.svg?logo=python&logoColor=white)
![Docker Compatible](https://img.shields.io/badge/docker-compatible-cyan.svg?logo=docker&logoColor=white)
![Architecture](https://img.shields.io/badge/architecture-Async--Worker-orange)
![Monitoring](https://img.shields.io/badge/monitoring-Prometheus%20%26%20Grafana-green?logo=grafana&logoColor=white)

ربات تلگرام برای گفتگوی ناشناس — کاربران به صورت تصادفی یا هدفمند به هم وصل می‌شن و چت می کنند.

> **نمونه اجرا شده:** [@Blluchatbot](https://t.me/Blluchatbot)

---

## ✨ امکانات

### 💬 چت ناشناس

- **اتصال تصادفی** — وصل شدن فوری به یه کاربر ناشناس با یه دکمه

- **جستجوی هدفمند** — فیلتر بر اساس جنسیت، بازه سنی، استان و شهر

- **درخواست چت** — ارسال درخواست چت به کاربر خاص از طریق پروفایل عمومی

- **چت امن** — پیام‌های غیرقابل فوروارد و ذخیره با `protect_content` تلگرام؛ هر کاربر مستقلاً برای پیام‌های خودش فعال می‌کنه

- **حذف پیام** — ارسال «حذف» یا «del» به‌عنوان ریپلای روی پیام ارسالی خودت، پیام رو از هر دو طرف پاک می‌کنه

- **ویرایش پیام** — ویرایش پیام متنی برای طرف مقابل هم اعمال میشه با برچسب «✏️ ویرایش شده · HH:MM»

- **حداقل زمان چت** — امکان بستن چت قبل از ۱۰ ثانیه وجود نداره

- **تأیید بستن چت** — قبل از پایان چت، پیام تأیید با دو دکمه نمایش داده می‌شه

<br>

### 🔧 دستورات

- `/stop` — پایان دادن به چت جاری یا خروج از صف انتظار

- `/next` — پایان چت فعلی و جستجوی فوری برای همراه جدید

- `/settings` — تنظیمات شخصی (ترجیح جنسیت، حریم خصوصی و غیره)

<br>

### 👤 پروفایل

- **پروفایل عمومی** — نام نمایشی، بیوگرافی، جنسیت، سن، استان، شهر و عکس پروفایل

- **آنلاین بودن** — نمایش وضعیت آنلاین یا آخرین بازدید (مثل «۵ دقیقه پیش»)

- **انتخاب استان/شهر** — کیبورد اینلاین با لیست کامل ۳۱ استان و تمام شهرهای ایران

- **لینک اختصاصی** — هر کاربر یه لینک `/u_<code>` داره برای اشتراک‌گذاری پروفایل

<br>

### 📩 پیام ناشناس

- **نوت ناشناس** — ارسال پیام ناشناس از طریق لینک اختصاصی کاربر

- **پیام دایرکت** — ارسال پیام با حفظ هویت فرستنده (برای دوستان)

- **پاسخ ناشناس** — صاحب لینک می‌تونه به پیام ناشناس پاسخ بده

<br>

### 🪙 سیستم سکه

- **سکه‌ی هدیه** — ۱۰ سکه در شروع به همه کاربران

- **معرفی دوستان** — دریافت سکه با معرفی کاربر جدید از طریق لینک رفرال

- **جستجوی با فیلتر جنسیت** — انتخاب دختر یا پسر ۲ سکه هزینه دارد؛ «فرقی نمی‌کنه» رایگان است

- **بازگشت خودکار سکه** — اگه چت کمتر از ۳ پیام داشته باشه (ناموفق)، یا جستجو لغو/تایم‌اوت بشه، سکه برگشت داده می‌شه

- **تاریخچه‌ی تراکنش‌ها** — تمام واریز و برداشت‌های سکه در دیتابیس ثبت می‌شن

<br>

### 🤖 هوش مصنوعی

- **مدیریت محتوا** — بررسی خودکار عکس پروفایل با Google Gemini

- **قضاوت گزارش** — تحلیل تاریخچه‌ی چت و قضاوت گزارش‌های کاربران با DeepSeek

- **اخطار و بن خودکار** — ۵ اخطار = بن خودکار توسط سیستم AI

<br>

### 🛡 امنیت

- **آنتی‌اسپم** — نرخ‌سنج لغزنده (sliding window) با Redis؛ محدودیت ۱۲ پیام در ۵ ثانیه، ۳۰ پیام در ۳۰ ثانیه (flood)، ۸ callback در ۱۰ ثانیه — تخطی = بلاک موقت ۶۰ ثانیه‌ای

- **پاک‌سازی ورودی** — حذف null byte، کنترل‌کاراکترها و normalize یونیکد (NFC) روی تمام فیلدهای متنی کاربر

- **جلوگیری از HTML injection** — تمام داده‌های کاربر قبل از درج در پیام‌های HTML-mode با `html.escape` escape می‌شن

- **محدودیت طول فیلد** — نام نمایشی ۲۴ کاراکتر، بیو ۱۵۰ کاراکتر، تگ واکنش ۲۰ کاراکتر (server-side)

<br>

### 📍 افراد نزدیک

- یافتن کاربران در محدوده‌ی جغرافیایی با PostGIS

- ذخیره‌ی موقعیت مکانی با رضایت کاربر

---

## 🛠 تکنولوژی‌ها

| لایه | ابزار |
|------|-------|
| زبان | Python 3.11 |
| فریم‌ورک ربات | python-telegram-bot 21 (async) |
| دیتابیس | PostgreSQL + PostGIS |
| ORM | SQLAlchemy (async) + asyncpg |
| کش / real-time | Redis |
| هوش مصنوعی — تصویر | Google Gemini |
| هوش مصنوعی — گزارش | DeepSeek |
| مانیتورینگ | Prometheus + Grafana |
| آنتی‌اسپم | Redis sliding window |
| استقرار | Docker Compose |

---

## 🚀 راه‌اندازی

### پیش‌نیازها
- Docker و Docker Compose
- توکن ربات تلگرام از [@BotFather](https://t.me/BotFather)
- API key برای Google Gemini (بررسی عکس پروفایل و مدیریت محتوا)
- API key برای DeepSeek (تحلیل گزارش‌ها و قضاوت تخلفات)

### مراحل

```bash
# ۱. کلون کن
git clone https://github.com/Moorgan21/bluechat.git
cd bluechat

# ۲. فایل env بساز
cp .env.example .env
# مقادیر .env رو پر کن

# ۳. دیتابیس رو بساز
psql $DATABASE_URL -f schema.sql

# ۴. اجرا کن
docker compose up -d --build
```

### متغیرهای محیطی (.env)

```env
BOT_TOKEN=          # توکن ربات از BotFather
BOT_USERNAME=       # یوزرنیم ربات (بدون @)
DATABASE_URL=       # آدرس PostgreSQL (primary)
READ_DATABASE_URL=  # آدرس read replica — اگه خالی باشه از primary استفاده می‌شه
REDIS_URL=          # آدرس Redis
GEMINI_API_KEY=     # کلید Google Gemini (مدیریت محتوا و تصویر)
DEEPSEEK_API_KEY=   # کلید DeepSeek (قضاوت گزارش‌ها)
GEMINI_RPM=100      # حداکثر درخواست به Gemini در هر دقیقه (پیش‌فرض: ۱۰۰)
DB_POOL_SIZE=20     # اندازه connection pool دیتابیس (پیش‌فرض: ۲۰)
DB_MAX_OVERFLOW=40  # حداکثر اتصال اضافه (پیش‌فرض: ۴۰)
WEBHOOK_URL=        # آدرس کامل webhook — اگه خالی باشه polling استفاده می‌شه
WEBHOOK_SECRET=     # توکن امنیتی webhook (یه رشته تصادفی)
WEBHOOK_PORT=8080   # پورت داخلی bot برای دریافت webhook (پیش‌فرض: ۸۰۸۰)
GRAFANA_PASSWORD=   # رمز ورود Grafana (پیش‌فرض: admin)

# آنتی‌اسپم (اختیاری — مقادیر پیش‌فرض برای اکثر حالت‌ها کافیه)
SPAM_MSG_LIMIT=12         # حداکثر پیام در پنجره‌ی کوتاه
SPAM_MSG_WINDOW=5         # پنجره‌ی کوتاه (ثانیه)
SPAM_FLOOD_LIMIT=30       # حداکثر پیام در پنجره‌ی flood
SPAM_FLOOD_WINDOW=30      # پنجره‌ی flood (ثانیه)
SPAM_CMD_LIMIT=8          # حداکثر callback/دستور
SPAM_CMD_WINDOW=10        # پنجره‌ی callback (ثانیه)
SPAM_BLOCK_DURATION=60    # مدت بلاک موقت (ثانیه)
```

---

## 📊 مانیتورینگ

### معماری

```
bot / worker  ──► prometheus_client (port 8081)  ─┐
node-exporter ──► سیستم (CPU، RAM، دیسک)          ├──► Prometheus ──► Grafana
postgres-exporter ──► دیتابیس                     │
redis-exporter ──► Redis                          ─┘
```

### دسترسی به Grafana

پنل مانیتورینگ از طریق nginx روی آدرس زیر در دسترسه:

```
https://your-domain.com/grafana/
```

- **user:** admin
- **pass:** مقدار `GRAFANA_PASSWORD` در `.env` (پیش‌فرض: admin)

### متریک‌های ربات

| متریک | نوع | توضیح |
|---|---|---|
| `bot_active_chats` | Gauge | تعداد چت‌های در حال اجرا |
| `bot_waiting_users` | Gauge | کاربران در صف انتظار |
| `bot_ai_queue_size` | Gauge | جاب‌های AI در انتظار پردازش |
| `bot_messages_relayed_total` | Counter | کل پیام‌های relay شده |
| `bot_chats_started_total` | Counter | کل چت‌های شروع‌شده |
| `bot_chats_ended_total` | Counter | کل چت‌های پایان‌یافته |
| `bot_ai_jobs_processed_total` | Counter | کل جاب‌های AI پردازش‌شده |
| `bot_spam_blocks_total` | Counter | درخواست‌های مسدودشده توسط spam guard (label: kind) |

### متریک‌های دیتابیس (custom queries)

| متریک | توضیح |
|---|---|
| `pg_users_total_count` | کل کاربران ثبت‌شده |
| `pg_users_total_new_today` | کاربران جدید ۲۴ ساعت اخیر |
| `pg_users_total_new_week` | کاربران جدید هفته اخیر |
| `pg_users_by_gender_count` | تعداد کاربر به تفکیک جنسیت |
| `pg_users_by_province_count` | تعداد کاربر به تفکیک استان |
| `pg_users_by_city_count` | تعداد کاربر به تفکیک شهر |
| `pg_total_coins_total` | مجموع سکه‌های همه کاربران |
| `pg_warnings_total` | کل اخطارهای صادرشده توسط DeepSeek |
| `pg_banned_users_total` | کل کاربران بن‌شده |
| `pg_banned_users_by_deepseek` | بن‌شده‌های خودکار توسط DeepSeek (۵+ اخطار) |
| `pg_gemini_bans_unique_users_banned` | کاربران یونیک بن‌شده توسط Gemini |
| `pg_gemini_bans_profile_report_guilty` | کل احکام guilty توسط Gemini |

### داشبورد پیش‌فرض

داشبورد **Blue Chat Bot** به‌صورت خودکار هنگام راه‌اندازی بارگذاری می‌شه و در ۶ بخش سازمان‌یافته:

| بخش | محتوا |
|---|---|
| ⚡ وضعیت لحظه‌ای | چت فعال، صف انتظار، صف AI، CPU/RAM/دیسک (Gauge) |
| 👥 آمار کاربران | کل/جدید کاربران، سکه‌ها، توزیع جنسیت، جدول شهر/استان |
| 🤖 هوش مصنوعی | اخطارهای DeepSeek، بن‌های Gemini، جاب‌های AI |
| 📈 ترافیک | نرخ پیام relay، چت شروع/پایان |
| 🛡 آنتی اسپم | بلاک‌های ۲۴ ساعت، نرخ لحظه‌ای، timeseries موج حملات |
| 🖥️ زیرساخت | CPU، RAM، Redis، اتصالات PostgreSQL |

---

## ⚡ مقیاس‌پذیری

### ظرفیت فعلی

| معیار | ظرفیت | محدودکننده |
|---|---|---|
| کاربر ثبت‌شده (کل) | نامحدود | PostgreSQL |
| کاربر فعال ماهانه (MAU) | ~۵۰,۰۰۰–۱۰۰,۰۰۰ | زیرساخت سرور |
| کاربر همزمان آنلاین | ~۱,۸۰۰ | Telegram API (30 msg/s رایگان) |
| چت همزمان فعال | ~۱۰۰–۲۰۰ جفت | Telegram API (30 msg/s رایگان) |
| اتصال همزمان به DB | حداکثر ۶۰ | pool_size=20, max_overflow=40 |

> **سقف اصلی:** محدودیت ۳۰ پیام در ثانیه Telegram برای همه ربات‌ها صدق می‌کنه. با فعال‌سازی **Paid Broadcast** در BotFather این سقف به ۱,۰۰۰ msg/s می‌رسد.

### ظرفیت با Paid Broadcast (1,000 msg/s)

| سرعت چت | چت همزمان | کاربر همزمان |
|---|---|---|
| ۱ پیام/ثانیه (خیلی سریع) | ۱,۰۰۰ جفت | ~۲,۰۰۰ نفر |
| ۱ پیام/۵ ثانیه (نرمال) | ۵,۰۰۰ جفت | ~۱۰,۰۰۰ نفر |
| ۱ پیام/۳۰ ثانیه (کند) | ۳۰,۰۰۰ جفت | ~۶۰,۰۰۰ نفر |

هزینه: هر پیام بیشتر از سقف رایگان ۳۰/s برابر **۰.۱ Star** از موجودی ربات کسر می‌شه. نیازی به تغییر کد نیست — فقط از BotFather فعال میشه.

> **نتیجه:** با ترکیب افزایش زیرساخت سرور (CPU، RAM، DB replica، Redis Cluster) و فعال‌سازی Telegram Paid Broadcast، مقیاس‌پذیری ربات عملاً **نامحدود** میشه و هیچ سقف ثابتی وجود نداره.

### ویژگی‌های مقیاس‌پذیری

| ویژگی | جزئیات |
|---|---|
| **Webhook** | تلگرام آپدیت‌ها رو push می‌کنه؛ latency کمتر و overhead polling حذف شده |
| **asyncio غیرمسدودکننده** | تمام I/O async هستن؛ هیچ عملیاتی event loop رو بلاک نمی‌کنه |
| **AI worker جداگانه** | `worker.py` در پروسه‌ی مستقل؛ جاب‌ها روی Redis queue ماندگارن و با restart از دست نمیرن |
| **Rate limiter Gemini** | Token bucket با نرخ قابل تنظیم (`GEMINI_RPM`) از خطای ۴۲۹ جلوگیری می‌کنه |
| **Redis برای state** | جفت‌شدن، صف انتظار و وضعیت چت in-memory نگه داشته می‌شن؛ latency زیر ۱ms |
| **DB connection pool** | پیش‌فرض ۲۰+۴۰ اتصال همزمان؛ قابل تنظیم با `DB_POOL_SIZE` و `DB_MAX_OVERFLOW` |
| **Read replica** | با تنظیم `READ_DATABASE_URL` query های خواندنی به replica هدایت می‌شن |

---

## 🏗 معماری

```
┌─────────────────────────────────────────┐
│              Telegram API               │
└────────────────┬────────────────────────┘
                 │
┌────────────────▼────────────────────────┐
│           python-telegram-bot 21        │
│            (async webhook)              │
└──────┬──────────────┬───────────────────┘
       │              │
┌──────▼──────┐ ┌─────▼──────┐
│  PostgreSQL │ │   Redis    │
│  + PostGIS  │ │  real-time │
│  (دیتا دائم)│ │ (session/  │
│             │ │  matching) │
└──────┬──────┘ └─────┬──────┘
       │              │
┌──────▼──────────────▼───────────────────┐
│         Gemini API + DeepSeek API       │
│    (مدیریت محتوا + قضاوت گزارش‌ها)     │
└─────────────────────────────────────────┘
```

---

## 📁 ساختار پروژه

```
bluechat/
├── main.py               # نقطه‌ی ورود و routing اصلی
├── db.py                 # مدل‌های دیتابیس و توابع
├── redis_client.py       # تمام عملیات Redis
├── keyboards.py          # کیبوردهای inline و reply
├── metrics.py            # متریک‌های Prometheus (counters و gauges)
├── security.py           # پاک‌سازی ورودی، escape HTML، جلوگیری از injection
├── spam_guard.py         # آنتی‌اسپم — sliding window rate limiter با Redis
├── schema.sql            # ساختار کامل دیتابیس (از صفر)
├── handlers/
│   ├── chat.py           # منطق چت ناشناس و matching
│   ├── profile.py        # پروفایل و onboarding
│   ├── public_profile.py # پروفایل عمومی و درخواست چت
│   ├── anon_note.py      # پیام‌های ناشناس و دایرکت
│   ├── search.py         # جستجوی هدفمند با فیلتر
│   ├── nearby.py         # افراد نزدیک (PostGIS)
│   ├── coins.py          # سیستم سکه و رفرال
│   ├── report.py         # گزارش تخلف
│   ├── settings.py       # تنظیمات کاربر
│   └── menu.py           # منوی اصلی
├── judge.py              # قضاوت گزارش‌ها با DeepSeek
├── moderation.py         # بررسی عکس پروفایل با Gemini
├── gemini_limiter.py     # rate limiter برای Gemini API (token bucket)
├── worker.py             # AI worker — پردازش صف قضاوت در پروسه‌ی جداگانه
├── verdict_notify.py     # اطلاع‌رسانی نتیجه‌ی قضاوت (مشترک بین bot و worker)
├── prometheus.yml        # تنظیمات scrape برای Prometheus
├── pg_custom_queries.yml # کوئری‌های سفارشی postgres-exporter
├── grafana/
│   ├── provisioning/
│   │   ├── datasources/  # auto-provision اتصال به Prometheus
│   │   └── dashboards/   # auto-provision مسیر داشبوردها
│   └── dashboards/
│       └── bluechat.json # داشبورد پیش‌فرض Blue Chat Bot
├── iran_cities.json      # لیست ۳۱ استان و تمام شهرهای ایران
├── LICENSE               # GNU Affero General Public License v3 (AGPL-3.0)
└── requirements.txt      # وابستگی‌های Python
```

---

## 📜 لایسنس

Copyright (C) 2026 Dariush Lashani

این پروژه تحت مجوز **GNU Affero General Public License v3.0** منتشر شده.

برای جزئیات کامل فایل LICENSE را ببینید یا به [gnu.org/licenses/agpl-3.0](https://www.gnu.org/licenses/agpl-3.0) مراجعه کنید.
