from starlette.requests import Request

from job_agent.dashboard import app as dashboard
from job_agent.db.store import Store
from job_agent.models import Job, JobStatus


def _request(query: bytes = b"") -> Request:
    return Request({"type": "http", "method": "GET", "path": "/tracker", "query_string": query, "headers": []})


def test_tracker_distinguishes_approved_from_submitted(tmp_path, monkeypatch) -> None:
    store = Store(tmp_path / "jobs.sqlite3")
    store.initialize()
    examples = (
        ("Approved Role", "greenhouse", "https://boards.greenhouse.io/acme/jobs/approved-role"),
        ("Submitted Role", "greenhouse", "https://boards.greenhouse.io/acme/jobs/submitted-role"),
        ("LinkedIn Role", "linkedin", "https://www.linkedin.com/jobs/view/123"),
    )
    for title, source, url in examples:
        job = Job(source, title, "Acme", "Bangalore", url)
        store.add_job(job)
        folder = tmp_path / job.id
        folder.mkdir()
        pdf = folder / "resume.pdf"
        cover = folder / "cover.txt"
        pdf.write_bytes(b"pdf")
        cover.write_text("Cover", encoding="utf-8")
        store.save_materials(job.id, pdf, cover)
        store.set_status(job.id, JobStatus.QUEUED)
        with store.connection() as connection:
            connection.execute("UPDATE applications SET approval_at=datetime('now') WHERE job_id=?", (job.id,))
        store.set_status(job.id, JobStatus.APPROVED)
        if title == "Submitted Role":
            screenshot = folder / "submitted.png"
            screenshot.write_bytes(b"png")
            store.mark_applied(job.id, screenshot, 7)

    monkeypatch.setattr(dashboard, "_context", lambda: (store, tmp_path))
    page = dashboard.tracker(_request()).body.decode()
    assert "Approved Role" in page
    assert "Submitted Role" in page
    assert "LinkedIn Role" in page
    assert "Approved · not applied" in page
    assert "<span class='submission yes'>Applied</span>" in page
    assert "<span class='submission no'>Approved · not applied</span>" in page
    assert "job-agent open-manual" in page
    assert page.count("Resume PDF") == 3
    assert page.count("Cover letter PDF") == 3

    applied_page = dashboard.tracker(_request(b"status=applied")).body.decode()
    assert "Submitted Role" in applied_page
    assert "Approved Role" not in applied_page

    pending_page = dashboard.tracker(_request(b"status=not_applied")).body.decode()
    assert "Approved Role" in pending_page
    assert "Submitted Role" not in pending_page

