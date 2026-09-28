"""Unit tests for src/nodes/batch_intake_node.py."""

import uuid

from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.nodes.batch_intake_node import BatchIntakeNode
from src.services.screening_service import MAX_BATCH_SIZE, get_default_service


def _state(input_context, session_id=None):
    return {
        "session_id": session_id or str(uuid.uuid4()),
        "caller_trust_level": TrustLevel.INTERNAL.value,
        "input_context": input_context,
        "correlation_id": "test-corr",
    }


def test_required_trust_level_is_internal():
    assert BatchIntakeNode.required_trust_level == TrustLevel.INTERNAL


def test_valid_batch_is_accepted_and_redacted():
    node = BatchIntakeNode()
    session_id = str(uuid.uuid4())
    state = _state(
        {
            "job_description": "Senior backend engineer. Required: Python, SQL.",
            "resume_batch": [
                {"id": "cand-1", "text": "Skilled Python and SQL engineer with 6 years experience."},
            ],
        },
        session_id=session_id,
    )
    result = node.on_initialize(state)

    assert result["batch_valid"] is True
    assert result["batch_size"] == 1
    assert result["candidate_results"] == []  # no malformed entries pre-seeded
    assert result["escalations"] == []

    # The redacted queue must be registered under this session_id.
    fetched = get_default_service().get_next_candidate(session_id)
    assert fetched["status"] == "ok"
    assert fetched["candidate_id"] == "cand-1"


def test_missing_job_description_fails_closed():
    node = BatchIntakeNode()
    session_id = str(uuid.uuid4())
    state = _state(
        {"resume_batch": [{"id": "c1", "text": "x" * 30}]},
        session_id=session_id,
    )
    result = node.on_initialize(state)

    assert result["batch_valid"] is False
    assert result["status"] == AgentStatus.ERROR
    assert "job_description" in result["error_log"][0]

    # fetch_next_resume must fail closed too, even if something tries anyway.
    fetched = get_default_service().get_next_candidate(session_id)
    assert fetched["status"] in ("unavailable", "no_more_resumes")


def test_empty_resume_batch_fails_closed():
    node = BatchIntakeNode()
    state = _state({"job_description": "JD text", "resume_batch": []})
    result = node.on_initialize(state)
    assert result["batch_valid"] is False
    assert result["status"] == AgentStatus.ERROR


def test_oversized_batch_fails_closed():
    node = BatchIntakeNode()
    oversized = [{"id": f"c{i}", "text": "x" * 30} for i in range(MAX_BATCH_SIZE + 1)]
    state = _state({"job_description": "JD text", "resume_batch": oversized})
    result = node.on_initialize(state)
    assert result["batch_valid"] is False
    assert "exceeds max batch size" in result["error_log"][0]


def test_malformed_resume_entry_is_recorded_insufficient_information_not_dropped():
    node = BatchIntakeNode()
    session_id = str(uuid.uuid4())
    state = _state(
        {
            "job_description": "JD text",
            "resume_batch": [
                {"id": "good-1", "text": "A" * 40},
                {"id": "bad-1", "text": ""},  # empty text -> unparseable
                {"id": "bad-2"},  # missing text key entirely
            ],
        },
        session_id=session_id,
    )
    result = node.on_initialize(state)

    assert result["batch_valid"] is True
    assert result["batch_size"] == 3
    preseeded_ids = {c["candidate_id"] for c in result["candidate_results"]}
    assert preseeded_ids == {"bad-1", "bad-2"}
    for entry in result["candidate_results"]:
        assert entry["outcome"] == "insufficient_information"
        assert entry["tier"] == "NEEDS_HUMAN_REVIEW"
        assert entry["score"] is None

    # Only the good candidate should be queryable via fetch_next_resume.
    fetched = get_default_service().get_next_candidate(session_id)
    assert fetched["candidate_id"] == "good-1"


def test_execute_merges_setup_and_on_initialize():
    """Sanity check: the framework's AutonomousInitializeNode.execute() Template
    Method merges _setup() (loop resets etc.) with our on_initialize() output."""
    node = BatchIntakeNode()
    state = _state(
        {
            "job_description": "JD",
            "resume_batch": [{"id": "c1", "text": "x" * 40}],
        }
    )
    result = node.execute(state)
    assert result["batch_valid"] is True
    assert result["tool_calls"] == []  # from _setup()
    assert result["iterations"] == 0  # from _setup()
