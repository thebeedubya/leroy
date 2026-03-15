import importlib.util
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
MCP_DIR = ROOT / "mcp"
CLIENT_PATH = MCP_DIR / "leroy_client.py"


class LeroyMcpConfigImportTest(unittest.TestCase):
    def _stub_module(self, name: str, **attrs):
        module = types.ModuleType(name)
        for key, value in attrs.items():
            setattr(module, key, value)
        return module

    def test_client_imports_mcp_config(self):
        spec = importlib.util.spec_from_file_location("leroy_client_test", CLIENT_PATH)
        module = importlib.util.module_from_spec(spec)

        fake_fastmcp = self._stub_module("fastmcp")

        class FakeFastMCP:
            def __init__(self, *_args, **_kwargs):
                pass

            def tool(self, *_args, **_kwargs):
                def decorator(func):
                    return func
                return decorator

        fake_fastmcp.FastMCP = FakeFastMCP

        injected = {
            "httpx": self._stub_module("httpx"),
            "fastmcp": fake_fastmcp,
            "spec_analyzer": self._stub_module(
                "spec_analyzer",
                extract_typed_ir=lambda *_args, **_kwargs: None,
                check_dedup=lambda *_args, **_kwargs: None,
                check_complexity=lambda *_args, **_kwargs: None,
                check_preflight=lambda *_args, **_kwargs: None,
            ),
            "quality_scoring": self._stub_module(
                "quality_scoring",
                score_pre_send=lambda *_args, **_kwargs: None,
            ),
            "task_db": self._stub_module(
                "task_db",
                plan_store=None,
                init=lambda *_args, **_kwargs: None,
                PlanStore=object,
            ),
            "persist_manager": self._stub_module(
                "persist_manager",
                PersistenceManager=lambda: object(),
            ),
        }

        original_modules = {}
        for name, stub in injected.items():
            original_modules[name] = sys.modules.get(name)
            sys.modules[name] = stub

        try:
            assert spec.loader is not None
            spec.loader.exec_module(module)
            self.assertEqual(Path(module.config.__file__).resolve(), (MCP_DIR / "config.py").resolve())
            self.assertTrue(hasattr(module.config, "LEROY_A2A_URL"))
            self.assertEqual(module._a2a_url(), module.config.LEROY_A2A_URL.rstrip("/"))
        finally:
            for name, original in original_modules.items():
                if original is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = original
            sys.modules.pop("config", None)


if __name__ == "__main__":
    unittest.main()
