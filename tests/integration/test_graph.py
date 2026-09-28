"""Integration tests — full compiled Graph.invoke() (initialize -> think <-> act -> finalize)."""

from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel

from src.graph.graph import Graph
from tests.fakes import ScriptedFakeLLM, final_step, tool_call_step

SESSION_ID = "integration-test-session"


def _internal_ctx(session_id: str = SESSION_ID) -> InvocationContext:
    return InvocationContext(session_id=session_id, caller_trust_level=TrustLevel.INTERNAL)


def _build_agent(script):
    llm = ScriptedFakeLLM(script)
    agent = Graph(config={"llm": llm, "budget_usd": 2.50, "max_iterations": 20})
    agent.compile()
    return agent, llm


def test_full_batch_success_path_two_candidates():
    script = [
        tool_call_step("fetch_next_resume", {"session_id": SESSION_ID}),
        tool_call_step(
            "score_candidate",
            {
                "session_id": SESSION_ID,
                "candidate_id": "cand-1",
                "extracted_skills": ["python", "sql"],
                "years_experience": 8,
                "education_level": "bachelor",
                "jd_requirements_met": ["python", "sql"],
                "jd_requirements_missing": [],
                "notes": "strong fit",
            },
        ),
        tool_call_step("fetch_next_resume", {"session_id": SESSION_ID}),
        tool_call_step(
            "score_candidate",
            {
                "session_id": SESSION_ID,
                "candidate_id": "cand-2",
                "extracted_skills": ["python"],
                "years_experience": 1,
                "education_level": "bachelor",
                "jd_requirements_met": [],
                "jd_requirements_missing": ["python", "sql"],
                "notes": "junior, limited fit",
            },
        ),
        tool_call_step("fetch_next_resume", {"session_id": SESSION_ID}),
        final_step("Batch complete."),
    ]
    agent, _llm = _build_agent(script)

    result = agent.invoke(
        "screen this batch",
        ctx=_internal_ctx(),
        input_context={
            "job_description": "Senior backend engineer. Required: Python, SQL, 5+ years.",
            "resume_batch": [
                {"id": "cand-1", "text": "Python and SQL engineer with 8 years experience."},
                {"id": "cand-2", "text": "Junior developer, one year of Python experience."},
            ],
        },
    )

    assert result["status"] == "success"
    output = result["output"]
    assert output["batch_valid"] is True
    assert output["batch_size"] == 2
    assert output["candidates_processed"] == 2
    tiers = {c["candidate_id"]: c["tier"] for c in output["candidate_results"]}
    assert tiers["cand-1"] == "STRONG_MATCH"
    assert tiers["cand-2"] == "NOT_A_MATCH"
    assert output["escalations"] == []
    assert "decision-support" in output["disclaimer"]


def test_malformed_batch_fails_closed_without_any_tool_calls():
    script = [final_step("Cannot process this batch.")]
    agent, llm = _build_agent(script)

    result = agent.invoke(
        "screen this batch",
        ctx=_internal_ctx(session_id="integration-test-session-malformed"),
        input_context={"resume_batch": []},  # job_description missing entirely
    )

    assert result["status"] == "error"
    # BatchIntakeNode sets status=ERROR on an invalid batch. BaseNode.__call__'s
    # S-2 gate check (`if state.get("status") == AgentStatus.ERROR.value: skip
    # execute()`) then short-circuits EVERY subsequent node — including
    # ThinkNode — before execute() runs, because it cannot distinguish "this
    # node's own S-2 rejection" from "an earlier node's domain error" by
    # inspecting bare `status`. The net (and, for this fail-closed design,
    # desirable) effect: the LLM is never called at all for an invalid batch.
    assert llm.call_count == 0


def test_trust_gate_denies_anonymous_caller():
    """S-1: BatchIntakeNode requires INTERNAL trust — an ANONYMOUS caller must
    be denied before on_initialize() ever runs."""
    script = [final_step("should not get here")]
    agent, llm = _build_agent(script)

    result = agent.invoke(
        "screen this batch",
        ctx=InvocationContext(session_id="anon-session", caller_trust_level=TrustLevel.ANONYMOUS),
        input_context={
            "job_description": "JD",
            "resume_batch": [{"id": "c1", "text": "x" * 40}],
        },
    )

    assert result["status"] == "error"
