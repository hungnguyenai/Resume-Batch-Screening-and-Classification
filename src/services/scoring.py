"""AgentCore Platform v1.0"""

# Deterministic rubric scoring (CMN-C2-686 §2b / docs/02_design.md).
#
# score_candidate_impl is a PURE function of its arguments plus a
# ResumeScreeningService ground-truth lookup — it does not call the LLM and
# produces the same tier for the same inputs every time. The LLM's job
# (in ThinkNode, via build_think_prompt) is to read the redacted resume
# evidence and extract structured fields; THIS function turns those
# structured fields into a tier using a fixed rubric. This split is the
# "the scoring itself is not LLM-vibes" requirement from the proposal.
#
# Non-suppressible bias escalation: protected_signal_detected is looked up
# from the service's SERVER-SIDE ground truth for this candidate_id — it is
# never taken from LLM-supplied arguments. An uncooperative or mistaken LLM
# therefore cannot suppress or spoof the escalation.

from __future__ import annotations

from typing import Any

from src.services.screening_service import ResumeScreeningService

STRONG_MATCH_THRESHOLD: float = 75.0
POSSIBLE_MATCH_THRESHOLD: float = 45.0
MAX_EXPERIENCE_YEARS_CREDIT: float = 10.0


def score_candidate_impl(
    service: ResumeScreeningService,
    session_id: str,
    candidate_id: str,
    extracted_skills: list[str] | None,
    years_experience: float | None,
    education_level: str | None,
    jd_requirements_met: list[str] | None,
    jd_requirements_missing: list[str] | None,
    notes: str | None,
) -> dict[str, Any]:
    """Score one candidate against the job description rubric.

    Fails closed (does not fabricate a score) when candidate_id was never
    served to the caller for this session — e.g. a hallucinated id, a batch
    that was never registered, or a duplicate/late call after the batch
    already completed.
    """
    if not service.is_known_candidate(session_id, candidate_id):
        return {
            "candidate_id": candidate_id,
            "tier": "NEEDS_HUMAN_REVIEW",
            "outcome": "insufficient_information",
            "score": None,
            "reason": ("candidate_id not recognized as served for this session_id; " "refusing to fabricate a score"),
            "extracted_skills": [],
            "jd_requirements_met": [],
            "jd_requirements_missing": [],
            "notes": "",
            "protected_signal_detected": False,
            "protected_signal_categories": [],
            "flag_for_human_review": True,
            "escalation_reason": None,
        }

    met = list(jd_requirements_met or [])
    missing = list(jd_requirements_missing or [])
    total = len(met) + len(missing)
    match_ratio = (len(met) / total) if total else 0.0
    experience_credit = min(max(years_experience or 0.0, 0.0), MAX_EXPERIENCE_YEARS_CREDIT)

    score = (match_ratio * 70.0) + (experience_credit * 3.0)
    score = max(0.0, min(100.0, score))

    if score >= STRONG_MATCH_THRESHOLD:
        tier = "STRONG_MATCH"
    elif score >= POSSIBLE_MATCH_THRESHOLD:
        tier = "POSSIBLE_MATCH"
    else:
        tier = "NOT_A_MATCH"

    ground_truth_categories = service.ground_truth_signal(session_id, candidate_id)
    protected_signal_detected = bool(ground_truth_categories)

    flag_for_human_review = False
    escalation_reason = None
    # Escalate only in the borderline zone — a clear STRONG_MATCH/NOT_A_MATCH
    # is not the "close decision" the proposal's bias-mitigation requirement
    # targets; a POSSIBLE_MATCH combined with a detected protected-attribute
    # proxy signal is exactly that close-decision case.
    if protected_signal_detected and tier == "POSSIBLE_MATCH":
        flag_for_human_review = True
        tier = "NEEDS_HUMAN_REVIEW"
        escalation_reason = (
            "borderline match combined with detected protected-attribute "
            f"proxy signal(s) ({', '.join(ground_truth_categories)}) — routed "
            "to human review to prevent automated bias in a close decision"
        )

    return {
        "candidate_id": candidate_id,
        "tier": tier,
        "outcome": "scored",
        "score": round(score, 1),
        "reason": None,
        "extracted_skills": list(extracted_skills or []),
        "jd_requirements_met": met,
        "jd_requirements_missing": missing,
        "notes": notes or "",
        "protected_signal_detected": protected_signal_detected,
        "protected_signal_categories": ground_truth_categories,
        "flag_for_human_review": flag_for_human_review,
        "escalation_reason": escalation_reason,
    }
