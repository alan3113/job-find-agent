"""Local review dashboard. Bind to 127.0.0.1 only."""

from __future__ import annotations

import html
import json
import logging
from pathlib import Path
from urllib.parse import quote, parse_qs, urlencode, urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse

from job_agent.config import load_config
from job_agent.db.store import Store
from job_agent.models import JobStatus
from job_agent.tailoring.service import render_cover_letter_pdf, render_markdown_pdf

LOG = logging.getLogger(__name__)
app = FastAPI(title="Job review dashboard", docs_url=None, redoc_url=None)


def _check_origin(request: Request) -> None:
    # A remote page must not be able to submit approvals to this local dashboard.
    if request.headers.get("origin") not in {"http://127.0.0.1:8000", "http://localhost:8000"}:
        raise HTTPException(403, "Dashboard actions require a local browser origin")


async def _form(request: Request) -> dict[str, str]:
    if request.headers.get("content-type", "").split(";")[0] != "application/x-www-form-urlencoded":
        raise HTTPException(415, "Expected URL encoded form")
    body = await request.body()
    if len(body) > 1_000_000:
        raise HTTPException(413, "Form too large")
    return {key: values[0] for key, values in parse_qs(body.decode("utf-8")).items()}


def _context() -> tuple[Store, Path]:
    config = load_config()
    return Store(config.database_path), config.artifacts_dir


def _page(title: str, body: str) -> HTMLResponse:
    css = Path(__file__).with_name("styles.css").read_text(encoding="utf-8")
    active = "tracker" if title == "Application tracker" else "review"
    review_current = " aria-current='page'" if active == "review" else ""
    tracker_current = " aria-current='page'" if active == "tracker" else ""
    return HTMLResponse(
        f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        f"<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{html.escape(title)} · Job agent</title><style>{css}</style></head>"
        f"<body><header class='topbar'><div class='shell'><a class='brand' href='/'>"
        f"<span class='brand-mark'>J</span><span class='brand-name'>Job agent</span></a>"
        f"<nav class='nav' aria-label='Main navigation'><a href='/'{review_current}>Review queue</a>"
        f"<a href='/tracker'{tracker_current}>All jobs</a></nav></div></header>"
        f"<main class='shell'><p class='eyebrow'>Application workspace</p>"
        f"<h1>{html.escape(title)}</h1>{body}</main></body></html>"
    )


def _h(value: object) -> str:
    return html.escape("" if value is None else str(value))


def _date(value: str | None) -> str:
    return f"{_h(value[:16])} UTC" if value else "—"


def _materials_ready(row: object) -> bool:
    return bool(row["resume_pdf_path"] and row["cover_letter_path"]
                and Path(row["resume_pdf_path"]).is_file()
                and Path(row["cover_letter_path"]).is_file())


def _stats(store: Store) -> dict[str, int]:
    with store.connection() as connection:
        row = connection.execute("""SELECT COUNT(*) AS total,
            SUM(CASE WHEN j.status='queued' THEN 1 ELSE 0 END) AS queued,
            SUM(CASE WHEN j.status='approved' AND a.applied_at IS NULL THEN 1 ELSE 0 END) AS approved,
            SUM(CASE WHEN a.applied_at IS NOT NULL THEN 1 ELSE 0 END) AS applied,
            SUM(CASE WHEN j.status='needs_attention' THEN 1 ELSE 0 END) AS attention
            FROM jobs j LEFT JOIN applications a ON a.job_id=j.id""").fetchone()
    return {key: int(row[key] or 0) for key in ("total", "queued", "approved", "applied", "attention")}


def _stat_cards(stats: dict[str, int]) -> str:
    items = (
        ("Total roles", stats["total"]),
        ("To review", stats["queued"]),
        ("Approved, not applied", stats["approved"]),
        ("Applied", stats["applied"]),
    )
    return "<section class='stats' aria-label='Application totals'>" + "".join(
        f"<div class='stat'><span>{label}</span><strong>{count}</strong></div>"
        for label, count in items
    ) + "</section>"


def _json_list(value: str | None) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
        return [str(item) for item in parsed] if isinstance(parsed, list) else []
    except (TypeError, ValueError):
        return []


