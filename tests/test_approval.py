import pytest
from fastapi import HTTPException
from starlette.requests import Request

from job_agent.apply.service import approved_jobs, open_manual
from job_agent.config import load_config
from job_agent.dashboard import app as dashboard_module
from job_agent.db.store import Store
from job_agent.models import Job, JobStatus


def test_dashboard_approval_is_required_before_apply(tmp_path, monkeypatch) -> None:
    store = Store(tmp_path / "jobs.sqlite3")
    store.initialize()
    job = Job("greenhouse", "Backend Engineer", "Acme", "Bangalore", "https://boards.greenhouse.io/acme/jobs/1")
    store.add_job(job)
    folder = tmp_path / "artifacts" / job.id
    folder.mkdir(parents=True)
    pdf = folder / "resume.pdf"
    cover = folder / "cover_letter.txt"
    pdf.write_bytes(b"test")
    cover.write_text("Hello", encoding="utf-8")
    store.save_materials(job.id, pdf, cover)
    store.set_status(job.id, JobStatus.QUEUED)
    monkeypatch.setattr(dashboard_module, "_context", lambda: (store, tmp_path / "artifacts"))
    assert approved_jobs(store, 15) == []
    with pytest.raises(HTTPException) as exc:
        dashboard_module.approve(job.id, Request({"type": "http", "headers": []}))
    assert exc.value.status_code == 403
    request = Request({"type": "http", "headers": [(b"origin", b"http://127.0.0.1:8000")]})
    response = dashboard_module.approve(job.id, request)
    assert response.status_code == 303
    assert store.get_job(job.id)["status"] == "approved"
    assert len(approved_jobs(store, 15)) == 1

    with pytest.raises(ValueError, match="Job not found in the current database"):
        open_manual(load_config("preferences.yaml"), store, "stale-job-id")

    class MissingBrowser:
        executable_path = str(tmp_path / "missing-chromium")

    class FakePlaywright:
        chromium = MissingBrowser()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr("playwright.sync_api.sync_playwright", FakePlaywright)
    with pytest.raises(ValueError, match="Playwright Chromium is missing"):
        open_manual(load_config("preferences.yaml"), store, job.id)
    assert store.get_job(job.id)["status"] == "approved"

