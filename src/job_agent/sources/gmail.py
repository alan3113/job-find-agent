"""Read-only Gmail alert discovery for LinkedIn, Indeed, and Naukri."""

from __future__ import annotations

import base64
import logging
import os
import re
from email.header import decode_header, make_header
from email.utils import parseaddr
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from job_agent.models import Job
from job_agent.sources.base import JobSource, html_to_text

LOG = logging.getLogger(__name__)
SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
ALLOWED_SENDERS = ("linkedin.com", "indeed.com", "naukri.com")


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str, list[str]]] = []
        self.href: str | None = None
        self.label: list[str] = []
        self.after: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self.href = dict(attrs).get("href")
            self.label = []
            self.after = None

    def handle_data(self, data: str) -> None:
        data = " ".join(data.split())
        if self.href:
            self.label.append(data)
        elif self.after is not None and data and len(self.after) < 6:
            self.after.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self.href:
            self.after = []
            self.links.append((self.href, " ".join(self.label).strip(), self.after))
            self.href = None


def _body_part(payload: dict, mime_type: str) -> str:
    if payload.get("mimeType") == mime_type and payload.get("body", {}).get("data"):
        encoded = payload["body"]["data"]
        return base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode("utf-8", "replace")
    for part in payload.get("parts", []):
        found = _body_part(part, mime_type)
        if found:
            return found
    return ""


def _unwrap_url(url: str) -> str:
    """Unwrap common email redirect parameters when they expose the job URL."""
    params = parse_qs(urlsplit(url).query)
    for name in ("url", "u", "target", "redirect", "dest"):
        if params.get(name):
            candidate = unquote(params[name][0])
            if candidate.startswith(("https://", "http://")):
                return candidate
    return url


def _job_url(url: str) -> str | None:
    """Keep job detail links and remove email tracking from LinkedIn URLs."""
    parts = urlsplit(url)
    host = parts.netloc.casefold()
    if host == "linkedin.com" or host.endswith(".linkedin.com"):
        match = re.search(r"/(?:comm/)?jobs/view/(\d+)", parts.path, re.I)
        return f"https://www.linkedin.com/jobs/view/{match.group(1)}/" if match else None
    if host == "indeed.com" or host.endswith(".indeed.com"):
        job_key = parse_qs(parts.query).get("jk", [None])[0]
        return f"https://www.indeed.com/viewjob?jk={job_key}" if job_key else (url if "/viewjob" in parts.path else None)
    if host == "naukri.com" or host.endswith(".naukri.com"):
        return url if "job-listings" in parts.path.casefold() else None
    if host == "greenhouse.io" or host.endswith(".greenhouse.io") or host == "lever.co" or host.endswith(".lever.co"):
        return url if re.search(r"/(?:jobs?|postings?)/", parts.path, re.I) else None
    return None


def _is_job_title(label: str) -> bool:
    lowered = " ".join(label.casefold().split())
    if len(lowered) < 3:
        return False
    if lowered in {"view", "view job", "apply", "apply now", "see more", "see all jobs", "view all jobs", "view jobs"}:
        return False
    return not (
        lowered.startswith(("manage ", "your job alert", "you may be a fit", "see who ", "view all ", "see all "))
        or (lowered.startswith("view ") and "post" in lowered)
    )


def _card_fields(after: list[str]) -> tuple[str | None, str | None]:
    if not after:
        return None, None
    first = after[0].strip()
    if " · " in first:
        company, location = first.split(" · ", 1)
        return company.strip() or None, location.strip() or None
    places = (
        "bengaluru", "bangalore", "remote", "india", "hyderabad", "pune", "chennai",
        "mumbai", "delhi", "noida", "gurugram", "gurgaon", "kochi", "kozhikode",
        "thiruvananthapuram", "trivandrum", "ahmedabad", "jaipur",
    )
    location = next((item.strip() for item in after[1:] if "(" in item or any(place in item.casefold() for place in places)), None)
    return first or None, location


