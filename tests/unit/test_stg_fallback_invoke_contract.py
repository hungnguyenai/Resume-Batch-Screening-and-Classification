"""End-to-end regression test: a valid batch request must not error out
through the STG fallback LLM path.

test_server_import.py only proves the server module imports and the config
is wired -- it would still pass even if every real /invoke request failed.
This test drives a real module.agent.invoke() call (the same object the
FastAPI /invoke handler uses) with a valid job_description + one valid
resume, and asserts the overall status is not "error". In this repo's CI
environment (langchain_anthropic is not installed),
this exercises the _StgStubLLM fallback path specifically; if the real
AnthropicLLMClient import succeeds instead (e.g. once the platform
dependency gap is resolved), the same assertion holds against the real
client's response, so this test is not fallback-path-specific by
construction.
"""

import importlib

from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from framework.secrets.context import bound_secrets


class TestStgFallbackInvokeContract:
    def test_valid_batch_request_does_not_error(self):
        module = importlib.import_module("src.api.server")
        with bound_secrets(module.agent._secrets_provider):
            ctx = InvocationContext(
                session_id="test-stg-stub-invoke",
                caller_trust_level=TrustLevel.INTERNAL,
                caller_id="test",
            )
            result = module.agent.invoke(
                "",
                ctx=ctx,
                input_context={
                    "job_description": "Need a Python developer with 3+ years experience.",
                    "resume_batch": [
                        {
                            "candidate_id": "candidate-1",
                            "raw_text": "Experienced Python developer with 5 years building web services.",
                        }
                    ],
                },
            )
        assert result["status"] != "error"
        final_output = (result.get("output") or {})
        assert final_output.get("batch_valid") is True
