"""Unit tests for src/services/scoring.py — deterministic rubric scoring."""

import pytest

from src.services.scoring import score_candidate_impl
from src.services.screening_service import ResumeScreeningService


@pytest.fixture
def service():
    """Fresh service instance per test — never the module-level singleton,
    so tests never leak state into each other."""
    return ResumeScreeningService()


def _register_and_serve(service: ResumeScreeningService, session_id, candidate_id,
                         redacted_text="evidence", categories=None):
    service.register_batch(
        session_id,
        "Senior backend engineer. Required: Python, SQL, 5+ years experience.",
        [{"candidate_id": candidate_id, "redacted_text": redacted_text,
          "protected_signal_categories": categories or []}],
    )
    service.get_next_candidate(session_id)  # marks candidate as "served"


def test_strong_match_scores_high_and_no_escalation(service):
    _register_and_serve(service, "s1", "cand-1")
    result = score_candidate_impl(
        service, "s1", "cand-1",
        extracted_skills=["python", "sql"],
        years_experience=8,
        education_level="bachelor",
        jd_requirements_met=["python", "sql"],
        jd_requirements_missing=[],
        notes="excellent fit",
    )
    assert result["outcome"] == "scored"
    assert result["tier"] == "STRONG_MATCH"
    assert result["flag_for_human_review"] is False
    assert result["protected_signal_detected"] is False


def test_not_a_match_scores_low(service):
    _register_and_serve(service, "s2", "cand-2")
    result = score_candidate_impl(
        service, "s2", "cand-2",
        extracted_skills=[],
        years_experience=0,
        education_level="",
        jd_requirements_met=[],
        jd_requirements_missing=["python", "sql"],
        notes="no relevant background",
    )
    assert result["tier"] == "NOT_A_MATCH"
    assert result["flag_for_human_review"] is False


def test_borderline_match_without_protected_signal_stays_possible_match(service):
    _register_and_serve(service, "s3", "cand-3", categories=[])
    result = score_candidate_impl(
        service, "s3", "cand-3",
        extracted_skills=["python"],
        years_experience=2,
        education_level="bachelor",
        jd_requirements_met=["python", "teamwork"],
        jd_requirements_missing=["leadership"],
        notes="maybe fits",
    )
    assert result["tier"] == "POSSIBLE_MATCH"
    assert result["flag_for_human_review"] is False
    assert result["escalation_reason"] is None


def test_borderline_match_with_protected_signal_escalates_to_human_review(service):
    _register_and_serve(service, "s4", "cand-4", categories=["age_proxy"])
    result = score_candidate_impl(
        service, "s4", "cand-4",
        extracted_skills=["python"],
        years_experience=2,
        education_level="bachelor",
        jd_requirements_met=["python", "teamwork"],
        jd_requirements_missing=["leadership"],
        notes="maybe fits",
    )
    assert result["tier"] == "NEEDS_HUMAN_REVIEW"
    assert result["flag_for_human_review"] is True
    assert result["protected_signal_detected"] is True
    assert "age_proxy" in result["protected_signal_categories"]
    assert result["escalation_reason"] is not None


def test_strong_match_with_protected_signal_does_not_auto_escalate(service):
    """A clear STRONG_MATCH is not the close/borderline decision this
    escalation targets — protected_signal_detected is still reported for
    transparency, but flag_for_human_review stays False."""
    _register_and_serve(service, "s5", "cand-5", categories=["gender_proxy"])
    result = score_candidate_impl(
        service, "s5", "cand-5",
        extracted_skills=["python", "sql"],
        years_experience=8,
        education_level="bachelor",
        jd_requirements_met=["python", "sql"],
        jd_requirements_missing=[],
        notes="excellent fit",
    )
    assert result["tier"] == "STRONG_MATCH"
    assert result["protected_signal_detected"] is True
    assert result["flag_for_human_review"] is False


def test_unknown_candidate_id_fails_closed_no_fabrication(service):
    """candidate_id was never served for this session — must refuse to score,
    not fabricate a result."""
    service.register_batch("s6", "JD text", [])
    result = score_candidate_impl(
        service, "s6", "never-fetched-id",
        extracted_skills=["python"],
        years_experience=5,
        education_level="bachelor",
        jd_requirements_met=["python"],
        jd_requirements_missing=[],
        notes="",
    )
    assert result["outcome"] == "insufficient_information"
    assert result["tier"] == "NEEDS_HUMAN_REVIEW"
    assert result["score"] is None
    assert result["flag_for_human_review"] is True


def test_unregistered_session_fails_closed(service):
    result = score_candidate_impl(
        service, "nonexistent-session", "cand-x",
        extracted_skills=[], years_experience=0, education_level="",
        jd_requirements_met=[], jd_requirements_missing=[], notes="",
    )
    assert result["outcome"] == "insufficient_information"
