"""AgentCore Platform v1.0"""

# In-process session cache for the bounded batch resume-screening loop.
#
# WHY THIS EXISTS: AutonomousBaseGraph tools (LangChain @tool callables bound
# via get_tools()/ThinkNode) receive ONLY the args the LLM supplies — they do
# not receive AgentState (see framework.nodes.defaults.tool_act_node.ToolActNode
# .execute(): `tool_fn.invoke(tool_args)`). The redacted resume evidence for the
# current batch therefore cannot live in State alone if we want a guarantee
# that unredacted text never reaches a tool; it is held here, keyed by
# session_id, and handed out one candidate at a time via fetch_next_resume.
#
# THREAD-SAFETY / LIFECYCLE NOTE (documented, not hidden — see
# docs/02_design.md "Known constraints" and docs/07_operation_guide.md):
#   - Sessions are keyed by session_id, so concurrent invoke() calls with
#     DIFFERENT session_ids do not corrupt each other.
#   - Two concurrent invoke() calls sharing the SAME session_id is not
#     supported (last register_batch() wins) — this is an acceptable
#     constraint for a batch HR-ops tool invoked per hiring requisition.
#   - clear_session() is called by ScreeningFinalizeNode at the end of every
#     invocation so redacted resume text does not accumulate in server
#     memory indefinitely (APPI/PII minimization).

from __future__ import annotations

from typing import Any

MAX_BATCH_SIZE: int = 8
MIN_TEXT_LEN: int = 20


def extract_candidate_id(entry: Any, index: int) -> str:
    """Best-effort candidate id extraction shared by intake and reconciliation.

    Never raises: falls back to a positional placeholder id for a malformed
    entry so every candidate in a batch — parseable or not — gets a stable,
    unique id it can be reported against (fail-closed, not silently dropped).
    """
    if isinstance(entry, dict):
        candidate_id = entry.get("id") or entry.get("candidate_id")
        if isinstance(candidate_id, str) and candidate_id.strip():
            return candidate_id.strip()
    return f"candidate-{index + 1}"


def extract_raw_text(entry: Any) -> str | None:
    """Best-effort raw resume text extraction. Returns None if absent/invalid.

    Accepts "text" / "resume_text" / "raw_text" as the caller's field name —
    "raw_text" matches this template's documented input contract
    (docs/07_operation_guide.md); the other two are accepted for leniency.
    """
    if not isinstance(entry, dict):
        return None
    text = entry.get("raw_text") or entry.get("text") or entry.get("resume_text")
    return text if isinstance(text, str) else None


class ResumeScreeningService:
    """Per-session queue of redacted candidate evidence for the think-act loop."""

    def __init__(self) -> None:
        self._sessions: dict[str, dict[str, Any]] = {}

    def register_batch(
        self,
        session_id: str,
        job_description: str,
        candidates: list[dict[str, Any]],
    ) -> None:
        """Register a validated, already-redacted candidate queue for a session.

        `candidates` entries: {"candidate_id": str, "redacted_text": str,
        "protected_signal_categories": list[str]}.
        """
        self._sessions[session_id] = {
            "job_description": job_description,
            "queue": list(candidates),
            "cursor": 0,
            "served": {},  # candidate_id -> candidate dict, for score_candidate lookups
        }

    def get_next_candidate(self, session_id: str) -> dict[str, Any]:
        """Fail-closed fetch: never fabricates evidence for a missing/invalid session."""
        session = self._sessions.get(session_id)
        if session is None:
            return {
                "status": "unavailable",
                "reason": "no batch registered for this session_id",
            }
        queue = session["queue"]
        cursor = session["cursor"]
        if cursor >= len(queue):
            return {"status": "no_more_resumes"}
        candidate = queue[cursor]
        session["cursor"] = cursor + 1
        session["served"][candidate["candidate_id"]] = candidate
        return {
            "status": "ok",
            "candidate_id": candidate["candidate_id"],
            "evidence_text": candidate["redacted_text"],
            "protected_signal_categories": candidate["protected_signal_categories"],
            "source": "batch_upload",
            "provenance": f"resume batch entry for candidate_id={candidate['candidate_id']!r}",
        }

    def ground_truth_signal(self, session_id: str, candidate_id: str) -> list[str]:
        """Authoritative (server-side, non-LLM-suppressible) protected-signal lookup."""
        session = self._sessions.get(session_id)
        if not session:
            return []
        served = session["served"].get(candidate_id)
        return list(served["protected_signal_categories"]) if served else []

    def is_known_candidate(self, session_id: str, candidate_id: str) -> bool:
        """True only for a candidate this session actually served via fetch_next_resume.

        score_candidate uses this to refuse fabricating a score for an
        unrecognized/never-fetched candidate_id (fail-closed, no fabrication).
        """
        session = self._sessions.get(session_id)
        if not session:
            return False
        return candidate_id in session["served"]

    def all_candidate_ids(self, session_id: str) -> list[str]:
        """Every candidate_id registered for this session (served or not)."""
        session = self._sessions.get(session_id)
        if not session:
            return []
        return [c["candidate_id"] for c in session["queue"]]

    def clear_session(self, session_id: str) -> None:
        """Purge the redacted cache for a completed session (PII minimization)."""
        self._sessions.pop(session_id, None)


_default_service = ResumeScreeningService()


def get_default_service() -> ResumeScreeningService:
    """Module-level singleton used by get_tools() closures and lifecycle nodes.

    A singleton (rather than constructor injection) is required so that
    BatchIntakeNode / ScreeningFinalizeNode / the resume tools all stay
    zero-argument constructible — tests/proof_of_boundary/test_pb_invoke_order.py
    auto-discovers every concrete BaseNode subclass under src/nodes/ and
    instantiates it with node_cls() (no args). See docs/02_design.md.
    """
    return _default_service
