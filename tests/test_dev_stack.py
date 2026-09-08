from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from koali_control.backends import WindowsBackend
from koali_control.config import DEFAULT_CONFIG
from koali_control.devstack import DevStackOrchestrator
from koali_control.process import ProcessRunner
from koali_control.products import ProductRegistry, ProductSpec
from koali_control.supervisor import ManagedProcessSnapshot, ProcessSupervisor

ROOT = Path(__file__).resolve().parents[1]


class DevStackTests(unittest.TestCase):
    def test_default_config_registers_modular_products_and_gate(self) -> None:
        self.assertEqual(DEFAULT_CONFIG["schema_version"], 3)
        self.assertEqual(DEFAULT_CONFIG["products"]["konnaxion"]["backend"], "windows")
        self.assertEqual(DEFAULT_CONFIG["products"]["koali-spaces"]["backend"], "windows")
        self.assertEqual(DEFAULT_CONFIG["products"]["koali-spaces"]["health_url"], "http://127.0.0.1:4173/health")
        self.assertEqual(DEFAULT_CONFIG["dev_stack"]["products"], ["konnaxion", "koali-spaces"])
        self.assertIn("integrations/konnaxion/tests", DEFAULT_CONFIG["dev_stack"]["gates"][0]["command"])

    def test_json_config_matches_v3_orchestration_surface(self) -> None:
        cfg = json.loads((ROOT / "koali-control.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["schema_version"], 3)
        self.assertEqual(cfg["products"]["konnaxion"]["open_url"], "http://127.0.0.1:4300/")
        self.assertEqual(cfg["products"]["koali-spaces"]["open_url"], "http://127.0.0.1:4173/")
        self.assertEqual(cfg["products"]["koali-spaces"]["commands"]["smoke"], "pnpm run smoke:runtime")

    def test_product_command_discovery_uses_package_scripts(self) -> None:
        supervisor = ProcessSupervisor(lambda _msg: None)
        registry = ProductRegistry({}, lambda _id: MagicMock(), supervisor, lambda _msg: None)
        spec = ProductSpec(
            product_id="demo", label="Demo", enabled=True, optional=False,
            backend="windows", roots=(r"C:\demo",), marker="package.json",
            commands={}, environment={}, open_url="", health_url="",
        )
        with patch.object(registry, "_package_scripts", return_value=("pnpm", {"dev": "vite", "test:ci": "vitest run"})):
            self.assertEqual(registry.command_for(spec, "start", r"C:\demo"), "pnpm run dev")
            self.assertEqual(registry.command_for(spec, "test", r"C:\demo"), "pnpm run test:ci")
            self.assertEqual(registry.command_for(spec, "build", r"C:\demo"), "")

    def test_explicit_product_command_overrides_discovery(self) -> None:
        supervisor = ProcessSupervisor(lambda _msg: None)
        registry = ProductRegistry({}, lambda _id: MagicMock(), supervisor, lambda _msg: None)
        spec = ProductSpec(
            product_id="demo", label="Demo", enabled=True, optional=False,
            backend="windows", roots=(r"C:\demo",), marker="package.json",
            commands={"start": "pnpm run dev:koali"}, environment={}, open_url="", health_url="",
        )
        self.assertEqual(registry.command_for(spec, "start", r"C:\demo"), "pnpm run dev:koali")

    def test_windows_supervisor_command_injects_environment_and_root(self) -> None:
        backend = WindowsBackend({}, ProcessRunner(lambda _msg: None), {})
        argv = ProcessSupervisor.shell_argv(
            backend,
            r"C:\mycode\kOA-Linux\koali-spaces",
            "pnpm run dev",
            {"KOALI_SPACES_PORT": "4173"},
        )
        command = argv[-1]
        self.assertIn("$env:KOALI_SPACES_PORT=\"4173\"", command)
        self.assertIn("Set-Location -LiteralPath", command)
        self.assertIn("pnpm run dev", command)

    def test_wait_ready_accepts_healthy_external_runtime(self) -> None:
        spec = SimpleNamespace(product_id="konnaxion", label="Konnaxion", health_url="http://127.0.0.1:4300/", open_url="")
        products = MagicMock()
        products.get.return_value = spec
        products.health.return_value = ("READY", "HTTP 200")
        supervisor = MagicMock()
        supervisor.snapshot.return_value = ManagedProcessSnapshot("konnaxion", "STOPPED", None, None, None)
        core = MagicMock()
        core.timeout.return_value = 30
        orchestrator = DevStackOrchestrator(
            {"dev_stack": {"startup_timeout_seconds": 2}}, MagicMock(), MagicMock(), core, products, supervisor, lambda _msg: None
        )
        self.assertTrue(orchestrator._wait_ready(["konnaxion"]))

    def test_ui_exposes_managed_dev_stack_actions(self) -> None:
        source = (ROOT / "koali_control" / "app.py").read_text(encoding="utf-8")
        for token in (
            "BRING KOALI TO READY",
            "START KOALI DEV STACK",
            "START DEV STACK",
            "STOP DEV STACK",
            "OPEN KOALI",
        ):
            self.assertIn(token, source)


if __name__ == "__main__":
    unittest.main()
