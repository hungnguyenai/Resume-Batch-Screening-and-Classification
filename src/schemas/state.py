"""AgentCore Platform v1.0"""

# ADR-005: State must be a flat TypedDict (see ADR-005 for the prohibited
# alternatives). LangGraph checkpoints use msgpack serialization, so only
# plain serializable fields are allowed. Do NOT add credentials or secrets.

from typing import Any
from framework.schemas.autonomous_state import AutonomousState


class State(AutonomousState):
    """Agent state — Batch Resume Screening & Classification (CMN-C2-686).

    All shared fields (user_input, status, session_id, node_history,
    error_log, hitl_*, iterations, thoughts, tool_calls, tool_results,
    working_memory, cost_usd, etc.) are inherited from AutonomousState
    (AutonomousBaseGraph parent — see src/graph/graph.py).

    Batch intake fields (written once by BatchIntakeNode.on_initialize;
    read-only for the rest of the invocation):

        job_description     Job description text supplied by the caller via
                            input_context["job_description"].
        resume_batch        The ORIGINAL caller-supplied batch, verbatim
                            (list of {"id"|"candidate_id": str, "text"|
                            "resume_text": str}). Kept for audit/traceability
                            per the APPI/PII retention note below.
        batch_size          len(resume_batch) as received (0 if malformed).
        batch_valid         False when job_description/resume_batch failed
                            intake validation (see docs/02_design.md
                            "Fail-closed intake"); every tool then refuses
                            to fabricate data for this session.

    Screening result fields (populated once, at assemble_output() time —
    see "State Flow" in docs/02_design.md for why these are NOT written
    incrementally per node/tool call):

        candidate_results   list[dict] — one entry per candidate: tier
                            (STRONG_MATCH | POSSIBLE_MATCH | NOT_A_MATCH |
                            NEEDS_HUMAN_REVIEW), outcome ("scored" |
                            "insufficient_information" | "not_processed_
                            iteration_limit"), score, extracted fields,
                            protected_signal_detected, flag_for_human_review.
        escalations         list[dict] — non-suppressible human-review
                            escalations (candidate_id, reason,
                            protected_signal_categories). Derived from the
                            full tool_results accumulator, which LangGraph
                            guarantees is never dropped mid-loop (operator.add
                            reducer) — see docs/02_design.md.
        current_resume_index  Best-effort progress counter, derived at
                            assemble_output() time from the count of
                            fetch_next_resume tool calls (tools cannot write
                            arbitrary top-level State fields in this
                            framework — see docs/02_design.md).

    APPI / PII retention note:
        resume_batch persists the caller's ORIGINAL text into the LangGraph
        checkpoint DB for traceability/audit. The REDACTED copy used for all
        downstream tool/LLM/output processing lives only in the in-process
        ResumeScreeningService session cache (src/services/screening_service.py),
        which is cleared at finalize (see ScreeningFinalizeNode). Operators
        must configure checkpoint-store retention/TTL per internal APPI
        policy for resume_batch; do not extend its retention beyond what the
        hiring workflow requires (see docs/07_operation_guide.md).
    """

    job_description: str
    resume_batch: list[dict[str, Any]]
    batch_size: int
    batch_valid: bool

    candidate_results: list[dict[str, Any]]
    escalations: list[dict[str, Any]]
    current_resume_index: int
