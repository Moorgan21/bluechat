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


def start_metrics_server() -> None:
    start_http_server(METRICS_PORT)
