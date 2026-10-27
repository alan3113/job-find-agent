"""Shared domain models and stable job identity."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import Enum
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


class JobStatus(str, Enum):
    DISCOVERED = "discovered"
    SCORED = "scored"
    QUEUED = "queued"
    APPROVED = "approved"
    APPLIED = "applied"
    FAILED = "failed"
    REJECTED = "rejected"
    INTERVIEW = "interview"
    OFFER = "offer"
    NEEDS_ATTENTION = "needs_attention"


def normalize_text(value: str) -> str:
    return " ".join(value.casefold().split())


def normalize_url(value: str) -> str:
    """Remove common tracking data without discarding job-identifying query IDs."""
    parts = urlsplit(value.strip())
    query = sorted(
        (key, val)
        for key, val in parse_qsl(parts.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_")
        and key.casefold() not in {"ref", "source", "trackingid", "trk"}
    )
    path = re.sub(r"/+$", "", parts.path) or "/"
    return urlunsplit(
        (parts.scheme.casefold(), parts.netloc.casefold(), path, urlencode(query), "")
    )


def job_fingerprint(company: str, title: str, url: str) -> str:
    canonical = "\x1f".join(
        (normalize_text(company), normalize_text(title), normalize_url(url))
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class Job:
    source: str
    title: str
    company: str
    location: str
    url: str
    description: str = ""
    salary_min_lpa: float | None = None
    salary_max_lpa: float | None = None
    source_job_id: str | None = None

    def __post_init__(self) -> None:
        if not all((self.source.strip(), self.title.strip(), self.company.strip(), self.url.strip())):
            raise ValueError("Job source, title, company, and URL are required")
        if not self.url.startswith(("https://", "http://")):
            raise ValueError("Job URL must be HTTP or HTTPS")

    @property
    def id(self) -> str:
        return job_fingerprint(self.company, self.title, self.url)

