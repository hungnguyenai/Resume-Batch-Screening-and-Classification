"""AgentCore Platform v1.0"""

# ─────────────────────────────────────────────────────────────────────────────
# Template Category (Cat) — choose ONE based on your Cat judgment:
#
# Cat 1 — Single technical capability (use-case-agnostic)
#   Purpose : Delivers one reusable, domain-independent capability.
#             The same template can be dropped into any project unchanged.
#   Examples: TextSummarizer, EmbeddingGenerator, LanguageDetector,
#             SentimentAnalyzer, KeywordExtractor
#   Parent  : AgentBaseGraph
#   Pipeline: START → initialize → pre_process → main → {route} → post_process → finalize → END
#                                             ↓ (RETRY, max 3)
#                                          pre_process
#   See src/examples/graph_cat1_sample.py for a full example.
#
# Cat 2 — Multi-step domain workflow (job-to-be-done)
#   Purpose : Orchestrates multiple steps to accomplish a specific business
#             outcome. The template name describes the outcome, not the
#             individual capabilities it uses.
#   Examples: InvoiceTriageAgent, ContractGapDetectionAgent,
#             ResumeScreeningAgent, SupportTicketResolutionAgent
#   Parent  : AgentBaseGraph (outer graph) + GraphNode in the `main` slot
#             wrapping an inner BaseGraph/AgentBaseGraph (domain workflow)
#   Pipeline: Same fixed 5-node backbone as Cat 1; domain complexity is
#             encapsulated inside DomainWorkflowGraphNode.get_subgraph().
#   Layout  : src/graph/graph.py                 ← outer graph (this file)
#             src/graph/domain_workflow_graph.py ← inner graph
#   See src/examples/graph_cat2_sample.py and src/examples/domain_workflow_graph_sample.py for examples.
#
# Cat 3 — Autonomous think→act→observe loop (self-directed)
#   Purpose : Runs an LLM-driven loop that decides its own next action,
#             executes tools, observes results, and terminates when the task
#             is complete or a budget/iteration ceiling is hit.
#   Examples: ResearchAgent, AutonomousCodeReviewAgent,
#             DataExplorationAgent, MultiStepPlannerAgent
#   Parent  : AutonomousBaseGraph
#   Pipeline: START → initialize → think ⇄ act → finalize → END
#   Config  : budget_usd and llm are required in config.yaml.
#   See src/examples/graph_cat3_sample.py for a full example.
#
# ── This template (CMN-C2-686) ────────────────────────────────────────────────
#
#   Business Cat = 2 (a specific job-to-be-done: batch resume screening &
#   classification), but the workflow itself is a bounded autonomous
#   think-act-observe loop over multiple resumes (batch iteration, self-
#   directed per-resume continuation) this
#   exceeds Cat 1/Cat 2's fixed 5-node pipeline shape, so it inherits
#   AutonomousBaseGraph (L1 direct) rather than AgentBaseGraph. Cat business
#   classification and L1 parent class are independent axes;
#   this is the documented exception where Cat 2 pairs with AutonomousBaseGraph.
#
#   L1 Base: AutonomousBaseGraph — see docs/02_design.md for the full node/
#   state/security design of the bounded batch think-act loop.
#
# framework.* imports are unchanged; agent-local imports use the src. prefix.
# ─────────────────────────────────────────────────────────────────────────────

from typing import Any
from framework.graph.autonomous_base_graph import AutonomousBaseGraph

from src.nodes.batch_intake_node import BatchIntakeNode
from src.nodes.screening_finalize_node import ScreeningFinalizeNode
from src.schemas.state import State
from src.services.output_assembly import assemble_batch_output
from src.services.resume_tools import RESUME_TOOLS

_MAX_ITERATIONS: int = 20


