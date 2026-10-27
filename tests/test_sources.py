import base64

from job_agent.sources.gmail import parse_alert_message
from job_agent.sources.public import GreenhouseSource, LeverSource, RemoteOKSource


def _encoded(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode()


def test_gmail_alert_parser_extracts_job_fields() -> None:
    message = {
        "id": "mail-1",
        "payload": {
            "headers": [
                {"name": "From", "value": "LinkedIn <jobs-noreply@linkedin.com>"},
                {"name": "Subject", "value": "Python Developer at Acme"},
            ],
            "mimeType": "text/html",
            "body": {"data": _encoded('<p>Location: Bengaluru</p><a href="https://www.linkedin.com/jobs/view/123">Python Developer</a>')},
        },
    }
    jobs = parse_alert_message(message)
    assert len(jobs) == 1
    assert (jobs[0].title, jobs[0].company, jobs[0].location) == ("Python Developer", "Acme", "Bengaluru")


def test_linkedin_alert_keeps_job_card_and_removes_tracking_links() -> None:
    body = """<a href="https://www.linkedin.com/comm/jobs/view/123/?midToken=one&trkEmail=x">Backend Engineer</a>
    <p>Acme · Bengaluru (Hybrid)</p><p>Python and FastAPI</p>
    <a href="https://www.linkedin.com/comm/jobs/view/123/?midToken=one">View job</a>
    <a href="https://www.linkedin.com/comm/jobs/alerts?midToken=one">Manage job alerts</a>
    <a href="https://www.linkedin.com/comm/jobs/search-results/?keywords=python">See all jobs</a>"""
    message = {"id": "mail-2", "payload": {"headers": [
        {"name": "From", "value": "LinkedIn <jobs-noreply@linkedin.com>"},
        {"name": "Subject", "value": "Your job alert for Backend Engineer"},
    ], "mimeType": "text/html", "body": {"data": _encoded(body)}}}
    jobs = parse_alert_message(message)
    assert len(jobs) == 1
    assert jobs[0].title == "Backend Engineer"
    assert jobs[0].company == "Acme"
    assert jobs[0].location == "Bengaluru (Hybrid)"
    assert jobs[0].url == "https://www.linkedin.com/jobs/view/123/"
    assert "Python and FastAPI" in jobs[0].description

    other_tracking = body.replace("midToken=one", "midToken=two")
    message["payload"]["body"]["data"] = _encoded(other_tracking)
    assert parse_alert_message(message)[0].id == jobs[0].id


def test_naukri_card_finds_location_after_experience() -> None:
    body = ('<a href="https://www.naukri.com/job-listings-python-developer-acme-123">Python Developer</a>'
            '<span>Acme</span><span>3-5 years</span><span>Hybrid - Bangalore</span>')
    message = {"payload": {"headers": [
        {"name": "From", "value": "Naukri <alerts@naukri.com>"},
        {"name": "Subject", "value": "Python Developer jobs"},
    ], "mimeType": "text/html", "body": {"data": _encoded(body)}}}
    jobs = parse_alert_message(message)
    assert len(jobs) == 1
    assert (jobs[0].company, jobs[0].location) == ("Acme", "Hybrid - Bangalore")


class FakeClient:
    def __init__(self, payload):
        self.payload = payload

    def get_json(self, url, params=None):
        return self.payload


def test_greenhouse_fetches_description() -> None:
    source = GreenhouseSource(["acme"], FakeClient({"jobs": [{"id": 1, "title": "Backend Engineer", "absolute_url": "https://boards.greenhouse.io/acme/jobs/1", "location": {"name": "Bangalore"}, "content": "<p>Build APIs</p>"}]}))
    assert source.fetch_jobs()[0].description == "Build APIs"


def test_lever_fetches_description() -> None:
    source = LeverSource(["acme"], FakeClient([{"id": "1", "text": "Python Developer", "hostedUrl": "https://jobs.lever.co/acme/1", "categories": {"location": "Remote"}, "description": "<p>Python</p>"}]))
    assert source.fetch_jobs()[0].description == "Python"


def test_remoteok_ignores_metadata() -> None:
    source = RemoteOKSource(FakeClient([{"legal": "terms"}, {"id": 4, "position": "Backend Engineer", "company": "Acme", "url": "https://remoteok.com/remote-jobs/4", "description": "<p>FastAPI</p>"}]))
    assert len(source.fetch_jobs()) == 1

