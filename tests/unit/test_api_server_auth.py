"""S-1 boundary tests for the standalone HTTP entry point.

The load-bearing one is test_bearer_token_alone_cannot_reach_internal: a shared deployment
token must never stand in for an authorized staff identity. It asserts on the trust level
the adapter actually builds, not on a status code, because a 200 could equally mean the
agent answered for some other reason.
"""

import importlib
from typing import Any

import pytest
from fastapi.testclient import TestClient

from framework.schemas.trust_level import TrustLevel

TOKEN = "test-deployment-token"


@pytest.fixture()
def server(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("INVOKE_AUTH_TOKEN", TOKEN)
    import src.api.server as srv

    importlib.reload(srv)
    return srv


def _captured_trust(srv: Any, monkeypatch: pytest.MonkeyPatch) -> list[TrustLevel]:
    """Record the trust level the adapter puts on the InvocationContext."""
    seen: list[TrustLevel] = []

    def fake_invoke(user_input: str, ctx: Any = None, **kw: Any) -> dict[str, Any]:
        seen.append(ctx.caller_trust_level)
        return {"status": "success", "output": "ok"}

    monkeypatch.setattr(srv.agent, "invoke", fake_invoke)
    return seen


def test_missing_token_is_rejected(server: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    _captured_trust(server, monkeypatch)
    r = TestClient(server.app).post("/invoke", json={"input": "hello"})
    assert r.status_code == 401


def test_wrong_token_is_rejected(server: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    _captured_trust(server, monkeypatch)
    r = TestClient(server.app).post("/invoke", json={"input": "hello"}, headers={"authorization": "Bearer nope"})
    assert r.status_code == 401


def test_valid_token_is_accepted(server: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _captured_trust(server, monkeypatch)
    r = TestClient(server.app).post("/invoke", json={"input": "hello"}, headers={"authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200
    assert seen, "the adapter never reached agent.invoke()"


def test_bearer_token_alone_cannot_reach_internal(server: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """A deployment token must not substitute for an authorized staff identity.

    It authenticates a deployment, not a person. If this ever asserts INTERNAL again, the
    S-1 gate on every node has a back door and the audience limit in docs/02_design.md is
    no longer enforced by anything.
    """
    seen = _captured_trust(server, monkeypatch)
    TestClient(server.app).post("/invoke", json={"input": "hello"}, headers={"authorization": f"Bearer {TOKEN}"})
    assert seen == [TrustLevel.VERIFIED_EXTERNAL]
    assert TrustLevel.INTERNAL not in seen


def test_upstream_middleware_trust_is_not_demoted(server: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Trust resolved by upstream middleware passes through unchanged.

    This is the one legitimate route to INTERNAL: something ahead of the adapter verified
    who the caller is and said so on request.state. The token branch must not run in that
    case, and must not demote the decision either.

    An earlier version of this test installed no middleware at all, so trust stayed
    ANONYMOUS, the token branch ran, and the assertion passed for a reason unrelated to
    its name. The middleware below is what makes the test mean what it says.
    """
    srv = server

    @srv.app.middleware("http")
    async def _vouch(request: Any, call_next: Any) -> Any:
        request.state.trust_level = TrustLevel.INTERNAL
        return await call_next(request)

    seen = _captured_trust(srv, monkeypatch)
    r = TestClient(srv.app).post(
        "/invoke", json={"input": "hello"}, headers={"authorization": "Bearer wrong-on-purpose"}
    )
    # The wrong token must not matter: the branch is guarded on trust being ANONYMOUS.
    assert r.status_code == 200
    assert seen == [TrustLevel.INTERNAL]
