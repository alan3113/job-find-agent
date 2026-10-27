"""Interface and helpers for discovery sources."""

from __future__ import annotations

import html
import logging
import re
import time
from abc import ABC, abstractmethod
from typing import Any

import httpx

from job_agent.models import Job

LOG = logging.getLogger(__name__)


class JobSource(ABC):
    name: str

    @abstractmethod
    def fetch_jobs(self) -> list[Job]:
        """Fetch publicly listed or user-authorized jobs."""


def html_to_text(value: str) -> str:
    text = re.sub(r"(?i)<br\s*/?>|</(?:p|li|div|h[1-6])>", "\n", value)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"[ \t]+", " ", html.unescape(text)).strip()


class RateLimitedClient:
    def __init__(self, min_interval_seconds: float = 1.0) -> None:
        self.min_interval_seconds = min_interval_seconds
        self._last_request = 0.0
        self.client = httpx.Client(
            timeout=20.0,
            follow_redirects=True,
            headers={"User-Agent": "PersonalJobAssistant/0.1 (respectful public API client)"},
        )

    def get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.min_interval_seconds:
            time.sleep(self.min_interval_seconds - elapsed)
        response = self.client.get(url, params=params)
        self._last_request = time.monotonic()
        response.raise_for_status()
        return response.json()

    def close(self) -> None:
        self.client.close()

