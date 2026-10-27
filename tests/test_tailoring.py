import json
from types import SimpleNamespace

import pytest

from job_agent.config import load_config
from job_agent.tailoring.service import (
    GroundedSentence,
    TailoredMaterials,
    build_tailored_resume,
    generate_materials,
    parse_materials_json,
    render_cover_letter_pdf,
    resume_skills,
)


def test_tailoring_rejects_unsupported_evidence_and_skills() -> None:
    resume = "Built APIs in Python and FastAPI for production systems."
    good = {"summary": [{"text": "I built APIs in Python.", "evidence_quote": "Built APIs in Python"}], "ordered_skills": ["Python"], "cover_letter": [{"text": "I have production API experience.", "evidence_quote": "Python and FastAPI for production systems"}]}
    assert parse_materials_json(json.dumps(good), resume, ("Python", "FastAPI")).ordered_skills == ("Python",)
    bad = dict(good, ordered_skills=["AWS"])
    with pytest.raises(ValueError):
        parse_materials_json(json.dumps(bad), resume, ("Python", "FastAPI"))
    bad = dict(good, summary=[{"text": "I built AWS systems.", "evidence_quote": "Worked with AWS at scale"}])
    with pytest.raises(ValueError):
        parse_materials_json(json.dumps(bad), resume, ("Python", "FastAPI"))


def test_tailoring_uses_openai_structured_response() -> None:
    payload = {
        "summary": [{"text": "I work with Python.", "evidence_quote": "Python, JavaScript, TypeScript, SQL, Node.js"}],
        "ordered_skills": ["Python", "FastAPI"],
        "cover_letter": [{"text": "I have FastAPI experience.", "evidence_quote": "FastAPI, Express, React, Angular"}],
    }

    class FakeResponses:
        def create(self, **kwargs):
            assert kwargs["text"]["format"]["name"] == "tailored_materials"
            assert kwargs["store"] is False
            return SimpleNamespace(status="completed", output_text=json.dumps(payload))

    config = load_config("preferences.yaml")
    job = {"id": "test-job", "title": "Backend Engineer", "company": "Acme", "description": "Build Python APIs"}
    result = generate_materials(job, config, SimpleNamespace(responses=FakeResponses()))
    assert result.ordered_skills == ("Python", "FastAPI")


def test_tailored_resume_keeps_portfolio_and_all_source_skills() -> None:
    resume = (
        "# Candidate\nPortfolio: https://example.com\n\n"
        "## Professional summary\nOriginal summary\n\n"
        "## Technical skills\n"
        "- **Languages:** Python, SQL\n"
        "- **AI:** RAG (Retrieval-Augmented Generation), LangChain, pgvector\n"
        "- **Data:** Pandas, NumPy\n\n"
        "## Professional experience\nBuilt systems.\n"
    )
    assert resume_skills(resume) == (
        "Python", "SQL", "RAG (Retrieval-Augmented Generation)",
        "LangChain", "pgvector", "Pandas", "NumPy",
    )
    materials = TailoredMaterials(
        (GroundedSentence("Tailored summary.", "Built systems."),),
        ("LangChain", "Python"),
        (),
    )
    tailored = build_tailored_resume(resume, materials)
    assert "Portfolio: https://example.com" in tailored
    assert "## Professional summary\n\nTailored summary." in tailored
    assert "Original summary" not in tailored
    assert "## Technical skills" in tailored
    assert tailored.index("- **AI:**") < tailored.index("- **Languages:**")
    for skill in resume_skills(resume):
        assert skill in tailored


def test_cover_letter_pdf_is_created(tmp_path) -> None:
    path = tmp_path / "cover_letter.pdf"
    render_cover_letter_pdf(path, "Dear Hiring Team,\n\nI build APIs.\n\nSincerely,\nAlex")
    assert path.read_bytes().startswith(b"%PDF-")
    assert path.stat().st_size > 1000
