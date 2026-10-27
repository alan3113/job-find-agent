"""Daily discovery pipeline and digest; never launches application browsers."""

from __future__ import annotations

import logging
import os
import smtplib
from datetime import datetime, time as dt_time, timezone
from email.message import EmailMessage
from zoneinfo import ZoneInfo

from job_agent.config import Config
from job_agent.db.store import Store
from job_agent.scoring.service import score_discovered
from job_agent.sources.runner import discover
from job_agent.tailoring.service import tailor_queued

LOG = logging.getLogger(__name__)
INDIA_TIME = ZoneInfo("Asia/Kolkata")


def digest_text(store: Store) -> str:
    now = datetime.now(INDIA_TIME)
    day_start = datetime.combine(now.date(), dt_time.min, INDIA_TIME).astimezone(timezone.utc)
    start = day_start.strftime("%Y-%m-%d %H:%M:%S")
    current = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with store.connection() as connection:
        rows = connection.execute("""SELECT event_type, COUNT(*) AS count FROM events
            WHERE created_at >= ? GROUP BY event_type""", (start,)).fetchall()
        followups = connection.execute("""SELECT j.title, j.company, j.url, a.follow_up_at
            FROM applications a JOIN jobs j ON j.id=a.job_id
            WHERE a.applied_at IS NOT NULL AND a.follow_up_at <= ?
            AND j.status IN ('applied', 'interview') ORDER BY a.follow_up_at""", (current,)).fetchall()
    counts = {row["event_type"]: row["count"] for row in rows}
    lines = [f"Job assistant digest — {now:%Y-%m-%d} (Asia/Kolkata)", "",
             f"New matches: {counts.get('queued', 0)}",
             f"Applied: {counts.get('applied', 0)}",
             f"Needs attention: {counts.get('needs_attention', 0)}",
             "", "Follow-ups due:"]
    if followups:
        lines.extend(f"- {row['title']} at {row['company']} (due {row['follow_up_at']} UTC): {row['url']}" for row in followups)
    else:
        lines.append("- None")
    return "\n".join(lines)


def send_digest(body: str) -> None:
    print(body)
    host = os.getenv("SMTP_HOST")
    recipient = os.getenv("DIGEST_TO_EMAIL")
    if not host or not recipient:
        return
    username, password = os.getenv("SMTP_USERNAME"), os.getenv("SMTP_PASSWORD")
    message = EmailMessage()
    message["Subject"] = "Job assistant daily digest"
    message["From"] = username or recipient
    message["To"] = recipient
    message.set_content(body)
    with smtplib.SMTP(host, int(os.getenv("SMTP_PORT", "587")), timeout=20) as smtp:
        smtp.starttls()
        if username and password:
            smtp.login(username, password)
        smtp.send_message(message)
    LOG.info("Sent daily digest to %s", recipient)


def run_once(config: Config) -> None:
    store = Store(config.database_path)
    store.initialize()
    for name, action in (
        ("discovery", lambda: discover(config, store)),
        ("scoring", lambda: score_discovered(config, store)),
        ("tailoring", lambda: tailor_queued(config, store)),
    ):
        try:
            LOG.info("Starting %s", name)
            LOG.info("%s results: %s", name, action())
        except Exception:
            LOG.exception("Pipeline stage failed: %s", name)
    try:
        send_digest(digest_text(store))
    except Exception:
        LOG.exception("Could not send digest")


def run_scheduler(config: Config) -> None:
    from apscheduler.schedulers.blocking import BlockingScheduler

    scheduler = BlockingScheduler(timezone=INDIA_TIME)
    scheduler.add_job(run_once, "cron", hour=8, minute=0, args=[config], id="daily_pipeline", max_instances=1, coalesce=True)
    LOG.info("Daily pipeline scheduled for 08:00 Asia/Kolkata; browser applications are manual-only")
    scheduler.start()

