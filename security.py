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

"""
ایمن‌سازی ورودی‌های کاربر
"""

import html
import re
import unicodedata

# کاراکترهای کنترلی و null byte
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# الگوهای مشکوک (تلاش برای HTML injection در فیلدهای متنی)
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def sanitize_text(text: str, max_len: int = 512, strip_html: bool = True) -> str:
    """پاک‌سازی پایه‌ی متن ورودی: حذف کنترل‌کاراکتر، null byte و whitespace اضافه."""
    if not isinstance(text, str):
        return ""
    # حذف null byte و کنترل‌کاراکترها
    text = _CONTROL_RE.sub("", text)
    # normalize unicode (جلوگیری از homograph attack)
    text = unicodedata.normalize("NFC", text)
    # strip کردن whitespace
    text = text.strip()
    # برش طول
    text = text[:max_len]
    return text


def sanitize_name(text: str) -> str:
    """نام نمایشی: حداکثر ۲۴ کاراکتر، بدون newline."""
    text = sanitize_text(text, max_len=24)
    text = text.replace("\n", " ").replace("\r", "")
    return text


def sanitize_bio(text: str) -> str:
    """بیو: حداکثر ۱۵۰ کاراکتر."""
    return sanitize_text(text, max_len=150)


def sanitize_tag(text: str) -> str:
    """تگ واکنش: حداکثر ۲۰ کاراکتر، بدون فاصله."""
    text = sanitize_text(text, max_len=20)
    text = re.sub(r"\s+", "", text)
    return text


def escape_html(text: str) -> str:
    """escape برای استفاده در parse_mode=HTML."""
    return html.escape(str(text))
