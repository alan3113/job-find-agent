"""Guarded headed-browser applications for approved Greenhouse and Lever jobs."""

from __future__ import annotations

import logging
import random
import re
import time
from datetime import datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from job_agent.config import Config
from job_agent.db.store import Store
from job_agent.llm import create_client, plain_response
from job_agent.models import JobStatus

LOG = logging.getLogger(__name__)
INDIA_TIME = ZoneInfo("Asia/Kolkata")


def _today_utc_bounds() -> tuple[str, str]:
    today = datetime.now(INDIA_TIME).date()
    start = datetime.combine(today, dt_time.min, INDIA_TIME).astimezone(timezone.utc)
    end = start + timedelta(days=1)
    return start.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S")


def approved_jobs(store: Store, daily_limit: int) -> list[dict[str, Any]]:
    start, end = _today_utc_bounds()
    with store.connection() as connection:
        applied_today = connection.execute("SELECT COUNT(*) FROM applications WHERE applied_at >= ? AND applied_at < ?", (start, end)).fetchone()[0]
        remaining = max(0, daily_limit - applied_today)
        rows = connection.execute("""SELECT j.*, a.resume_pdf_path, a.cover_letter_path, a.approval_at
            FROM jobs j JOIN applications a ON a.job_id=j.id
            WHERE j.status='approved' AND a.approval_at IS NOT NULL AND a.resume_pdf_path IS NOT NULL
            ORDER BY a.approval_at LIMIT ?""", (remaining,)).fetchall()
    return [dict(row) for row in rows]


def _delay() -> None:
    time.sleep(random.uniform(0.7, 1.6))


