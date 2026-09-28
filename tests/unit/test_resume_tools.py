"""Unit tests for src/services/resume_tools.py — the bound LangChain tools."""

import uuid

from src.services.resume_tools import RESUME_TOOLS, fetch_next_resume, score_candidate
from src.services.screening_service import get_default_service


def test_resume_tools_registry_has_both_tools():
    names = {t.name for t in RESUME_TOOLS}
    assert names == {"fetch_next_resume", "score_candidate"}


def test_fetch_next_resume_tool_invoke_returns_registered_candidate():
    session_id = str(uuid.uuid4())
    get_default_service().register_batch(
        session_id, "JD",
        [{"candidate_id": "c1", "redacted_text": "redacted evidence", "protected_signal_categories": []}],
    )
    result = fetch_next_resume.invoke({"session_id": session_id})
    assert result["status"] == "ok"
    assert result["candidate_id"] == "c1"
    assert result["evidence_text"] == "redacted evidence"


def test_fetch_next_resume_tool_fails_closed_for_unknown_session():
    result = fetch_next_resume.invoke({"session_id": "no-such-session"})
    assert result["status"] == "unavailable"


def test_score_candidate_tool_invoke_end_to_end():
    session_id = str(uuid.uuid4())
    get_default_service().register_batch(
        session_id, "JD",
        [{"candidate_id": "c1", "redacted_text": "redacted evidence", "protected_signal_categories": []}],
    )
    fetch_next_resume.invoke({"session_id": session_id})  # marks c1 as served

    result = score_candidate.invoke(
        {
            "session_id": session_id,
            "candidate_id": "c1",
            "extracted_skills": ["python", "sql"],
            "years_experience": 6,
            "education_level": "bachelor",
            "jd_requirements_met": ["python", "sql"],
            "jd_requirements_missing": [],
            "notes": "strong candidate",
        }
    )
    assert result["outcome"] == "scored"
    assert result["tier"] == "STRONG_MATCH"


def test_score_candidate_tool_fails_closed_for_unfetched_candidate():
    session_id = str(uuid.uuid4())
    get_default_service().register_batch(session_id, "JD", [])
    result = score_candidate.invoke(
        {
            "session_id": session_id,
            "candidate_id": "never-fetched",
            "extracted_skills": [],
        }
    )
    assert result["outcome"] == "insufficient_information"
