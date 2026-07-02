-- migration_3: افزودن ستون ترجیح جنسیت برای matching
ALTER TABLE users ADD COLUMN IF NOT EXISTS next_gender_pref VARCHAR(8) DEFAULT NULL;
