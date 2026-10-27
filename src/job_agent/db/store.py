"""SQLite schema and operations shared across pipeline stages."""

from __future__ import annotations

import logging
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from typing import TYPE_CHECKING

from job_agent.models import Job, JobStatus

if TYPE_CHECKING:
    from job_agent.scoring.service import ScoreResult

LOG = logging.getLogger(__name__)

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    source_job_id TEXT,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    location TEXT NOT NULL,
    url TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    salary_min_lpa REAL,
    salary_max_lpa REAL,
    status TEXT NOT NULL DEFAULT 'discovered' CHECK(status IN (
        'discovered', 'scored', 'queued', 'approved', 'applied', 'failed',
        'rejected', 'interview', 'offer', 'needs_attention'
    )),
    discovered_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE TABLE IF NOT EXISTS scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL UNIQUE REFERENCES jobs(id) ON DELETE CASCADE,
    score INTEGER NOT NULL CHECK(score BETWEEN 0 AND 100),
    reasons_json TEXT NOT NULL,
    missing_skills_json TEXT NOT NULL,
    dealbreakers_json TEXT NOT NULL,
    scored_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL UNIQUE REFERENCES jobs(id) ON DELETE CASCADE,
    resume_pdf_path TEXT,
    cover_letter_path TEXT,
    approval_at TEXT,
    applied_at TEXT,
    screenshot_path TEXT,
    notes TEXT NOT NULL DEFAULT '',
    follow_up_at TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT REFERENCES jobs(id) ON DELETE SET NULL,
    event_type TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_events_job_id ON events(job_id);
"""


class Store:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connection() as connection:
            connection.executescript(SCHEMA)
        LOG.info("Initialized database at %s", self.path)

    def add_job(self, job: Job) -> bool:
        """Insert a new job; return False for a duplicate fingerprint."""
        with self.connection() as connection:
            cursor = connection.execute(
                """INSERT OR IGNORE INTO jobs
                (id, source, source_job_id, title, company, location, url,
                 description, salary_min_lpa, salary_max_lpa)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    job.id, job.source, job.source_job_id, job.title, job.company,
                    job.location, job.url, job.description, job.salary_min_lpa,
                    job.salary_max_lpa,
                ),
            )
            inserted = cursor.rowcount == 1
            if inserted:
                connection.execute(
                    "INSERT INTO events (job_id, event_type) VALUES (?, ?)",
                    (job.id, "discovered"),
                )
        LOG.info("%s job %s: %s at %s", "Inserted" if inserted else "Duplicate", job.id, job.title, job.company)
        return inserted

    def get_job(self, job_id: str) -> sqlite3.Row | None:
        with self.connection() as connection:
            return connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()

    def set_status(self, job_id: str, status: JobStatus, detail: str = "") -> None:
        with self.connection() as connection:
            cursor = connection.execute(
                "UPDATE jobs SET status = ?, updated_at = datetime('now') WHERE id = ?",
                (status.value, job_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"Job not found: {job_id}")
            connection.execute(
                "INSERT INTO events (job_id, event_type, detail) VALUES (?, ?, ?)",
                (job_id, status.value, detail),
            )
        LOG.info("Job %s status=%s %s", job_id, status.value, detail)

    def save_score(self, job_id: str, result: ScoreResult) -> None:
        with self.connection() as connection:
            connection.execute(
                """INSERT INTO scores (job_id, score, reasons_json, missing_skills_json, dealbreakers_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET score=excluded.score,
                reasons_json=excluded.reasons_json,
                missing_skills_json=excluded.missing_skills_json,
                dealbreakers_json=excluded.dealbreakers_json,
                scored_at=datetime('now')""",
                (job_id, result.score, json.dumps(result.reasons), json.dumps(result.missing_skills), json.dumps(result.dealbreakers)),
            )
        LOG.info("Saved score %d for job %s", result.score, job_id)

    def save_materials(self, job_id: str, resume_pdf_path: Path, cover_letter_path: Path) -> None:
        with self.connection() as connection:
            connection.execute(
                """INSERT INTO applications (job_id, resume_pdf_path, cover_letter_path)
                VALUES (?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET resume_pdf_path=excluded.resume_pdf_path,
                cover_letter_path=excluded.cover_letter_path""",
                (job_id, str(resume_pdf_path), str(cover_letter_path)),
            )
            connection.execute(
                "INSERT INTO events (job_id, event_type, detail) VALUES (?, 'tailored', ?)",
                (job_id, str(resume_pdf_path)),
            )
        LOG.info("Saved tailored materials for job %s", job_id)

    def mark_applied(self, job_id: str, screenshot_path: Path, follow_up_days: int) -> None:
        if not screenshot_path.is_file():
            raise ValueError(f"Submission screenshot not found: {screenshot_path}")
        now = datetime.now(timezone.utc)
        applied_at = now.strftime("%Y-%m-%d %H:%M:%S")
        follow_up_at = (now + timedelta(days=follow_up_days)).strftime("%Y-%m-%d %H:%M:%S")
        with self.connection() as connection:
            row = connection.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
            approval = connection.execute("SELECT approval_at FROM applications WHERE job_id=?", (job_id,)).fetchone()
            if row is None or row["status"] != "approved" or approval is None or not approval["approval_at"]:
                raise ValueError("An approved job and explicit approval timestamp are required")
            connection.execute("UPDATE applications SET applied_at=?, follow_up_at=?, screenshot_path=? WHERE job_id=?", (applied_at, follow_up_at, str(screenshot_path), job_id))
            connection.execute("UPDATE jobs SET status='applied', updated_at=datetime('now') WHERE id=?", (job_id,))
            connection.execute("INSERT INTO events (job_id, event_type, detail) VALUES (?, 'applied', ?)", (job_id, str(screenshot_path)))
        LOG.info("Application submitted for %s; screenshot=%s", job_id, screenshot_path)

