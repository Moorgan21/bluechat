# 💬 Blue Chat

ربات تلگرام برای گفتگوی ناشناس — کاربران به صورت تصادفی یا هدفمند به هم وصل می‌شن و بدون افشای هویت چت می‌کنن.

---

## ✨ امکانات

- **چت ناشناس تصادفی** — اتصال فوری به یه کاربر ناشناس
- **جستجوی هدفمند** — فیلتر بر اساس جنسیت، سن، استان و شهر
- **پروفایل عمومی** — نام نمایشی، بیوگرافی، عکس، استان/شهر و آنلاین بودن
- **چت امن** — پیام‌های غیرقابل فوروارد و ذخیره با `protect_content`
- **پیام ناشناس** — ارسال نوت ناشناس از طریق لینک اختصاصی
- **سیستم سکه** — جوایز معرفی و امکانات ویژه
- **گزارش و مدیریت** — سیستم گزارش پروفایل و محتوا
- **واکنش‌ها** — ارسال واکنش روی پروفایل عمومی
- **افراد نزدیک** — یافتن کاربران بر اساس موقعیت جغرافیایی

---

## 🛠 تکنولوژی‌ها

| لایه | ابزار |
|------|-------|
| زبان | Python 3.11 |
| فریم‌ورک ربات | python-telegram-bot 21 (async) |
| دیتابیس | PostgreSQL + PostGIS |
| ORM | SQLAlchemy (async) |
| کش / real-time | Redis |
| هوش مصنوعی | Google Gemini + DeepSeek |
| استقرار | Docker Compose |

---

## 🚀 راه‌اندازی

### پیش‌نیازها
- Docker و Docker Compose
- توکن ربات تلگرام از [@BotFather](https://t.me/BotFather)
- API key برای Gemini (برای مدیریت و مدیریت محتوا)
- API key برای DeepSeek (برای قضاوت گزارش‌ها)

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

### متغیرهای محیطی (`.env`)

```env
BOT_TOKEN=          # توکن ربات از BotFather
BOT_USERNAME=       # یوزرنیم ربات (بدون @)
DATABASE_URL=       # آدرس PostgreSQL
REDIS_URL=          # آدرس Redis
GEMINI_API_KEY=     # کلید Gemini (مدیریت محتوا و تصویر)
DEEPSEEK_API_KEY=   # کلید DeepSeek (قضاوت گزارش‌ها)
```

---

## 📁 ساختار پروژه

```
bluechat/
├── main.py               # نقطه‌ی ورود و routing اصلی
├── db.py                 # مدل‌های دیتابیس و توابع
├── redis_client.py       # تمام عملیات Redis
├── keyboards.py          # کیبوردهای inline و reply
├── schema.sql            # ساختار کامل دیتابیس (از صفر)
├── handlers/
│   ├── chat.py           # منطق چت ناشناس
│   ├── profile.py        # پروفایل و onboarding
│   ├── public_profile.py # پروفایل عمومی و درخواست چت
│   ├── anon_note.py      # پیام‌های ناشناس
│   ├── search.py         # جستجوی هدفمند
│   ├── nearby.py         # افراد نزدیک
│   ├── coins.py          # سیستم سکه
│   ├── report.py         # گزارش
│   ├── settings.py       # تنظیمات
│   └── menu.py           # منوی اصلی
├── judge.py              # مدیریت محتوا با AI
├── moderation.py         # فیلتر محتوای نامناسب
├── iran_cities.json      # لیست استان‌ها و شهرهای ایران
├── LICENSE               # All Rights Reserved
└── requirements.txt      # وابستگی‌های Python
```

---

## 📜 لایسنس

این پروژه خصوصی است و تمام حقوق محفوظ می‌باشد.
