from __future__ import annotations

import os
import base64
import re
from pathlib import Path
import platform
import shlex
import shutil
import subprocess
from abc import ABC, abstractmethod
from urllib.parse import quote

from .models import CheckResult, CheckState, Workspace
from .process import CaptureResult, ProcessRunner


def shell_join(parts: list[str]) -> str:
    return shlex.join([str(item) for item in parts])


class ExecutionBackend(ABC):
    backend_id: str

    def __init__(self, config: dict, runner: ProcessRunner, app_config: dict) -> None:
        self.config = config
        self.runner = runner
        self.app_config = app_config

    @abstractmethod
    def capture_shell(self, script: str, *, timeout: int = 30) -> CaptureResult: ...

    @abstractmethod
    def run_shell(self, script: str, label: str, *, timeout: int | None = None) -> int: ...

    @abstractmethod
    def start(self) -> int: ...

    @abstractmethod
    def preflight(self) -> list[CheckResult]: ...

    @abstractmethod
    def resolve_path(self, template: str) -> str: ...

    @abstractmethod
    def open_shell(self, workspace: Workspace) -> None: ...

    @abstractmethod
    def open_editor(self, workspace: Workspace) -> None: ...

    def execute_in_workspace(self, workspace: Workspace, command: str, label: str, *, timeout: int | None = None) -> int:
        root = self.resolve_path(workspace.root)
        script = f"cd {shlex.quote(root)} && {command}"
        return self.run_shell(script, label, timeout=timeout)

    def capture_in_workspace(self, workspace: Workspace, command: str, *, timeout: int = 30) -> CaptureResult:
        root = self.resolve_path(workspace.root)
        return self.capture_shell(f"cd {shlex.quote(root)} && {command}", timeout=timeout)


