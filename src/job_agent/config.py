"""Validated non-secret configuration and environment loading."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True, slots=True)
class Profile:
    name: str
    email: str
    phone: str
    current_role: str
    experience_years: float
    skills: tuple[str, ...]
    linkedin_url: str
    github_url: str
    resume_path: Path


@dataclass(frozen=True, slots=True)
class SearchPreferences:
    target_roles: tuple[str, ...]
    locations: tuple[str, ...]
    minimum_salary_lpa: float
    blacklisted_companies: tuple[str, ...]
    review_threshold: int
    daily_apply_limit: int
    follow_up_days: int


@dataclass(frozen=True, slots=True)
class Config:
    profile: Profile
    search: SearchPreferences
    sources: dict[str, dict[str, Any]]
    database_path: Path
    artifacts_dir: Path
    config_path: Path


def _required(mapping: dict[str, Any], key: str, kind: type | tuple[type, ...]) -> Any:
    value = mapping.get(key)
    if not isinstance(value, kind) or (kind is str and not value.strip()):
        raise ValueError(f"Missing or invalid configuration value: {key}")
    return value


def _strings(mapping: dict[str, Any], key: str) -> tuple[str, ...]:
    value = mapping.get(key)
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ValueError(f"{key} must be a list of nonempty strings")
    return tuple(value)


def load_config(path: str | Path = "preferences.yaml") -> Config:
    config_path = Path(path).expanduser().resolve()
    with config_path.open(encoding="utf-8") as stream:
        raw = yaml.safe_load(stream)
    if not isinstance(raw, dict):
        raise ValueError("preferences.yaml must contain a mapping")
    profile_raw = _required(raw, "profile", dict)
    search_raw = _required(raw, "search", dict)
    sources = _required(raw, "sources", dict)
    root = config_path.parent

    def resolve(value: str) -> Path:
        candidate = Path(value).expanduser()
        return candidate if candidate.is_absolute() else root / candidate

    profile = Profile(
        name=_required(profile_raw, "name", str),
        email=_required(profile_raw, "email", str),
        phone=_required(profile_raw, "phone", str),
        current_role=_required(profile_raw, "current_role", str),
        experience_years=float(_required(profile_raw, "experience_years", (int, float))),
        skills=_strings(profile_raw, "skills"),
        linkedin_url=_required(profile_raw, "linkedin_url", str),
        github_url=_required(profile_raw, "github_url", str),
        resume_path=resolve(_required(profile_raw, "resume_path", str)),
    )
    search = SearchPreferences(
        target_roles=_strings(search_raw, "target_roles"),
        locations=_strings(search_raw, "locations"),
        minimum_salary_lpa=float(
            _required(search_raw, "minimum_salary_lpa", (int, float))
        ),
        blacklisted_companies=_strings(search_raw, "blacklisted_companies"),
        review_threshold=int(_required(search_raw, "review_threshold", int)),
        daily_apply_limit=int(_required(search_raw, "daily_apply_limit", int)),
        follow_up_days=int(_required(search_raw, "follow_up_days", int)),
    )
    if not 0 <= search.review_threshold <= 100:
        raise ValueError("review_threshold must be between 0 and 100")
    if search.daily_apply_limit < 1 or search.follow_up_days < 1:
        raise ValueError("daily_apply_limit and follow_up_days must be positive")
    if search.minimum_salary_lpa < 0 or profile.experience_years < 0:
        raise ValueError("salary and experience cannot be negative")
    if not profile.resume_path.is_file():
        raise ValueError(f"Resume not found: {profile.resume_path}")
    return Config(
        profile=profile,
        search=search,
        sources=sources,
        database_path=resolve(_required(raw, "database_path", str)),
        artifacts_dir=resolve(_required(raw, "artifacts_dir", str)),
        config_path=config_path,
    )