class Graph(AutonomousBaseGraph):
    """Autonomous think-act-observe loop graph (bounded batch iteration).

    Processes a bounded batch of resumes against one job description, one
    candidate per fetch/score sub-loop, via the standard
    initialize -> think <-> act -> finalize backbone (add_edges() is NOT
    overridden — see AutonomousBaseGraph.add_edges()).
    """

    @property
    def name(self) -> str:
        return "cmn_c2_686"

    @property
    def state_schema(self) -> type:
        return State

    @property
    def max_iterations(self) -> int:
        """Iteration ceiling for the think-act loop.

        Reads from config.yaml `max_iterations`; falls back to _MAX_ITERATIONS.
        Sized to the batch: each candidate costs ~2 iterations (one
        fetch_next_resume call + one score_candidate call), plus one closing
        fetch that returns no_more_resumes, plus one final no-tool-call
        iteration to end the loop — i.e. roughly 2*MAX_BATCH_SIZE + 2.
        With MAX_BATCH_SIZE=8 (src/services/screening_service.py) that is 18,
        comfortably under the default ceiling of 20. Operators processing
        larger batches must raise both MAX_BATCH_SIZE and max_iterations
        together (capped by the framework at 1000) — see
        docs/07_operation_guide.md.
        """
        return int(self.config.get("max_iterations", _MAX_ITERATIONS))

    def get_tools(self) -> list[Any]:
        """Tools available to the LLM on each think iteration.

        fetch_next_resume — pulls the next candidate's redacted evidence.
        score_candidate    — deterministic rubric scoring of LLM-extracted
                             structured fields, with server-side (non-LLM-
                             suppressible) bias-escalation ground truth.
        See src/services/resume_tools.py.
        """
        return RESUME_TOOLS

    def build_think_prompt(self, state: Any) -> str:
        """Build the prompt fed to ThinkNode on each iteration."""
        if not state.get("batch_valid", False):
            reasons = "; ".join(state.get("error_log", [])) or "unknown validation error"
            return (
                "The submitted resume batch is invalid and cannot be processed: "
                f"{reasons}. Do not call any tools. Respond with a short message "
                "explaining that the batch could not be processed."
            )

        job_description = state.get("job_description", "")
        batch_size = state.get("batch_size", 0)
        iterations = state.get("iterations", 0)
        tool_results = state.get("tool_results", []) or []
        last_result = tool_results[-1] if tool_results else None

        scored_ids = {
            entry.get("result", {}).get("candidate_id")
            for entry in tool_results
            if entry.get("tool") == "score_candidate"
        }

        return (
            "You are an internal HR resume-screening assistant processing a "
            "bounded batch of candidates for ONE job requisition.\n\n"
            f"Job description:\n{job_description}\n\n"
            f"Batch size: {batch_size}. Candidates scored so far: {len(scored_ids)}.\n"
            f"Iterations used: {iterations}/{self.max_iterations}.\n"
            f"Most recent tool result: {last_result}\n\n"
            "Process ONE candidate at a time:\n"
            f"1. Call fetch_next_resume(session_id={state.get('session_id', '')!r}) to "
            "get the next candidate's redacted evidence text.\n"
            "   - If status='no_more_resumes': the batch is complete — call no "
            "     further tools and respond with a short summary.\n"
            "   - If status='unavailable': the batch/session is invalid — call "
            "     no further tools; do not fabricate a result.\n"
            "2. Read evidence_text. Extract ONLY facts present in the text: "
            "skills, years_experience, education_level, and which job "
            "requirements are met vs missing. Never invent facts not present "
            "in the evidence.\n"
            f"3. Call score_candidate(session_id={state.get('session_id', '')!r}, "
            "candidate_id=..., extracted_skills=[...], years_experience=..., "
            "education_level=..., jd_requirements_met=[...], "
            "jd_requirements_missing=[...], notes=...) with your extraction.\n"
            "4. Repeat from step 1 until fetch_next_resume returns "
            "no_more_resumes, then stop calling tools and respond with a short "
            "final summary."
        )

    def assemble_output(self, state: Any) -> dict[str, Any]:
        """Shape the final output dict from terminal state.

        Wired into the response by ScreeningFinalizeNode.on_finalize() (CASE 2
        override — the default FinalizeNode does not call this). Delegates to
        the free function in src/services/output_assembly.py so the same
        logic is usable without an agent-instance reference (keeps
        ScreeningFinalizeNode zero-argument constructible).
        """
        return assemble_batch_output(state)

    def register_nodes(self) -> None:
        # CASE 2 (framework's graph_cat3_sample.py): call super() first to wire
        # all four default slots, then replace initialize/finalize only.
        # Do NOT override add_edges() — the think<->act loop wiring stays
        # framework-owned.
        super().register_nodes()
        self._nodes["initialize"] = BatchIntakeNode()
        self._nodes["finalize"] = ScreeningFinalizeNode()
