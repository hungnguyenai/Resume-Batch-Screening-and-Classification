# CMN-C2-686 — Test Specification

## Test Strategy

- **Test types**: Unit (per node/service), Integration (full `agent.invoke()` think-act loop, scripted `FakeLLM`), Proof-of-Boundary (import isolation, state safety, invoke order, HITL placeholder, bias-escalation non-suppressibility).
- **No live dependencies**: no real LLM call and no network in any test — every test uses a scripted `FakeLLM` (`.complete()` + `.bind_tools()`) per `sdk/how-to/write-tests.md`.
- **Determinism**: `score_candidate_impl` and `redact_protected_attributes` are pure functions — same input → same output, no randomness, no wall-clock branching.

## Layout

```
tests/
  unit/
    test_bias_redaction.py            # each protected-attribute category + clean-text no-op
    test_scoring.py                   # tier thresholds, unknown-candidate fail-closed, escalation rule
    test_screening_service.py         # session cache: register/fetch/exhaust/unavailable/clear
    test_batch_intake_node.py         # valid batch, malformed JD, oversized batch, malformed resume entries
    test_screening_finalize_node.py   # assemble_output wiring, disclaimer, defensive escalation recovery
    test_resume_tools.py              # fetch_next_resume / score_candidate as bound LangChain tools
  integration/
    test_graph.py                     # full compiled Graph.invoke() — success path + malformed-batch path
  proof_of_boundary/
    test_import_isolation.py          # PB-4 (pre-existing, unmodified)
    test_state_safety.py              # PB-2/5 (pre-existing, unmodified)
    test_pb_invoke_order.py           # PB-6 (pre-existing, unmodified) — auto-discovers BatchIntakeNode + ScreeningFinalizeNode
    test_pb7_hitl_interrupt_propagation.py  # PB-7 (pre-existing, skip-guarded — HITL not enabled)
    test_pb_bias_escalation_survival.py     # NEW — graph-level proof: redaction + non-suppressible escalation
```

## Acceptance test cases

| ID | Scenario | Input | Expected |
|----|----------|-------|----------|
| TC-01 | Clean batch, all strong matches | 2 clean resumes, JD fully met | `status: success`; both `tier: STRONG_MATCH`; `escalations: []` |
| TC-02 | Malformed JD | `job_description` missing | `batch_valid: False`; `status: error`; no tool calls occur |
| TC-03 | Empty resume batch | `resume_batch: []` | `batch_valid: False`; `status: error` |
| TC-04 | Oversized batch | `resume_batch` with > `MAX_BATCH_SIZE` entries | `batch_valid: False`; `status: error`; explicit size message in `error_log` |
| TC-05 | Unparseable resume in an otherwise valid batch | one entry with empty/short `text` | that candidate: `outcome: insufficient_information`, `tier: NEEDS_HUMAN_REVIEW`; other candidates unaffected |
| TC-06 | Protected-attribute signal + borderline match | resume containing a redaction-triggering phrase, JD partial match | `tier: NEEDS_HUMAN_REVIEW`; `escalations` contains the candidate; evidence text handed to the LLM has the raw phrase replaced with `[REDACTED:...]` |
| TC-07 | Protected-attribute signal + strong/clear match | resume containing a redaction-triggering phrase, JD fully met | `protected_signal_detected: True` reported, but NOT auto-escalated (escalation is reserved for the borderline zone) |
| TC-08 | Escalation survives subsequent candidates (non-suppressibility) | 3-candidate batch: clean, protected+borderline, clean | the middle candidate's escalation is present in the FINAL `assemble_output()` result after 2 more iterations of unrelated processing |
| TC-09 | Unknown `candidate_id` passed to `score_candidate` | id never returned by `fetch_next_resume` | `outcome: insufficient_information`; no fabricated score |
| TC-10 | Iteration ceiling reached mid-batch | `max_iterations` set low enough that not all candidates are reached | unreached candidates appear with `outcome: not_processed_iteration_limit`; none silently omitted |
| TC-11 | Trust gate | `caller_trust_level < INTERNAL` on `BatchIntakeNode`/`ScreeningFinalizeNode` | S-1 denies; `status: error`; `execute()` never runs |
| TC-12 | Import isolation | class/module scan | no `agenticstar` import; `Graph` inherits `AutonomousBaseGraph` |
| TC-13 | Invoke order | PB-6 (pre-existing) | `BatchIntakeNode`/`ScreeningFinalizeNode` follow S-1→node_start→S-2→execute→S-3→node_complete |

## Traceability

| Impl issue | Design section (docs/02) | Test file(s) |
|---|---|---|
| #2 state schema | "State Definition" | `tests/proof_of_boundary/test_state_safety.py` |
| #3 input/security node | "Why a custom initialize node" | `tests/unit/test_batch_intake_node.py` |
| #4 retrieval/evidence node | "Tools" table (`fetch_next_resume`) | `tests/unit/test_resume_tools.py`, `tests/unit/test_screening_service.py` |
| #5 decision/workflow node | "Tools" table (`score_candidate`) | `tests/unit/test_scoring.py`, `tests/unit/test_resume_tools.py` |
| #6 output/audit node | "Why a custom finalize node" | `tests/unit/test_screening_finalize_node.py` |
| #7 graph composition | "Architecture Overview" | `tests/integration/test_graph.py`, `tests/proof_of_boundary/test_pb_bias_escalation_survival.py` |
| #9 PB state-safety | "State Constraints" | `tests/proof_of_boundary/test_state_safety.py` |
| #10 PB import-isolation | "Import Isolation Confirmation" | `tests/proof_of_boundary/test_import_isolation.py` |

## CI acceptance

- `run-tests` stage: `python -m pytest tests/ -v --tb=short` and `python -m pytest tests/proof_of_boundary/ -v --tb=short` both green.
- No test function is a bare stub (`gate-stub-check`).
- `pyproject.toml` dependency sections remain exactly pinned (`gate-dep-pinning`).
