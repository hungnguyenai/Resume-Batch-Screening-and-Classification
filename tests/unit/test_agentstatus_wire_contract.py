"""Regression test: AgentStatus wire-contract.

BatchIntakeNode.on_initialize() only ever sets "status" on its fail-closed
(invalid batch) path -- the success path deliberately omits "status" (the
framework backbone defaults it). ScreeningFinalizeNode.on_finalize() re-
asserts "status" on the same invalid-batch path (see its docstring rationale
for why ThinkNode's derived status must not silently overwrite a fail-closed
intake decision). These tests pin the EXACT wire type on both status-bearing
paths: a plain str equal to AgentStatus.ERROR.value, never the bare enum
member (existing tests compare against AgentStatus.ERROR directly, which is
insufficient because AgentStatus is str-compatible and both forms compare
equal).
"""

from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from src.nodes.batch_intake_node import BatchIntakeNode
from src.nodes.screening_finalize_node import ScreeningFinalizeNode


def _state(**kw) -> AgentState:
    base = {
        "correlation_id": "test-correlation",
        "session_id": "test-session",
        "thread_id": "test-thread",
        "trace_id": "test-trace",
        "node_history": [],
        "error_log": [],
        "input_context": {},
        "tool_results": [],
        "batch_valid": False,
        "job_description": "",
        "resume_batch": [],
    }
    base.update(kw)
    return base  # type: ignore[return-value]


class TestAgentStatusWireContract:
    def test_invalid_batch_status_is_plain_value_string(self):
        result = BatchIntakeNode().on_initialize(_state(input_context={}))
        assert result["status"] == AgentStatus.ERROR.value
        assert type(result["status"]) is str


class TestAgentStatusWireContractFinalize:
    def test_invalid_batch_status_is_plain_value_string(self):
        result = ScreeningFinalizeNode().on_finalize(_state(batch_valid=False))
        assert result["status"] == AgentStatus.ERROR.value
        assert type(result["status"]) is str
