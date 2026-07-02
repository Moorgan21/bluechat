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

import os
from prometheus_client import Counter, Gauge, start_http_server

METRICS_PORT = int(os.environ.get("METRICS_PORT", "8081"))

messages_relayed = Counter("bot_messages_relayed_total", "Total messages relayed between users")
chats_started    = Counter("bot_chats_started_total", "Total chat sessions started")
chats_ended      = Counter("bot_chats_ended_total", "Total chat sessions ended")
reports_total    = Counter("bot_reports_total", "Total reports submitted", ["type"])
ai_jobs_done     = Counter("bot_ai_jobs_processed_total", "Total AI jobs processed", ["type"])

active_chats     = Gauge("bot_active_chats", "Currently active chat pairs")
waiting_users    = Gauge("bot_waiting_users", "Users currently waiting for a match")
ai_queue_size    = Gauge("bot_ai_queue_size", "Pending AI jobs in Redis queue")

spam_blocks      = Counter("bot_spam_blocks_total", "Requests blocked by spam guard", ["kind"])


def start_metrics_server() -> None:
    start_http_server(METRICS_PORT)
