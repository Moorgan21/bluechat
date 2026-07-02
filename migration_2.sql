-- Migration برای قابلیت‌های جدید: پروفایلِ عمومی (/user_<code>)، حالتِ
-- سایلنت، درخواستِ چت، و سیستمِ واکنش با تگ.
-- idempotent است (اجرای چندباره مشکلی ایجاد نمی‌کنه).

ALTER TABLE users ADD COLUMN IF NOT EXISTS reactions_enabled BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS is_silent BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS reaction_tags (
    id SERIAL PRIMARY KEY,
    owner_id BIGINT NOT NULL,
    label VARCHAR(32) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT now(),
    CONSTRAINT uq_owner_tag_label UNIQUE (owner_id, label)
);

CREATE TABLE IF NOT EXISTS reaction_logs (
    id SERIAL PRIMARY KEY,
    owner_id BIGINT NOT NULL,
    sender_id BIGINT NOT NULL,
    tag_id INTEGER NOT NULL REFERENCES reaction_tags(id),
    tag_label VARCHAR(32) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT now()
);
