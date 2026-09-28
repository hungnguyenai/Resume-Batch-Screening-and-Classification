"""AgentCore Platform v1.0"""

# Pure function shared by Graph.assemble_output() (the AutonomousBaseGraph
# abstract-method contract) and ScreeningFinalizeNode.on_finalize() (the CASE 2
# override that actually WIRES assemble_output into the response — see
# framework's graph_cat3_sample.py note: the default FinalizeNode does not
# call assemble_output() on its own). Kept as a free function (not a Graph
# method) so ScreeningFinalizeNode can call it without needing a reference to
# the agent instance, keeping the node zero-argument constructible for
# tests/proof_of_boundary/test_pb_invoke_order.py's auto-discovery.

from __future__ import annotations

from typing import Any

from src.services.screening_service import extract_candidate_id


def assemble_batch_output(state: dict[str, Any]) -> dict[str, Any]:
    """Derive the final batch report from the full tool_results accumulator.

    tool_results is an Annotated[list[dict], operator.add] field — LangGraph
    guarantees every entry ever returned by ToolActNode is still present here
    at finalize time, regardless of how many further iterations followed. This
    is what makes a mid-batch escalation "non-suppressible": it is read from
    a framework-guaranteed append-only log, not from a value that a later
    node/tool call could silently overwrite.
    """
    tool_results = state.get("tool_results", []) or []

    # Malformed candidates recorded at intake (fail-closed, before the loop
    # ever started) — preserved as the starting point.
    candidate_results: list[dict[str, Any]] = list(state.get("candidate_results", []) or [])
    escalations: list[dict[str, Any]] = list(state.get("escalations", []) or [])
    seen_ids = {c.get("candidate_id") for c in candidate_results}

    fetch_count = 0
    for entry in tool_results:
        tool_name = entry.get("tool")
        if tool_name == "fetch_next_resume":
            fetch_count += 1
            continue
        if tool_name != "score_candidate":
            continue
        result = entry.get("result") or {}
        candidate_id = result.get("candidate_id")
        if not candidate_id or candidate_id in seen_ids:
            continue
        seen_ids.add(candidate_id)
        candidate_results.append(result)
        if result.get("flag_for_human_review"):
            escalations.append(
                {
                    "candidate_id": candidate_id,
                    "reason": result.get("escalation_reason") or result.get("reason") or "flagged for human review",
                    "protected_signal_categories": result.get("protected_signal_categories", []),
                }
            )

    # Reconciliation: any candidate registered in the original batch that
    # never received a candidate_results entry (e.g. the iteration ceiling
    # was hit mid-batch) must be reported explicitly — never silently
    # omitted from the report (fail-closed, no fabrication of "processed").
    if state.get("batch_valid", False):
        for idx, entry in enumerate(state.get("resume_batch", []) or []):
            candidate_id = extract_candidate_id(entry, idx)
            if candidate_id in seen_ids:
                continue
            seen_ids.add(candidate_id)
            candidate_results.append(
                {
                    "candidate_id": candidate_id,
                    "tier": "NEEDS_HUMAN_REVIEW",
                    "outcome": "not_processed_iteration_limit",
                    "score": None,
                    "reason": "batch iteration ceiling reached before this candidate was processed",
                    "protected_signal_detected": False,
                    "protected_signal_categories": [],
                    "flag_for_human_review": True,
                    "escalation_reason": None,
                }
            )

    final_output = {
        "batch_size": state.get("batch_size", 0),
        "batch_valid": state.get("batch_valid", False),
        "candidates_processed": len(candidate_results),
        "candidate_results": candidate_results,
        "escalations": escalations,
        "iterations_used": state.get("iterations", 0),
        "current_resume_index": fetch_count,
    }

    return {
        "final_output": final_output,
        "candidate_results": candidate_results,
        "escalations": escalations,
        "current_resume_index": fetch_count,
    }
