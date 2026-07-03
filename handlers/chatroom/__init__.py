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
مسئولیت شکسته می‌شه. فعلاً فقط creation.py (فلوی ساختِ اتاق) هست؛
matching.py (عضویت)، relay.py، moderation.py و membership.py توی
فازهای بعدی اضافه می‌شن.
"""

from .creation import (
    room_menu_callback_router,
    show_room_menu,
)

__all__ = [
    "room_menu_callback_router",
    "show_room_menu",
]
