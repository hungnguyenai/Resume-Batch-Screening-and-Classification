# Template Design Specification

> CMN-C2-686 — ResumeScreeningClassificationAgent — Resume Batch Screening & Classification
> Scaffold issue: · Cat 2 · Industry CMN

## Position in AgentCore Architecture

- **Agent Class**: `Graph` (`src/graph/graph.py`)
- **L1 Base**: `AutonomousBaseGraph`
- **Three-Layer Separation**:
  - State: flat TypedDict composition (`src/schemas/state.py`, extends `AutonomousState`) — no Pydantic (msgpack incompatible)
  - Node: L1 inheritance (Template Method: `execute(self, state) -> dict` / `on_initialize` / `on_finalize` overrides only)
  - Graph: composition (`register_nodes()` node substitution; `add_edges()` is NOT overridden)

### Cat 2 business classification vs AutonomousBaseGraph L1 parent (documented exception)

Business Cat = **2** (a specific
job-to-be-done — batch resume screening & classification for one job
requisition), but the *workflow shape* is a bounded autonomous think-act-observe
loop over multiple resumes (batch iteration with self-directed per-resume
continuation), which exceeds Cat 1/Cat 2's fixed 5-node pipeline shape. Cat
business classification and L1 parent class are independent axes — pairing a Cat 2
workflow with `AutonomousBaseGraph` is a deliberate choice for this template, not a
misclassification.

## Architecture Overview — bounded think-act loop

```
START → initialize → think ⇄ act → finalize → END
```

All edges are wired by `AutonomousBaseGraph.add_edges()` (framework-owned, not
overridden). `_route_after_think()` routes to `act` while `tool_calls` is
non-empty, and to `finalize` once `status` is terminal
(`success`/`error`/`cancelled`) or `iterations >= max_iterations`.

### Node Configuration

| Slot | Class | Responsibility | Overrides default? |
|------|-------|-----------------|--------------------|
| `initialize` | `BatchIntakeNode(AutonomousInitializeNode)` (`src/nodes/batch_intake_node.py`) | Validate batch (JD present, batch non-empty, size ≤ `MAX_BATCH_SIZE`); apply protected-attribute redaction (`src/services/bias_redaction.py`) to every resume BEFORE the loop starts; register the redacted queue with `ResumeScreeningService` | Yes — CASE 2 |
| `think` | `ThinkNode` (framework default) | Calls `build_think_prompt(state)`; invokes the LLM; parses `tool_calls` | No |
| `act` | `ToolActNode` (framework default) | Executes `fetch_next_resume` / `score_candidate` tool calls | No |
| `finalize` | `ScreeningFinalizeNode(FinalizeNode)` (`src/nodes/screening_finalize_node.py`) | Calls `assemble_output()` (the default `FinalizeNode` does NOT — see "Why a custom FinalizeNode" below); defensive escalation-preservation check; output redaction pass; audit event; session cleanup | Yes — CASE 2 |

### Why a custom `initialize` node (input/security node)

Tools bound via `get_tools()` receive **only the arguments the LLM supplies**
(`ToolActNode.execute()`: `tool_fn.invoke(tool_args)`) — they do not receive
`AgentState`. If redaction happened lazily inside a tool, an LLM that never
calls that tool (or a malformed batch that skips it) could let unredacted text
leak into `working_memory`/prompts by some other path. Redacting once, for the
whole batch, before the loop starts — and registering only the redacted copy
in the session cache that tools read from — makes "unredacted text never
reaches a tool" a structural property of the design, not a per-call habit.

### Why a custom `finalize` node (output/audit node)

Per the framework's own Cat 3 sample (`scaffold/scaffold/src/examples/graph_cat3_sample.py`):
> "`finalize` — `FinalizeNode`: ... The default does NOT call `assemble_output()`
> ... unless you wire a custom `FinalizeNode` (CASE 2) whose `on_finalize()`
> returns `assemble_output(state)`."

Without this override, `Graph.assemble_output()` (the abstract method
`AutonomousBaseGraph` requires every subclass to implement) would simply never
run, and `final_output` would stay empty. `ScreeningFinalizeNode.on_finalize()`
calls the shared `assemble_batch_output(state)` function (`src/services/output_assembly.py`)
— the SAME function `Graph.assemble_output()` delegates to — so both paths
produce identical output shaping.

### Tools (retrieval/evidence node + decision/workflow node)