def _screenshot(page: Any, folder: Path, name: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    page.screenshot(path=str(path), full_page=True)
    return path


def _has_challenge(page: Any) -> bool:
    return page.locator("iframe[src*='recaptcha'], iframe[src*='hcaptcha'], input[name*='captcha'], [id*='captcha']").count() > 0 or bool(page.get_by_text("verify you are human", exact=False).count())


def _fill_known(page: Any, selectors: list[str], value: str) -> bool:
    for selector in selectors:
        locator = page.locator(selector).first
        if locator.count() and locator.is_visible():
            locator.fill(value)
            _delay()
            return True
    return False


def _unknown_required_fields(page: Any) -> list[str]:
    known = {"first_name", "last_name", "name", "email", "phone", "resume", "cover_letter", "comments"}
    unknown: list[str] = []
    for field in page.locator("input[required], textarea[required], select[required], input[name*='question'], textarea[name*='question'], select[name*='question']").all():
        if not field.is_visible() or field.get_attribute("type") == "hidden":
            continue
        name = (field.get_attribute("name") or field.get_attribute("id") or "").casefold()
        if not any(token in name for token in known):
            unknown.append(name or "unnamed required field")
        elif field.get_attribute("type") in {"checkbox", "radio"}:
            unknown.append(name)
        elif field.get_attribute("type") != "file" and not field.input_value().strip():
            unknown.append(name)
    return unknown


def _draft_custom_questions(job: dict[str, Any], config: Config, fields: list[str], folder: Path) -> None:
    """Draft grounded suggestions for review; never insert them into the form."""
    try:
        resume = config.profile.resume_path.read_text(encoding="utf-8")
        prompt = (
            "Draft short answers to these form fields using only facts explicitly stated in the resume. "
            "For each field, include an exact resume quote supporting the answer. If uncertain, write 'Needs user input'. "
            "Do not claim missing skills or invent facts. Return plain text.\n\n"
            f"Fields: {fields}\nResume:\n{resume}\nJob:\n{job['description'][:12000]}"
        )
        text = plain_response(create_client(), prompt, max_output_tokens=800)
        (folder / "answers_draft.txt").write_text(text, encoding="utf-8")
    except Exception:
        LOG.exception("Could not draft custom question answers for %s", job["id"])


def _fill_standard_form(page: Any, job: dict[str, Any], config: Config) -> list[str]:
    name_parts = config.profile.name.split(" ", 1)
    first, last = name_parts[0], name_parts[1] if len(name_parts) > 1 else ""
    if job["source"] == "greenhouse":
        _fill_known(page, ["input#first_name", "input[name='job_application[first_name]']"], first)
        _fill_known(page, ["input#last_name", "input[name='job_application[last_name]']"], last)
        _fill_known(page, ["input#email", "input[type='email']"], config.profile.email)
        _fill_known(page, ["input#phone", "input[type='tel']"], config.profile.phone)
    else:
        _fill_known(page, ["input[name='name']", "input[autocomplete='name']"], config.profile.name)
        _fill_known(page, ["input[name='email']", "input[type='email']"], config.profile.email)
        _fill_known(page, ["input[name='phone']", "input[type='tel']"], config.profile.phone)
    resume_file = page.locator("input[type='file'][name*='resume'], input[type='file'][id*='resume']").first
    if resume_file.count():
        resume_file.set_input_files(job["resume_pdf_path"])
    else:
        return ["Resume upload not recognized"]
    cover = Path(job["cover_letter_path"]).read_text(encoding="utf-8")
    _fill_known(page, ["textarea[name*='cover_letter']", "textarea#cover_letter", "textarea[name='comments']"], cover)
    return _unknown_required_fields(page)


def apply_approved(config: Config, store: Store) -> dict[str, int]:
    from playwright.sync_api import sync_playwright

    jobs = approved_jobs(store, config.search.daily_apply_limit)
    counts = {"applied": 0, "needs_attention": 0, "failed": 0, "manual": 0}
    if not jobs:
        return counts
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        try:
            for job in jobs:
                host = urlsplit(job["url"]).netloc.casefold()
                if job["source"] not in {"greenhouse", "lever"} or not ("greenhouse.io" in host or "lever.co" in host):
                    counts["manual"] += 1
                    LOG.info("Manual application required for %s: %s", job["id"], job["url"])
                    continue
                page = browser.new_page()
                folder = config.artifacts_dir / job["id"]
                try:
                    form_url = job["url"].rstrip("/") + "/apply" if job["source"] == "lever" else job["url"]
                    page.goto(form_url, wait_until="domcontentloaded", timeout=30000)
                    if job["source"] == "greenhouse" and not page.locator("input[type='file'][name*='resume'], input[type='file'][id*='resume']").count():
                        apply_link = page.get_by_role("link", name=re.compile("apply for this job", re.I)).first
                        if apply_link.count():
                            apply_link.click()
                            if "greenhouse.io" not in urlsplit(page.url).netloc.casefold():
                                raise NeedsAttention("Application form redirected outside Greenhouse")
                    _delay()
                    if _has_challenge(page) or page.locator("input[type='password']").count():
                        raise NeedsAttention("CAPTCHA or login challenge")
                    unknown = _fill_standard_form(page, job, config)
                    if unknown:
                        _draft_custom_questions(job, config, unknown, folder)
                        raise NeedsAttention("Unresolved form fields: " + ", ".join(unknown))
                    submit = page.locator("button[type='submit'], input[type='submit']").first
                    if not submit.count() or not submit.is_visible():
                        raise NeedsAttention("Unknown submit button")
                    _delay()
                    submit.click()
                    screenshot = _screenshot(page, folder, "submission.png")
                    with store.connection() as connection:
                        connection.execute("UPDATE applications SET screenshot_path=? WHERE job_id=?", (str(screenshot), job["id"]))
                    page.wait_for_timeout(1500)
                    if not any(page.get_by_text(text, exact=False).count() for text in ("Thank you for applying", "Application submitted", "Application received", "Thanks for applying")):
                        raise NeedsAttention("Submission confirmation not detected; inspect screenshot")
                    store.mark_applied(job["id"], screenshot, config.search.follow_up_days)
                    counts["applied"] += 1
                except NeedsAttention as exc:
                    try:
                        screenshot = _screenshot(page, folder, "needs_attention.png")
                        with store.connection() as connection:
                            connection.execute("UPDATE applications SET screenshot_path=? WHERE job_id=?", (str(screenshot), job["id"]))
                        detail = f"{exc}; screenshot: {screenshot}"
                    except Exception:
                        detail = str(exc)
                    store.set_status(job["id"], JobStatus.NEEDS_ATTENTION, detail)
                    counts["needs_attention"] += 1
                except Exception as exc:
                    LOG.exception("Application failed for %s", job["id"])
                    try:
                        screenshot = _screenshot(page, folder, "failed.png")
                        detail = f"{exc}; screenshot: {screenshot}"
                    except Exception:
                        detail = str(exc)
                    store.set_status(job["id"], JobStatus.FAILED, detail)
                    counts["failed"] += 1
                finally:
                    page.close()
        finally:
            browser.close()
    return counts


class NeedsAttention(Exception):
    pass


def open_manual(config: Config, store: Store, job_id: str) -> None:
    """Open an approved site for user-controlled application and record a screenshot."""
    from playwright.sync_api import Error as PlaywrightError, sync_playwright

    job = store.get_job(job_id)
    if job is None:
        raise ValueError(f"Job not found in the current database: {job_id}. Refresh the dashboard and use its current job ID.")
    if job["status"] not in {"approved", "needs_attention"}:
        raise ValueError("Job must have a dashboard approval or need attention after approval")
    with store.connection() as connection:
        application = connection.execute("SELECT approval_at, resume_pdf_path, cover_letter_path FROM applications WHERE job_id=?", (job_id,)).fetchone()
    if application is None or not application["approval_at"]:
        raise ValueError("No explicit approval timestamp exists")
    resume_path = Path(application["resume_pdf_path"] or "")
    cover_path = Path(application["cover_letter_path"] or "")
    if not resume_path.is_file() or not cover_path.is_file():
        raise ValueError("Tailored resume or cover letter is missing. Recreate the materials before applying.")
    start, end = _today_utc_bounds()
    with store.connection() as connection:
        count = connection.execute("SELECT COUNT(*) FROM applications WHERE applied_at >= ? AND applied_at < ?", (start, end)).fetchone()[0]
    if count >= config.search.daily_apply_limit:
        raise ValueError("Daily application limit reached")
    # LinkedIn and Indeed prohibit third-party application automation. Naukri
    # terms could not be verified here, so its flow is also user-controlled.
    profile_path = config.config_path.parent / "data/browser-profile"
    profile_path.mkdir(parents=True, exist_ok=True)
    print(f"Resume PDF to upload: {resume_path}")
    cover_pdf = cover_path.with_suffix(".pdf")
    if not cover_pdf.is_file():
        from job_agent.tailoring.service import render_cover_letter_pdf
        render_cover_letter_pdf(cover_pdf, cover_path.read_text(encoding="utf-8"))
    print(f"Cover letter PDF to upload: {cover_pdf}")
    print(f"Cover letter text to paste: {cover_path}")
    with sync_playwright() as playwright:
        if not Path(playwright.chromium.executable_path).is_file():
            raise ValueError("Playwright Chromium is missing. Run: python -m playwright install chromium")
        try:
            context = playwright.chromium.launch_persistent_context(str(profile_path), headless=False)
        except PlaywrightError as exc:
            raise ValueError(f"Could not start Chromium: {str(exc).splitlines()[0]}. Check your desktop session and Playwright installation.") from exc
        try:
            page = context.new_page()
            page.goto(job["url"], wait_until="domcontentloaded")
            print("Complete the application yourself in the browser. No login or form action is automated.")
            answer = input("After submitting, type 'submitted' to save a screenshot and track it, or press Enter to leave it approved: ").strip().casefold()
            if answer == "submitted":
                folder = config.artifacts_dir / job_id
                screenshot = _screenshot(page, folder, "manual_submission.png")
                if job["status"] == "needs_attention":
                    store.set_status(job_id, JobStatus.APPROVED, "Manual completion confirmed by user")
                store.mark_applied(job_id, screenshot, config.search.follow_up_days)
        finally:
            context.close()

