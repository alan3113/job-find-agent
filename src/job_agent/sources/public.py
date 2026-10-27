"""Public JSON job feeds. Each configured board is read at a modest rate."""

from __future__ import annotations

import logging
import os

from job_agent.models import Job
from job_agent.sources.base import JobSource, RateLimitedClient, html_to_text

LOG = logging.getLogger(__name__)


class GreenhouseSource(JobSource):
    name = "greenhouse"

    def __init__(self, board_tokens: list[str], client: RateLimitedClient | None = None) -> None:
        self.board_tokens = board_tokens
        self.client = client or RateLimitedClient()

    def fetch_jobs(self) -> list[Job]:
        jobs: list[Job] = []
        for token in self.board_tokens:
            try:
                data = self.client.get_json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs", {"content": "true"})
                for item in data.get("jobs", []):
                    location = item.get("location") or {}
                    jobs.append(Job(self.name, item["title"], token, location.get("name", "Unknown"), item["absolute_url"], html_to_text(item.get("content") or ""), source_job_id=str(item["id"])))
            except Exception:
                LOG.exception("Greenhouse board failed: %s", token)
        return jobs


class LeverSource(JobSource):
    name = "lever"

    def __init__(self, company_slugs: list[str], client: RateLimitedClient | None = None) -> None:
        self.company_slugs = company_slugs
        self.client = client or RateLimitedClient()

    def fetch_jobs(self) -> list[Job]:
        jobs: list[Job] = []
        for slug in self.company_slugs:
            try:
                skip = 0
                while True:
                    data = self.client.get_json(f"https://api.lever.co/v0/postings/{slug}", {"mode": "json", "skip": skip, "limit": 100})
                    for item in data:
                        categories = item.get("categories") or {}
                        jobs.append(Job(self.name, item["text"], slug, categories.get("location", "Unknown"), item["hostedUrl"], html_to_text(item.get("description") or item.get("descriptionPlain") or ""), source_job_id=item.get("id")))
                    if len(data) < 100:
                        break
                    skip += 100
            except Exception:
                LOG.exception("Lever site failed: %s", slug)
        return jobs


class AdzunaSource(JobSource):
    name = "adzuna"

    def __init__(self, country: str, roles: tuple[str, ...], locations: tuple[str, ...], client: RateLimitedClient | None = None) -> None:
        self.country, self.roles, self.locations = country, roles, locations
        self.client = client or RateLimitedClient()

    def fetch_jobs(self) -> list[Job]:
        app_id, app_key = os.getenv("ADZUNA_APP_ID"), os.getenv("ADZUNA_APP_KEY")
        if not app_id or not app_key:
            raise ValueError("ADZUNA_APP_ID and ADZUNA_APP_KEY are required")
        jobs: list[Job] = []
        for role in self.roles:
            for location in self.locations:
                try:
                    data = self.client.get_json(f"https://api.adzuna.com/v1/api/jobs/{self.country}/search/1", {"app_id": app_id, "app_key": app_key, "what": role, "where": location, "results_per_page": 50})
                    for item in data.get("results", []):
                        salary = item.get("salary_min")
                        # Adzuna India salary amounts are yearly INR, while the local setting is LPA.
                        salary_lpa = float(salary) / 100000 if salary and self.country == "in" else None
                        jobs.append(Job(self.name, item["title"], (item.get("company") or {}).get("display_name", "Unknown company"), (item.get("location") or {}).get("display_name", location), item["redirect_url"], html_to_text(item.get("description") or ""), salary_min_lpa=salary_lpa, source_job_id=str(item.get("id", ""))))
                except Exception as exc:
                    # HTTP errors can embed app_key in the request URL; never log it.
                    LOG.error("Adzuna search failed: %s / %s (%s)", role, location, type(exc).__name__)
        return jobs


class RemoteOKSource(JobSource):
    name = "remoteok"

    def __init__(self, client: RateLimitedClient | None = None) -> None:
        self.client = client or RateLimitedClient()

    def fetch_jobs(self) -> list[Job]:
        data = self.client.get_json("https://remoteok.com/api")
        jobs: list[Job] = []
        for item in data:
            if not isinstance(item, dict) or not item.get("position") or not item.get("url"):
                continue
            jobs.append(Job(self.name, item["position"], item.get("company", "Unknown company"), item.get("location") or "Remote", item["url"], html_to_text(item.get("description") or ""), source_job_id=str(item.get("id", ""))))
        return jobs

