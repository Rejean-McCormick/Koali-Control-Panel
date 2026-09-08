from __future__ import annotations

import json
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest.mock import patch

from koali_control.backends import WslBackend
from koali_control.config import ConfigStore, migrate_v1
from koali_control.debugdiag import DebugDiagnosticsRunner
from koali_control.levelupdiag import LevelUpDiagAdapter
from koali_control.models import CheckResult, CheckState, Workspace
from koali_control.orchestration import Orchestrator
from koali_control.workspaces import WorkspaceManager
from koali_control.process import CaptureResult, ProcessRunner, decode_bytes
from koali_control.qemu import QemuEnvironmentManager


class CoreTests(unittest.TestCase):
    def test_v1_config_migrates_to_workspace_model(self) -> None:
        old = {
            "schema_version": 1,
            "wsl_distro": "Ubuntu",
            "repo_windows": r"C:\repo",
            "repo_wsl": "/mnt/c/repo",
            "profile": "developer-windows-wsl",
            "assembly": {"renderer": "systemd", "output": "generated/x"},
        }
        new = migrate_v1(old)
        self.assertEqual(new["schema_version"], 2)
        self.assertEqual(new["backends"]["wsl"]["distribution"], "Ubuntu")
        self.assertIn("koa-linux-main", new["workspaces"])
        self.assertEqual(new["workspaces"]["koa-linux-main"]["root"], "/mnt/c/repo")

    def test_config_store_writes_schema_v3(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "cfg.json"
            cfg = ConfigStore(path).load()
            self.assertEqual(cfg["schema_version"], 3)
            self.assertTrue(path.exists())
            loaded = json.loads(path.read_text())
            self.assertEqual(loaded["environment"]["default_workspace"], "koa-linux-main")
            self.assertEqual(cfg["diagnostics"]["levelupdiag"]["campaigns"]["run_all"], "validation")
            self.assertTrue(cfg["backends"]["wsl"]["toolchain_provisioning"]["prime_cargo_cache"])
            self.assertEqual(cfg["backends"]["wsl"]["toolchain_provisioning"]["native_build_apt_packages"], ["build-essential"])
            qemu = cfg["system_test"]["qemu"]
            for key in (
                "confinement_ready_regex",
                "general_surface_denied_regex",
                "privilege_path_denied_regex",
                "active_profile",
                "navigation_surface_id",
                "navigation_ready_regex",
                "navigation_result_regex",
                "navigation_keys",
                "mediatheque_selection",
                "active_release_set",
                "mediatheque_artifact_ref",
                "mediatheque_offline_regex",
            ):
                self.assertIn(key, qemu)
            self.assertNotIn("semantik_active_profile", qemu)

    def test_workspace_roundtrip(self) -> None:
        raw = {
            "repository": "koa-linux",
            "backend": "wsl",
            "profile": "developer-windows-wsl",
            "root": "{home}/work/koa-linux",
            "assembly": {"renderer": "systemd"},
        }
        ws = Workspace.from_config("main", raw)
        self.assertEqual(ws.workspace_id, "main")
        self.assertEqual(ws.to_config()["backend"], "wsl")

    def test_koa_cli_uses_canonical_repo_surface(self) -> None:
        cmd = Orchestrator.koa_cli(["validate", "--warnings-as-errors"])
        self.assertIn("koa_tools.cli", cmd)
        self.assertIn("--repository-root", cmd)
        self.assertIn("validate", cmd)

    def test_component_build_uses_repository_command_module(self) -> None:
        cmd = Orchestrator.component_build_cli("audit-broker", "1788526256")
        self.assertIn("koa_tools.commands.build_component", cmd)
        self.assertIn("--component audit-broker", cmd)
        self.assertIn("--source-date-epoch 1788526256", cmd)
        self.assertNotIn("koa_tools.cli", cmd)
        self.assertNotIn(" build-component ", f" {cmd} ")

    def test_effective_profile_output_uses_repository_generated_convention(self) -> None:
        self.assertEqual(
            Orchestrator.effective_profile_output("sovereign-linux-node"),
            "generated/profiles/sovereign_linux_node/effective-profile.json",
        )
        self.assertEqual(
            Orchestrator.effective_profile_output("developer-windows-wsl"),
            "generated/profiles/developer_windows_wsl/effective-profile.json",
        )

    def test_effective_profile_cli_uses_repository_owned_resolver(self) -> None:
        cmd = Orchestrator.effective_profile_cli(
            "sovereign-linux-node",
            ("high-assurance",),
        )
        self.assertIn("uv run --project assembly python -m koa_assembly", cmd)
        self.assertIn("resolve-profile", cmd)
        self.assertIn("--profile sovereign-linux-node", cmd)
        self.assertIn(
            "--output generated/profiles/sovereign_linux_node/effective-profile.json",
            cmd,
        )
        self.assertIn("--overlay high-assurance", cmd)
        self.assertNotIn("--check", cmd)

    def test_build_tab_exposes_effective_profile_generation_action(self) -> None:
        source = (Path(__file__).parents[1] / "koali_control" / "app.py").read_text(encoding="utf-8")
        self.assertIn('("Generate Effective Profile", self.generate_effective_profile)', source)
        self.assertIn("self.orchestrator.generate_effective_profile(ws)", source)

    def test_windows_utf16_output_decodes_without_mojibake(self) -> None:
        original = "Une distribution portant le nom fourni existe déjà.\r\nCode d'erreur : Wsl/InstallDistro/ERROR_ALREADY_EXISTS\r\n"
        encoded = original.encode("utf-16-le")
        self.assertEqual(decode_bytes(encoded), original)

    def test_wsl_preflight_stops_at_uninitialized_runtime(self) -> None:
        runner = ProcessRunner(lambda _msg: None)

        class StubWsl(WslBackend):
            def list_distros(self) -> CaptureResult:
                return CaptureResult(0, "  NAME              STATE           VERSION\n* Ubuntu-24.04      Stopped         2\n")

            def list_distro_names(self) -> list[str]:
                return ["Ubuntu-24.04"]

            def runtime_probe(self, *, timeout: int = 8) -> CaptureResult:
                return CaptureResult(124, "TIMEOUT after 8s")

        backend = StubWsl(
            {
                "distribution": "Ubuntu-24.04",
                "expected_wsl_version": 2,
                "expected_distribution_id": "ubuntu",
                "expected_release": "24.04",
                "require_systemd": True,
            },
            runner,
            {},
        )
        with patch("koali_control.backends.shutil.which", return_value=r"C:\\Windows\\System32\\wsl.exe"):
            checks = backend.preflight()
        keys = [item.key for item in checks]
        self.assertEqual(keys, ["wsl", "distro", "wsl_version", "distro_runtime"])
        self.assertEqual(checks[-1].state, CheckState.BLOCKED)
        self.assertIn("first-run", checks[-1].detail)
        self.assertNotIn("systemd", keys)
        self.assertNotIn("backend_tool_uv", keys)

    def test_wsl_first_run_uses_dedicated_persistent_console_on_windows(self) -> None:
        runner = ProcessRunner(lambda _msg: None)
        backend = WslBackend({"distribution": "Ubuntu-24.04"}, runner, {"terminal_exe": "wt.exe"})
        fake_proc = type("P", (), {"pid": 4321})()
        with patch("koali_control.backends.os.name", "nt"), \
             patch("koali_control.backends.os.environ", {"COMSPEC": r"C:\\Windows\\System32\\cmd.exe"}), \
             patch("koali_control.backends.subprocess.Popen", return_value=fake_proc) as popen:
            ok, detail = backend._launch_visible(["wsl.exe", "-d", "Ubuntu-24.04"], title="Koali setup")
        self.assertTrue(ok)
        self.assertIn("dedicated console", detail)
        argv = popen.call_args.args[0]
        self.assertEqual(argv[1:3], ["/d", "/k"])
        self.assertIn("Ubuntu-24.04", argv[3])

    def test_prepare_does_not_claim_console_open_when_spawn_fails(self) -> None:
        logs: list[str] = []
        runner = ProcessRunner(logs.append)

        class StubWsl(WslBackend):
            def is_registered(self) -> bool:
                return True
            def runtime_probe(self, *, timeout: int = 8) -> CaptureResult:
                return CaptureResult(124, "TIMEOUT")
            def _launch_visible(self, argv: list[str], *, title: str = "Koali") -> tuple[bool, str]:
                return False, "spawn failed"

        backend = StubWsl({"distribution": "Ubuntu-24.04"}, runner, {})
        mode = backend.prepare_interactive()
        self.assertEqual(mode, "launch_failed")
        self.assertTrue(any("could not open" in line.lower() for line in logs))
        self.assertFalse(any("console launched" in line.lower() for line in logs))


    def test_systemd_degraded_is_nonblocking_when_pid1_is_systemd(self) -> None:
        runner = ProcessRunner(lambda _msg: None)

        class StubWsl(WslBackend):
            def list_distros(self) -> CaptureResult:
                return CaptureResult(0, "* Ubuntu-24.04 Running 2\n")
            def list_distro_names(self) -> list[str]:
                return ["Ubuntu-24.04"]
            def runtime_probe(self, *, timeout: int = 8) -> CaptureResult:
                return CaptureResult(0, "KOALI_WSL_READY\n")
            def capture_shell(self, script: str, *, timeout: int = 30) -> CaptureResult:
                if "id -un" in script:
                    return CaptureResult(0, "user=rejean\nuid=1000\n")
                if "/etc/os-release" in script:
                    return CaptureResult(0, 'ID=ubuntu\nVERSION_ID="24.04"\n')
                if "is-system-running" in script:
                    return CaptureResult(0, "pid1=systemd\nstate=degraded\nfailed=example.service\n")
                if "resolve_koali_tool" in script:
                    return CaptureResult(0, "tool:git=ok:/usr/bin/git\ntool:python=ok:/usr/bin/python\ntool:uv=ok:/home/rejean/.local/bin/uv\ntool:python3=ok:/usr/bin/python3\n")
                return CaptureResult(0, "")

        backend = StubWsl({
            "distribution": "Ubuntu-24.04",
            "expected_wsl_version": 2,
            "expected_distribution_id": "ubuntu",
            "expected_release": "24.04",
            "require_systemd": True,
        }, runner, {})
        with patch("koali_control.backends.shutil.which", return_value=r"C:\\Windows\\System32\\wsl.exe"):
            checks = backend.preflight()
        systemd = next(item for item in checks if item.key == "systemd")
        self.assertEqual(systemd.state, CheckState.WARN)
        self.assertFalse(systemd.required)
        self.assertIn("example.service", systemd.detail)

    def test_systemd_ignores_declared_non_applicable_wsl_getty_units(self) -> None:
        runner = ProcessRunner(lambda _msg: None)

        class StubWsl(WslBackend):
            def list_distros(self) -> CaptureResult:
                return CaptureResult(0, "* Ubuntu-24.04 Running 2\n")
            def list_distro_names(self) -> list[str]:
                return ["Ubuntu-24.04"]
            def runtime_probe(self, *, timeout: int = 8) -> CaptureResult:
                return CaptureResult(0, "KOALI_WSL_READY\n")
            def capture_shell(self, script: str, *, timeout: int = 30) -> CaptureResult:
                if "id -un" in script:
                    return CaptureResult(0, "user=rejean\nuid=1000\n")
                if "/etc/os-release" in script:
                    return CaptureResult(0, 'ID=ubuntu\nVERSION_ID="24.04"\n')
                if "is-system-running" in script:
                    return CaptureResult(0, "pid1=systemd\nstate=degraded\nfailed=console-getty.service,getty@tty1.service\n")
                if "resolve_koali_tool" in script:
                    return CaptureResult(0, "tool:git=ok:/usr/bin/git\ntool:python=ok:/usr/bin/python\ntool:uv=ok:/home/rejean/.local/bin/uv\ntool:python3=ok:/usr/bin/python3\n")
                return CaptureResult(0, "")

        backend = StubWsl({
            "distribution": "Ubuntu-24.04",
            "expected_wsl_version": 2,
            "expected_distribution_id": "ubuntu",
            "expected_release": "24.04",
            "require_systemd": True,
            "ignored_systemd_units": ["console-getty.service", "getty@tty1.service"],
        }, runner, {})
        with patch("koali_control.backends.shutil.which", return_value=r"C:\\Windows\\System32\\wsl.exe"):
            checks = backend.preflight()
        systemd = next(item for item in checks if item.key == "systemd")
        self.assertEqual(systemd.state, CheckState.PASS)
        self.assertIn("ignored non-applicable", systemd.detail)

    def test_wsl_toolchain_provisioner_uses_scoped_root_packages_and_user_uv(self) -> None:
        logs: list[str] = []
        runner = ProcessRunner(logs.append)
        calls: list[tuple[str, str]] = []

        class StubWsl(WslBackend):
            def capture_shell(self, script: str, *, timeout: int = 30) -> CaptureResult:
                if "resolve_koali_tool" in script and "version:git=" not in script:
                    return CaptureResult(1, "tool:git=missing\ntool:python=missing\ntool:uv=missing\ntool:pipx=missing\ntool:python3=ok:/usr/bin/python3\n")
                if "version:git=" in script:
                    return CaptureResult(0, "tool:git=ok:/usr/bin/git\ntool:python=ok:/usr/bin/python\ntool:uv=ok:/home/user/.local/bin/uv\nversion:git=git version 2\nversion:python=Python 3.12\nversion:uv=uv 0.12\n")
                return CaptureResult(0, "")
            def run_root_shell(self, script: str, label: str, *, timeout: int | None = None) -> int:
                calls.append(("root", script))
                return 0
            def run_shell(self, script: str, label: str, *, timeout: int | None = None) -> int:
                calls.append(("user", script))
                return 0

        backend = StubWsl({
            "distribution": "Ubuntu-24.04",
            "toolchain_provisioning": {
                "enabled": True,
                "provider": "ubuntu_apt_pipx",
                "apt_packages": ["git", "python3", "python-is-python3", "pipx"],
                "uv_pipx_package": "uv",
            },
        }, runner, {})
        self.assertEqual(backend.provision_profile_toolchain(), 0)
        root_script = next(script for kind, script in calls if kind == "root")
        user_script = next(script for kind, script in calls if kind == "user")
        self.assertIn("apt-get install -y", root_script)
        self.assertIn("python-is-python3", root_script)
        self.assertIn("pipx install uv", user_script)
        self.assertIn("$HOME/.local/bin", user_script)

    def test_tool_probe_accepts_explicit_uv_path_without_startup_path(self) -> None:
        script = WslBackend._tool_probe_script(include_versions=True)
        self.assertIn('$HOME/.local/bin/uv', script)
        self.assertIn('/usr/bin/git', script)
        self.assertIn('/usr/bin/python', script)
        self.assertIn('tool:uv=ok:', script)
        self.assertNotIn('{;', script)

    def test_workspace_preflight_emits_venv_and_precommit_markers(self) -> None:
        source = (Path(__file__).parents[1] / 'koali_control' / 'workspaces.py').read_text(encoding='utf-8')
        self.assertIn("echo 'venv=ok'", source)
        self.assertIn("echo 'precommit=ok'", source)
        self.assertNotIn('tool:$t=ok', source)



    def test_process_runner_capture_supports_stdin_script_transport(self) -> None:
        runner = ProcessRunner(lambda _msg: None)
        fake = type("Completed", (), {"returncode": 0, "stdout": b"ok\n"})()
        with patch("koali_control.process.subprocess.run", return_value=fake) as run:
            result = runner.capture(["example"], input_text="echo ok\n")
        self.assertEqual(result.code, 0)
        self.assertEqual(result.output, "ok\n")
        self.assertEqual(run.call_args.kwargs["input"], b"echo ok\n")

    def test_wsl_shell_scripts_are_transported_over_stdin(self) -> None:
        calls: list[tuple[list[str], str | None]] = []

        class RecordingRunner:
            def log(self, _message: str) -> None:
                pass
            def capture(self, argv, *, timeout=30, cwd=None, input_text=None):
                calls.append((list(argv), input_text))
                return CaptureResult(0, "ok")
            def run(self, argv, label, *, timeout=None, cwd=None, input_text=None):
                calls.append((list(argv), input_text))
                return 0

        backend = WslBackend({"distribution": "Ubuntu-24.04", "shell": "bash"}, RecordingRunner(), {})
        script = "printf '%s\\n' \"$HOME\"; awk '{print $1}' /etc/passwd"
        backend.capture_shell(script)
        argv, transported = calls[-1]
        self.assertEqual(argv[-2:], ["bash", "-s"])
        self.assertIn(script, transported or "")
        self.assertNotIn(script, " ".join(argv))

    def test_tool_probe_avoids_eval_and_keeps_explicit_profile_paths(self) -> None:
        script = WslBackend._tool_probe_script(include_pipx=True, include_versions=True)
        self.assertNotIn("eval ", script)
        self.assertIn('"/usr/bin/git"', script)
        self.assertIn('"/usr/bin/python"', script)
        self.assertIn('"$HOME/.local/bin/uv"', script)
        self.assertIn("version:uv=", script)

    def test_windows_source_path_translation_uses_stdin_wslpath_for_real_koa_path(self) -> None:
        runner = ProcessRunner(lambda _msg: None)

        class StubWsl(WslBackend):
            def capture_shell(self, script: str, *, timeout: int = 30) -> CaptureResult:
                self.last_script = script
                return CaptureResult(0, "/mnt/c/mycode/kOA-Linux/koa-linux\n")

        backend = StubWsl({"distribution": "Ubuntu-24.04"}, runner, {})
        translated = backend.windows_to_linux_path(r"C:\mycode\kOA-Linux\koa-linux")
        self.assertEqual(translated, "/mnt/c/mycode/kOA-Linux/koa-linux")
        self.assertIn("base64 -d", backend.last_script)
        self.assertIn("wslpath -u", backend.last_script)
        self.assertNotIn(r"C:\mycode\kOA-Linux\koa-linux", backend.last_script)

    def test_windows_source_path_translation_has_default_wsl_mount_fallback(self) -> None:
        runner = ProcessRunner(lambda _msg: None)

        class StubWsl(WslBackend):
            def capture_shell(self, script: str, *, timeout: int = 30) -> CaptureResult:
                return CaptureResult(1, "wslpath failed")

        backend = StubWsl({"distribution": "Ubuntu-24.04"}, runner, {})
        translated = backend.windows_to_linux_path(r"C:\mycode\kOA-Linux\koa-linux")
        self.assertEqual(translated, "/mnt/c/mycode/kOA-Linux/koa-linux")


    def test_windows_checkout_import_transfers_data_without_git_state_validation(self) -> None:
        scripts: list[str] = []

        class StubWsl(WslBackend):
            def windows_to_linux_path(self, windows_path: str) -> str:
                self.asserted_source = windows_path
                return "/mnt/c/mycode/kOA-Linux/koa-linux"
            def resolve_path(self, template: str) -> str:
                return template.replace("{home}", "/home/rejean")
            def run_shell(self, script: str, label: str, *, timeout: int | None = None) -> int:
                scripts.append(script)
                self.last_label = label
                return 0

        backend = StubWsl({"distribution": "Ubuntu-24.04"}, ProcessRunner(lambda _msg: None), {})
        manager = WorkspaceManager({}, lambda _name: backend)
        ws = Workspace.from_config("koa-linux-main", {
            "repository": "koa-linux",
            "backend": "wsl",
            "profile": "developer-windows-wsl",
            "root": "{home}/work/koa-linux",
            "windows_source": r"C:\mycode\kOA-Linux\koa-linux",
        })
        self.assertEqual(manager.create_from_windows_source(ws), 0)
        script = scripts[-1]
        self.assertIn('tar -C "$SRC"', script)
        self.assertIn('tar -C "$STAGE" -xf -', script)
        self.assertNotIn('git status', script)
        self.assertNotIn('git branch', script)
        self.assertNotIn('git rev-parse', script)
        self.assertNotIn('git -C "$SRC"', script)
        self.assertNotIn('git clone', script)
        self.assertNotIn('safe.directory', script)
        self.assertIn("--exclude=./.venv", script)
        self.assertIn("--exclude=./node_modules", script)
        self.assertIn("--exclude=./.levelupdiag", script)
        self.assertNotIn("--exclude='*/build'", script)
        self.assertNotIn("--exclude='*.egg-info'", script)
        self.assertIn("Source fingerprint recorded", script)

    def test_workspace_exists_rejects_incomplete_directory_without_repository_markers(self) -> None:
        seen: list[str] = []

        class StubBackend:
            def resolve_path(self, template: str) -> str:
                return template.replace("{home}", "/home/rejean")
            def capture_shell(self, script: str, *, timeout: int = 30) -> CaptureResult:
                seen.append(script)
                return CaptureResult(2, "")

        backend = StubBackend()
        manager = WorkspaceManager({}, lambda _name: backend)
        ws = Workspace.from_config("koa-linux-main", {
            "repository": "koa-linux",
            "backend": "wsl",
            "profile": "developer-windows-wsl",
            "root": "{home}/work/koa-linux",
        })
        self.assertFalse(manager.workspace_exists(ws))
        self.assertNotIn("git status", seen[-1])
        self.assertNotIn("git rev-parse", seen[-1])
        self.assertIn("/.git", seen[-1])


    def test_windows_checkout_import_preserves_source_content_without_git_state_management(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            src = base / "windows-source"
            dst = base / "linux-workspace"
            src.mkdir()
            subprocess.run(["git", "init", "-q", str(src)], check=True)
            subprocess.run(["git", "-C", str(src), "config", "user.email", "koali@test.invalid"], check=True)
            subprocess.run(["git", "-C", str(src), "config", "user.name", "Koali Test"], check=True)
            (src / "tracked.txt").write_text("base\n", encoding="utf-8")
            (src / "pyproject.toml").write_text("[project]\nname='fixture'\nversion='0'\n", encoding="utf-8")
            (src / "uv.lock").write_text("version = 1\n", encoding="utf-8")
            (src / ".python-version").write_text("3.13\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(src), "add", "tracked.txt"], check=True)
            subprocess.run(["git", "-C", str(src), "commit", "-qm", "base"], check=True)
            (src / "tracked.txt").write_text("modified\n", encoding="utf-8")
            (src / "untracked.txt").write_text("keep me\n", encoding="utf-8")
            (src / ".venv").mkdir()
            (src / ".venv" / "windows-only.txt").write_text("exclude\n", encoding="utf-8")
            (src / ".levelupdiag" / "runs").mkdir(parents=True)
            (src / ".levelupdiag" / "runs" / "result.json").write_text("{}\n", encoding="utf-8")
            (src / "component" / "build" / "lib").mkdir(parents=True)
            (src / "component" / "build" / "lib" / "generated.py").write_text("x=1\n", encoding="utf-8")
            (src / "component" / "src" / "demo.egg-info").mkdir(parents=True)
            (src / "component" / "src" / "demo.egg-info" / "PKG-INFO").write_text("generated\n", encoding="utf-8")

            class LocalWsl(WslBackend):
                def windows_to_linux_path(self, windows_path: str) -> str:
                    return str(src)
                def resolve_path(self, template: str) -> str:
                    return str(dst)
                def run_shell(self, script: str, label: str, *, timeout: int | None = None) -> int:
                    completed = subprocess.run(["bash", "-s"], input=script.encode("utf-8"), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
                    self.last_output = completed.stdout.decode("utf-8", errors="replace")
                    return completed.returncode

            backend = LocalWsl({"distribution": "Ubuntu-24.04"}, ProcessRunner(lambda _msg: None), {})
            manager = WorkspaceManager({}, lambda _name: backend)
            ws = Workspace.from_config("koa-linux-main", {
                "repository": "koa-linux",
                "backend": "wsl",
                "profile": "developer-windows-wsl",
                "root": str(dst),
                "windows_source": r"C:\mycode\kOA-Linux\koa-linux",
            })
            self.assertEqual(manager.create_from_windows_source(ws), 0, getattr(backend, "last_output", ""))
            self.assertEqual((dst / "tracked.txt").read_text(encoding="utf-8"), "modified\n")
            self.assertEqual((dst / "untracked.txt").read_text(encoding="utf-8"), "keep me\n")
            self.assertFalse((dst / ".venv").exists())
            self.assertFalse((dst / ".levelupdiag").exists())
            self.assertTrue((dst / "component" / "build").exists())
            self.assertTrue((dst / "component" / "src" / "demo.egg-info").exists())
            status = subprocess.run(["git", "-C", str(dst), "status", "--porcelain"], stdout=subprocess.PIPE, check=True, text=True).stdout
            self.assertIn("tracked.txt", status)
            self.assertIn("untracked.txt", status)


    def test_source_sync_status_detects_changed_windows_source_without_git_on_source(self) -> None:
        scripts: list[str] = []

        class StubWsl(WslBackend):
            def windows_to_linux_path(self, windows_path: str) -> str:
                return "/mnt/c/mycode/kOA-Linux/koa-linux"
            def resolve_path(self, template: str) -> str:
                return template.replace("{home}", "/home/rejean")
            def capture_shell(self, script: str, *, timeout: int = 30) -> CaptureResult:
                scripts.append(script)
                return CaptureResult(0, "status=source_changed\ndetail=Windows source changed since the last imported snapshot\n")

        backend = StubWsl({"distribution": "Ubuntu-24.04"}, ProcessRunner(lambda _msg: None), {})
        manager = WorkspaceManager({}, lambda _name: backend)
        ws = Workspace.from_config("koa-linux-main", {
            "repository": "koa-linux",
            "backend": "wsl",
            "profile": "developer-windows-wsl",
            "root": "{home}/work/koa-linux",
            "windows_source": r"C:\mycode\kOA-Linux\koa-linux",
        })
        state, detail = manager.source_sync_status(ws)
        self.assertEqual(state, "source_changed")
        self.assertIn("changed", detail)
        self.assertNotIn('git -C "$SRC"', scripts[-1])
        self.assertIn(".koali-control-state", scripts[-1])



    def test_debug_diagnostics_demotes_architecture_formalities_and_returns_success(self) -> None:
        logs: list[str] = []
        reports: list[str] = []

        class StubBackend:
            def capture_in_workspace(self, workspace, command, *, timeout=30):
                payload = {
                    "architecture_readiness": "blocked",
                    "checks": [{
                        "check_id": "file-architecture",
                        "status": "fail",
                        "counts": {"errors": 2, "warnings": 0},
                        "findings": [
                            {"message": "unknown file at repository root", "path": "GitSink.bat"},
                            {"message": "committed path is not present in lock", "path": "build/out.py"},
                        ],
                    }],
                    "pipeline": {"component_bundles": {"status": "blocked", "missing": ["audit-broker"]}},
                }
                return CaptureResult(1, json.dumps(payload))

        runner = DebugDiagnosticsRunner({}, lambda _name: StubBackend(), logs.append, reports.append)
        ws = Workspace.from_config("main", {"backend": "wsl", "root": "/work/koa", "profile": "sovereign-linux-node"})
        code = runner.run(ws, timeout=30)
        self.assertEqual(code, 0)
        self.assertIn("EXECUTION COMPLETE", reports[-1])
        self.assertIn("Architecture/conformance: WARN in DEBUG", reports[-1])
        self.assertIn("GitSink.bat", reports[-1])
        self.assertIn("audit-broker", reports[-1])

    def test_debug_diagnostics_returns_error_only_when_diagnostic_cannot_be_read(self) -> None:
        class StubBackend:
            def capture_in_workspace(self, workspace, command, *, timeout=30):
                return CaptureResult(2, "usage: koa diagnose ...")

        runner = DebugDiagnosticsRunner({}, lambda _name: StubBackend(), lambda _msg: None)
        ws = Workspace.from_config("main", {"backend": "wsl", "root": "/work/koa", "profile": "sovereign-linux-node"})
        self.assertEqual(runner.run(ws, timeout=30), 2)

    def test_levelupdiag_adapter_delegates_campaign_inside_active_backend(self) -> None:
        scripts: list[str] = []

        class StubWsl(WslBackend):
            def windows_to_linux_path(self, windows_path: str) -> str:
                self.seen_windows_root = windows_path
                return "/mnt/c/mycode/kOA-Linux/LevelUpDiag-Koali"
            def resolve_path(self, template: str) -> str:
                return template.replace("{home}", "/home/rejean")
            def capture_shell(self, script: str, *, timeout: int = 30) -> CaptureResult:
                return CaptureResult(0, "")
            def run_shell(self, script: str, label: str, *, timeout: int | None = None) -> int:
                scripts.append(script)
                self.last_label = label
                return 0

        backend = StubWsl({"distribution": "Ubuntu-24.04"}, ProcessRunner(lambda _msg: None), {})
        cfg = {
            "diagnostics": {
                "levelupdiag": {
                    "enabled": True,
                    "root": r"C:\mycode\kOA-Linux\LevelUpDiag-Koali",
                    "campaigns": {"run_all": "nightly"},
                }
            }
        }
        ws = Workspace.from_config("koa-linux-main", {
            "repository": "koa-linux",
            "backend": "wsl",
            "profile": "developer-windows-wsl",
            "root": "{home}/work/koa-linux",
        })
        adapter = LevelUpDiagAdapter(cfg, lambda _name: backend, lambda _msg: None)
        self.assertEqual(adapter.run_configured_campaign(ws, "run_all"), 0)
        script = scripts[-1]
        self.assertIn("/mnt/c/mycode/kOA-Linux/LevelUpDiag-Koali", script)
        self.assertIn("LEVELUPDIAG_TARGET_REPO_ROOT=/home/rejean/work/koa-linux", script)
        self.assertIn("python levelupdiag.py run nightly", script)
        self.assertNotIn("koa_tools.cli", script)

    def test_levelupdiag_resolves_repository_relative_qemu_paths_inside_workspace(self) -> None:
        class StubWsl(WslBackend):
            def resolve_path(self, template: str) -> str:
                return template.replace("{home}", "/home/rejean")

        backend = StubWsl({"distribution": "Ubuntu-24.04"}, ProcessRunner(lambda _msg: None), {})
        cfg = {"system_test": {"qemu": {"image": "generated/system/koa.img", "active_profile": "generated/profiles/effective-profile.json"}}}
        ws = Workspace.from_config("main", {"repository": "koa-linux", "backend": "wsl", "profile": "developer-windows-wsl", "root": "{home}/work/koa-linux"})
        adapter = LevelUpDiagAdapter(cfg, lambda _name: backend, lambda _msg: None)
        env = adapter.qemu_environment(ws)
        self.assertEqual(env["KOA_QEMU_IMAGE"], "/home/rejean/work/koa-linux/generated/system/koa.img")
        self.assertEqual(env["KOA_QEMU_ACTIVE_PROFILE"], "/home/rejean/work/koa-linux/generated/profiles/effective-profile.json")

    def test_levelupdiag_system_receives_qemu_context_without_direct_pytest(self) -> None:
        scripts: list[str] = []

        class StubWsl(WslBackend):
            def windows_to_linux_path(self, windows_path: str) -> str:
                if windows_path == r"C:\images\koa.img":
                    return "/mnt/c/images/koa.img"
                return "/mnt/c/LevelUpDiag-Koali"
            def resolve_path(self, template: str) -> str:
                return template.replace("{home}", "/home/rejean")
            def capture_shell(self, script: str, *, timeout: int = 30) -> CaptureResult:
                return CaptureResult(0, "")
            def run_shell(self, script: str, label: str, *, timeout: int | None = None) -> int:
                scripts.append(script)
                return 0

        backend = StubWsl({"distribution": "Ubuntu-24.04"}, ProcessRunner(lambda _msg: None), {})
        cfg = {
            "diagnostics": {"levelupdiag": {"enabled": True, "root": r"C:\LevelUpDiag-Koali"}},
            "system_test": {"qemu": {
                "image": r"C:\images\koa.img",
                "image_format": "raw",
                "network": "off",
                "expected_release_identity": "koa-test",
                "session_ready_regex": "session-ready",
                "compositor_ready_regex": "compositor-ready",
                "confinement_ready_regex": "confined-ready",
                "general_surface_denied_regex": "general-denied",
                "privilege_path_denied_regex": "privilege-denied",
                "active_profile": "/tmp/effective-profile.json",
                "navigation_surface_id": "home",
                "navigation_ready_regex": "nav-ready",
                "navigation_result_regex": "nav-result",
                "navigation_keys": "down,ret",
                "mediatheque_selection": "selected",
                "active_release_set": "/tmp/release-set.json",
                "mediatheque_artifact_ref": "sha256:test",
                "mediatheque_offline_regex": "offline-ready",
                "semantik_selection": "selected",
                "semantik_ready_regex": "semantik-ready",
            }},
        }
        ws = Workspace.from_config("main", {
            "repository": "koa-linux",
            "backend": "wsl",
            "profile": "developer-windows-wsl",
            "root": "{home}/work/koa-linux",
        })
        adapter = LevelUpDiagAdapter(cfg, lambda _name: backend, lambda _msg: None)
        self.assertEqual(adapter.run_system(ws), 0)
        script = scripts[-1]
        self.assertIn("python levelupdiag.py run N10", script)
        self.assertIn("KOA_QEMU_IMAGE=/mnt/c/images/koa.img", script)
        self.assertIn("KOA_QEMU_EXPECTED_RELEASE_IDENTITY=koa-test", script)
        self.assertIn("KOA_QEMU_CONFINEMENT_READY_REGEX=confined-ready", script)
        self.assertIn("KOA_QEMU_GENERAL_SURFACE_DENIED_REGEX=general-denied", script)
        self.assertIn("KOA_QEMU_PRIVILEGE_PATH_DENIED_REGEX=privilege-denied", script)
        self.assertIn("KOA_QEMU_ACTIVE_PROFILE=/tmp/effective-profile.json", script)
        self.assertIn("KOA_QEMU_NAVIGATION_SURFACE_ID=home", script)
        self.assertIn("KOA_QEMU_NAVIGATION_KEYS=down,ret", script)
        self.assertIn("KOA_QEMU_MEDIATHEQUE_SELECTION=selected", script)
        self.assertIn("KOA_QEMU_ACTIVE_RELEASE_SET=/tmp/release-set.json", script)
        self.assertIn("KOA_QEMU_MEDIATHEQUE_ARTIFACT_REF=sha256:test", script)
        self.assertIn("KOA_QEMU_MEDIATHEQUE_OFFLINE_REGEX=offline-ready", script)
        self.assertNotIn("pytest", script)
        self.assertNotIn("tests/system/", script)

    def test_levelupdiag_surfaces_structured_findings_from_fresh_campaign_summary(self) -> None:
        scripts: list[str] = []
        reports: list[str] = []
        logs: list[str] = []
        marker_calls = 0

        summary = {
            "schema": "levelupdiag.campaign-summary.v2",
            "selection": "developer-fast",
            "qualification_scope": "koali_core_pre_subsystem",
            "deferred_subsystems": ["konnaxion", "ariane", "orgo", "semantik_architect"],
            "verdict": "WARN",
            "levels": [
                {"id": "N01", "name": "Environment", "verdict": "WARN", "result": "levels/N01/result.json"},
                {"id": "N02", "name": "Repository", "verdict": "PASS", "result": "levels/N02/result.json"},
            ],
        }
        n01 = {
            "schema": "levelupdiag.report.v2",
            "level_id": "N01",
            "level_name": "Environment",
            "verdict": "WARN",
            "findings": [
                {
                    "id": "n01.tool.optional.cargo.missing",
                    "verdict": "WARN",
                    "message": "Optional tool is not available: cargo",
                    "recommendation": "Install cargo only when a task requires it.",
                    "evidence": "tool=cargo\nstatus=missing",
                }
            ],
        }
        n02 = {
            "schema": "levelupdiag.report.v2",
            "level_id": "N02",
            "level_name": "Repository",
            "verdict": "PASS",
            "findings": [],
        }

        class StubWsl(WslBackend):
            def windows_to_linux_path(self, windows_path: str) -> str:
                return "/mnt/c/LevelUpDiag-Koali"

            def resolve_path(self, template: str) -> str:
                return template.replace("{home}", "/home/rejean")

            def capture_shell(self, script: str, *, timeout: int = 30) -> CaptureResult:
                nonlocal marker_calls
                if script.startswith("test -d "):
                    return CaptureResult(0, "")
                if "summary.json" in script and "_control_root" in script and "latest" in script:
                    marker_calls += 1
                    path = "/tmp/old/summary.json" if marker_calls == 1 else "/tmp/new/summary.json"
                    return CaptureResult(0, f"{path}\t{marker_calls}\t100\n")
                if script.startswith("cat -- /tmp/new/summary.json"):
                    return CaptureResult(0, json.dumps(summary))
                if script.startswith("cat -- /tmp/new/N01/result.json"):
                    return CaptureResult(0, json.dumps(n01))
                if script.startswith("cat -- /tmp/new/N02/result.json"):
                    return CaptureResult(0, json.dumps(n02))
                return CaptureResult(0, "")

            def run_shell(self, script: str, label: str, *, timeout: int | None = None) -> int:
                scripts.append(script)
                return 0

        backend = StubWsl({"distribution": "Ubuntu-24.04"}, ProcessRunner(lambda _msg: None), {})
        cfg = {
            "diagnostics": {"levelupdiag": {"enabled": True, "root": r"C:\LevelUpDiag-Koali"}}
        }
        ws = Workspace.from_config("main", {
            "repository": "koa-linux",
            "backend": "wsl",
            "profile": "developer-windows-wsl",
            "root": "{home}/work/koa-linux",
        })
        adapter = LevelUpDiagAdapter(cfg, lambda _name: backend, logs.append, on_report=reports.append)

        self.assertEqual(adapter.run_campaign(ws, "developer-fast"), 0)
        self.assertEqual(len(reports), 1)
        self.assertIn("LevelUpDiag campaign — developer-fast: WARN", reports[0])
        self.assertIn("Qualification scope: koali_core_pre_subsystem", reports[0])
        self.assertIn("Deferred subsystems: konnaxion, ariane, orgo, semantik_architect", reports[0])
        self.assertIn("N01 — Environment: WARN", reports[0])
        self.assertIn("Optional tool is not available: cargo", reports[0])
        self.assertIn("Recommendation: Install cargo only when a task requires it.", reports[0])
        self.assertIn("Evidence (LevelUpDiag):", reports[0])
        self.assertIn("tool=cargo", reports[0])
        self.assertIn("status=missing", reports[0])
        self.assertIn("N02 — Repository: PASS", reports[0])
        self.assertEqual(adapter.last_report(), reports[0])
        self.assertTrue(any("N01 [WARN] Optional tool is not available: cargo" in line for line in logs))
        self.assertTrue(any("evidence (LevelUpDiag):" in line for line in logs))
        self.assertTrue(any("tool=cargo" in line for line in logs))
        self.assertIn("python levelupdiag.py run developer-fast", scripts[-1])


    def test_levelupdiag_evidence_presentation_is_bounded_without_interpreting_verdicts(self) -> None:
        evidence = "A" * 9000 + "\nBLOCKED prerequisite detail\n" + "Z" * 9000
        clipped = LevelUpDiagAdapter._clip_evidence(evidence, 4000)
        self.assertLess(len(clipped), len(evidence))
        self.assertIn("evidence character(s) omitted by Control Panel presentation", clipped)
        self.assertTrue(clipped.startswith("A"))
        self.assertTrue(clipped.endswith("Z"))

    def test_levelupdiag_does_not_present_stale_structured_report(self) -> None:
        reports: list[str] = []

        class StubWsl(WslBackend):
            def windows_to_linux_path(self, windows_path: str) -> str:
                return "/mnt/c/LevelUpDiag-Koali"

            def resolve_path(self, template: str) -> str:
                return template.replace("{home}", "/home/rejean")

            def capture_shell(self, script: str, *, timeout: int = 30) -> CaptureResult:
                if script.startswith("test -d "):
                    return CaptureResult(0, "")
                if "summary.json" in script and "_control_root" in script and "latest" in script:
                    return CaptureResult(0, "/tmp/same-summary.json\t1\t100\n")
                if script.startswith("cat --"):
                    self.fail("stale summary must not be read")
                return CaptureResult(0, "")

            def run_shell(self, script: str, label: str, *, timeout: int | None = None) -> int:
                return 3

        backend = StubWsl({"distribution": "Ubuntu-24.04"}, ProcessRunner(lambda _msg: None), {})
        cfg = {
            "diagnostics": {"levelupdiag": {"enabled": True, "root": r"C:\LevelUpDiag-Koali"}}
        }
        ws = Workspace.from_config("main", {
            "repository": "koa-linux",
            "backend": "wsl",
            "profile": "developer-windows-wsl",
            "root": "{home}/work/koa-linux",
        })
        adapter = LevelUpDiagAdapter(cfg, lambda _name: backend, lambda _msg: None, on_report=reports.append)

        self.assertEqual(adapter.run_campaign(ws, "developer-fast"), 3)
        self.assertEqual(len(reports), 1)
        self.assertIn("No fresh structured LevelUpDiag report was produced", reports[0])

    def test_qemu_preflight_is_not_ready_when_infrastructure_or_context_is_missing(self) -> None:
        class StubWsl(WslBackend):
            def resolve_path(self, template: str) -> str:
                return template.replace("{home}", "/home/rejean")

            def capture_shell(self, script: str, *, timeout: int = 30) -> CaptureResult:
                if "qemu-system-x86_64" in script:
                    return CaptureResult(0, "qemu=missing\nuefi=missing\n")
                return CaptureResult(0, "")

        backend = StubWsl({"distribution": "Ubuntu-24.04"}, ProcessRunner(lambda _msg: None), {})
        cfg = {"system_test": {"qemu": {"image_format": "raw", "network": "off"}}}
        ws = Workspace.from_config("main", {
            "repository": "koa-linux",
            "backend": "wsl",
            "profile": "developer-windows-wsl",
            "root": "{home}/work/koa-linux",
        })
        manager = QemuEnvironmentManager(cfg, lambda _name: backend, lambda _msg: None)
        result = manager.preflight(ws)
        self.assertFalse(result.ready)
        self.assertIn("BLOCKED qemu-system-x86_64: missing", result.text())
        self.assertIn("BLOCKED QEMU image: not configured", result.text())
        self.assertTrue(result.text().endswith("QEMU PREFLIGHT: NOT READY"))

    def test_qemu_prepare_provisions_wsl_and_discovers_repository_owned_context(self) -> None:
        state = {"provisioned": False}
        root_commands: list[str] = []

        class StubWsl(WslBackend):
            def resolve_path(self, template: str) -> str:
                return template.replace("{home}", "/home/rejean")

            def capture_shell(self, script: str, *, timeout: int = 30) -> CaptureResult:
                if "qemu-system-x86_64" in script:
                    if state["provisioned"]:
                        return CaptureResult(0, "qemu=ok\nuefi=/usr/share/OVMF/OVMF_CODE.fd\n")
                    return CaptureResult(0, "qemu=missing\nuefi=missing\n")
                if "then echo image=ok" in script:
                    return CaptureResult(0, "image=ok\nactive_profile=ok\n")
                if "session_runtime" in script and "generated.rglob" in script:
                    return CaptureResult(0, '["/home/rejean/work/koa-linux/generated/profiles/effective-profile.json"]\n')
                if "*.build.json" in script:
                    return CaptureResult(0, '[{"image":"/home/rejean/work/koa-linux/generated/system/koa.img","metadata":"/home/rejean/work/koa-linux/generated/system/koa.img.build.json","profile_id":"sovereign_linux_node","image_version":"1","backend_id":"qemu-uefi-validation"}]\n')
                if "packaging/system/image.toml" in script:
                    return CaptureResult(0, '{"present":true,"public_build_surface":true,"status":"ready","profile_contract":"docs/contracts/profiles/sovereign-linux-node.profile.json","activation_ready":false}\n')
                return CaptureResult(0, "")

            def run_root_shell(self, script: str, label: str, *, timeout: int | None = None) -> int:
                root_commands.append(script)
                state["provisioned"] = True
                return 0

        backend = StubWsl({"distribution": "Ubuntu-24.04"}, ProcessRunner(lambda _msg: None), {})
        cfg = {
            "build": {"image": {"enabled": False, "args": ["--output", "generated/system/koa.img"], "custom_command": ""}},
            "system_test": {"qemu": {
                "image": "",
                "image_format": "raw",
                "network": "off",
                "expected_release_identity": "release-test",
                "session_ready_regex": "session",
                "compositor_ready_regex": "compositor",
                "confinement_ready_regex": "confined",
                "general_surface_denied_regex": "general-denied",
                "privilege_path_denied_regex": "privilege-denied",
                "active_profile": "",
                "navigation_surface_id": "home",
                "navigation_ready_regex": "nav-ready",
                "navigation_result_regex": "nav-result",
                "navigation_keys": "ret",
                "mediatheque_selection": "not_selected",
                "provisioning": {"enabled": True, "provider": "ubuntu_apt", "apt_packages": ["qemu-system-x86", "qemu-utils", "ovmf"]},
            }},
        }
        ws = Workspace.from_config("main", {
            "repository": "koa-linux",
            "backend": "wsl",
            "profile": "developer-windows-wsl",
            "root": "{home}/work/koa-linux",
        })
        manager = QemuEnvironmentManager(cfg, lambda _name: backend, lambda _msg: None)
        result = manager.prepare(ws)
        self.assertTrue(root_commands)
        self.assertIn("qemu-system-x86", root_commands[0])
        self.assertIn("apt-get update -qq", root_commands[0])
        self.assertEqual(cfg["system_test"]["qemu"]["active_profile"], "/home/rejean/work/koa-linux/generated/profiles/effective-profile.json")
        self.assertEqual(cfg["system_test"]["qemu"]["image"], "/home/rejean/work/koa-linux/generated/system/koa.img")
        self.assertTrue(result.ready)

    def test_qemu_runtime_probe_supports_ubuntu_2404_ovmf_4m(self) -> None:
        class StubWsl(WslBackend):
            def resolve_path(self, template: str) -> str:
                return template.replace("{home}", "/home/rejean")

            def capture_shell(self, script: str, *, timeout: int = 30) -> CaptureResult:
                self.assert_probe_script(script)
                return CaptureResult(0, "qemu=ok\nuefi=/usr/share/OVMF/OVMF_CODE_4M.fd\n")

            @staticmethod
            def assert_probe_script(script: str) -> None:
                if "/usr/share/OVMF/OVMF_CODE_4M.fd" not in script:
                    raise AssertionError("Ubuntu 24.04 OVMF 4M path is missing from QEMU runtime probe")

        backend = StubWsl({"distribution": "Ubuntu-24.04"}, ProcessRunner(lambda _msg: None), {})
        cfg = {"system_test": {"qemu": {}}}
        ws = Workspace.from_config("main", {
            "repository": "koa-linux",
            "backend": "wsl",
            "profile": "developer-windows-wsl",
            "root": "{home}/work/koa-linux",
        })
        manager = QemuEnvironmentManager(cfg, lambda _name: backend, lambda _msg: None)
        qemu_ok, uefi_ok, uefi_path = manager._probe_runtime(ws)
        self.assertTrue(qemu_ok)
        self.assertTrue(uefi_ok)
        self.assertEqual(uefi_path, "/usr/share/OVMF/OVMF_CODE_4M.fd")

    def test_qemu_prepare_does_not_invent_semantic_runtime_markers(self) -> None:
        class StubWsl(WslBackend):
            def resolve_path(self, template: str) -> str:
                return template.replace("{home}", "/home/rejean")

            def capture_shell(self, script: str, *, timeout: int = 30) -> CaptureResult:
                if "qemu-system-x86_64" in script:
                    return CaptureResult(0, "qemu=ok\nuefi=/usr/share/OVMF/OVMF_CODE.fd\n")
                if "profiles/implementation-settings/developer-windows-wsl.toml" in script:
                    return CaptureResult(0, "")
                return CaptureResult(0, "")

        backend = StubWsl({"distribution": "Ubuntu-24.04"}, ProcessRunner(lambda _msg: None), {})
        cfg = {"build": {"image": {"enabled": False, "args": []}}, "system_test": {"qemu": {"image_format": "raw", "network": "off"}}}
        ws = Workspace.from_config("main", {"repository": "koa-linux", "backend": "wsl", "profile": "developer-windows-wsl", "root": "{home}/work/koa-linux"})
        manager = QemuEnvironmentManager(cfg, lambda _name: backend, lambda _msg: None)
        result = manager.prepare(ws)
        qemu = cfg["system_test"]["qemu"]
        self.assertNotIn("expected_release_identity", qemu)
        self.assertNotIn("session_ready_regex", qemu)
        self.assertNotIn("navigation_keys", qemu)
        self.assertFalse(result.ready)

    def test_qemu_prepare_clears_obsolete_implementation_settings_profile(self) -> None:
        class StubWsl(WslBackend):
            def resolve_path(self, template: str) -> str:
                return template.replace("{home}", "/home/rejean")

            def capture_shell(self, script: str, *, timeout: int = 30) -> CaptureResult:
                if "session_runtime" in script and "generated.rglob" in script:
                    return CaptureResult(0, '["/home/rejean/work/koa-linux/generated/assembly/effective-profile.json"]\n')
                return CaptureResult(0, "")

        cfg = {"system_test": {"qemu": {"active_profile": "/home/rejean/work/koa-linux/profiles/implementation-settings/developer-windows-wsl.toml"}}}
        backend = StubWsl({"distribution": "Ubuntu-24.04"}, ProcessRunner(lambda _msg: None), {})
        ws = Workspace.from_config("main", {"repository": "koa-linux", "backend": "wsl", "profile": "developer-windows-wsl", "root": "{home}/work/koa-linux"})
        manager = QemuEnvironmentManager(cfg, lambda _name: backend, lambda _msg: None)
        self.assertTrue(manager._auto_profile(ws))
        self.assertEqual(cfg["system_test"]["qemu"]["active_profile"], "/home/rejean/work/koa-linux/generated/assembly/effective-profile.json")

    def test_qemu_n10_scoped_preflight_does_not_require_n09_navigation_context(self) -> None:
        class StubWsl(WslBackend):
            def resolve_path(self, template: str) -> str:
                return template.replace("{home}", "/home/rejean")

            def capture_shell(self, script: str, *, timeout: int = 30) -> CaptureResult:
                if "qemu-system-x86_64" in script:
                    return CaptureResult(0, "qemu=ok\nuefi=/usr/share/OVMF/OVMF_CODE.fd\n")
                if "then echo image=ok" in script:
                    return CaptureResult(0, "image=ok\n")
                if "packaging/system/image.toml" in script:
                    return CaptureResult(0, '{"present":true,"public_build_surface":true,"status":"blocked_missing_assembly_and_component_bundles","profile_contract":"docs/contracts/profiles/sovereign-linux-node.profile.json","activation_ready":false}\n')
                return CaptureResult(0, "")

        cfg = {"system_test": {"qemu": {
            "image": "/home/rejean/work/koa-linux/generated/system/koa.img",
            "image_format": "raw",
            "network": "off",
            "expected_release_identity": "release-test",
            "session_ready_regex": "session",
            "compositor_ready_regex": "compositor",
        }}}
        backend = StubWsl({"distribution": "Ubuntu-24.04"}, ProcessRunner(lambda _msg: None), {})
        ws = Workspace.from_config("main", {"repository": "koa-linux", "backend": "wsl", "profile": "developer-windows-wsl", "root": "{home}/work/koa-linux"})
        manager = QemuEnvironmentManager(cfg, lambda _name: backend, lambda _msg: None)
        n10 = manager.preflight(ws, scopes="N10")
        combined = manager.preflight(ws)
        self.assertTrue(n10.ready)
        self.assertIn("QEMU PREFLIGHT [N10]: READY", n10.text())
        self.assertFalse(combined.ready)
        self.assertIn("BLOCKED context.navigation_surface_id: not configured", combined.text())

    def test_qemu_preflight_surfaces_repository_image_package_blocker(self) -> None:
        class StubWsl(WslBackend):
            def resolve_path(self, template: str) -> str:
                return template.replace("{home}", "/home/rejean")

            def capture_shell(self, script: str, *, timeout: int = 30) -> CaptureResult:
                if "qemu-system-x86_64" in script:
                    return CaptureResult(0, "qemu=ok\nuefi=/usr/share/OVMF/OVMF_CODE.fd\n")
                if "packaging/system/image.toml" in script:
                    return CaptureResult(0, '{"present":true,"public_build_surface":true,"status":"blocked_missing_assembly_and_component_bundles","profile_contract":"docs/contracts/profiles/sovereign-linux-node.profile.json","activation_ready":false,"build_pipeline":"missing"}\n')
                return CaptureResult(0, "")

        cfg = {"system_test": {"qemu": {"image_format": "raw", "network": "off"}}}
        backend = StubWsl({"distribution": "Ubuntu-24.04"}, ProcessRunner(lambda _msg: None), {})
        ws = Workspace.from_config("main", {"repository": "koa-linux", "backend": "wsl", "profile": "developer-windows-wsl", "root": "{home}/work/koa-linux"})
        manager = QemuEnvironmentManager(cfg, lambda _name: backend, lambda _msg: None)
        result = manager.preflight(ws, scopes="N10")
        self.assertFalse(result.ready)
        self.assertIn("BLOCKED repository image package: blocked_missing_assembly_and_component_bundles", result.text())
        self.assertIn("INFO    repository image target profile: docs/contracts/profiles/sovereign-linux-node.profile.json", result.text())

    def test_koali_no_longer_owns_diagnostic_test_sequences(self) -> None:
        root = Path(__file__).parents[1]
        orchestration = (root / "koali_control" / "orchestration.py").read_text(encoding="utf-8")
        app = (root / "koali_control" / "app.py").read_text(encoding="utf-8")
        self.assertNotIn("quality_sequence", orchestration)
        self.assertNotIn("qemu_test", orchestration)
        self.assertNotIn("tests/system/test_qemu_", app)
        self.assertNotIn('["validate", "--warnings-as-errors"]', app)
        self.assertNotIn('["test", "--architecture"]', app)
        self.assertIn("LevelUpDiagAdapter", app)


    def test_wsl_profile_path_includes_cargo_bin(self) -> None:
        script = WslBackend._profile_script("cargo --version")
        self.assertIn('$HOME/.cargo/bin', script)
        self.assertIn('$HOME/.local/bin', script)

    def test_repository_rust_preflight_uses_declared_toolchain(self) -> None:
        class StubWsl(WslBackend):
            def capture_in_workspace(self, workspace: Workspace, command: str, *, timeout: int = 30) -> CaptureResult:
                if "PYRUST" in command:
                    return CaptureResult(0, "rust_required=yes\nchannel=1.85.1\nprofile=minimal\ncomponents=clippy,rustfmt\n")
                return CaptureResult(
                    0,
                    "rustup=ok:/usr/bin/rustup\n"
                    "toolchain=ok\n"
                    "rustc=ok:rustc 1.85.1 (fixture)\n"
                    "cargo=ok:cargo 1.85.1 (fixture)\n"
                    "component:clippy=ok\n"
                    "component:rustfmt=ok\n",
                )

        backend = StubWsl({"distribution": "Ubuntu-24.04"}, ProcessRunner(lambda _msg: None), {})
        ws = Workspace.from_config("main", {"repository": "koa-linux", "backend": "wsl", "profile": "developer-windows-wsl", "root": "/home/rejean/work/koa-linux"})
        checks = backend.repository_rust_toolchain_preflight(ws)
        self.assertTrue(checks)
        self.assertTrue(all(item.state == CheckState.PASS for item in checks))
        self.assertIn("channel 1.85.1", checks[0].detail)
        self.assertTrue(any(item.label == "Tool: cargo" for item in checks))
        self.assertTrue(any(item.label == "Rust component: clippy" for item in checks))
        self.assertTrue(any(item.label == "Rust component: rustfmt" for item in checks))

    def test_repository_rust_provisioner_installs_exact_contract_without_git(self) -> None:
        calls: list[tuple[str, str]] = []

        class StubWsl(WslBackend):
            def _repository_rust_requirement(self, workspace: Workspace):
                return ("1.85.1", "minimal", ("clippy", "rustfmt"))
            def capture_shell(self, script: str, *, timeout: int = 30) -> CaptureResult:
                if "command -v rustup" in script:
                    return CaptureResult(0, "")
                return CaptureResult(0, "")
            def run_root_shell(self, script: str, label: str, *, timeout: int | None = None) -> int:
                calls.append(("root", script))
                return 0
            def run_shell(self, script: str, label: str, *, timeout: int | None = None) -> int:
                calls.append(("user", script))
                return 0
            def resolve_path(self, template: str) -> str:
                return template
            def repository_rust_toolchain_preflight(self, workspace: Workspace):
                return [CheckResult("rust_tool_cargo", "Tool: cargo", CheckState.PASS, "cargo 1.85.1")]

        backend = StubWsl(
            {"distribution": "Ubuntu-24.04", "toolchain_provisioning": {"enabled": True, "rustup_apt_package": "rustup"}},
            ProcessRunner(lambda _msg: None),
            {},
        )
        ws = Workspace.from_config("main", {"repository": "koa-linux", "backend": "wsl", "profile": "developer-windows-wsl", "root": "/home/rejean/work/koa-linux"})
        self.assertEqual(backend.provision_repository_rust_toolchain(ws), 0)
        root_script = next(script for kind, script in calls if kind == "root")
        user_script = next(script for kind, script in calls if kind == "user")
        self.assertIn("apt-get install -y rustup", root_script)
        self.assertIn("rustup toolchain install 1.85.1 --profile minimal --component clippy --component rustfmt", user_script)
        self.assertIn("$HOME/.cargo/bin", user_script)
        self.assertNotIn("rustup default", user_script)
        self.assertNotIn("git status", user_script)
        self.assertNotIn("git reset", user_script)

    def test_native_build_preflight_proves_cc_compile_link(self) -> None:
        calls: list[str] = []

        class StubWsl(WslBackend):
            def _repository_rust_requirement(self, workspace: Workspace):
                return ("1.85.1", "minimal", ("clippy", "rustfmt"))
            def capture_in_workspace(self, workspace: Workspace, command: str, *, timeout: int = 30) -> CaptureResult:
                calls.append(command)
                return CaptureResult(0, "cc=ok:/usr/bin/cc (cc fixture)\nlink=ok:temporary C compile/link probe succeeded\n")

        backend = StubWsl({"distribution": "Ubuntu-24.04"}, ProcessRunner(lambda _msg: None), {})
        ws = Workspace.from_config("main", {"repository": "koa-linux", "backend": "wsl", "profile": "developer-windows-wsl", "root": "/home/rejean/work/koa-linux"})
        checks = backend.repository_native_build_toolchain_preflight(ws)
        self.assertTrue(all(item.state == CheckState.PASS for item in checks))
        self.assertTrue(any(item.label == "Tool: cc" for item in checks))
        self.assertTrue(any(item.label == "Native compiler/linker" for item in checks))
        self.assertIn("command -v cc", calls[0])
        self.assertIn("/tmp/.koali-native-link-", calls[0])
        self.assertIn('"$cc_bin" "$tmp/probe.c" -o "$tmp/probe"', calls[0])

    def test_native_build_provisioner_installs_configured_package_without_git(self) -> None:
        calls: list[tuple[str, str]] = []

        class StubWsl(WslBackend):
            def _repository_rust_requirement(self, workspace: Workspace):
                return ("1.85.1", "minimal", ("clippy", "rustfmt"))
            def run_root_shell(self, script: str, label: str, *, timeout: int | None = None) -> int:
                calls.append((label, script))
                return 0
            def repository_native_build_toolchain_preflight(self, workspace: Workspace):
                return [
                    CheckResult("native_tool_cc", "Tool: cc", CheckState.PASS, "/usr/bin/cc"),
                    CheckResult("native_link", "Native compiler/linker", CheckState.PASS, "probe succeeded"),
                ]

        backend = StubWsl(
            {"distribution": "Ubuntu-24.04", "toolchain_provisioning": {"enabled": True, "provider": "ubuntu_apt_pipx", "native_build_apt_packages": ["build-essential"]}},
            ProcessRunner(lambda _msg: None),
            {},
        )
        ws = Workspace.from_config("main", {"repository": "koa-linux", "backend": "wsl", "profile": "developer-windows-wsl", "root": "/home/rejean/work/koa-linux"})
        self.assertEqual(backend.provision_repository_native_build_toolchain(ws), 0)
        label, script = calls[0]
        self.assertEqual(label, "Provision native build toolchain")
        self.assertIn("apt-get install -y build-essential", script)
        self.assertNotIn("git status", script)
        self.assertNotIn("git reset", script)
        self.assertNotIn("Cargo.lock", script)

    def test_prepare_orders_native_linker_before_cargo_cache_and_setup(self) -> None:
        source = (Path(__file__).parents[1] / "koali_control" / "orchestration.py").read_text(encoding="utf-8")
        rust_pos = source.index("repository_rust_toolchain_preflight")
        native_pos = source.index("repository_native_build_toolchain_preflight", rust_pos)
        cargo_pos = source.index("repository_cargo_cache_preflight", native_pos)
        setup_pos = source.index("run_setup = bool", cargo_pos)
        self.assertLess(rust_pos, native_pos)
        self.assertLess(native_pos, cargo_pos)
        self.assertLess(cargo_pos, setup_pos)
        self.assertIn("provision_repository_native_build_toolchain", source[native_pos:cargo_pos])

    def test_prepare_freezes_repository_rust_toolchain_before_repository_setup(self) -> None:
        source = (Path(__file__).parents[1] / "koali_control" / "orchestration.py").read_text(encoding="utf-8")
        rust_pos = source.index("repository_rust_toolchain_preflight")
        cargo_pos = source.index("repository_cargo_cache_preflight", rust_pos)
        setup_pos = source.index("run_setup = bool", cargo_pos)
        self.assertLess(rust_pos, cargo_pos)
        self.assertLess(cargo_pos, setup_pos)
        self.assertIn("provision_repository_rust_toolchain", source[rust_pos:cargo_pos])
        self.assertIn("prime_repository_cargo_cache", source[cargo_pos:setup_pos])

    def test_cargo_cache_preflight_proves_locked_offline_availability(self) -> None:
        calls: list[str] = []

        class StubWsl(WslBackend):
            def _repository_rust_requirement(self, workspace: Workspace):
                return ("1.85.1", "minimal", ("clippy", "rustfmt"))
            def capture_in_workspace(self, workspace: Workspace, command: str, *, timeout: int = 30) -> CaptureResult:
                calls.append(command)
                return CaptureResult(0, "")

        backend = StubWsl({"distribution": "Ubuntu-24.04"}, ProcessRunner(lambda _msg: None), {})
        ws = Workspace.from_config("main", {"repository": "koa-linux", "backend": "wsl", "profile": "developer-windows-wsl", "root": "/home/rejean/work/koa-linux"})
        checks = backend.repository_cargo_cache_preflight(ws)
        self.assertEqual(checks[0].state, CheckState.PASS)
        self.assertIn("rustup run 1.85.1 cargo fetch", calls[0])
        self.assertIn("--locked", calls[0])
        self.assertIn("--offline", calls[0])
        self.assertIn("--manifest-path Cargo.toml", calls[0])

    def test_cargo_cache_prime_fetches_then_verifies_offline(self) -> None:
        calls: list[tuple[str, str]] = []

        class StubWsl(WslBackend):
            def _repository_rust_requirement(self, workspace: Workspace):
                return ("1.85.1", "minimal", ("clippy", "rustfmt"))
            def execute_in_workspace(self, workspace: Workspace, command: str, label: str, *, timeout: int | None = None) -> int:
                calls.append((label, command))
                return 0

        backend = StubWsl({"distribution": "Ubuntu-24.04", "toolchain_provisioning": {"prime_cargo_cache": True}}, ProcessRunner(lambda _msg: None), {})
        ws = Workspace.from_config("main", {"repository": "koa-linux", "backend": "wsl", "profile": "developer-windows-wsl", "root": "/home/rejean/work/koa-linux"})
        self.assertEqual(backend.prime_repository_cargo_cache(ws), 0)
        self.assertEqual([label for label, _ in calls], ["Prime Cargo cache from Cargo.lock", "Verify Cargo cache offline"])
        self.assertNotIn("--offline", calls[0][1])
        self.assertIn("--offline", calls[1][1])
        self.assertTrue(all("--locked" in command for _, command in calls))
        self.assertTrue(all("Cargo.toml" in command for _, command in calls))

    def test_cargo_build_diagnostic_replays_offline_build_in_tmp(self) -> None:
        calls: list[tuple[str, str]] = []

        class StubWsl(WslBackend):
            def _repository_rust_requirement(self, workspace: Workspace):
                return ("1.85.1", "minimal", ("clippy", "rustfmt"))
            def execute_in_workspace(self, workspace: Workspace, command: str, label: str, *, timeout: int | None = None) -> int:
                calls.append((label, command))
                return 101

        backend = StubWsl({"distribution": "Ubuntu-24.04"}, ProcessRunner(lambda _msg: None), {})
        ws = Workspace.from_config("main", {"repository": "koa-linux", "backend": "wsl", "profile": "developer-windows-wsl", "root": "/home/rejean/work/koa-linux"})
        self.assertEqual(backend.run_cargo_build_diagnostic(ws, "koa-node-agent", "1788526810"), 101)
        label, script = calls[0]
        self.assertIn("Cargo build diagnostic", label)
        self.assertIn("CARGO_NET_OFFLINE=true", script)
        self.assertIn("SOURCE_DATE_EPOCH=1788526810", script)
        self.assertIn("rustup run 1.85.1 cargo build --locked --offline --release --package koa-node-agent --bins", script)
        self.assertIn("/tmp/.koali-cargo-diagnostic-", script)
        self.assertIn("--target-dir", script)

    def test_node_agent_failure_triggers_visible_cargo_diagnostic(self) -> None:
        source = (Path(__file__).parents[1] / "koali_control" / "orchestration.py").read_text(encoding="utf-8")
        self.assertIn('component == "koa-node-agent"', source)
        self.assertIn("run_cargo_build_diagnostic", source)
        self.assertIn("full compiler/offline-cache error is visible", source)


if __name__ == "__main__":
    unittest.main()
