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

"""هندلرهای هسته‌ی چت ناشناس. این پکیج قبلاً یه فایلِ ۸۰۰ خطیِ تکی
(handlers/chat.py) بود و برای خوانایی به ۴ تا شکسته شده: matching.py
(انتخاب جنسیت، صف‌بندی، matching)، session.py (پایانِ چت، تاییدِ پایان،
ثبتِ سشن)، relay.py (انتقالِ پیام/ویرایش/ریکشن بین دو طرف) و extras.py
(پروفایلِ طرفِ مقابل، چتِ امن، پاک‌سازیِ تاریخچه).

این __init__ همه‌چیز رو دوباره export می‌کنه تا main.py و
handlers/search.py که با `chat.stop_chat`، `chat.relay_message` یا
`from handlers.chat import try_match` صداش می‌زنن، بدونِ تغییر کار کنن؛
این فقط یه تقسیمِ داخلیه، نه تغییرِ API.
"""

from .extras import (
    handle_delete_history_callback,
    offer_history_deletion,
    show_partner_profile,
    toggle_secure_chat_button,
)
from .matching import (
    check_room_conflict,
    handle_desired_gender_callback,
    start_chat,
    try_match,
)
from .relay import (
    relay_edit,
    relay_message,
    relay_reaction,
)
from .session import (
    end_chat_button,
    end_chat_confirm_callback,
    handle_cancel_queue_button,
    next_chat,
    stop_chat,
)

__all__ = [
    "handle_delete_history_callback",
    "offer_history_deletion",
    "show_partner_profile",
    "toggle_secure_chat_button",
    "check_room_conflict",
    "handle_desired_gender_callback",
    "start_chat",
    "try_match",
    "relay_edit",
    "relay_message",
    "relay_reaction",
    "end_chat_button",
    "end_chat_confirm_callback",
    "handle_cancel_queue_button",
    "next_chat",
    "stop_chat",
]