class WslBackend(ExecutionBackend):
    backend_id = "wsl"

    @property
    def distro(self) -> str:
        return str(self.config.get("distribution", "Ubuntu-24.04")).strip()

    @property
    def shell(self) -> str:
        return str(self.config.get("shell", "bash")).strip() or "bash"

    def _wsl(self, *args: str) -> list[str]:
        return ["wsl.exe", "-d", self.distro, "--", *args]

    @staticmethod
    def _profile_script(script: str) -> str:
        # Profile-level tools installed for the Linux user (for example via pipx)
        # live in ~/.local/bin. Keep that path explicit so the Control Panel does not depend on
        # terminal-specific shell startup behavior.
        return 'export PATH="$HOME/.cargo/bin:$HOME/.local/bin:$PATH"; ' + script


    @staticmethod
    def _tool_probe_script(*, include_pipx: bool = False, include_versions: bool = False) -> str:
        """Return a deterministic profile-tool probe.

        The probe intentionally avoids ``eval`` and shell-startup assumptions.
        Canonical profile paths are checked first, followed by ``command -v``.
        Combined with stdin script transport this keeps Windows command-line
        quoting out of Linux tool discovery entirely.
        """
        specs = {
            "git": ['"/usr/bin/git"', '"/usr/local/bin/git"'],
            "python": ['"/usr/bin/python"', '"/usr/local/bin/python"'],
            "uv": ['"$HOME/.local/bin/uv"', '"/usr/local/bin/uv"', '"/usr/bin/uv"'],
            "pipx": ['"/usr/bin/pipx"', '"/usr/local/bin/pipx"', '"$HOME/.local/bin/pipx"'],
        }
        names = ["git", "python", "uv"] + (["pipx"] if include_pipx else [])
        lines = [
            "set +e",
            "resolve_koali_tool() {",
            '  name="$1"; shift',
            '  for candidate in "$@"; do',
            '    if test -x "$candidate"; then printf \'%s\\n\' "$candidate"; return 0; fi',
            '  done',
            '  command -v "$name" 2>/dev/null || true',
            "}",
            "missing=0",
        ]
        for name in names:
            candidates = " ".join(specs[name])
            lines += [
                f"{name}_bin=$(resolve_koali_tool {name} {candidates})",
                f'if test -n "${{{name}_bin}}" && test -x "${{{name}_bin}}"; then',
                f'  printf \'tool:{name}=ok:%s\\n\' "${{{name}_bin}}"',
                "else",
                f"  echo 'tool:{name}=missing'",
                "  missing=1",
                "fi",
            ]
        lines += [
            "python3_bin=$(command -v python3 2>/dev/null || true)",
            'test -n "$python3_bin" && printf \'tool:python3=ok:%s\\n\' "$python3_bin" || true',
        ]
        if include_versions:
            lines += [
                'if test -n "$git_bin"; then "$git_bin" --version 2>&1 | sed \'s/^/version:git=/\' ; fi',
                'if test -n "$python_bin"; then "$python_bin" --version 2>&1 | sed \'s/^/version:python=/\' ; fi',
                'if test -n "$uv_bin"; then "$uv_bin" --version 2>&1 | sed \'s/^/version:uv=/\' ; fi',
            ]
            if include_pipx:
                lines += ['if test -n "$pipx_bin"; then "$pipx_bin" --version 2>&1 | sed \'s/^/version:pipx=/\' ; fi']
        lines += ['test "$missing" -eq 0']
        return "\n".join(lines)

    def _tool_probe(self, *, include_pipx: bool = False, include_versions: bool = False, timeout: int = 30) -> CaptureResult:
        return self.capture_shell(
            self._tool_probe_script(include_pipx=include_pipx, include_versions=include_versions),
            timeout=timeout,
        )

    @staticmethod
    def _probe_has_tool(output: str, tool: str) -> bool:
        return f"tool:{tool}=ok:" in output

    def capture_shell(self, script: str, *, timeout: int = 30) -> CaptureResult:
        # Transport shell programs through stdin instead of embedding them in the
        # Windows command line.  This preserves quotes, dollar expressions, awk
        # programs and multiline scripts exactly across the Windows -> WSL boundary.
        return self.runner.capture(
            self._wsl(self.shell, "-s"),
            timeout=timeout,
            input_text=self._profile_script(script) + "\n",
        )

    def run_shell(self, script: str, label: str, *, timeout: int | None = None) -> int:
        return self.runner.run(
            self._wsl(self.shell, "-s"),
            label,
            timeout=timeout,
            input_text=self._profile_script(script) + "\n",
        )

    def run_root_shell(self, script: str, label: str, *, timeout: int | None = None) -> int:
        argv = ["wsl.exe", "-d", self.distro, "-u", "root", "--", self.shell, "-s"]
        return self.runner.run(argv, label, timeout=timeout, input_text=script + "\n")

    def start(self) -> int:
        if not self.is_registered():
            self.runner.log(f"WSL distribution {self.distro} is not installed")
            return 78
        probe = self.runtime_probe(timeout=15)
        if probe.code == 0:
            return 0
        if probe.code == 124:
            self.runner.log(f"WSL distribution {self.distro} is registered but not command-ready; use PREPARE WSL BACKEND")
            return 78
        self.runner.log(f"WSL distribution {self.distro} could not start: {probe.output.strip() or f'exit {probe.code}'}")
        return probe.code

    def list_distros(self) -> CaptureResult:
        return self.runner.capture(["wsl.exe", "-l", "-v"], timeout=15)

    def list_distro_names(self) -> list[str]:
        result = self.runner.capture(["wsl.exe", "-l", "-q"], timeout=15)
        if result.code != 0:
            return []
        return [line.strip().lstrip("*").strip() for line in result.output.splitlines() if line.strip()]

    def is_registered(self) -> bool:
        target = self.distro.casefold()
        return any(name.casefold() == target for name in self.list_distro_names())

    def runtime_probe(self, *, timeout: int = 8) -> CaptureResult:
        if not self.is_registered():
            return CaptureResult(78, f"{self.distro} is not installed")
        return self.capture_shell("printf 'KOALI_WSL_READY\\n'", timeout=timeout)

    def _launch_visible(self, argv: list[str], *, title: str = "Koali Control Panel") -> tuple[bool, str]:
        """Launch an interactive command in a console the user can actually see.

        The Control Panel is a ``.pyw`` application and therefore has no console.
        Starting ``wsl.exe`` indirectly through Windows Terminal can be accepted
        by an existing terminal process without creating a reliably visible
        interactive surface.  For first-run/OOBE work we therefore prefer a
        dedicated ``cmd.exe /k`` console.  ``/k`` intentionally keeps the
        window open if WSL exits immediately so the error remains inspectable.
        """
        errors: list[str] = []
        flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)

        if os.name == "nt":
            comspec = os.environ.get("COMSPEC") or shutil.which("cmd.exe") or "cmd.exe"
            try:
                command_line = subprocess.list2cmdline(argv)
                proc = subprocess.Popen(
                    [comspec, "/d", "/k", command_line],
                    creationflags=flags,
                )
                return True, f"dedicated console pid={proc.pid}"
            except OSError as exc:
                errors.append(f"cmd.exe: {exc}")

        terminal = str(self.app_config.get("terminal_exe", "wt.exe")).strip() or "wt.exe"
        try:
            proc = subprocess.Popen([terminal, "new-tab", "--title", title, *argv])
            return True, f"Windows Terminal pid={proc.pid}"
        except OSError as exc:
            errors.append(f"{terminal}: {exc}")

        try:
            proc = subprocess.Popen(argv, creationflags=flags)
            return True, f"direct console pid={proc.pid}"
        except OSError as exc:
            errors.append(f"direct: {exc}")

        self.runner.log("Unable to open an interactive console: " + " | ".join(errors))
        return False, " | ".join(errors)

    def prepare_interactive(self) -> str:
        """Idempotently expose the one-time WSL installation/initialization UI.

        Fresh Store-style Ubuntu distributions can be registered before their
        first-run user setup is complete.  Running `wsl --install` again in that
        state returns ALREADY_EXISTS, so the Control Panel must distinguish registration
        from command readiness.
        """
        if not self.is_registered():
            launched, detail = self._launch_visible(
                ["wsl.exe", "--install", "-d", self.distro],
                title=f"Koali Control Panel — install {self.distro}",
            )
            if not launched:
                self.runner.log(f"Could not open the WSL installer for {self.distro}: {detail}")
                return "launch_failed"
            self.runner.log(
                f"Opened visible WSL installation for {self.distro} ({detail}). "
                "Complete the Windows/Ubuntu prompts in that window."
            )
            return "install"
        probe = self.runtime_probe(timeout=5)
        if probe.code == 0 and "KOALI_WSL_READY" in probe.output:
            self.runner.log(f"WSL backend {self.distro} is already installed and command-ready")
            return "ready"
        launched, detail = self._launch_visible(
            ["wsl.exe", "-d", self.distro],
            title=f"Koali Control Panel — initialize {self.distro}",
        )
        if not launched:
            self.runner.log(
                f"{self.distro} is registered but Koali Control Panel could not open an interactive first-run console: {detail}"
            )
            return "launch_failed"
        self.runner.log(
            f"{self.distro} is already registered but not command-ready. "
            f"Interactive first-run console launched ({detail}); complete the one-time Linux user setup there."
        )
        return "initialize"

    def install(self) -> int:
        """Compatibility entry point; installation is intentionally idempotent."""
        mode = self.prepare_interactive()
        return 0 if mode in {"install", "initialize", "ready"} else 1

    def terminate(self) -> int:
        return self.runner.run(["wsl.exe", "--terminate", self.distro], f"Terminate {self.distro}", timeout=60)

    def resolve_path(self, template: str) -> str:
        if "{home}" not in template:
            return template
        result = self.capture_shell("printf '%s' \"$HOME\"", timeout=15)
        if result.code != 0 or not result.output.strip():
            return template
        return template.replace("{home}", result.output.strip())

    def windows_to_linux_path(self, windows_path: str) -> str:
        """Translate a Windows path inside the configured WSL distribution.

        Do not pass the raw Windows path as a wsl.exe command-line argument.
        Backslashes, drive-letter colons, Unicode and quoting at the Windows ->
        WSL boundary have already caused false translation failures in real
        Control Panel usage.  Instead, carry the path as base64 inside the stdin shell
        program and let the distro's own ``wslpath`` resolve its mount policy.

        A conservative /mnt/<drive> fallback is retained for normal absolute
        drive paths so a missing/broken wslpath does not block the canonical
        Windows-source import under the default WSL automount policy.
        """
        raw = str(windows_path).strip().strip('"')
        if not raw:
            return ""

        payload = base64.b64encode(raw.encode("utf-8")).decode("ascii")
        script = f"""set -eu
WIN_PATH=$(printf '%s' {shlex.quote(payload)} | base64 -d)
wslpath -u -- "$WIN_PATH"
"""
        result = self.capture_shell(script, timeout=15)
        if result.code == 0:
            lines = [line.strip() for line in result.output.splitlines() if line.strip()]
            if lines:
                return lines[-1]

        match = re.match(r"^([A-Za-z]):[\\/](.*)$", raw)
        if match:
            drive = match.group(1).lower()
            tail = match.group(2).replace("\\", "/")
            return f"/mnt/{drive}/{tail}"
        return ""

    @staticmethod
    def _clean_detail(text: str, limit: int = 240) -> str:
        lines = [line.strip() for line in text.replace("\x00", "").splitlines() if line.strip()]
        detail = " | ".join(lines)
        return detail[:limit] if detail else ""

    def preflight(self) -> list[CheckResult]:
        checks: list[CheckResult] = []
        if shutil.which("wsl.exe") is None and os.name != "nt":
            checks.append(CheckResult("backend", "WSL executable", CheckState.BLOCKED, "wsl.exe is not available"))
            return checks

        listed = self.list_distros()
        checks.append(
            CheckResult(
                "wsl",
                "WSL",
                CheckState.PASS if listed.code == 0 else CheckState.BLOCKED,
                "available" if listed.code == 0 else (self._clean_detail(listed.output) or f"exit {listed.code}"),
            )
        )
        if listed.code != 0:
            return checks

        names = self.list_distro_names()
        installed = any(name.casefold() == self.distro.casefold() for name in names)
        checks.append(
            CheckResult(
                "distro",
                "Configured distro",
                CheckState.PASS if installed else CheckState.BLOCKED,
                self.distro if installed else f"{self.distro} is not installed — use PREPARE WSL BACKEND",
            )
        )
        if not installed:
            return checks

        expected_version = int(self.config.get("expected_wsl_version", 2))
        version_ok = False
        actual_version = "unknown"
        for line in listed.output.replace("\x00", "").splitlines():
            if self.distro.casefold() in line.casefold():
                fields = line.replace("*", " ").split()
                if fields and fields[-1].isdigit():
                    actual_version = fields[-1]
                    version_ok = int(fields[-1]) == expected_version
                break
        checks.append(
            CheckResult(
                "wsl_version",
                "WSL version",
                CheckState.PASS if version_ok else CheckState.BLOCKED,
                f"WSL {actual_version}" if version_ok else f"expected WSL {expected_version}; detected {actual_version}",
            )
        )
        if not version_ok:
            return checks

        ready = self.runtime_probe(timeout=8)
        ready_ok = ready.code == 0 and "KOALI_WSL_READY" in ready.output
        if not ready_ok:
            if ready.code == 124:
                detail = "registered but not command-ready; first-run initialization/startup is pending — use PREPARE WSL BACKEND"
            else:
                detail = self._clean_detail(ready.output) or f"cannot execute Linux commands (exit {ready.code})"
            checks.append(CheckResult("distro_runtime", "Distro runtime", CheckState.BLOCKED, detail))
            # Do not report fake systemd/tool failures when Linux command execution itself is unavailable.
            return checks
        checks.append(CheckResult("distro_runtime", "Distro runtime", CheckState.PASS, "command-ready"))

        user = self.capture_shell("printf 'user='; id -un; printf 'uid='; id -u", timeout=10)
        user_name = "unknown"
        uid = "unknown"
        for line in user.output.splitlines():
            if line.startswith("user="):
                user_name = line.partition("=")[2].strip()
            elif line.startswith("uid="):
                uid = line.partition("=")[2].strip()
        unprivileged = user.code == 0 and uid not in {"0", "unknown", ""}
        checks.append(
            CheckResult(
                "default_user",
                "Linux default user",
                CheckState.PASS if unprivileged else CheckState.BLOCKED,
                f"{user_name} (uid {uid})" if unprivileged else "profile requires an initialized unprivileged default user",
            )
        )
        if not unprivileged:
            return checks

        osr = self.capture_shell("cat /etc/os-release 2>/dev/null", timeout=10)
        expected_id = str(self.config.get("expected_distribution_id", "")).lower()
        expected_release = str(self.config.get("expected_release", ""))
        os_text = osr.output.lower()
        distro_ok = osr.code == 0 and (not expected_id or f"id={expected_id}" in os_text or f'id="{expected_id}"' in os_text)
        release_ok = osr.code == 0 and (not expected_release or expected_release in osr.output)
        identity_ok = distro_ok and release_ok
        checks.append(
            CheckResult(
                "linux_identity",
                "Linux environment",
                CheckState.PASS if identity_ok else CheckState.BLOCKED,
                f"{expected_id or 'linux'} {expected_release}".strip() if identity_ok else f"expected {expected_id or 'Linux'} {expected_release}".strip(),
            )
        )
        if not identity_ok:
            return checks

        if bool(self.config.get("require_systemd", True)):
            sd = self.capture_shell(
                "printf 'pid1='; ps -p 1 -o comm= 2>/dev/null | tr -d ' '; "
                "printf '\\nstate='; systemctl is-system-running 2>/dev/null || true; "
                "printf '\\nfailed='; systemctl --failed --no-legend --plain --no-pager 2>/dev/null | sed -E 's/^[[:space:]●*]+//' | awk '{print $1}' | paste -sd, - || true",
                timeout=10,
            )
            pid1 = ""
            state = ""
            failed_units = ""
            for line in sd.output.splitlines():
                if line.startswith("pid1="):
                    pid1 = line.partition("=")[2].strip()
                elif line.startswith("state="):
                    state = line.partition("=")[2].strip()
                elif line.startswith("failed="):
                    failed_units = line.partition("=")[2].strip()
            if pid1 != "systemd":
                checks.append(CheckResult("systemd", "systemd", CheckState.BLOCKED, f"PID 1 is {pid1 or 'unknown'}; systemd is required"))
                return checks

            required_units = [str(x).strip() for x in self.config.get("required_systemd_units", []) if str(x).strip()]
            if required_units:
                quoted = " ".join(shlex.quote(unit) for unit in required_units)
                unit_probe = self.capture_shell(
                    f"for u in {quoted}; do systemctl is-active --quiet \"$u\" && echo \"$u=active\" || echo \"$u=inactive\"; done",
                    timeout=10,
                )
                inactive = [unit for unit in required_units if f"{unit}=active" not in unit_probe.output]
                if inactive:
                    checks.append(CheckResult("systemd_required_units", "Required systemd units", CheckState.BLOCKED, "inactive: " + ", ".join(inactive)))
                    return checks
                checks.append(CheckResult("systemd_required_units", "Required systemd units", CheckState.PASS, ", ".join(required_units)))

            ignored_units = {str(x).strip() for x in self.config.get("ignored_systemd_units", []) if str(x).strip()}
            failed_set = {item.strip() for item in failed_units.split(",") if item.strip()}
            actionable_failed = sorted(failed_set - ignored_units)
            ignored_failed = sorted(failed_set & ignored_units)
            if state == "running":
                checks.append(CheckResult("systemd", "systemd", CheckState.PASS, "running"))
            elif state == "degraded" and not actionable_failed:
                detail = "operational under WSL"
                if ignored_failed:
                    detail += "; ignored non-applicable console units: " + ", ".join(ignored_failed)
                checks.append(CheckResult("systemd", "systemd", CheckState.PASS, detail))
            elif state in {"degraded", "starting", "maintenance"}:
                detail = state
                if actionable_failed:
                    detail += "; failed units: " + ", ".join(actionable_failed)
                checks.append(CheckResult("systemd", "systemd", CheckState.WARN, detail, required=False))
            else:
                checks.append(CheckResult("systemd", "systemd", CheckState.BLOCKED, state or "systemd state unavailable"))
                return checks

        tools = self._tool_probe(timeout=15)
        for tool in ("git", "python", "uv"):
            ok = self._probe_has_tool(tools.output, tool)
            resolved = ""
            for line in tools.output.splitlines():
                prefix = f"tool:{tool}=ok:"
                if line.startswith(prefix):
                    resolved = line[len(prefix):].strip()
                    break
            if ok:
                detail = resolved or "available"
            elif tool == "python" and self._probe_has_tool(tools.output, "python3"):
                detail = "python3 exists, but the active toolchain contract requires executable 'python'"
            else:
                detail = "missing from the active development-profile toolchain"
            checks.append(CheckResult(f"backend_tool_{tool}", f"Tool: {tool}", CheckState.PASS if ok else CheckState.BLOCKED, detail))
        return checks

    def provision_profile_toolchain(self, *, timeout: int = 1800) -> int:
        """Supply the profile-owned Git/Python/UV toolchain for Ubuntu WSL.

        Repository bootstrap scripts intentionally assume Python and UV are already
        supplied by the active development profile. The Control Panel therefore provisions
        those host/profile tools before invoking repository bootstrap. The default
        Ubuntu implementation uses apt for OS packages and pipx for the user-level
        UV executable; application dependencies remain workspace-local under UV.
        """
        cfg = dict(self.config.get("toolchain_provisioning", {}))
        if not bool(cfg.get("enabled", True)):
            self.runner.log("Profile toolchain provisioning is disabled")
            return 78
        provider = str(cfg.get("provider", "ubuntu_apt_pipx"))
        if provider != "ubuntu_apt_pipx":
            self.runner.log(f"Unsupported WSL toolchain provisioner: {provider}")
            return 78

        # Only install what the backend is currently missing. apt is run as the
        # distro root account in one bounded provisioning step; normal development
        # continues under the configured unprivileged default user.
        status = self._tool_probe(include_pipx=True, timeout=20)
        need_git = not self._probe_has_tool(status.output, "git")
        need_python = not self._probe_has_tool(status.output, "python")
        need_pipx = not self._probe_has_tool(status.output, "pipx")
        need_uv = not self._probe_has_tool(status.output, "uv")

        packages: list[str] = []
        configured_packages = [str(x).strip() for x in cfg.get("apt_packages", ["git", "python3", "python-is-python3", "pipx"]) if str(x).strip()]
        if need_git and "git" in configured_packages:
            packages.append("git")
        if need_python:
            for name in ("python3", "python-is-python3"):
                if name in configured_packages and name not in packages:
                    packages.append(name)
        if (need_pipx or need_uv) and "pipx" in configured_packages:
            packages.append("pipx")

        if packages:
            apt_script = (
                "set -euo pipefail; export DEBIAN_FRONTEND=noninteractive; "
                "apt-get update; apt-get install -y " + " ".join(shlex.quote(x) for x in packages)
            )
            rc = self.run_root_shell(apt_script, "Provision profile OS toolchain", timeout=timeout)
            if rc != 0:
                return rc

        if need_uv:
            uv_package = str(cfg.get("uv_pipx_package", "uv")).strip() or "uv"
            user_script = (
                "set -euo pipefail; mkdir -p \"$HOME/.local/bin\"; "
                "command -v pipx >/dev/null 2>&1; "
                f"test -x \"$HOME/.local/bin/uv\" || pipx install {shlex.quote(uv_package)}; "
                "pipx ensurepath >/dev/null 2>&1 || true; "
                "export PATH=\"$HOME/.local/bin:$PATH\"; uv --version"
            )
            rc = self.run_shell(user_script, "Provision UV for development profile", timeout=timeout)
            if rc != 0:
                return rc

        verify = self._tool_probe(include_versions=True, timeout=45)
        for tool in ("git", "python", "uv"):
            prefix = f"tool:{tool}="
            line = next((item.strip() for item in verify.output.splitlines() if item.startswith(prefix)), "")
            if line:
                self.runner.log("toolchain: " + line)
        for line in verify.output.splitlines():
            if line.startswith("version:"):
                self.runner.log("toolchain: " + line)
        missing = [tool for tool in ("git", "python", "uv") if not self._probe_has_tool(verify.output, tool)]
        if verify.code != 0 or missing:
            detail = "missing/unusable: " + ", ".join(missing) if missing else (self._clean_detail(verify.output) or f"exit {verify.code}")
            self.runner.log("Profile toolchain verification failed: " + detail)
            return verify.code if verify.code != 0 else 71
        return 0

    def _repository_rust_requirement(self, workspace: Workspace) -> tuple[str, str, tuple[str, ...]] | None:
        """Read the repository-owned Rust toolchain contract without consulting Git state."""
        script = r'''
if ! test -f rust-toolchain.toml; then
  echo 'rust_required=no'
  exit 0
fi
python - <<'PYRUST'
from pathlib import Path
import tomllib
path = Path('rust-toolchain.toml')
data = tomllib.loads(path.read_text(encoding='utf-8'))
toolchain = data.get('toolchain', {})
channel = str(toolchain.get('channel', '')).strip()
profile = str(toolchain.get('profile', 'minimal')).strip() or 'minimal'
components = toolchain.get('components', [])
if not channel:
    raise SystemExit('rust-toolchain.toml has no [toolchain].channel')
if not isinstance(components, list) or not all(isinstance(x, str) and x.strip() for x in components):
    raise SystemExit('rust-toolchain.toml has invalid [toolchain].components')
print('rust_required=yes')
print('channel=' + channel)
print('profile=' + profile)
print('components=' + ','.join(x.strip() for x in components))
PYRUST
'''
        result = self.capture_in_workspace(workspace, script, timeout=20)
        if result.code != 0:
            raise RuntimeError(self._clean_detail(result.output) or f"cannot read rust-toolchain.toml (exit {result.code})")
        values: dict[str, str] = {}
        for line in result.output.splitlines():
            if '=' in line:
                key, _, value = line.partition('=')
                values[key.strip()] = value.strip()
        if values.get('rust_required') != 'yes':
            return None
        channel = values.get('channel', '')
        profile = values.get('profile', 'minimal') or 'minimal'
        components = tuple(item for item in values.get('components', '').split(',') if item)
        if not channel:
            raise RuntimeError('rust-toolchain.toml did not yield a toolchain channel')
        return channel, profile, components

    def repository_rust_toolchain_preflight(self, workspace: Workspace) -> list[CheckResult]:
        """Verify the exact Rust toolchain declared by rust-toolchain.toml."""
        try:
            requirement = self._repository_rust_requirement(workspace)
        except RuntimeError as exc:
            return [CheckResult('rust_contract', 'Rust toolchain contract', CheckState.BLOCKED, str(exc))]
        if requirement is None:
            return []
        channel, profile, components = requirement
        quoted_channel = shlex.quote(channel)
        component_words = ' '.join(shlex.quote(item) for item in components)
        channel_pattern = re.escape(channel)
        script = f'''set +e
rustup_bin=$(command -v rustup 2>/dev/null || true)
if test -n "$rustup_bin"; then echo "rustup=ok:$rustup_bin"; else echo 'rustup=missing'; fi
if test -n "$rustup_bin" && "$rustup_bin" toolchain list 2>/dev/null | grep -Eq '^{channel_pattern}(-|[[:space:]])'; then
  echo 'toolchain=ok'
  rv=$("$rustup_bin" run {quoted_channel} rustc --version 2>&1); rc=$?; test $rc -eq 0 && echo "rustc=ok:$rv" || echo "rustc=missing:$rv"
  cv=$("$rustup_bin" run {quoted_channel} cargo --version 2>&1); cc=$?; test $cc -eq 0 && echo "cargo=ok:$cv" || echo "cargo=missing:$cv"
  installed=$("$rustup_bin" component list --toolchain {quoted_channel} --installed 2>/dev/null || true)
  for component in {component_words}; do
    if printf '%s\n' "$installed" | grep -Eq "^${{component}}(-|[[:space:]])"; then echo "component:${{component}}=ok"; else echo "component:${{component}}=missing"; fi
  done
else
  echo 'toolchain=missing'
  echo 'rustc=missing'
  echo 'cargo=missing'
  for component in {component_words}; do echo "component:${{component}}=missing"; done
fi
'''
        probe = self.capture_in_workspace(workspace, script, timeout=45)
        output = probe.output
        checks = [
            CheckResult(
                'rust_contract',
                'Rust toolchain contract',
                CheckState.PASS,
                f"channel {channel}; profile {profile}; components {', '.join(components) or 'none'}",
            )
        ]
        rustup_line = next((line for line in output.splitlines() if line.startswith('rustup=')), 'rustup=missing')
        rustup_ok = rustup_line.startswith('rustup=ok:')
        checks.append(CheckResult('rust_tool_rustup', 'Tool: rustup', CheckState.PASS if rustup_ok else CheckState.BLOCKED, rustup_line.partition(':')[2] if rustup_ok else 'required to realize rust-toolchain.toml'))
        toolchain_ok = 'toolchain=ok' in output
        checks.append(CheckResult('rust_toolchain', f'Rust toolchain {channel}', CheckState.PASS if toolchain_ok else CheckState.BLOCKED, 'installed' if toolchain_ok else 'not installed'))
        rustc_line = next((line for line in output.splitlines() if line.startswith('rustc=')), 'rustc=missing')
        rustc_ok = rustc_line.startswith('rustc=ok:') and f'rustc {channel}' in rustc_line
        checks.append(CheckResult('rust_tool_rustc', 'Tool: rustc', CheckState.PASS if rustc_ok else CheckState.BLOCKED, rustc_line.partition(':')[2] if rustc_ok else f'exact repository toolchain {channel} unavailable'))
        cargo_line = next((line for line in output.splitlines() if line.startswith('cargo=')), 'cargo=missing')
        cargo_ok = cargo_line.startswith('cargo=ok:')
        checks.append(CheckResult('rust_tool_cargo', 'Tool: cargo', CheckState.PASS if cargo_ok else CheckState.BLOCKED, cargo_line.partition(':')[2] if cargo_ok else f'cargo for repository toolchain {channel} unavailable'))
        for component in components:
            ok = f'component:{component}=ok' in output
            checks.append(CheckResult(f'rust_component_{component}', f'Rust component: {component}', CheckState.PASS if ok else CheckState.BLOCKED, 'installed' if ok else f'missing from toolchain {channel}'))
        return checks

    def provision_repository_rust_toolchain(self, workspace: Workspace, *, timeout: int = 1800) -> int:
        """Provision the repository-declared Rust toolchain under the WSL user.

        This does not inspect or alter Git state and does not choose a Rust version:
        channel/profile/components come exclusively from rust-toolchain.toml.
        """
        try:
            requirement = self._repository_rust_requirement(workspace)
        except RuntimeError as exc:
            self.runner.log('Rust toolchain contract could not be read: ' + str(exc))
            return 78
        if requirement is None:
            return 0
        channel, profile, components = requirement
        cfg = dict(self.config.get('toolchain_provisioning', {}))
        if not bool(cfg.get('enabled', True)):
            self.runner.log('Repository Rust toolchain provisioning is disabled')
            return 78
        rustup = self.capture_shell('command -v rustup 2>/dev/null || true', timeout=15)
        if not rustup.output.strip():
            package = str(cfg.get('rustup_apt_package', 'rustup')).strip() or 'rustup'
            apt_script = 'set -euo pipefail; export DEBIAN_FRONTEND=noninteractive; apt-get update; apt-get install -y ' + shlex.quote(package)
            rc = self.run_root_shell(apt_script, 'Provision Rust toolchain manager', timeout=timeout)
            if rc != 0:
                return rc
        args = ['rustup', 'toolchain', 'install', channel, '--profile', profile]
        for component in components:
            args.extend(['--component', component])
        command = shell_join(args)
        root = self.resolve_path(workspace.root)
        script = (
            'set -euo pipefail; export PATH="$HOME/.cargo/bin:$HOME/.local/bin:$PATH"; '
            + command + '; cd ' + shlex.quote(root) + '; '
            + 'rustc --version; cargo --version'
        )
        rc = self.run_shell(script, f'Provision Rust {channel} from repository contract', timeout=timeout)
        if rc != 0:
            return rc
        checks = self.repository_rust_toolchain_preflight(workspace)
        blocked = [item for item in checks if item.required and item.state in {CheckState.BLOCKED, CheckState.FAIL}]
        for item in checks:
            self.runner.log(f'rust-toolchain: {item.state.value} {item.label}: {item.detail}')
        return 0 if not blocked else 71

    def repository_native_build_toolchain_preflight(self, workspace: Workspace) -> list[CheckResult]:
        """Verify the native C compiler/linker required by repository Rust builds."""
        try:
            requirement = self._repository_rust_requirement(workspace)
        except RuntimeError as exc:
            return [CheckResult("native_build", "Native build toolchain", CheckState.BLOCKED, str(exc))]
        if requirement is None:
            return []

        script = r'''set +e
cc_bin=$(command -v cc 2>/dev/null || true)
if test -z "$cc_bin"; then
  echo 'cc=missing'
  echo 'link=missing:cc executable unavailable'
  exit 0
fi
cc_version=$("$cc_bin" --version 2>/dev/null | head -n 1)
echo "cc=ok:$cc_bin${cc_version:+ ($cc_version)}"
tmp=$(mktemp -d /tmp/.koali-native-link-XXXXXX 2>/dev/null || true)
if test -z "$tmp"; then
  echo 'link=missing:could not create temporary probe directory'
  exit 0
fi
trap 'rm -rf "$tmp"' EXIT
printf '%s\n' 'int main(void) { return 0; }' > "$tmp/probe.c"
if "$cc_bin" "$tmp/probe.c" -o "$tmp/probe" >"$tmp/link.log" 2>&1 && "$tmp/probe"; then
  echo 'link=ok:temporary C compile/link probe succeeded'
else
  detail=$(tr '\n' ' ' < "$tmp/link.log" | head -c 800)
  echo "link=missing:${detail:-native compile/link probe failed}"
fi
'''
        probe = self.capture_in_workspace(workspace, script, timeout=60)
        output = probe.output
        cc_line = next((line for line in output.splitlines() if line.startswith("cc=")), "cc=missing")
        cc_ok = cc_line.startswith("cc=ok:")
        link_line = next((line for line in output.splitlines() if line.startswith("link=")), "link=missing:native compile/link probe did not run")
        link_ok = link_line.startswith("link=ok:")
        return [
            CheckResult(
                "native_tool_cc",
                "Tool: cc",
                CheckState.PASS if cc_ok else CheckState.BLOCKED,
                cc_line.partition(":")[2] if cc_ok else "required by the Rust linker/build scripts",
            ),
            CheckResult(
                "native_link",
                "Native compiler/linker",
                CheckState.PASS if link_ok else CheckState.BLOCKED,
                link_line.partition(":")[2] if ":" in link_line else "native compile/link probe failed",
            ),
        ]

    def provision_repository_native_build_toolchain(self, workspace: Workspace, *, timeout: int = 1800) -> int:
        """Provision the Ubuntu native compiler/linker needed by repository Rust builds.

        This is a host-development prerequisite only. It does not inspect or alter
        repository Git state, Cargo.lock, generated artifacts, or release evidence.
        """
        try:
            requirement = self._repository_rust_requirement(workspace)
        except RuntimeError as exc:
            self.runner.log("Native build requirement could not be read: " + str(exc))
            return 78
        if requirement is None:
            return 0
        cfg = dict(self.config.get("toolchain_provisioning", {}))
        if not bool(cfg.get("enabled", True)):
            self.runner.log("Native build toolchain provisioning is disabled")
            return 78
        provider = str(cfg.get("provider", "ubuntu_apt_pipx"))
        if provider != "ubuntu_apt_pipx":
            self.runner.log(f"Unsupported WSL native build provisioner: {provider}")
            return 78
        packages = [
            str(item).strip()
            for item in cfg.get("native_build_apt_packages", ["build-essential"])
            if str(item).strip()
        ]
        if not packages:
            self.runner.log("No native build packages are configured")
            return 78
        apt_script = (
            "set -euo pipefail; export DEBIAN_FRONTEND=noninteractive; "
            "apt-get update; apt-get install -y " + " ".join(shlex.quote(item) for item in packages)
        )
        rc = self.run_root_shell(apt_script, "Provision native build toolchain", timeout=timeout)
        if rc != 0:
            return rc
        checks = self.repository_native_build_toolchain_preflight(workspace)
        blocked = [item for item in checks if item.required and item.state in {CheckState.BLOCKED, CheckState.FAIL}]
        for item in checks:
            self.runner.log(f"native-toolchain: {item.state.value} {item.label}: {item.detail}")
        return 0 if not blocked else 71

    def repository_cargo_cache_preflight(self, workspace: Workspace) -> list[CheckResult]:
        """Verify that Cargo can satisfy the locked Rust dependency graph offline."""
        try:
            requirement = self._repository_rust_requirement(workspace)
        except RuntimeError as exc:
            return [CheckResult("cargo_cache", "Cargo offline cache", CheckState.BLOCKED, str(exc))]
        if requirement is None:
            return []
        channel, _profile, _components = requirement
        command = shell_join([
            "rustup", "run", channel, "cargo", "fetch",
            "--locked", "--offline", "--manifest-path", "Cargo.toml",
        ])
        probe = self.capture_in_workspace(workspace, command, timeout=180)
        if probe.code == 0:
            return [CheckResult("cargo_cache", "Cargo offline cache", CheckState.PASS, "Cargo.lock dependencies available offline")]
        detail = self._clean_detail(probe.output) or f"cargo fetch --locked --offline exited {probe.code}"
        return [CheckResult("cargo_cache", "Cargo offline cache", CheckState.BLOCKED, detail)]

    def prime_repository_cargo_cache(self, workspace: Workspace, *, timeout: int = 1800) -> int:
        """Populate the user Cargo cache from Cargo.lock, then prove offline availability.

        Network access is used only for this PREPARE step. The repository source,
        Git state, and Cargo.lock are not modified; the actual component builder
        remains --locked --offline.
        """
        try:
            requirement = self._repository_rust_requirement(workspace)
        except RuntimeError as exc:
            self.runner.log("Cargo cache contract could not be read: " + str(exc))
            return 78
        if requirement is None:
            return 0
        channel, _profile, _components = requirement
        cfg = dict(self.config.get("toolchain_provisioning", {}))
        if not bool(cfg.get("prime_cargo_cache", True)):
            self.runner.log("Cargo dependency cache provisioning is disabled")
            return 78
        online = shell_join([
            "rustup", "run", channel, "cargo", "fetch",
            "--locked", "--manifest-path", "Cargo.toml",
        ])
        rc = self.execute_in_workspace(workspace, online, "Prime Cargo cache from Cargo.lock", timeout=timeout)
        if rc != 0:
            return rc
        offline = shell_join([
            "rustup", "run", channel, "cargo", "fetch",
            "--locked", "--offline", "--manifest-path", "Cargo.toml",
        ])
        return self.execute_in_workspace(workspace, offline, "Verify Cargo cache offline", timeout=min(timeout, 300))

    def run_cargo_build_diagnostic(
        self,
        workspace: Workspace,
        component: str,
        source_date_epoch: str,
        *,
        timeout: int = 1800,
    ) -> int:
        """Replay the Rust cargo invocation in /tmp so full Cargo stderr reaches the UI."""
        try:
            requirement = self._repository_rust_requirement(workspace)
        except RuntimeError as exc:
            self.runner.log("Cargo diagnostic unavailable: " + str(exc))
            return 78
        if requirement is None:
            self.runner.log("Cargo diagnostic unavailable: repository has no rust-toolchain.toml")
            return 78
        channel, _profile, _components = requirement
        quoted_epoch = shlex.quote(str(source_date_epoch))
        quoted_component = shlex.quote(component)
        quoted_channel = shlex.quote(channel)
        script = f"""set -u
tmp=$(mktemp -d /tmp/.koali-cargo-diagnostic-XXXXXX) || exit 1
trap 'rm -rf "$tmp"' EXIT
export LANG=C LC_ALL=C TZ=UTC CARGO_NET_OFFLINE=true CARGO_INCREMENTAL=0 SOURCE_DATE_EPOCH={quoted_epoch}
rustup run {quoted_channel} cargo build --locked --offline --release --package {quoted_component} --bins --target-dir "$tmp/target"
"""
        return self.execute_in_workspace(
            workspace,
            script,
            f"Cargo build diagnostic (offline): {component}",
            timeout=timeout,
        )

    def open_shell(self, workspace: Workspace) -> None:
        root = self.resolve_path(workspace.root)
        terminal = str(self.app_config.get("terminal_exe", "wt.exe")).strip() or "wt.exe"
        argv = [terminal, "wsl.exe", "-d", self.distro, "--cd", root]
        try:
            subprocess.Popen(argv)
        except OSError:
            subprocess.Popen(["wsl.exe", "-d", self.distro, "--cd", root])

    def open_editor(self, workspace: Workspace) -> None:
        root = self.resolve_path(workspace.root)
        editor = str(self.app_config.get("editor_exe", "code")).strip() or "code"
        uri = f"vscode-remote://wsl+{quote(self.distro, safe='')}{quote(root, safe='/')}"
        try:
            subprocess.Popen([editor, "--folder-uri", uri])
        except OSError:
            self.run_shell(f"cd {shlex.quote(root)} && code . >/dev/null 2>&1 &", "Open editor", timeout=15)

    def open_explorer(self, workspace: Workspace) -> None:
        root = self.resolve_path(workspace.root)
        self.run_shell(f"cd {shlex.quote(root)} && explorer.exe . >/dev/null 2>&1 &", "Open Explorer", timeout=15)


