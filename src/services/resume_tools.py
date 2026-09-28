"""AgentCore Platform v1.0"""

# LangChain-compatible tools bound to the module-level ResumeScreeningService
# singleton (see screening_service.get_default_service). These are returned by
# Graph.get_tools() and bound to the LLM via AutonomousBaseGraph._build_llm_with_tools()
# / ThinkNode; executed by the framework's ToolActNode.

from __future__ import annotations

from typing import Any
from langchain_core.tools import tool

from src.services.screening_service import get_default_service
from src.services.scoring import score_candidate_impl


@tool
def fetch_next_resume(session_id: str) -> dict[str, Any]:
    """Fetch the next candidate's already-redacted resume evidence for the current batch.

    Returns one of:
      - {"status": "ok", "candidate_id": ..., "evidence_text": ..., ...} — the
        next candidate's redacted evidence, with source provenance.
      - {"status": "no_more_resumes"} — the batch is exhausted; stop calling
        tools and summarize.
      - {"status": "unavailable", "reason": ...} — the session/batch is
        invalid; never fabricates resume content in this case.
    """
    return get_default_service().get_next_candidate(session_id)


@tool
def score_candidate(
    session_id: str,
    candidate_id: str,
    extracted_skills: list[str],
    years_experience: float = 0.0,
    education_level: str = "",
    jd_requirements_met: list[str] | None = None,
    jd_requirements_missing: list[str] | None = None,
    notes: str = "",
) -> dict[str, Any]:
    """Deterministically score one candidate against the job description rubric.

    Call this AFTER fetch_next_resume, using structured fields you extracted
    from the evidence_text (do not invent facts not present in the evidence).
    Returns a tier (STRONG_MATCH | POSSIBLE_MATCH | NOT_A_MATCH |
    NEEDS_HUMAN_REVIEW) plus any human-review escalation. Fails closed
    (NEEDS_HUMAN_REVIEW / insufficient_information) if candidate_id was never
    fetched for this session — it will not fabricate a score.
    """
    return score_candidate_impl(
        service=get_default_service(),
        session_id=session_id,
        candidate_id=candidate_id,
        extracted_skills=extracted_skills,
        years_experience=years_experience,
        education_level=education_level,
        jd_requirements_met=jd_requirements_met,
        jd_requirements_missing=jd_requirements_missing,
        notes=notes,
    )


RESUME_TOOLS: list[Any] = [fetch_next_resume, score_candidate]