| Tool | Impl issue mapping | Behaviour |
|------|--------------------|-----------|
| `fetch_next_resume(session_id)` | #4 retrieval/evidence node | Returns the next candidate's REDACTED evidence text + provenance from `ResumeScreeningService`. Fail-closed: `status="no_more_resumes"` when the queue is exhausted, `status="unavailable"` (never fabricated content) when the session/batch is invalid. |
| `score_candidate(session_id, candidate_id, extracted_skills, years_experience, education_level, jd_requirements_met, jd_requirements_missing, notes)` | #5 decision/workflow node | Deterministic rubric scoring (`src/services/scoring.py:score_candidate_impl`) of LLM-*extracted* structured fields — the LLM's `think` step decides *when* to call it and turns ambiguous free text into structured fields; the tier computation itself is a pure function of those fields plus server-side ground truth, not "LLM vibes". Fails closed (`NEEDS_HUMAN_REVIEW` / `insufficient_information`) if `candidate_id` was never served for this session — refuses to fabricate a score for an unrecognized id. |

## Data Flow (per iteration)

```
                 ┌────────────────────────────────────────────┐
                 │ 1. think: build_think_prompt(state)          │
                 │    LLM reads JD + last tool_result +         │
                 │    iteration budget, decides next tool_call  │
                 └───────────────────┬──────────────────────────┘
                                     │ tool_calls non-empty
                                     ▼
                 ┌────────────────────────────────────────────┐
                 │ 2. act: ToolActNode executes                 │
                 │    fetch_next_resume  → redacted evidence    │
                 │    score_candidate    → tier + escalation    │
                 │    result appended to state["tool_results"]  │
                 │    (Annotated[list[dict], operator.add])     │
                 └───────────────────┬──────────────────────────┘
                                     │ loop back to think
                                     ▼
                       (repeat until no_more_resumes, then a
                        final no-tool-call think ends the loop)
                                     │
                                     ▼
                 ┌────────────────────────────────────────────┐
                 │ 3. finalize: ScreeningFinalizeNode           │
                 │    assemble_batch_output(state) scans the    │
                 │    FULL tool_results log → candidate_results │
                 │    + escalations; reconciles any candidate    │
                 │    never reached (iteration ceiling);         │
                 │    defensive non-suppressibility check;       │
                 │    output redaction pass; audit event;        │
                 │    session cache cleared                      │
                 └────────────────────────────────────────────┘
```

### State Flow — why `candidate_results`/`escalations` are NOT written incrementally

`ToolActNode.execute()` only ever returns `{"tool_calls": [], "tool_results":
results, "hitl_count": ...}` — it does not let a tool's return value update
arbitrary other top-level State keys. So `candidate_results` and `escalations`
are **derived once, at `assemble_output()` time**, by scanning the complete
`tool_results` accumulator (`Annotated[list[dict], operator.add]`). This is a
deliberate, framework-shaped design choice, not an oversight:

- `tool_results` is append-only by LangGraph's reducer — the framework itself
  guarantees no entry is ever dropped mid-loop, regardless of how many further
  `think`/`act` iterations follow.
- Deriving the final report from that guaranteed-complete log at the end is
  what makes a mid-batch bias escalation **non-suppressible**: it is not
  something any node/tool could "forget" to propagate forward — it is read
  straight from the log the framework itself maintains.
- `current_resume_index` is likewise derived at finalize (count of
  `fetch_next_resume` tool calls) rather than mutated per-iteration, for the
  same reason.

### State Definition (fields added to `AutonomousState`)

| Field | Type | Purpose | Set by |
|-------|------|---------|--------|
| `job_description` | str | JD text from `input_context["job_description"]` | `BatchIntakeNode` |
| `resume_batch` | list[dict] | Original caller-supplied batch (audit copy — see APPI note) | `BatchIntakeNode` |
| `batch_size` | int | `len(resume_batch)` as received | `BatchIntakeNode` |
| `batch_valid` | bool | False when intake validation failed | `BatchIntakeNode` |
| `candidate_results` | list[dict] | Pre-seeded fail-closed entries (malformed resumes) at intake; full list at finalize | `BatchIntakeNode` (partial) → `ScreeningFinalizeNode`/`assemble_output` (full) |
| `escalations` | list[dict] | Non-suppressible human-review escalations | `ScreeningFinalizeNode`/`assemble_output` |
| `current_resume_index` | int | Best-effort progress counter | `ScreeningFinalizeNode`/`assemble_output` |

**State Constraints (mandatory):**
- Flat TypedDict only (primitives + JSON-serializable types)
- No JWT, API keys, credentials in State (checkpoint DB leakage)
- `InvocationContext` via `InvocationContext.from_state(state)` only (not stored in State)
- No Pydantic models, dataclass, arbitrary Python objects (msgpack incompatible)

**APPI / PII retention note:** `resume_batch` persists the caller's ORIGINAL
text into the LangGraph checkpoint DB for audit/traceability. The REDACTED
working copy lives only in the in-process `ResumeScreeningService` session
cache and is purged via `clear_session()` at finalize. Operators must
configure checkpoint-store retention/TTL for `resume_batch` per internal APPI
policy — see `docs/07_operation_guide.md`.

