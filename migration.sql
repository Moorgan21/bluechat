-- Migration دستی برای همگام‌کردن schema دیتابیس با مدل‌های جدید پایتون.
-- این اسکریپت idempotent است (چند بار اجرا کردنش مشکلی ایجاد نمی‌کنه)
-- چون همه‌جا از IF NOT EXISTS استفاده شده.

-- ستون‌های جدید روی جدول users
ALTER TABLE users ADD COLUMN IF NOT EXISTS warning_count INTEGER NOT NULL DEFAULT 0;

-- ستون جدید روی جدول chat_sessions
ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS history_deleted BOOLEAN NOT NULL DEFAULT FALSE;

-- ستون‌های جدید روی جدول reports (نتیجه‌ی قضاوت AI)
DO $$ BEGIN
    CREATE TYPE reportverdict AS ENUM ('pending', 'guilty', 'dismissed', 'no_history');
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

ALTER TABLE reports ADD COLUMN IF NOT EXISTS verdict reportverdict NOT NULL DEFAULT 'pending';
ALTER TABLE reports ADD COLUMN IF NOT EXISTS verdict_reason TEXT;
ALTER TABLE reports ADD COLUMN IF NOT EXISTS verdict_at TIMESTAMP;

-- جدول جدید: متن پیام‌های چت (برای قضاوت AI)
CREATE TABLE IF NOT EXISTS chat_messages (
    id SERIAL PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES chat_sessions(id),
    sender_id BIGINT NOT NULL,
    content TEXT,
    content_type VARCHAR(32) NOT NULL DEFAULT 'text',
    created_at TIMESTAMP NOT NULL DEFAULT now()
);

-- جدول جدید: اخطارها
CREATE TABLE IF NOT EXISTS warnings (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    report_id INTEGER REFERENCES reports(id),
    warning_number INTEGER NOT NULL,
    reason TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT now()
);

-- جدول جدید: بلاک‌کردن فرستنده‌های لینک ناشناس
CREATE TABLE IF NOT EXISTS blocked_senders (
    id SERIAL PRIMARY KEY,
    owner_id BIGINT NOT NULL,
    sender_id BIGINT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT now(),
    CONSTRAINT uq_owner_sender_block UNIQUE (owner_id, sender_id)
);

-- جدول جدید: گزارش پروفایل
CREATE TABLE IF NOT EXISTS profile_reports (
    id SERIAL PRIMARY KEY,
    reporter_id BIGINT NOT NULL,
    reported_id BIGINT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT now(),
    verdict reportverdict NOT NULL DEFAULT 'pending',
    verdict_reason TEXT,
    verdict_at TIMESTAMP
);