def parse_alert_message(message: dict) -> list[Job]:
    headers = {item["name"].casefold(): item["value"] for item in message.get("payload", {}).get("headers", [])}
    sender = parseaddr(headers.get("from", ""))[1].casefold()
    if not any(sender.endswith("@" + domain) or sender.endswith("." + domain) for domain in ALLOWED_SENDERS):
        return []
    source = next(domain.split(".")[0] for domain in ALLOWED_SENDERS if domain in sender)
    subject = str(make_header(decode_header(headers.get("subject", ""))))
    html_body = _body_part(message.get("payload", {}), "text/html")
    plain_body = _body_part(message.get("payload", {}), "text/plain")
    links = _Links()
    links.feed(html_body)
    if not links.links:
        links.links = [(url, subject.split(" at ")[0].strip(), []) for url in re.findall(r"https?://[^\s<>\"]+", plain_body)]
    text = html_to_text(html_body) if html_body else plain_body
    company_hint = re.search(r"\bat\s+([\w&., -]{2,70})", subject)
    location_hint = re.search(r"(?:location|job location)\s*[:\-]\s*([^\n|]{2,80})", text, re.I)
    jobs: list[Job] = []
    for raw_url, label, after in links.links:
        url = _job_url(_unwrap_url(raw_url))
        if not url or not _is_job_title(label):
            continue
        card_company, card_location = _card_fields(after)
        company = card_company or (company_hint.group(1).strip(" .,-") if company_hint else "Unknown company")
        location = card_location or (location_hint.group(1).strip() if location_hint else "Unknown")
        if company.casefold() in {"unknown company", "view job", "see more"}:
            continue
        description = " | ".join(dict.fromkeys([label.strip(), company, location, *after[:5]])) if html_body else text[:8000]
        jobs.append(Job(source, label.strip(), company, location, url, description[:8000], source_job_id=message.get("id")))
    return jobs


class GmailAlertSource(JobSource):
    name = "gmail"

    def __init__(self, query: str, max_messages: int = 100) -> None:
        self.query = query
        self.max_messages = max_messages

    def fetch_jobs(self) -> list[Job]:
        # Gmail is accessed only with gmail.readonly; OAuth happens locally in a browser.
        from google.auth.transport.requests import Request
        from google.auth.exceptions import RefreshError
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build

        secret_file = os.getenv("GMAIL_CLIENT_SECRET_FILE")
        token_file = Path(os.getenv("GMAIL_TOKEN_FILE", "data/oauth-token.json"))
        if not secret_file:
            raise ValueError("GMAIL_CLIENT_SECRET_FILE is required for Gmail discovery")
        credentials = Credentials.from_authorized_user_file(token_file, [SCOPE]) if token_file.exists() else None
        if credentials and credentials.expired and credentials.refresh_token:
            try:
                credentials.refresh(Request())
            except RefreshError:
                LOG.info("Saved Gmail authorization expired; requesting consent again")
                credentials = None
        if not credentials or not credentials.valid:
            flow = InstalledAppFlow.from_client_secrets_file(secret_file, [SCOPE])
            credentials = flow.run_local_server(port=0)
        token_file.parent.mkdir(parents=True, exist_ok=True)
        token_file.write_text(credentials.to_json(), encoding="utf-8")
        service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
        result: list[Job] = []
        page_token = None
        processed = 0
        while processed < self.max_messages:
            response = service.users().messages().list(
                userId="me", q=self.query, maxResults=min(100, self.max_messages - processed), pageToken=page_token
            ).execute()
            for item in response.get("messages", []):
                processed += 1
                try:
                    message = service.users().messages().get(userId="me", id=item["id"], format="full").execute()
                    result.extend(parse_alert_message(message))
                except Exception:
                    LOG.exception("Failed to parse Gmail message %s", item.get("id"))
            page_token = response.get("nextPageToken")
            if not page_token:
                break
        LOG.info("Gmail discovery found %d job links", len(result))
        return result

