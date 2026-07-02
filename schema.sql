-- Blue Chat — Database Schema
-- اجرای این فایل دیتابیس رو از صفر می‌سازه (idempotent)

-- فعال‌سازی افزونه‌ی PostGIS برای موقعیت مکانی
CREATE EXTENSION IF NOT EXISTS postgis;

-- -------------------------------------------------------
-- Enums
-- -------------------------------------------------------

DO $$ BEGIN
    CREATE TYPE gender AS ENUM ('male', 'female', 'unset');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE reportreason AS ENUM ('spam', 'scam', 'abuse', 'sexual', 'fake_profile', 'other');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE reportverdict AS ENUM ('pending', 'guilty', 'dismissed', 'no_history');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- -------------------------------------------------------
-- users
-- -------------------------------------------------------

CREATE TABLE IF NOT EXISTS users (
    id                   BIGINT PRIMARY KEY,
    username             VARCHAR(64),
    first_name           VARCHAR(128),

    -- پروفایل داخل ربات
    display_name         VARCHAR(64),
    bio                  VARCHAR(512),
    gender               gender NOT NULL DEFAULT 'unset',
    age                  INTEGER,
    province             VARCHAR(50),
    city                 VARCHAR(50),
    photo_file_id        VARCHAR(256),
    photo_approved_at    TIMESTAMP,

    -- اقتصاد
    coins                INTEGER NOT NULL DEFAULT 10,

    -- موقعیت مکانی (PostGIS)
    location             GEOGRAPHY(POINT, 4326),
    location_updated_at  TIMESTAMP,

    -- لینک ناشناس
    referral_code        VARCHAR(16) UNIQUE NOT NULL,
    invited_by           BIGINT,

    -- وضعیت
    is_banned            BOOLEAN NOT NULL DEFAULT FALSE,
    warning_count        INTEGER NOT NULL DEFAULT 0,
    reactions_enabled    BOOLEAN NOT NULL DEFAULT FALSE,
    is_silent            BOOLEAN NOT NULL DEFAULT FALSE,
    next_gender_pref     VARCHAR(8),

    -- آمار
    total_chats          INTEGER NOT NULL DEFAULT 0,
    total_reports_received INTEGER NOT NULL DEFAULT 0,

    created_at           TIMESTAMP NOT NULL DEFAULT now()
);

-- -------------------------------------------------------
-- chat_sessions
-- -------------------------------------------------------

CREATE TABLE IF NOT EXISTS chat_sessions (
    id              SERIAL PRIMARY KEY,
    user_a_id       BIGINT NOT NULL,
    user_b_id       BIGINT NOT NULL,
    started_at      TIMESTAMP NOT NULL DEFAULT now(),
    ended_at        TIMESTAMP,
    ended_by        BIGINT,
    was_successful  BOOLEAN NOT NULL DEFAULT FALSE,
    history_deleted BOOLEAN NOT NULL DEFAULT FALSE
);

-- -------------------------------------------------------
-- chat_messages
-- -------------------------------------------------------

CREATE TABLE IF NOT EXISTS chat_messages (
    id           SERIAL PRIMARY KEY,
    session_id   INTEGER NOT NULL REFERENCES chat_sessions(id),
    sender_id    BIGINT NOT NULL,
    content      TEXT,
    content_type VARCHAR(32) NOT NULL DEFAULT 'text',
    created_at   TIMESTAMP NOT NULL DEFAULT now()
);

-- -------------------------------------------------------
-- reports
-- -------------------------------------------------------

CREATE TABLE IF NOT EXISTS reports (
    id             SERIAL PRIMARY KEY,
    reporter_id    BIGINT NOT NULL,
    reported_id    BIGINT NOT NULL,
    session_id     INTEGER REFERENCES chat_sessions(id),
    reason         reportreason NOT NULL,
    verdict        reportverdict NOT NULL DEFAULT 'pending',
    verdict_reason TEXT,
    verdict_at     TIMESTAMP,
    created_at     TIMESTAMP NOT NULL DEFAULT now()
);

-- -------------------------------------------------------
-- profile_reports
-- -------------------------------------------------------

CREATE TABLE IF NOT EXISTS profile_reports (
    id             SERIAL PRIMARY KEY,
    reporter_id    BIGINT NOT NULL,
    reported_id    BIGINT NOT NULL,
    verdict        reportverdict NOT NULL DEFAULT 'pending',
    verdict_reason TEXT,
    verdict_at     TIMESTAMP,
    created_at     TIMESTAMP NOT NULL DEFAULT now()
);

-- -------------------------------------------------------
-- warnings
-- -------------------------------------------------------

CREATE TABLE IF NOT EXISTS warnings (
    id             SERIAL PRIMARY KEY,
    user_id        BIGINT NOT NULL,
    report_id      INTEGER REFERENCES reports(id),
    warning_number INTEGER NOT NULL,
    reason         TEXT NOT NULL,
    created_at     TIMESTAMP NOT NULL DEFAULT now()
);

-- -------------------------------------------------------
-- blocked_senders
-- -------------------------------------------------------

CREATE TABLE IF NOT EXISTS blocked_senders (
    id         SERIAL PRIMARY KEY,
    owner_id   BIGINT NOT NULL,
    sender_id  BIGINT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT now(),
    CONSTRAINT uq_owner_sender_block UNIQUE (owner_id, sender_id)
);

-- -------------------------------------------------------
-- reaction_tags
-- -------------------------------------------------------

CREATE TABLE IF NOT EXISTS reaction_tags (
    id         SERIAL PRIMARY KEY,
    owner_id   BIGINT NOT NULL,
    label      VARCHAR(32) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT now(),
    CONSTRAINT uq_owner_tag_label UNIQUE (owner_id, label)
);

-- -------------------------------------------------------
-- reaction_logs
-- -------------------------------------------------------

CREATE TABLE IF NOT EXISTS reaction_logs (
    id         SERIAL PRIMARY KEY,
    owner_id   BIGINT NOT NULL,
    sender_id  BIGINT NOT NULL,
    tag_id     INTEGER NOT NULL REFERENCES reaction_tags(id),
    tag_label  VARCHAR(32) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT now()
);
