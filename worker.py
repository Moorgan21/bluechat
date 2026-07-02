"""
AI Worker — پردازش صف قضاوت‌های AI در یک پروسه‌ی جداگانه

جاب‌ها از Redis queue می‌خونه، قضاوت رو اجرا می‌کنه، و نتیجه رو
مستقیماً از طریق Bot API برای کاربر می‌فرسته.
"""
import asyncio
import base64
import logging
import os

from telegram import Bot

import redis_client as rc
import metrics
from judge import judge_report
from profile_judge import judge_profile_report
from verdict_notify import notify_chat_verdict, notify_profile_verdict

BOT_TOKEN = os.environ["BOT_TOKEN"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("melogap.worker")


async def _process_job(bot: Bot, job: dict) -> None:
    job_type = job.get("type")

    if job_type == "chat_report":
        try:
            result = await judge_report(
                report_id=job["report_id"],
                session_id=job["session_id"],
                reporter_id=job["reporter_id"],
                reported_id=job["reported_id"],
                reason=job["reason"],
                details=job.get("details"),
            )
        except Exception:
            logger.exception("خطا در judge_report report_id=%s", job.get("report_id"))
            result = {"verdict": "pending"}
        metrics.ai_jobs_done.labels(type="chat_report").inc()
        await notify_chat_verdict(bot, job["reporter_id"], job["reported_id"], result)

    elif job_type == "profile_report":
        image_b64 = job.get("image_b64")
        image_bytes = base64.b64decode(image_b64) if image_b64 else None
        try:
            result = await judge_profile_report(
                job["profile_report_id"],
                job["reporter_id"],
                job["reported_id"],
                job["snapshot"],
                image_bytes,
            )
        except Exception:
            logger.exception("خطا در judge_profile_report id=%s", job.get("profile_report_id"))
            result = {"verdict": "pending"}
        metrics.ai_jobs_done.labels(type="profile_report").inc()
        await notify_profile_verdict(bot, job["reporter_id"], job["reported_id"], result)

    else:
        logger.warning("نوع job ناشناخته: %s", job_type)


async def _update_queue_gauge() -> None:
    while True:
        try:
            size = await rc.r.llen(rc.KEY_AI_JOBS)
            metrics.ai_queue_size.set(size)
        except Exception:
            pass
        await asyncio.sleep(15)


async def main() -> None:
    metrics.start_metrics_server()
    bot = Bot(token=BOT_TOKEN)
    logger.info("AI worker started — listening for jobs")
    asyncio.create_task(_update_queue_gauge())
    while True:
        try:
            job = await rc.pop_ai_job(timeout=5)
            if job is None:
                continue
            asyncio.create_task(_process_job(bot, job))
        except Exception:
            logger.exception("خطای غیرمنتظره در حلقه‌ی worker")
            await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())