class NativeLinuxBackend(ExecutionBackend):
    backend_id = "native_linux"

    @property
    def shell(self) -> str:
        return str(self.config.get("shell", "bash")).strip() or "bash"

    def capture_shell(self, script: str, *, timeout: int = 30) -> CaptureResult:
        return self.runner.capture([self.shell, "-lc", script], timeout=timeout)

    def run_shell(self, script: str, label: str, *, timeout: int | None = None) -> int:
        return self.runner.run([self.shell, "-lc", script], label, timeout=timeout)

    def start(self) -> int:
        return 0

    def resolve_path(self, template: str) -> str:
        return template.replace("{home}", str(Path.home()))

    def preflight(self) -> list[CheckResult]:
        is_linux = platform.system().lower() == "linux"
        return [CheckResult("backend", "Native Linux", CheckState.PASS if is_linux else CheckState.BLOCKED, platform.platform())]

    def open_shell(self, workspace: Workspace) -> None:
        root = self.resolve_path(workspace.root)
        terminal = str(self.app_config.get("terminal_exe", "")).strip()
        if terminal:
            try:
                subprocess.Popen([terminal], cwd=root)
                return
            except OSError:
                pass
        subprocess.Popen([self.shell], cwd=root)

    def open_editor(self, workspace: Workspace) -> None:
        editor = str(self.app_config.get("editor_exe", "code")).strip() or "code"
        subprocess.Popen([editor, self.resolve_path(workspace.root)])