def _submission_label(row: object) -> tuple[str, str]:
    if row["applied_at"]:
        return "Applied", "yes"
    if row["status"] == "approved":
        return "Approved · not applied", "no"
    if row["status"] == "needs_attention":
        return "Submission not confirmed", "no"
    return "Not applied", "no"


def _status_badge(status: str) -> str:
    labels = {
        "discovered": ("Discovered", "gray"), "scored": ("Scored", "gray"),
        "queued": ("Awaiting review", "blue"), "approved": ("Approved", "blue"),
        "applied": ("Applied", "green"), "interview": ("Interview", "green"),
        "offer": ("Offer", "green"), "needs_attention": ("Needs attention", "orange"),
        "failed": ("Failed", "red"), "rejected": ("Rejected", "gray"),
    }
    label, color = labels.get(status, (status.replace("_", " ").title(), "gray"))
    return f"<span class='badge {color}'>{_h(label)}</span>"


def _automatic_apply_supported(row: object) -> bool:
    host = urlsplit(row["url"]).netloc.casefold()
    return (row["source"] == "greenhouse" and (host == "greenhouse.io" or host.endswith(".greenhouse.io"))) or (
        row["source"] == "lever" and (host == "lever.co" or host.endswith(".lever.co"))
    )


def _row(job_id: str):
    store, _ = _context()
    with store.connection() as connection:
        return connection.execute("""SELECT j.*, s.score, s.reasons_json, s.missing_skills_json,
            a.resume_pdf_path, a.cover_letter_path, a.approval_at, a.applied_at, a.screenshot_path, a.notes, a.follow_up_at
            FROM jobs j LEFT JOIN scores s ON s.job_id=j.id LEFT JOIN applications a ON a.job_id=j.id WHERE j.id=?""", (job_id,)).fetchone()


@app.get("/", response_class=HTMLResponse)
def queue() -> HTMLResponse:
    store, _ = _context()
    stats = _stats(store)
    with store.connection() as connection:
        rows = connection.execute("""SELECT j.*, s.score, s.reasons_json, s.missing_skills_json,
            a.resume_pdf_path, a.cover_letter_path
            FROM jobs j LEFT JOIN scores s ON s.job_id=j.id
            LEFT JOIN applications a ON a.job_id=j.id
            WHERE j.status='queued' ORDER BY s.score DESC, j.discovered_at DESC""").fetchall()
    cards = []
    for row in rows:
        job_id = quote(row["id"])
        ready = _materials_ready(row)
        reasons = _json_list(row["reasons_json"])
        missing = _json_list(row["missing_skills_json"])
        reason_html = "<ul>" + "".join(f"<li>{_h(reason)}</li>" for reason in reasons) + "</ul>" if reasons else "<span class='muted'>No reasons recorded</span>"
        missing_html = "<div class='chips'>" + "".join(f"<span class='chip'>{_h(skill)}</span>" for skill in missing) + "</div>" if missing else "<span class='muted'>None listed</span>"
        materials = (
            f"<a href='/materials/{job_id}/resume'>Resume PDF</a>"
            f"<a href='/materials/{job_id}/cover'>Cover letter PDF</a>"
            f"<a href='/jobs/{job_id}/edit'>Edit materials</a>"
        ) if ready else "<span class='muted'>Materials pending</span>"
        disabled = "" if ready else " disabled"
        cards.append(
            f"<article class='card'><div class='card-head'><div><h2>{_h(row['title'])}</h2>"
            f"<p class='company'>{_h(row['company'])}</p>"
            f"<p class='meta'><span>{_h(row['location'])}</span><span>{_h(row['source']).title()}</span>"
            f"<span>Found {_date(row['discovered_at'])}</span></p></div>"
            f"<div class='score'>{row['score'] if row['score'] is not None else '—'}<small>/100</small></div></div>"
            f"<div class='detail-grid'><div><h3>Why it matches</h3>{reason_html}</div>"
            f"<div><h3>Missing skills</h3>{missing_html}</div></div>"
            f"<div class='card-footer'><div class='links'><a href='{_h(row['url'])}' target='_blank' rel='noopener noreferrer'>View job ↗</a>{materials}</div>"
            f"<div class='actions'><form method='post' action='/jobs/{job_id}/approve'>"
            f"<button class='btn btn-primary'{disabled}>Approve for application</button></form>"
            f"<form method='post' action='/jobs/{job_id}/reject'><button class='btn btn-secondary'>Reject</button></form></div></div>"
            f"<p class='hint'>Approval adds this role to the apply queue; submission only runs when you start the apply command.</p></article>"
        )
    empty = "<div class='empty'><strong>No jobs awaiting review</strong>Run discovery, scoring, and tailoring to add matching roles here. <a href='/tracker'>See all jobs</a>.</div>"
    body = (
        "<p class='intro'>Review each match and its materials before approving an application.</p>"
        + _stat_cards(stats)
        + f"<div class='section-head'><h2>Ready for your decision</h2><a href='/tracker'>View all roles →</a></div>"
        + "<div class='job-list'>" + ("".join(cards) or empty) + "</div>"
    )
    return _page("Review queue", body)


