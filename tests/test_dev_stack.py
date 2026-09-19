from __future__ import annotations

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from koali_control.backends import WindowsBackend
from koali_control.config import DEFAULT_CONFIG
from koali_control.devstack import DevStackOrchestrator
from koali_control.process import ProcessRunner
from koali_control.products import ProductRegistry, ProductServiceSpec, ProductSpec
from koali_control.supervisor import ManagedProcessSnapshot, ProcessSupervisor
from koali_control.spaces_integration import KoaliSpacesIntegrationController
from koali_control.spaces_pilot import LegacyKoaliSpacesPilotState

ROOT = Path(__file__).resolve().parents[1]


class DevStackTests(unittest.TestCase):
    def test_default_config_registers_modular_products_and_gate(self) -> None:
        self.assertEqual(DEFAULT_CONFIG["schema_version"], 4)
        self.assertEqual(DEFAULT_CONFIG["products"]["konnaxion"]["backend"], "windows")
        self.assertEqual(DEFAULT_CONFIG["products"]["koali-spaces"]["backend"], "windows")
        self.assertEqual(DEFAULT_CONFIG["products"]["koali-spaces"]["health_url"], "http://127.0.0.1:4173/health")
        self.assertEqual(DEFAULT_CONFIG["products"]["koali-spaces"]["commands"]["start"], "pnpm dev")
        self.assertEqual(set(DEFAULT_CONFIG["products"]["konnaxion"]["services"]), {"api", "web"})
        self.assertEqual(DEFAULT_CONFIG["products"]["konnaxion"]["services"]["web"]["health_url"], "http://127.0.0.1:4300/")
        self.assertEqual(set(DEFAULT_CONFIG["products"]["konnaxion-capsule-manager"]["services"]), {"agent", "manager"})
        self.assertEqual(DEFAULT_CONFIG["dev_stack"]["products"], ["koali-spaces"])
        self.assertIn("integrations/konnaxion/tests", DEFAULT_CONFIG["dev_stack"]["gates"][0]["command"])
        integration = DEFAULT_CONFIG["dev_stack"]["koali_spaces_integration"]
        self.assertTrue(integration["enabled"])
        self.assertEqual(integration["mode"], "delegated")
        self.assertEqual(integration["product_id"], "koali-spaces")
        self.assertEqual({item["module_id"] for item in integration["verify"]["modules"]}, {"konnaxion", "orgo", "semantik_architect", "koa_mediatheque"})
        self.assertEqual(integration["legacy_projection"]["konnaxion_embed_base"], "http://127.0.0.1:4300")
        self.assertEqual(DEFAULT_CONFIG["products"]["koali-spaces"]["environment"]["KOALI_SPACES_STATE_ROOT"], integration["state_root"])

    def test_json_config_matches_v4_orchestration_surface(self) -> None:
        cfg = json.loads((ROOT / "koali-control.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["schema_version"], 4)
        self.assertEqual(cfg["products"]["konnaxion"]["open_url"], "http://127.0.0.1:4300/")
        self.assertEqual(cfg["products"]["koali-spaces"]["open_url"], "http://127.0.0.1:4173/")
        self.assertEqual(cfg["products"]["koali-spaces"]["commands"]["smoke"], "pnpm run smoke:runtime")
        self.assertEqual(cfg["products"]["koali-spaces"]["commands"]["start"], "pnpm dev")
        self.assertNotIn("konnaxion", cfg["dev_stack"]["product_actions"])
        self.assertIn("frontend", cfg["products"]["konnaxion"]["commands"]["build"])
        self.assertIn("--create-db", cfg["products"]["konnaxion"]["commands"]["test"])
        self.assertIn("pnpm exec cross-env FORCE_COLOR=1 jest --runInBand", cfg["products"]["konnaxion"]["commands"]["test"])
        self.assertNotIn("pnpm run test -- --runInBand", cfg["products"]["konnaxion"]["commands"]["test"])
        web = cfg["products"]["konnaxion"]["services"]["web"]
        self.assertEqual(web["command"], "pnpm exec cross-env FORCE_COLOR=1 next dev --turbo --hostname 127.0.0.1 --port 4300")
        self.assertEqual(web["health_url"], "http://127.0.0.1:4300/")
        integration = cfg["dev_stack"]["koali_spaces_integration"]
        self.assertTrue(integration["enabled"])
        self.assertEqual(integration["mode"], "delegated")
        self.assertEqual(cfg["products"]["koali-spaces"]["environment"]["KOALI_SPACES_STATE_ROOT"], integration["state_root"])
        self.assertTrue(cfg["products"]["koali-spaces"]["environment"]["KOALI_SPACES_SURFACE_REGISTRY"].endswith("surface-runtime.json"))

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
            "pnpm run start",
            {"KOALI_SPACES_PORT": "4173"},
        )
        command = argv[-1]
        self.assertIn("$env:KOALI_SPACES_PORT='4173'", command)
        self.assertIn("Set-Location -LiteralPath", command)
        self.assertIn("pnpm run start", command)

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

    def test_composite_product_starts_each_service_under_one_product_identity(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "package.json").write_text("{}", encoding="utf-8")
            (root / "backend").mkdir()
            (root / "backend" / "manage.py").write_text("", encoding="utf-8")
            (root / "frontend").mkdir()
            (root / "frontend" / "package.json").write_text("{}", encoding="utf-8")
            cfg = {
                "environment": {"default_backend": "windows"},
                "products": {
                    "demo": {
                        "label": "Demo", "enabled": True, "optional": False,
                        "backend": "windows", "roots": [str(root)], "marker": "package.json",
                        "commands": {}, "environment": {}, "open_url": "", "health_url": "",
                        "services": {
                            "api": {"label": "API", "root": "backend", "marker": "manage.py", "command": "api-run", "health_url": ""},
                            "web": {"label": "Web", "root": "frontend", "marker": "package.json", "command": "web-run", "health_url": ""},
                        },
                    }
                },
            }
            runner = ProcessRunner(lambda _msg: None)
            backend = WindowsBackend({}, runner, {})
            supervisor = MagicMock()
            supervisor.snapshot.return_value = ManagedProcessSnapshot("x", "STOPPED", None, None, None)
            supervisor.start_shell.return_value = True
            supervisor.stop.return_value = True
            registry = ProductRegistry(cfg, lambda _id: backend, supervisor, lambda _msg: None)
            self.assertTrue(registry.start("demo"))
            process_ids = [call.args[0] for call in supervisor.start_shell.call_args_list]
            self.assertEqual(process_ids, ["demo:api", "demo:web"])
            self.assertTrue(registry.stop("demo"))
            stopped = [call.args[0] for call in supervisor.stop.call_args_list]
            self.assertEqual(stopped, ["demo:web", "demo:api"])

    def test_composite_health_requires_all_services_ready(self) -> None:
        supervisor = MagicMock()
        registry = ProductRegistry({}, lambda _id: MagicMock(), supervisor, lambda _msg: None)
        spec = ProductSpec(
            product_id="demo", label="Demo", enabled=True, optional=False, backend="windows",
            roots=(r"C:\demo",), marker="package.json", commands={}, environment={}, open_url="", health_url="",
            services=(
                ProductServiceSpec("api", "API", "windows", "backend", "manage.py", "api", {}, "http://127.0.0.1:8000/"),
                ProductServiceSpec("web", "Web", "windows", "frontend", "package.json", "web", {}, "http://127.0.0.1:4300/health"),
            ),
        )
        with patch.object(registry, "_probe_url", side_effect=[("READY", "HTTP 200"), ("READY", "HTTP 200")]):
            state, detail = registry.health(spec)
        self.assertEqual(state, "READY")
        self.assertIn("API=READY", detail)
        self.assertIn("Web=READY", detail)

    def test_legacy_v300_action_list_is_upgraded_without_schema_bump(self) -> None:
        from koali_control.config import normalize_v3_product_orchestration
        cfg = {"dev_stack": {"product_actions": {"konnaxion": ["validate", "test", "build"]}}}
        normalize_v3_product_orchestration(cfg)
        self.assertEqual(
            cfg["dev_stack"]["product_actions"]["konnaxion"],
            ["prepare", "migrate", "validate", "test", "build"],
        )

    def test_v301_konnaxion_test_command_is_upgraded_to_fresh_test_db(self) -> None:
        from koali_control.config import normalize_v3_product_orchestration
        old = "Push-Location 'backend'; & '.\\.venv\\Scripts\\python.exe' -m pytest -q; $rc=$LASTEXITCODE; Pop-Location; if ($rc -ne 0) { exit $rc }; Push-Location 'frontend'; pnpm run test -- --runInBand; $rc=$LASTEXITCODE; Pop-Location; exit $rc"
        cfg = {"products": {"konnaxion": {"commands": {"test": old}}}}
        normalize_v3_product_orchestration(cfg)
        self.assertIn("--create-db", cfg["products"]["konnaxion"]["commands"]["test"])


    def test_v302_konnaxion_jest_command_is_upgraded_without_pattern_separator(self) -> None:
        from koali_control.config import normalize_v3_product_orchestration
        old = "Push-Location 'backend'; & '.\\.venv\\Scripts\\python.exe' -m pytest -q --create-db; $rc=$LASTEXITCODE; Pop-Location; if ($rc -ne 0) { exit $rc }; Push-Location 'frontend'; pnpm run test -- --runInBand; $rc=$LASTEXITCODE; Pop-Location; exit $rc"
        cfg = {"products": {"konnaxion": {"commands": {"test": old}}}}
        normalize_v3_product_orchestration(cfg)
        command = cfg["products"]["konnaxion"]["commands"]["test"]
        self.assertIn("--create-db", command)
        self.assertIn("pnpm exec cross-env FORCE_COLOR=1 jest --runInBand", command)
        self.assertNotIn("pnpm run test -- --runInBand", command)


    def test_v303_konnaxion_web_start_and_health_are_upgraded(self) -> None:
        from koali_control.config import normalize_v3_product_orchestration
        cfg = {"products": {"konnaxion": {"services": {"web": {
            "command": "pnpm run dev -- --hostname 127.0.0.1 --port 4300",
            "health_url": "http://127.0.0.1:4300/health",
        }}}}}
        normalize_v3_product_orchestration(cfg)
        web = cfg["products"]["konnaxion"]["services"]["web"]
        self.assertEqual(web["command"], "pnpm exec cross-env FORCE_COLOR=1 next dev --turbo --hostname 127.0.0.1 --port 4300")
        self.assertEqual(web["health_url"], "http://127.0.0.1:4300/")
        integration = cfg["dev_stack"]["koali_spaces_integration"]
        self.assertTrue(integration["enabled"])


    def test_app_close_uses_devstack_stop_so_integration_state_is_released(self) -> None:
        source = (ROOT / "koali_control" / "app.py").read_text(encoding="utf-8")
        close_block = source.split("def _on_close", 1)[1].split("def run_stabilization", 1)[0]
        self.assertIn("self.dev_stack.stop()", close_block)
        self.assertNotIn("self.supervisor.stop_all()", close_block)

    def test_explicit_legacy_projection_still_materializes_and_cleans_current_pilot(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            logs: list[str] = []
            cfg = {
                "dev_stack": {
                    "koali_spaces_integration": {
                        "enabled": True,
                        "mode": "legacy_projection",
                        "state_root": td,
                        "legacy_projection": {"konnaxion_embed_base": "http://127.0.0.1:4300"},
                    }
                }
            }
            pilot = LegacyKoaliSpacesPilotState(cfg, logs.append)
            self.assertTrue(pilot.write("ready", "ready"))
            state = json.loads((Path(td) / "active-state.json").read_text(encoding="utf-8"))
            registry = json.loads((Path(td) / "surface-runtime.json").read_text(encoding="utf-8"))
            self.assertEqual([m["module_id"] for m in state["modules"]], ["space_home", "konnaxion"])
            self.assertEqual([m["module_id"] for m in state["active_space"]["module_instances"]], ["space_home", "konnaxion"])
            self.assertEqual(state["active_space"]["default_module_id"], "space_home")
            self.assertEqual(registry["runtimeRegistrations"][0]["moduleId"], "konnaxion")
            self.assertEqual(registry["resolvedTargets"][0]["embedBase"], "http://127.0.0.1:4300")
            self.assertIn("allow-same-origin", registry["resolvedTargets"][0]["sandboxTokens"])
            self.assertEqual(registry["runtimeObservations"][0]["state"], "ready")
            self.assertTrue(pilot.clear())
            self.assertFalse((Path(td) / "active-state.json").exists())
            self.assertFalse((Path(td) / "surface-runtime.json").exists())

    def test_devstack_runs_generic_spaces_integration_boundary(self) -> None:
        products = MagicMock()
        products.enabled_ids.return_value = []
        core = MagicMock()
        core.timeout.return_value = 30
        orchestrator = DevStackOrchestrator(
            {"dev_stack": {"products": [], "startup_timeout_seconds": 2}},
            MagicMock(), MagicMock(), core, products, MagicMock(), lambda _msg: None,
        )
        orchestrator.spaces_integration = MagicMock()
        orchestrator.spaces_integration.before_start.return_value = True
        orchestrator.spaces_integration.after_ready.return_value = True
        result = orchestrator.start(MagicMock(), prepare_core=False, run_gates=False, prepare_products=False)
        self.assertTrue(result.ready)
        orchestrator.spaces_integration.before_start.assert_called_once_with()
        orchestrator.spaces_integration.after_ready.assert_called_once_with()


    def test_generic_navigation_probe_requires_declared_module_and_route(self) -> None:
        cfg = {
            "products": {"koali-spaces": {"open_url": "http://127.0.0.1:4173/"}},
            "dev_stack": {"koali_spaces_integration": {
                "enabled": True,
                "mode": "delegated",
                "product_id": "koali-spaces",
                "verify": {"modules": [{"module_id": "konnaxion", "required": True, "route": "/apps/konnaxion"}]},
            }},
        }
        controller = KoaliSpacesIntegrationController(cfg, MagicMock(), lambda _msg: None)

        class FakeResponse:
            def __init__(self, payload: bytes = b"", status: int = 200) -> None:
                self.payload = payload
                self.status = status
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def read(self) -> bytes: return self.payload

        shell = json.dumps({
            "modules": [{"module_id": "space_home"}, {"module_id": "konnaxion"}],
            "active_space": {"module_instances": [
                {"module_id": "space_home", "enabled": True},
                {"module_id": "konnaxion", "enabled": True},
            ]},
        }).encode("utf-8")
        with patch("koali_control.spaces_integration.urllib.request.urlopen", side_effect=[FakeResponse(shell), FakeResponse(status=200)]):
            self.assertTrue(controller.verify())

    def test_v4_integration_state_root_is_projected_into_spaces_environment(self) -> None:
        from koali_control.config import normalize_v3_product_orchestration, normalize_v4_spaces_integration
        cfg = {
            "dev_stack": {"koali_spaces_integration": {"state_root": r"D:\runtime\spaces"}},
            "products": {"koali-spaces": {"environment": {}}},
        }
        normalize_v4_spaces_integration(cfg)
        normalize_v3_product_orchestration(cfg)
        env = cfg["products"]["koali-spaces"]["environment"]
        self.assertEqual(env["KOALI_SPACES_STATE_ROOT"], r"D:\runtime\spaces")
        self.assertEqual(env["KOALI_SPACES_SURFACE_REGISTRY"], r"D:\runtime\spaces\surface-runtime.json")


    def test_v3_pilot_config_migrates_losslessly_to_schema4_integration(self) -> None:
        from koali_control.config import ConfigStore

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "koali-control.json"
            legacy = json.loads(json.dumps(DEFAULT_CONFIG))
            legacy["schema_version"] = 3
            legacy["dev_stack"].pop("koali_spaces_integration", None)
            legacy["dev_stack"]["koali_spaces_pilot"] = {
                "enabled": False,
                "state_root": r"D:\old-runtime\spaces",
                "konnaxion_embed_base": "http://127.0.0.1:9999",
                "module_id": "demo",
            }
            path.write_text(json.dumps(legacy), encoding="utf-8")
            cfg = ConfigStore(path).load()

        self.assertEqual(cfg["schema_version"], 4)
        self.assertNotIn("koali_spaces_pilot", cfg["dev_stack"])
        integration = cfg["dev_stack"]["koali_spaces_integration"]
        self.assertFalse(integration["enabled"])
        self.assertEqual(integration["mode"], "delegated")
        self.assertEqual(integration["actions"]["activate"], "ecosystem:ready")
        self.assertEqual(integration["state_root"], r"D:\old-runtime\spaces")
        self.assertEqual(integration["legacy_projection"]["konnaxion_embed_base"], "http://127.0.0.1:9999")
        self.assertEqual(integration["verify"]["modules"][0]["module_id"], "demo")

    def test_delegated_mode_invokes_only_repository_owned_product_action(self) -> None:
        products = MagicMock()
        products.get.return_value = SimpleNamespace(enabled=True)
        products.run_action.return_value = 0
        cfg = {
            "dev_stack": {"koali_spaces_integration": {
                "enabled": True,
                "mode": "delegated",
                "product_id": "koali-spaces",
                "actions": {"activate": "activate-space", "deactivate": "deactivate-space"},
                "verify": {"modules": []},
            }},
            "products": {"koali-spaces": {"open_url": "http://127.0.0.1:4173/"}},
        }
        controller = KoaliSpacesIntegrationController(cfg, products, lambda _msg: None)
        with patch.object(controller, "verify", return_value=True):
            self.assertTrue(controller.after_ready())
        products.run_action.assert_called_once_with("koali-spaces", "activate-space", timeout=1800)
        self.assertTrue(controller.stop())
        self.assertEqual(products.run_action.call_args_list[-1], unittest.mock.call("koali-spaces", "deactivate-space", timeout=1800))

    def test_delegated_mode_fails_closed_when_activation_action_is_absent(self) -> None:
        logs: list[str] = []
        cfg = {"dev_stack": {"koali_spaces_integration": {
            "enabled": True,
            "mode": "delegated",
            "product_id": "koali-spaces",
            "actions": {"activate": ""},
        }}}
        controller = KoaliSpacesIntegrationController(cfg, MagicMock(), logs.append)
        self.assertFalse(controller.after_ready())
        self.assertTrue(any("will not fabricate Koali state" in line for line in logs))

    def test_new_integration_controller_contains_no_product_manifest_fabrication(self) -> None:
        source = (ROOT / "koali_control" / "spaces_integration.py").read_text(encoding="utf-8")
        self.assertNotIn("_konnaxion_manifest", source)
        self.assertNotIn("_home_manifest", source)
        self.assertNotIn('"surface-runtime.json"', source)
        self.assertIn("Control Panel will not fabricate Koali state", source)


    def test_stop_releases_spaces_integration_before_stopping_products(self) -> None:
        products = MagicMock()
        products.enabled_ids.return_value = ["konnaxion", "koali-spaces"]
        products.stop.return_value = True
        core = MagicMock()
        core.timeout.return_value = 30
        orchestrator = DevStackOrchestrator(
            {"dev_stack": {"products": ["konnaxion", "koali-spaces"]}},
            MagicMock(), MagicMock(), core, products, MagicMock(), lambda _msg: None,
        )
        events: list[str] = []
        integration = MagicMock()
        integration.stop.side_effect = lambda: events.append("integration") or True
        orchestrator.spaces_integration = integration
        products.stop.side_effect = lambda product_id: events.append(f"stop:{product_id}") or True
        self.assertTrue(orchestrator.stop())
        self.assertEqual(events, ["integration", "stop:koali-spaces", "stop:konnaxion"])

    def test_verification_routes_are_same_origin_paths_only(self) -> None:
        cfg = {
            "products": {"koali-spaces": {"open_url": "http://127.0.0.1:4173/"}},
            "dev_stack": {"koali_spaces_integration": {
                "enabled": True,
                "mode": "delegated",
                "verify": {"modules": [{"module_id": "demo", "required": True, "route": "https://example.com/"}]},
            }},
        }
        logs: list[str] = []
        controller = KoaliSpacesIntegrationController(cfg, MagicMock(), logs.append)

        class FakeResponse:
            status = 200
            def __init__(self, payload: bytes) -> None: self.payload = payload
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def read(self) -> bytes: return self.payload

        shell = json.dumps({
            "modules": [{"module_id": "demo"}],
            "active_space": {"module_instances": [{"module_id": "demo", "enabled": True}]},
        }).encode("utf-8")
        with patch("koali_control.spaces_integration.urllib.request.urlopen", return_value=FakeResponse(shell)) as urlopen:
            self.assertFalse(controller.verify())
        self.assertEqual(urlopen.call_count, 1)
        self.assertTrue(any("same-origin absolute path" in line for line in logs))

    def test_v305_spaces_dev_start_is_promoted_to_packaged_runtime(self) -> None:
        from koali_control.config import ConfigStore

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "koali-control.json"
            legacy = json.loads(json.dumps(DEFAULT_CONFIG))
            legacy["products"]["koali-spaces"]["commands"]["start"] = "pnpm run dev"
            path.write_text(json.dumps(legacy), encoding="utf-8")
            cfg = ConfigStore(path).load()
        self.assertEqual(cfg["products"]["koali-spaces"]["commands"]["start"], "pnpm dev")


if __name__ == "__main__":
    unittest.main()
