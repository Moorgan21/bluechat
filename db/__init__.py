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

"""لایه‌ی دیتابیس. این پکیج قبلاً یه فایلِ ۷۵۱ خطیِ تکی (db.py) بود و
برای خوانایی به ۳ تا شکسته شده: connections.py (engine/session/Base و
init_db)، models.py (enumها و کلاس‌های ORM) و queries.py (توابعِ async
که روی مدل‌ها کار می‌کنن).

این __init__ همه‌چیز رو دوباره export می‌کنه تا بقیه‌ی پروژه که با
`from db import X, Y, Z` یا `db.X` صداش می‌زنه بدونِ تغییر کار کنه؛ این
فقط یه تقسیمِ داخلیه، نه تغییرِ API.
"""

from .connections import (
    Base,
    DATABASE_URL,
    READ_DATABASE_URL,
    async_session,
    engine,
    init_db,
    read_session,
    _read_engine,
)
from .models import (
    BlockedSender,
    ChatMessage,
    ChatRoom,
    ChatRoomMember,
    ChatSession,
    CoinTransaction,
    Gender,
    ProfileReport,
    ReactionLog,
    ReactionTag,
    Report,
    ReportReason,
    ReportVerdict,
    RoomGenderPref,
    RoomStatus,
    User,
    Warning,
)
from .queries import (
    add_reaction_tag,
    add_warning,
    ban_user,
    block_sender,
    clear_photo_file_id,
    create_chat_room,
    deduct_coins,
    delete_reaction_tag,
    find_open_room_for_join,
    get_chat_room,
    get_display_name,
    get_or_create_user,
    get_reaction_counts,
    get_reaction_tag,
    get_room_member_ids,
    get_session_transcript,
    get_user_by_referral_code,
    get_user_profile_snapshot,
    grant_referral_bonus,
    grant_report_reward,
    increment_total_chats,
    is_sender_blocked,
    join_chat_room,
    leave_chat_room,
    list_open_room_ids_with_spare_capacity,
    list_reaction_tags,
    list_users_with_active_room,
    log_reaction,
    make_point,
    mark_session_history_deleted,
    purge_old_chat_messages,
    refund_coins,
    set_reactions_enabled,
    set_silent_mode,
    store_chat_message,
    unblock_sender,
    update_next_gender_pref,
    update_profile_report_verdict,
    update_report_verdict,
)

__all__ = [
    "Base",
    "DATABASE_URL",
    "READ_DATABASE_URL",
    "async_session",
    "engine",
    "init_db",
    "read_session",
    "BlockedSender",
    "ChatMessage",
    "ChatRoom",
    "ChatRoomMember",
    "ChatSession",
    "CoinTransaction",
    "Gender",
    "ProfileReport",
    "ReactionLog",
    "ReactionTag",
    "Report",
    "ReportReason",
    "ReportVerdict",
    "RoomGenderPref",
    "RoomStatus",
    "User",
    "Warning",
    "add_reaction_tag",
    "add_warning",
    "ban_user",
    "block_sender",
    "clear_photo_file_id",
    "create_chat_room",
    "deduct_coins",
    "delete_reaction_tag",
    "find_open_room_for_join",
    "get_chat_room",
    "get_display_name",
    "get_or_create_user",
    "get_reaction_counts",
    "get_reaction_tag",
    "get_room_member_ids",
    "get_session_transcript",
    "get_user_by_referral_code",
    "get_user_profile_snapshot",
    "grant_referral_bonus",
    "grant_report_reward",
    "increment_total_chats",
    "is_sender_blocked",
    "join_chat_room",
    "leave_chat_room",
    "list_open_room_ids_with_spare_capacity",
    "list_reaction_tags",
    "list_users_with_active_room",
    "log_reaction",
    "make_point",
    "mark_session_history_deleted",
    "purge_old_chat_messages",
    "refund_coins",
    "set_reactions_enabled",
    "set_silent_mode",
    "store_chat_message",
    "unblock_sender",
    "update_next_gender_pref",
    "update_profile_report_verdict",
    "update_report_verdict",
]