## §2b — Bias-mitigation design (fail-closed + non-suppressible escalation)

### Fail-closed, no fabrication

- **Malformed resume** (empty/corrupt/missing text, below `MIN_TEXT_LEN`):
  `BatchIntakeNode` records an explicit `outcome: "insufficient_information"` /
  `tier: "NEEDS_HUMAN_REVIEW"` entry — never a fabricated score, never a
  "probably fine" default.
- **Malformed JD / empty batch**: `batch_valid=False`; `build_think_prompt()`
  instructs the LLM to call no tools; `fetch_next_resume` independently
  returns `status="unavailable"` even if the LLM tries anyway (defense in
  depth — the design does not rely on the LLM cooperating). In practice this
  branch is rarely even reached: `BaseNode.__call__()`'s S-2 gate check
  (`if state.get("status") == AgentStatus.ERROR.value: skip execute()`)
  short-circuits every node downstream of `BatchIntakeNode` — including
  `think` — the moment `status` is `ERROR`, because it cannot distinguish
  "this node's own S-2 rejection" from "an earlier node's domain error" by
  inspecting bare `status`. The net effect (verified in
  `tests/integration/test_graph.py::test_malformed_batch_fails_closed_without_any_tool_calls`)
  is that the LLM is never even called for an invalid batch — a stronger
  fail-closed guarantee than the prompt instruction alone would provide.
  `ScreeningFinalizeNode.on_finalize()` also explicitly re-asserts
  `status=ERROR` when `batch_valid` is False, as a second line of defense in
  case a future change to the loop ever reaches `finalize` without going
  through this cascade.
- **Unknown `candidate_id`** at `score_candidate`: refuses to score, returns
  `insufficient_information` instead of a fabricated result (protects against
  both a confused LLM and a stale/replayed tool call).
- **Iteration ceiling reached mid-batch**: `assemble_output()` reconciles the
  original batch against `candidate_results` and explicitly reports any
  un-reached candidate as `outcome: "not_processed_iteration_limit"` — never
  silently omitted from the report.

### Protected-attribute redaction + non-suppressible escalation

`src/services/bias_redaction.py` detects (regex/keyword heuristics — see file
docstring for the full limitation notes) proxy signals the platform's generic
PII masker does not target: graduation-year age proxies, gendered honorifics,
disability disclosure phrases, pregnancy/parental-leave mentions,
religious-affiliation org names, national-origin-coded phrases. Detected spans
are replaced with `[REDACTED:<CATEGORY>]` before the text is registered in the
session cache — this is the text `fetch_next_resume` hands to the LLM.

`score_candidate` looks up **server-side ground truth**
(`ResumeScreeningService.ground_truth_signal`) for whether the current
candidate had a protected signal — it does **not** trust an LLM-supplied flag.
When a protected signal coincides with a `POSSIBLE_MATCH` (the borderline
zone where a close decision could be biased), the tier is escalated to
`NEEDS_HUMAN_REVIEW` and an entry is appended to `escalations`.

**Non-suppressibility is proven at the graph level**, not asserted at the
node level: `tests/proof_of_boundary/test_pb_bias_escalation_survival.py`
compiles the real `Graph`, drives it through `.invoke()` with a scripted
`FakeLLM` across a 3-candidate batch (clean → protected-signal/borderline →
clean), and asserts the escalation raised for the middle candidate is still
present in `assemble_output()`'s final result after two more iterations of
unrelated candidate processing.

## Framework Utilization

### Shared Components Used
- [x] `InvocationContext` via `InvocationContext.from_state(state)` (available to any future node needing secrets; not required by the current tool set)
- [x] `SecurityViolationError` (`framework.errors`) — inherited from `BaseNode.__init_subclass__` S-5 scan
- [x] S-1: `required_trust_level = TrustLevel.INTERNAL` declared explicitly on `BatchIntakeNode` and `ScreeningFinalizeNode` (candidate PII; internal HR-ops tool only)
- [x] S-2: default PII scan (framework `@final`) + domain redaction happens in `on_initialize` (not the `_extra_security_gate_input` hook — redaction here targets `input_context`-derived batch content, not the `_PII_SCAN_FIELDS` the framework hook targets)
- [x] S-3: `ScreeningFinalizeNode._extra_security_gate_output()` — defense-in-depth re-redaction of any free-text `notes` field before the report leaves the node boundary
- [x] S-4: `emit_trace_event("resume_batch_completed", ...)` in `ScreeningFinalizeNode.on_finalize()` — domain-specific event only, does not duplicate the framework's automatic `node_start`/`node_complete`/`node_error` events

