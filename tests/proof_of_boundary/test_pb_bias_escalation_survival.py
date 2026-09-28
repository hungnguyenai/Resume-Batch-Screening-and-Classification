# PB — Bias-mitigation non-suppressibility (CMN-C2-686 §2b)
#
# This is the #1 recurring defect class from prior batch templates: an
# isolated node-level test can prove a redaction/escalation function works in
# isolation while missing a real bypass in how the compiled graph wires nodes
# together. This test therefore compiles the REAL Graph, drives it through
# .invoke() end-to-end with a scripted FakeLLM (no real LLM call, no
# network), and proves two properties at the GRAPH level:
#
#   (a) A resume containing a protected-attribute-revealing signal never
#       reaches the LLM (think prompts) or the final report in unredacted
#       form.
#   (b) An escalation raised for one candidate mid-batch survives ALL
#       subsequent candidate processing and is still present in the final
#       assemble_output() result.

from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel

from src.graph.graph import Graph
from tests.fakes import ScriptedFakeLLM, final_step, tool_call_step

SESSION_ID = "pb-bias-escalation-session"
RAW_TRIGGER_PHRASE = "Graduated in 1975"  # age-proxy signal — must never leak downstream


def _score_step(candidate_id: str, met: list[str], missing: list[str], years: float) -> dict:
    return tool_call_step(
        "score_candidate",
        {
            "session_id": SESSION_ID,
            "candidate_id": candidate_id,
            "extracted_skills": met,
            "years_experience": years,
            "education_level": "bachelor",
            "jd_requirements_met": met,
            "jd_requirements_missing": missing,
            "notes": f"summary for {candidate_id}",
        },
    )


def test_bias_signal_redacted_and_escalation_survives_subsequent_candidates():
    script = [
        tool_call_step("fetch_next_resume", {"session_id": SESSION_ID}),  # -> cand-A
        _score_step("cand-A", met=["python", "sql"], missing=[], years=8),  # STRONG_MATCH
        tool_call_step("fetch_next_resume", {"session_id": SESSION_ID}),  # -> cand-B
        _score_step("cand-B", met=["python", "teamwork"], missing=["leadership"], years=2),  # POSSIBLE_MATCH -> escalates
        tool_call_step("fetch_next_resume", {"session_id": SESSION_ID}),  # -> cand-C
        _score_step("cand-C", met=["python", "sql"], missing=[], years=9),  # STRONG_MATCH, AFTER the escalation
        tool_call_step("fetch_next_resume", {"session_id": SESSION_ID}),  # -> no_more_resumes
        final_step("Batch complete."),
    ]
    llm = ScriptedFakeLLM(script)
    agent = Graph(config={"llm": llm, "budget_usd": 2.50, "max_iterations": 20})
    agent.compile()

    result = agent.invoke(
        "screen this batch",
        ctx=InvocationContext(session_id=SESSION_ID, caller_trust_level=TrustLevel.INTERNAL),
        input_context={
            "job_description": "Backend engineer. Required: Python, SQL, teamwork, leadership.",
            "resume_batch": [
                {"id": "cand-A", "text": "Skilled Python and SQL engineer, 8 years experience."},
                {
                    "id": "cand-B",
                    "text": f"{RAW_TRIGGER_PHRASE}. Solid Python and teamwork background, 2 years experience.",
                },
                {"id": "cand-C", "text": "Experienced Python and SQL engineer, 9 years experience."},
            ],
        },
    )

    assert result["status"] == "success"
    output = result["output"]

    # ── (a) unredacted signal never reaches the LLM or the final report ──
    for prompt in llm.prompts:
        assert RAW_TRIGGER_PHRASE not in prompt, "raw bias-signal text leaked into a think prompt"
        assert "1975" not in prompt, "raw age-proxy year leaked into a think prompt"

    output_str = str(output)
    assert RAW_TRIGGER_PHRASE not in output_str
    assert "1975" not in output_str

    # ── (b) escalation raised for cand-B survives cand-C's processing ──
    escalated_ids = {e["candidate_id"] for e in output["escalations"]}
    assert "cand-B" in escalated_ids

    results_by_id = {c["candidate_id"]: c for c in output["candidate_results"]}
    assert results_by_id["cand-B"]["tier"] == "NEEDS_HUMAN_REVIEW"
    assert results_by_id["cand-B"]["flag_for_human_review"] is True
    assert results_by_id["cand-B"]["protected_signal_detected"] is True
    assert "age_proxy" in results_by_id["cand-B"]["protected_signal_categories"]

    # cand-A and cand-C (processed before/after the escalation) are unaffected.
    assert results_by_id["cand-A"]["tier"] == "STRONG_MATCH"
    assert results_by_id["cand-A"]["flag_for_human_review"] is False
    assert results_by_id["cand-C"]["tier"] == "STRONG_MATCH"
    assert results_by_id["cand-C"]["flag_for_human_review"] is False

    # All three candidates were actually processed — none silently dropped.
    assert output["candidates_processed"] == 3
