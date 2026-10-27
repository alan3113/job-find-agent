"""Instantiate configured plugins and save discovered jobs independently."""

from __future__ import annotations

import logging

from job_agent.config import Config
from job_agent.db.store import Store
from job_agent.sources.base import JobSource
from job_agent.sources.gmail import GmailAlertSource
from job_agent.sources.public import AdzunaSource, GreenhouseSource, LeverSource, RemoteOKSource

LOG = logging.getLogger(__name__)


def configured_sources(config: Config) -> list[JobSource]:
    settings = config.sources
    sources: list[JobSource] = []
    if settings.get("gmail", {}).get("enabled"):
        sources.append(GmailAlertSource(settings["gmail"].get("query", "newer_than:7d")))
    if settings.get("greenhouse", {}).get("enabled"):
        sources.append(GreenhouseSource(settings["greenhouse"].get("board_tokens", [])))
    if settings.get("lever", {}).get("enabled"):
        sources.append(LeverSource(settings["lever"].get("company_slugs", [])))
    if settings.get("adzuna", {}).get("enabled"):
        sources.append(AdzunaSource(settings["adzuna"].get("country", "in"), config.search.target_roles, config.search.locations))
    if settings.get("remoteok", {}).get("enabled"):
        sources.append(RemoteOKSource())
    return sources


def discover(config: Config, store: Store) -> dict[str, int]:
    counts: dict[str, int] = {}
    for source in configured_sources(config):
        counts[source.name] = 0
        try:
            for job in source.fetch_jobs():
                try:
                    if store.add_job(job):
                        counts[source.name] += 1
                except Exception:
                    LOG.exception("Could not save %s job: %s", source.name, job.url)
        except Exception:
            LOG.exception("Discovery source failed: %s", source.name)
    return counts