class WindowsBackend(ExecutionBackend):
    """Native Windows backend for independently runnable UI products.

    kOA-Linux development/qualification remains on WSL. This backend exists so
    browser-facing products can use the developer's native Node/pnpm toolchain
    without forcing their frontend process through the Linux workspace lifecycle.
    """

    backend_id = "windows"

    @property
    def shell(self) -> str:
        return str(self.config.get("shell", "powershell.exe")).strip() or "powershell.exe"

    def powershell_argv(self, script: str) -> list[str]:
        return [self.shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script]

    # Backward-compatible private alias for older callers.
    def _powershell(self, script: str) -> list[str]:
        return self.powershell_argv(script)

    def capture_shell(self, script: str, *, timeout: int = 30) -> CaptureResult:
        return self.runner.capture(self.powershell_argv(script), timeout=timeout)

    def run_shell(self, script: str, label: str, *, timeout: int | None = None) -> int:
        return self.runner.run(self.powershell_argv(script), label, timeout=timeout)

    def start(self) -> int:
        return 0 if os.name == "nt" else 78

    def preflight(self) -> list[CheckResult]:
        if os.name != "nt":
            return [CheckResult("backend", "Native Windows", CheckState.BLOCKED, "Windows host required")]
        shell = shutil.which(self.shell) or shutil.which("powershell.exe")
        return [CheckResult("backend", "Native Windows", CheckState.PASS if shell else CheckState.BLOCKED, shell or "PowerShell unavailable")]

    def resolve_path(self, template: str) -> str:
        return template.replace("{home}", str(Path.home()))

    def open_shell(self, workspace: Workspace) -> None:
        root = self.resolve_path(workspace.root)
        terminal = str(self.app_config.get("terminal_exe", "wt.exe")).strip() or "wt.exe"
        try:
            subprocess.Popen([terminal, "-d", root])
        except OSError:
            subprocess.Popen([self.shell, "-NoExit"], cwd=root)

    def open_editor(self, workspace: Workspace) -> None:
        root = self.resolve_path(workspace.root)
        editor = str(self.app_config.get("editor_exe", "code")).strip() or "code"
        try:
            subprocess.Popen([editor, root])
        except OSError as exc:
            self.runner.log(f"Cannot open editor: {exc}")
