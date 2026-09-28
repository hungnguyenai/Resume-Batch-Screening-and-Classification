"""Regression test: src.api.server must import cleanly.
The original bug this guards against was an import-time
ConfigError crash (Graph() constructed with no config), which meant the
standalone server never even started -- health_200/invoke_200 both failed at
deploy-stg with no clearer signal than a bare exit code.
"""

import importlib


class TestServerImport:
    def test_server_module_imports_without_error(self):
        module = importlib.import_module("src.api.server")
        assert module.app is not None
        assert module.agent is not None

    def test_agent_config_wired_from_manifest(self):
        module = importlib.import_module("src.api.server")
        assert module.agent.config.get("budget_usd") == 2.50
        assert module.agent.config.get("max_iterations") == 20
        assert module.agent.config.get("llm") is not None
