"""AgentCore Platform v1.0"""

# Standalone HTTP entry point for the agent.
# Entry points are adapters only — no business logic here.
# For platform-level routing, AgentGateway calls agent.invoke() directly.

from typing import Any, cast
import os
import secrets
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

import re
from pathlib import Path

from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from framework.secrets.context import bound_secrets
from shared.secrets import factory as secrets_factory
from src.graph.graph import Graph

app = FastAPI(title="Agent")


def _read_agent_config() -> dict[str, float | int | None]:
    """Read budget_usd/max_iterations from config/agent.yaml's `config:` block.

    config/agent.yaml is the single source of truth for these values (per
    docs/sdk/concepts/config-and-secrets.md) -- this must never duplicate
    literal values that could drift from the manifest. Stdlib-only, line-based
    extraction (no pyyaml dependency), matching the convention already used by
    scripts/check_cat_consistency.py for this same file.
    """
    text = Path(__file__).resolve().parents[2].joinpath("config", "agent.yaml").read_text()
    budget_match = re.search(r"^\s*budget_usd:\s*([\d.]+|null)\s*(?:#.*)?$", text, re.MULTILINE)
    iterations_match = re.search(r"^\s*max_iterations:\s*(\d+)\s*(?:#.*)?$", text, re.MULTILINE)
    budget_raw = budget_match.group(1) if budget_match else None
    budget_usd = None if budget_raw is None or budget_raw == "null" else float(budget_raw)
    max_iterations = int(iterations_match.group(1)) if iterations_match else None
    return {"budget_usd": budget_usd, "max_iterations": max_iterations}


class _StgStubLLM:
    """Deterministic stand-in for AnthropicLLMClient, used only when the
    `anthropic` extra (langchain_anthropic) is unavailable in this
    environment -- a framework/CI gap tracked separately, not caused by this
    template. .complete() MUST return the same structured
    {"content": str, "tool_calls": list} shape as the real LLM client --
    this is the contract ThinkNode's response parsing requires (see
    tests/fakes.py's ScriptedFakeLLM/final_step, the project's own
    documented compatible fake). Always returning an empty tool_calls list
    means ThinkNode treats the very first iteration as final (no tool call),
    so the loop ends immediately via the framework's normal "no tool_calls
    left" path -- real graph/node wiring (BatchIntakeNode -> ThinkNode ->
    ScreeningFinalizeNode) still runs end-to-end, it simply performs no
    candidate scoring (output_assembly.py's reconciliation step then reports
    every candidate honestly as not_processed_iteration_limit, rather than
    fabricating a score). This lets Stage 5 STG smoke boot the real server
    and exercise real graph/node wiring without a live Anthropic call;
    production callers get the real AnthropicLLMClient once the dependency
    gap is resolved platform-side.
    """

    def complete(self, prompt: str, **kwargs: Any) -> dict[str, Any]:
        return {
            "content": (
                "STG stub response -- no live LLM call was made "
                "(langchain_anthropic is unavailable in this environment); "
                "ending the batch without candidate scoring."
            ),
            "tool_calls": [],
        }

    def bind_tools(self, tools: list[Any]) -> "_StgStubLLM":
        return self


# AutonomousBaseGraph (this agent's L1 parent) requires an llm client plus
# budget_usd/max_iterations wired into config at construction time -- omitting
# them raises ConfigError at compile(). AnthropicLLMClient resolves the API
# key via the bound SecretProvider at call time (ctx.secrets.require), never
# os.environ -- see docs/sdk/concepts/config-and-secrets.md.
try:
    from shared.services.llm.anthropic_client import AnthropicLLMClient

    _llm = AnthropicLLMClient(model="claude-3-5-sonnet-20241022")
except ImportError:
    _llm = _StgStubLLM()

_agent_config = _read_agent_config()

agent = Graph(config={"llm": _llm, **_agent_config})
agent.compile()
# Replace namespace/agent_name to match the agent's manifest values.
agent.provision_secrets(secrets_factory(namespace="cmn-c2-686", agent_name="cmn_c2_686"))


class InvokeRequest(BaseModel):
    # `input` stays the primary AgentCore contract field (per
    # docs/sdk/how-to/add-standalone-api.md). Batch screening callers should
    # prefer job_description/resume_batch below (passed through as
    # input_context — see src/nodes/batch_initialize_node.py
    # _extract_batch_request()); `input` remains supported as a JSON-encoded
    # {"job_description": ..., "resume_batch": [...]} fallback for callers
    # that can only pass a single string.
    input: str = ""
    session_id: str = ""
    job_description: str = ""
    resume_batch: list[dict[str, Any]] = []


@app.post("/invoke", response_model=None)
async def invoke(req: InvokeRequest, request: Request) -> dict[str, Any]:
    trust = getattr(request.state, "trust_level", TrustLevel.ANONYMOUS)
    # Standalone caller auth: when
    # INVOKE_AUTH_TOKEN is set on the server environment, callers that no upstream
    # middleware vouched for (still ANONYMOUS) must present it as a Bearer token
    # and run at VERIFIED_EXTERNAL. Middleware-established trust is never demoted.
    # This adapter is the entry-point auth boundary (standalone equivalent of
    # platform AuthMiddleware) — a deployment-level caller credential, not an
    # agent secret, so ctx.secrets does not apply (no InvocationContext exists
    # before auth). This is the documented entry-point exception.
    expected = os.environ.get("INVOKE_AUTH_TOKEN")
    if expected and trust is TrustLevel.ANONYMOUS:
        supplied = request.headers.get("authorization", "")
        # Compare bytes: compare_digest raises TypeError on non-ASCII str input
        # (headers decode as latin-1), which would 500 instead of the generic 401.
        if not secrets.compare_digest(supplied.encode(), f"Bearer {expected}".encode()):
            # Generic body on purpose — do not leak whether the token was absent,
            # malformed, or wrong.
            raise HTTPException(status_code=401, detail="Token is invalid or expired.")
        # VERIFIED_EXTERNAL, never INTERNAL: this token authenticates a deployment,
        # not a person. Handing out INTERNAL here would let anyone holding one shared
        # server secret read records the S-1 gate reserves for named internal staff.
        # Consequence, stated deliberately: the nodes require INTERNAL, so this path
        # now reaches no node either. That is fail-closed and intended -- see
        # docs/02_design.md. Do NOT 'fix' it by lowering the nodes' required level.
        trust = TrustLevel.VERIFIED_EXTERNAL
    with bound_secrets(agent._secrets_provider):
        ctx = InvocationContext(
            session_id=req.session_id or str(uuid4()),
            caller_trust_level=trust,
            caller_id=getattr(request.state, "caller_id", ""),
        )
        # This template's real payload (job_description + resume_batch) rides
        # in input_context, the documented read-only caller-metadata channel
        # (see docs/sdk/concepts/state-and-schemas.md) — BatchInitializeNode
        # reads it from there first, falling back to a JSON-encoded `input`
        # string. `input` alone still satisfies the base AgentCore contract.
        return cast(
            "dict[str, Any]",
            agent.invoke(
                req.input,
                ctx=ctx,
                input_context={
                    "job_description": req.job_description,
                    "resume_batch": req.resume_batch,
                },
            ),
        )


@app.get("/health", response_model=None)
def health() -> dict[str, str]:
    return {"status": "ok", "agent": "cmn_c2_686"}
