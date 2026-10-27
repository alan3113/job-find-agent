from job_agent.db.store import Store
from job_agent.models import Job, job_fingerprint


def test_fingerprint_normalizes_case_whitespace_and_tracking() -> None:
    first = job_fingerprint(
        " Acme  Corp ", "Senior Python Engineer", "https://EXAMPLE.com/jobs/42?utm_source=mail&ref=alert"
    )
    second = job_fingerprint(
        "acme corp", " senior  python engineer ", "https://example.com/jobs/42/"
    )
    assert first == second


def test_store_deduplicates_and_logs_one_discovery(tmp_path) -> None:
    store = Store(tmp_path / "jobs.sqlite3")
    store.initialize()
    job = Job("greenhouse", "Python Developer", "Acme", "Remote", "https://example.com/42")
    duplicate = Job("lever", " python developer ", "ACME", "Remote", "https://example.com/42?utm_medium=email")

    assert store.add_job(job)
    assert not store.add_job(duplicate)
    assert store.get_job(job.id)["status"] == "discovered"
    with store.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1

