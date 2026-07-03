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

"""پکیجِ هندلرهای اتاقِ چت، مثلِ handlers/chat/ به فایل‌های جدا بر اساسِ
مسئولیت شکسته می‌شه: creation.py (فلوی ساختِ اتاق)، matching.py
(فلوی عضویت)، relay.py (رله‌ی پیام داخلِ اتاق)، membership.py (ترکِ
اتاق توسطِ عضوِ عادی)، moderation.py (فعلاً فقط حذفِ اتاق توسطِ owner؛
بقیه‌ی ابزارهای owner — حذفِ پیامِ دیگران، اخراج، بستن/بازکردن — بعداً
اضافه می‌شن).
"""

from .creation import (
    room_menu_callback_router,
    show_room_menu,
)
from .matching import sweep_room_join_queue
from .relay import relay_room_edit, relay_room_message, toggle_secure_chat_button
from .membership import leave_room_button
from .moderation import delete_room_button, delete_room_confirm_callback

__all__ = [
    "delete_room_button",
    "delete_room_confirm_callback",
    "leave_room_button",
    "relay_room_edit",
    "relay_room_message",
    "room_menu_callback_router",
    "show_room_menu",
    "sweep_room_join_queue",
    "toggle_secure_chat_button",
]
