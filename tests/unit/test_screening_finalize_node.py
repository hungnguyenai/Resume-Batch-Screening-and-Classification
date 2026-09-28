"""Unit tests for src/nodes/screening_finalize_node.py."""

import uuid

from framework.schemas.trust_level import TrustLevel

import src.nodes.screening_finalize_node as finalize_module
from src.nodes.screening_finalize_node import ScreeningFinalizeNode
from src.services.screening_service import get_default_service


def _base_state(session_id, tool_results=None, batch_valid=True, batch_size=1):
    return {
        "session_id": session_id,
        "caller_trust_level": TrustLevel.INTERNAL.value,
        "correlation_id": "test-corr",
        "tool_results": tool_results or [],
        "candidate_results": [],
        "escalations": [],
        "batch_valid": batch_valid,
        "batch_size": batch_size,
        "resume_batch": [],
        "iterations": 3,
        "execution_time": {},
        "node_history": [],
    }


def test_required_trust_level_is_internal():
    assert ScreeningFinalizeNode.required_trust_level == TrustLevel.INTERNAL


def test_on_finalize_wires_assemble_output_and_adds_disclaimer():
    node = ScreeningFinalizeNode()
    session_id = str(uuid.uuid4())
    get_default_service().register_batch(session_id, "JD", [])
    tool_results = [
        {
            "tool": "score_candidate",
            "args": {},
            "result": {
                "candidate_id": "c1",
                "tier": "STRONG_MATCH",
                "outcome": "scored",
                "flag_for_human_review": False,
                "notes": "great fit",
            },
        }
    ]
    state = _base_state(session_id, tool_results=tool_results)
    result = node.on_finalize(state)

    final_output = result["final_output"]
    assert final_output["candidates_processed"] == 1
    assert final_output["candidate_results"][0]["candidate_id"] == "c1"
    assert "disclaimer" in final_output
    assert "decision-support" in final_output["disclaimer"]


def test_on_finalize_clears_the_session_cache():
    node = ScreeningFinalizeNode()
    session_id = str(uuid.uuid4())
    get_default_service().register_batch(
        session_id, "JD",
        [{"candidate_id": "c1", "redacted_text": "t", "protected_signal_categories": []}],
    )
    state = _base_state(session_id)
    node.on_finalize(state)
    assert get_default_service().get_next_candidate(session_id)["status"] == "unavailable"


def test_extra_security_gate_output_redacts_residual_notes():
    node = ScreeningFinalizeNode()
    result = {
        "final_output": {
            "candidate_results": [
                {"candidate_id": "c1", "notes": "Graduated in 1999 from State University."},
            ],
        }
    }
    gated = node._extra_security_gate_output(result)
    assert "1999" not in gated["final_output"]["candidate_results"][0]["notes"]


def test_defensive_check_recovers_a_dropped_escalation(monkeypatch):
    """Simulate a hypothetical bug in assemble_batch_output that omits an
    escalation from its output, and prove ScreeningFinalizeNode's defensive
    check forces it back in from the raw tool_results log."""

    def _broken_assemble(state):
        return {
            "final_output": {"candidate_results": [], "escalations": [], "batch_size": 1},
            "candidate_results": [],
            "escalations": [],  # BUG: dropped the escalation
            "current_resume_index": 1,
        }

    monkeypatch.setattr(finalize_module, "assemble_batch_output", _broken_assemble)

    node = ScreeningFinalizeNode()
    session_id = str(uuid.uuid4())
    get_default_service().register_batch(session_id, "JD", [])
    tool_results = [
        {
            "tool": "score_candidate",
            "result": {
                "candidate_id": "flagged-candidate",
                "flag_for_human_review": True,
                "tier": "NEEDS_HUMAN_REVIEW",
            },
        }
    ]
    state = _base_state(session_id, tool_results=tool_results)
    result = node.on_finalize(state)

    escalated_ids = {e["candidate_id"] for e in result["escalations"]}
    assert "flagged-candidate" in escalated_ids
    assert "flagged-candidate" in {
        e["candidate_id"] for e in result["final_output"]["escalations"]
    }
