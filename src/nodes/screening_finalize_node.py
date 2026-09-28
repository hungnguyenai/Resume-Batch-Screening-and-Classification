"""AgentCore Platform v1.0"""

# Output/audit node: replaces the default FinalizeNode slot
# (CASE 2 override — see framework's graph_cat3_sample.py: "the default
# FinalizeNode does NOT call assemble_output()"). Wires assemble_output() into
# the response, applies a final output security/redaction pass via
# _extra_security_gate_output(), asserts no escalation was silently dropped,
# appends the decision-support disclaimer, emits the batch-completion audit
# event, and clears the session's redacted-text cache (PII minimization).
#
# Zero-argument constructible — see src/services/screening_service.py
# docstring and tests/proof_of_boundary/test_pb_invoke_order.py.

from __future__ import annotations

from typing import Any, ClassVar

from framework.nodes.defaults.finalize_node import FinalizeNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.services.bias_redaction import redact_protected_attributes
from src.services.output_assembly import assemble_batch_output
from src.services.screening_service import get_default_service

_DISCLAIMER = (
    "This report is decision-support only. It is not an automated hiring "
    "decision. All NEEDS_HUMAN_REVIEW and escalated candidates require human "
    "review before any employment action."
)


class ScreeningFinalizeNode(FinalizeNode):
    """Assemble, audit, and safely finalize the batch screening report."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.INTERNAL

    def on_finalize(self, state: AgentState) -> dict[str, Any]:
        assembled = assemble_batch_output(state)
        final_output = dict(assembled.get("final_output", {}))
        escalations = list(assembled.get("escalations", []))

        # Defensive non-suppressibility check: recompute which candidate_ids
        # raised flag_for_human_review directly from tool_results (the
        # framework-guaranteed append-only log) and force any that are
        # missing from the assembled escalations list back in. This must
        # never trigger in normal operation — it exists so a future defect
        # in assemble_batch_output() fails safe instead of silently dropping
        # a bias escalation.
        raised_ids = set()
        for entry in state.get("tool_results", []) or []:
            if entry.get("tool") != "score_candidate":
                continue
            result = entry.get("result") or {}
            if result.get("flag_for_human_review") and result.get("candidate_id"):
                raised_ids.add(result["candidate_id"])

        assembled_ids = {e.get("candidate_id") for e in escalations}
        for missing_id in sorted(raised_ids - assembled_ids):
            escalations.append(
                {
                    "candidate_id": missing_id,
                    "reason": "recovered by finalize defensive check — see tool_results",
                    "protected_signal_categories": [],
                }
            )

        final_output["escalations"] = escalations
        final_output["disclaimer"] = _DISCLAIMER

        emit_trace_event(
            "resume_batch_completed",
            {
                "batch_size": final_output.get("batch_size", 0),
                "candidates_processed": final_output.get("candidates_processed", 0),
                "escalation_count": len(escalations),
            },
            state,
        )

        result_update: dict[str, Any] = {"final_output": final_output, "escalations": escalations}

        # ThinkNode derives status purely from tool_calls emptiness (SUCCESS
        # once no tool_calls remain) — that would silently overwrite a
        # batch-intake failure with "success" the moment the LLM (correctly)
        # stops calling tools for an invalid batch. Re-assert ERROR here so
        # the overall invocation status reflects the fail-closed intake
        # decision, not just "the loop ended without a tool call".
        if not state.get("batch_valid", False):
            result_update["status"] = AgentStatus.ERROR.value

        session_id = str(state.get("session_id", ""))
        get_default_service().clear_session(session_id)

        return result_update

    def _extra_security_gate_output(self, result: dict[str, Any]) -> dict[str, Any]:
        """Final output redaction pass — S-3 domain extension.

        Runs after the framework's default credential scan. Re-applies the
        protected-attribute redaction to any free-text notes field so a
        human-review packet never carries an un-redacted proxy signal, even
        if a node upstream forgot to (defense in depth).
        """
        final_output = result.get("final_output")
        if isinstance(final_output, dict):
            for candidate in final_output.get("candidate_results", []) or []:
                if isinstance(candidate, dict) and isinstance(candidate.get("notes"), str):
                    redacted_notes, _ = redact_protected_attributes(candidate["notes"])
                    candidate["notes"] = redacted_notes
        return result
