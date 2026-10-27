import pytest
from types import SimpleNamespace

from job_agent.config import SearchPreferences, load_config
from job_agent.models import Job
from job_agent.scoring.service import hard_filter, parse_score_json, score_with_openai


PREFS = SearchPreferences(("Backend Engineer",), ("Bangalore", "Remote"), 15, ("Blocked Inc",), 70, 15, 7)


def test_hard_filters_blacklist_location_and_salary() -> None:
    job = Job("test", "Engineer", "Blocked Inc", "New York", "https://example.com", salary_max_lpa=12)
    reasons = hard_filter(job, PREFS)
    assert len(reasons) == 3


def test_bengaluru_and_remote_are_accepted() -> None:
    for location in ("Bengaluru", "Remote - India"):
        assert not hard_filter(Job("test", "Engineer", "Acme", location, "https://example.com"), PREFS)


def test_explicit_lpa_range_below_minimum_is_filtered() -> None:
    job = Job("test", "Engineer", "Acme", "Bengaluru", "https://example.com", description="Salary: ₹12-14 LPA")
    assert any("Salary ceiling 14 LPA" in reason for reason in hard_filter(job, PREFS))


def test_score_json_is_strict() -> None:
    result = parse_score_json('{"score": 75, "reasons": ["Python"], "missing_skills": [], "dealbreakers": []}')
    assert result.score == 75
    for bad in ('```json\n{}\n```', '{"score": true, "reasons": [], "missing_skills": [], "dealbreakers": []}', '{"score": 101, "reasons": [], "missing_skills": [], "dealbreakers": []}', '{"score": 75, "reasons": "yes", "missing_skills": [], "dealbreakers": []}'):
        with pytest.raises(ValueError):
            parse_score_json(bad)


def test_openai_score_retries_invalid_json_once() -> None:
    class FakeResponses:
        def __init__(self) -> None:
            self.calls = 0

        def create(self, **kwargs):
            self.calls += 1
            text = "not json" if self.calls == 1 else '{"score": 72, "reasons": ["Python"], "missing_skills": [], "dealbreakers": []}'
            assert kwargs["text"]["format"]["type"] == "json_schema"
            assert kwargs["store"] is False
            return SimpleNamespace(status="completed", output_text=text)

    fake = SimpleNamespace(responses=FakeResponses())
    config = load_config("preferences.yaml")
    result = score_with_openai(Job("test", "Backend Engineer", "Acme", "Remote", "https://example.com", description="Python APIs"), config, fake)
    assert result.score == 72
    assert fake.responses.calls == 2
