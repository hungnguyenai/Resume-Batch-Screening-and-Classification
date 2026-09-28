"""AgentCore Platform v1.0"""

# Input/security node: replaces the default AutonomousInitializeNode
# slot (CASE 2 override — see framework's graph_cat3_sample.py). Validates the
# incoming batch (schema/size limits), applies the domain-aware protected-attribute
# redaction pass (src/services/bias_redaction.py) BEFORE the think-act loop ever
# starts, and registers the redacted candidate queue with the shared
# ResumeScreeningService session cache.
#
# Zero-argument constructible by design (module-level singleton service, not
# constructor injection) — see src/services/screening_service.py docstring and
# tests/proof_of_boundary/test_pb_invoke_order.py, which auto-instantiates every
# concrete BaseNode subclass under src/nodes/ via node_cls() with no arguments.

from __future__ import annotations

from typing import Any, ClassVar, cast

from framework.nodes.defaults.autonomous_initialize_node import AutonomousInitializeNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.services.bias_redaction import redact_protected_attributes
from src.services.screening_service import (
    MAX_BATCH_SIZE,
    MIN_TEXT_LEN,
    extract_candidate_id,
    extract_raw_text,
    get_default_service,
)


class BatchIntakeNode(AutonomousInitializeNode):
    """Validate + redact the batch before the autonomous loop starts.

    This is an internal HR-ops tool that handles candidate PII — S-1 trust
    gate requires INTERNAL (declared explicitly; never rely
    on the framework's ANONYMOUS default).
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.INTERNAL

    def on_initialize(self, state: AgentState) -> dict[str, Any]:
        input_context = state.get("input_context", {}) or {}
        job_description = input_context.get("job_description")
        resumes_in = input_context.get("resume_batch")
        if resumes_in is None:
            resumes_in = input_context.get("resumes")

        errors: list[str] = []
        if not isinstance(job_description, str) or not job_description.strip():
            errors.append("job_description missing or empty in input_context")
        if not isinstance(resumes_in, list) or not resumes_in:
            errors.append("resume_batch missing or empty in input_context")
        elif len(resumes_in) > MAX_BATCH_SIZE:
            errors.append(f"resume_batch size {len(resumes_in)} exceeds max batch size {MAX_BATCH_SIZE}")

        session_id = str(state.get("session_id", ""))

        if errors:
            # Fail-closed: register an EMPTY queue so fetch_next_resume can never
            # fabricate evidence for this session, even if the LLM tries anyway.
            get_default_service().register_batch(session_id, job_description="", candidates=[])
            return {
                "job_description": job_description if isinstance(job_description, str) else "",
                "resume_batch": resumes_in if isinstance(resumes_in, list) else [],
                "batch_size": 0,
                "batch_valid": False,
                "candidate_results": [],
                "escalations": [],
                "current_resume_index": 0,
                "status": AgentStatus.ERROR.value,
                "error_log": [f"[BatchIntakeNode] invalid batch: {'; '.join(errors)}"],
            }

        # The check above already returned ERROR if either value was invalid, so
        # from here their types are guaranteed. cast is a no-op at run time.
        resumes_in = cast("list[Any]", resumes_in)
        job_description = cast("str", job_description)

        sanitized_candidates: list[dict[str, Any]] = []
        preseeded_results: list[dict[str, Any]] = []

        for idx, entry in enumerate(resumes_in):
            candidate_id = extract_candidate_id(entry, idx)
            raw_text = extract_raw_text(entry)
            if not raw_text or len(raw_text.strip()) < MIN_TEXT_LEN:
                # Fail-closed per candidate: never fabricate a score for an
                # empty/corrupt/missing resume — record an explicit outcome
                # instead (spec §2a).
                preseeded_results.append(
                    {
                        "candidate_id": candidate_id,
                        "tier": "NEEDS_HUMAN_REVIEW",
                        "outcome": "insufficient_information",
                        "score": None,
                        "reason": "resume text missing, empty, or too short to screen",
                        "extracted_skills": [],
                        "jd_requirements_met": [],
                        "jd_requirements_missing": [],
                        "notes": "",
                        "protected_signal_detected": False,
                        "protected_signal_categories": [],
                        "flag_for_human_review": True,
                        "escalation_reason": None,
                    }
                )
                continue

            redacted_text, signal_categories = redact_protected_attributes(raw_text)
            sanitized_candidates.append(
                {
                    "candidate_id": candidate_id,
                    "redacted_text": redacted_text,
                    "protected_signal_categories": signal_categories,
                }
            )

        get_default_service().register_batch(session_id, job_description, sanitized_candidates)

        return {
            "job_description": job_description,
            "resume_batch": resumes_in,
            "batch_size": len(resumes_in),
            "batch_valid": True,
            "candidate_results": preseeded_results,
            "escalations": [],
            "current_resume_index": 0,
        }