> **S-2/S-3 gate behaviour by node type (ADR-017):**
> - `FunctionNode` subclass (and its `AutonomousInitializeNode`/`FinalizeNode` descendants) → framework `@final` gate always runs automatically; extend via `_extra_security_gate_input()` / `_extra_security_gate_output()` only.

### Composition Pattern

- **Pattern**: `AutonomousBaseGraph` direct extension (no inner `GraphNode` subgraph — the bounded per-candidate loop IS the autonomous think-act loop; no separate domain workflow graph is needed).
- **Error propagation strategy**: n/a (no `GraphNode`/`RemoteAgentNode` composition in this template).

## Import Isolation Confirmation
- [x] Template does not import the agenticstar-platform SDK (Level 0)
- [x] Import targets: `framework/`, `shared/`, `langchain_core.tools`, and `src.*` only

## Known constraints (documented, not hidden)

- `ResumeScreeningService` is a module-level singleton keyed by `session_id`.
  Two concurrent `invoke()` calls sharing the SAME `session_id` are not
  supported (last `register_batch()` wins) — acceptable for a batch HR-ops
  tool invoked per hiring requisition; different `session_id`s never collide.
- `MAX_BATCH_SIZE` (8, `src/services/screening_service.py`) is sized against
  `config/agent.yaml`'s `max_iterations: 20` (≈2 iterations/candidate + 2). See
  `docs/07_operation_guide.md` for how to raise both together.

## Design Decision Record

| Decision | Option A | Option B | Chosen | Rationale |
|----------|----------|----------|--------|-----------|
| L1 base type | `AgentBaseGraph` | `AutonomousBaseGraph` | `AutonomousBaseGraph` | Bounded but self-directed per-candidate continuation (batch iteration count is not fixed at design time and the "next action" — fetch vs stop — is a loop-termination decision) exceeds the fixed 5-node pipeline shape |
| Evidence delivery to LLM | Tool reads full state directly | Tool reads a session-keyed service cache populated once at intake | Session-keyed service cache | `ToolActNode` only passes LLM-supplied args to tools — no state access — so a redacted, session-scoped cache is the mechanism that guarantees unredacted text is structurally unreachable by any tool |
| Escalation propagation | Incrementally written State field, mutated per tool call | Derived once at `assemble_output()` from the full `tool_results` accumulator | Derived from `tool_results` | `ToolActNode` cannot route a tool's return value into arbitrary State fields; the accumulator is framework-guaranteed append-only, which makes the escalation non-suppressible by construction rather than by convention |
| Bias-signal authority | Trust the LLM's self-reported flag | Server-side ground truth lookup by `candidate_id` | Server-side ground truth | An uncooperative or mistaken LLM must not be able to suppress or spoof a compliance-relevant escalation |
| Malformed-JD handling | Rely on the LLM to notice and stop | `build_think_prompt` instructs AND `fetch_next_resume`/`score_candidate` independently refuse to fabricate data | Defense in depth (both) | Fail-closed behaviour must hold even if the LLM ignores the instruction |

---

## Supported entry point — HTTP/gateway only (Marketplace out of scope)

Every node in this template declares `required_trust_level = INTERNAL`, which is the design
decision recorded for this agent: the data it reads is not material an arbitrary authenticated
caller should be able to query.

The one-shot Marketplace runner stamps the caller at `VERIFIED_EXTERNAL` and exposes no
configuration surface or elevation path to `INTERNAL`, so the S-1 gate refuses every Marketplace
invocation **before** `execute()` runs. Two consequences are worth stating, because both read as
a broken image: the Pod still reports success and the audit counters do not move, and the terminal
failure carries no reason, so the chat surface shows an opaque error.

Nesting does not change this. A subgraph is invoked with the caller's own context
(`subgraph.invoke(..., ctx=ctx)`), so the trust level propagates unchanged and an inner node
cannot be reached at a higher level than the outer call arrived with.

### The HTTP path is also closed, deliberately

The standalone adapter used to promote an anonymous caller straight to `INTERNAL` once it
presented the shared `INVOKE_AUTH_TOKEN`. That token authenticates a *deployment*, not a person,
so granting `INTERNAL` on it placed a back door behind the very gate this design depends on. The
adapter now grants `VERIFIED_EXTERNAL`, which is what its own documentation always described.
**The gate is unchanged** — every node still requires `INTERNAL`.

The consequence is stated rather than hidden: since the nodes require `INTERNAL` and nothing in
either entry point can now supply it, **this template currently has no reachable entry point at
all**. That is fail-closed and intended.

One legitimate route remains open: trust established by upstream middleware is passed through
unchanged, so a gateway that has verified the caller's identity can still reach these nodes.

### What is NOT being done

- The nodes' `required_trust_level` is **not** lowered. Doing so would widen who may query this
  data, which is a product decision and not an engineering one.
- No Marketplace image is published and the template is not registered as a Marketplace agent.