@app.get("/tracker", response_class=HTMLResponse)
def tracker(request: Request) -> HTMLResponse:
    store, _ = _context()
    stats = _stats(store)
    selected = request.query_params.get("status", "all")
    if selected not in {"all", "not_applied", "approved", "applied", "review", "attention"}:
        selected = "all"
    search = request.query_params.get("q", "").strip()[:100]
    try:
        page_number = max(1, int(request.query_params.get("page", "1")))
    except ValueError:
        page_number = 1
    page_size = 20
    clauses = []
    args: list[str] = []
    if selected == "not_applied":
        clauses.append("a.applied_at IS NULL")
    elif selected == "approved":
        clauses.append("j.status='approved' AND a.applied_at IS NULL")
    elif selected == "applied":
        clauses.append("a.applied_at IS NOT NULL")
    elif selected == "review":
        clauses.append("j.status='queued'")
    elif selected == "attention":
        clauses.append("j.status='needs_attention'")
    if search:
        clauses.append("(j.title LIKE ? OR j.company LIKE ? OR j.location LIKE ?)")
        args.extend([f"%{search}%"] * 3)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with store.connection() as connection:
        matching = connection.execute("SELECT COUNT(*) FROM jobs j LEFT JOIN applications a ON a.job_id=j.id" + where, args).fetchone()[0]
        last_page = max(1, (matching + page_size - 1) // page_size)
        page_number = min(page_number, last_page)
        rows = connection.execute("""SELECT j.*, s.score, a.approval_at, a.applied_at,
            a.follow_up_at, a.notes, a.screenshot_path, a.resume_pdf_path, a.cover_letter_path
            FROM jobs j LEFT JOIN scores s ON s.job_id=j.id
            LEFT JOIN applications a ON a.job_id=j.id"""
            + where + " ORDER BY j.updated_at DESC, j.id DESC LIMIT ? OFFSET ?",
            [*args, page_size, (page_number - 1) * page_size]).fetchall()
    filters = (
        ("all", "All roles"), ("not_applied", "Not applied"),
        ("approved", "Approved"), ("applied", "Applied"),
        ("review", "To review"), ("attention", "Needs attention"),
    )
    tabs = "".join(
        f"<a class='filter{' active' if key == selected else ''}' href='/tracker?{urlencode({'status': key, 'q': search})}'>{label}</a>"
        for key, label in filters
    )
    cards = []
    for row in rows:
        job_id = quote(row["id"])
        submission, submission_class = _submission_label(row)
        score_html = f"<span class='score'>{row['score']}<small>/100</small></span>" if row["score"] is not None else ""
        screenshot = f"<a href='/materials/{job_id}/screenshot'>Submission screenshot</a>" if row["screenshot_path"] else ""
        materials = (
            f"<a href='/materials/{job_id}/resume' target='_blank'>Resume PDF</a>"
            f"<a href='/materials/{job_id}/cover' target='_blank'>Cover letter PDF</a>"
        ) if _materials_ready(row) else ""
        status_controls = ""
        if row["status"] in {"applied", "interview"}:
            status_controls = f"<form class='actions' method='post' action='/jobs/{job_id}/status'><select name='status' aria-label='New application status'><option value='interview'>Interview</option><option value='offer'>Offer</option></select><button class='btn btn-secondary'>Update</button></form>"
        elif row["status"] == "needs_attention":
            status_controls = f"<p class='help'>Review the screenshot and any draft answers, then finish manually with <code>job-agent open-manual {job_id}</code>.</p>"
        elif row["status"] == "approved":
            if _automatic_apply_supported(row):
                status_controls = "<p class='help'>Ready to submit when you run <code>job-agent apply</code>.</p>"
            else:
                status_controls = f"<p class='help'>Apply yourself in the browser with <code>job-agent open-manual {job_id}</code>.</p>"
        cards.append(
            f"<article class='card tracker-card'><div class='card-head'><div><h2>{_h(row['title'])}</h2>"
            f"<p class='company'>{_h(row['company'])}</p><p class='meta'><span>{_h(row['location'])}</span>"
            f"<span>{_h(row['source']).title()}</span></p></div>"
            f"{score_html}</div>"
            f"<div><p class='state-line'>{_status_badge(row['status'])}"
            f"<span class='submission {submission_class}'>{_h(submission)}</span></p>"
            f"<p class='timeline'>Found {_date(row['discovered_at'])} · "
            f"Approved {_date(row['approval_at'])} · Applied {_date(row['applied_at'])}"
            f"{' · Follow up ' + _date(row['follow_up_at']) if row['follow_up_at'] else ''}</p></div>"
            f"<div class='tracker-side links'><a href='{_h(row['url'])}' target='_blank' rel='noopener noreferrer'>View job ↗</a>{materials}{screenshot}</div>"
            f"<div class='tracker-bottom'><form class='notes' method='post' action='/jobs/{job_id}/notes'>"
            f"<label for='note-{job_id}'>Note</label><input id='note-{job_id}' name='notes' value='{html.escape(row['notes'] or '', quote=True)}' maxlength='5000'>"
            f"<button class='btn btn-secondary'>Save note</button></form>{status_controls}</div></article>"
        )
    empty = "<div class='empty'><strong>No roles found</strong>Try another filter, or run <code>job-agent discover</code> to add jobs.</div>"
    first = (page_number - 1) * page_size + 1 if matching else 0
    last = first + len(rows) - 1 if matching else 0
    pagination = ""
    if last_page > 1:
        previous = f"<a class='btn btn-secondary' href='/tracker?{urlencode({'status': selected, 'q': search, 'page': page_number - 1})}'>← Previous</a>" if page_number > 1 else ""
        following = f"<a class='btn btn-secondary' href='/tracker?{urlencode({'status': selected, 'q': search, 'page': page_number + 1})}'>Next →</a>" if page_number < last_page else ""
        pagination = f"<nav class='pagination' aria-label='Tracker pages'>{previous}<span>Page {page_number} of {last_page}</span>{following}</nav>"
    body = (
        "<p class='intro'>See every role and whether an application was actually submitted. Approval alone does not count as applied.</p>"
        + _stat_cards(stats)
        + f"<div class='section-head'><h2>Roles <span class='muted'>({first}–{last} of {matching})</span></h2></div>"
        + "<div class='filters' aria-label='Filter roles'>" + tabs
        + f"<form class='search' method='get' action='/tracker'><input type='hidden' name='status' value='{_h(selected)}'>"
        + f"<input name='q' type='search' value='{html.escape(search, quote=True)}' placeholder='Search role or company' aria-label='Search role, company, or location'>"
        + "<button class='btn btn-secondary'>Search</button></form></div>"
        + "<div class='job-list'>" + ("".join(cards) or empty) + "</div>" + pagination
    )
    return _page("Application tracker", body)


@app.get("/materials/{job_id}/{kind}")
def material(job_id: str, kind: str) -> FileResponse:
    row = _row(job_id)
    if row is None or kind not in {"resume", "cover", "screenshot"}:
        raise HTTPException(404)
    path = row["resume_pdf_path"] if kind == "resume" else row["cover_letter_path"] if kind == "cover" else row["screenshot_path"]
    if kind == "cover" and path and Path(path).is_file():
        pdf_path = Path(path).with_suffix(".pdf")
        if not pdf_path.is_file():
            render_cover_letter_pdf(pdf_path, Path(path).read_text(encoding="utf-8"))
        path = pdf_path
    if not path or not Path(path).is_file():
        raise HTTPException(404)
    return FileResponse(path)


@app.get("/jobs/{job_id}/edit", response_class=HTMLResponse)
def edit_form(job_id: str) -> HTMLResponse:
    row = _row(job_id)
    if row is None or row["status"] != "queued" or not _materials_ready(row):
        raise HTTPException(404)
    markdown_path = Path(row["resume_pdf_path"]).with_suffix(".md")
    markdown = markdown_path.read_text(encoding="utf-8") if markdown_path.exists() else ""
    cover = Path(row["cover_letter_path"]).read_text(encoding="utf-8")
    body = (
        "<p class='intro'>Check every claim against your source resume before approving this application.</p>"
        f"<form class='editor' method='post'><label>Resume Markdown<textarea name='resume_markdown' required>{html.escape(markdown)}</textarea></label>"
        f"<label>Cover letter<textarea name='cover_letter' required>{html.escape(cover)}</textarea></label>"
        "<div class='editor-actions'><button class='btn btn-primary'>Save changes</button>"
        "<a class='btn btn-secondary' href='/'>Back to review</a></div></form>"
    )
    return _page(f"Edit: {row['title']}", body)


@app.post("/jobs/{job_id}/edit")
async def save_edit(job_id: str, request: Request) -> RedirectResponse:
    _check_origin(request)
    form = await _form(request)
    resume_markdown, cover_letter = form.get("resume_markdown", ""), form.get("cover_letter", "")
    row = _row(job_id)
    if row is None or row["status"] != "queued" or not _materials_ready(row):
        raise HTTPException(404)
    if not resume_markdown.strip() or not cover_letter.strip():
        raise HTTPException(400, "Both materials are required")
    pdf = Path(row["resume_pdf_path"])
    render_markdown_pdf(pdf, resume_markdown)
    pdf.with_suffix(".md").write_text(resume_markdown, encoding="utf-8")
    Path(row["cover_letter_path"]).write_text(cover_letter, encoding="utf-8")
    render_cover_letter_pdf(Path(row["cover_letter_path"]).with_suffix(".pdf"), cover_letter)
    store, _ = _context()
    with store.connection() as connection:
        connection.execute("INSERT INTO events (job_id, event_type) VALUES (?, 'materials_edited')", (job_id,))
    LOG.info("Materials edited for %s", job_id)
    return RedirectResponse("/", status_code=303)


@app.post("/jobs/{job_id}/approve")
def approve(job_id: str, request: Request) -> RedirectResponse:
    _check_origin(request)
    row = _row(job_id)
    if row is None or row["status"] != "queued" or not _materials_ready(row):
        raise HTTPException(400, "Job must be queued with materials")
    store, _ = _context()
    with store.connection() as connection:
        connection.execute("UPDATE applications SET approval_at=datetime('now') WHERE job_id=?", (job_id,))
    store.set_status(job_id, JobStatus.APPROVED, "Explicit dashboard approval")
    return RedirectResponse("/", status_code=303)


@app.post("/jobs/{job_id}/reject")
def reject(job_id: str, request: Request) -> RedirectResponse:
    _check_origin(request)
    row = _row(job_id)
    if row is None or row["status"] != "queued":
        raise HTTPException(400, "Job is not queued")
    store, _ = _context()
    store.set_status(job_id, JobStatus.REJECTED, "Explicit dashboard rejection")
    return RedirectResponse("/", status_code=303)


@app.post("/jobs/{job_id}/notes")
async def save_note(job_id: str, request: Request) -> RedirectResponse:
    _check_origin(request)
    row = _row(job_id)
    if row is None:
        raise HTTPException(404)
    note = (await _form(request)).get("notes", "").strip()
    if len(note) > 5000:
        raise HTTPException(400, "Note too long")
    store, _ = _context()
    with store.connection() as connection:
        connection.execute("INSERT INTO applications (job_id, notes) VALUES (?, ?) ON CONFLICT(job_id) DO UPDATE SET notes=excluded.notes", (job_id, note))
        connection.execute("INSERT INTO events (job_id, event_type, detail) VALUES (?, 'note_updated', ?)", (job_id, note[:200]))
    return RedirectResponse("/tracker", status_code=303)


@app.post("/jobs/{job_id}/status")
async def update_status(job_id: str, request: Request) -> RedirectResponse:
    _check_origin(request)
    row = _row(job_id)
    if row is None or row["status"] not in {"applied", "interview"}:
        raise HTTPException(400, "Status cannot be updated here")
    status = (await _form(request)).get("status")
    if status not in {"interview", "offer"}:
        raise HTTPException(400, "Invalid status")
    store, _ = _context()
    store.set_status(job_id, JobStatus(status), "Manual tracker update")
    return RedirectResponse("/tracker", status_code=303)

