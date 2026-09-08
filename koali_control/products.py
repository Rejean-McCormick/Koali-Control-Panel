from __future__ import annotations

import json
import os
import re
import shlex
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from dataclasses import dataclass
from typing import Callable

from .backends import ExecutionBackend, WindowsBackend, WslBackend
from .supervisor import ProcessSupervisor


_WINDOWS_ABSOLUTE = re.compile(r"^[A-Za-z]:[\\/]")
_SCRIPT_CANDIDATES: dict[str, tuple[str, ...]] = {
    "validate": ("validate", "check", "verify"),
    "test": ("test", "test:ci"),
    "build": ("build",),
    "smoke": ("smoke:runtime", "smoke", "test:runtime"),
    "start": ("dev", "start"),
}


@dataclass(frozen=True, slots=True)
class ProductSpec:
    product_id: str
    label: str
    enabled: bool
    optional: bool
    backend: str
    roots: tuple[str, ...]
    marker: str
    commands: dict[str, str]
    environment: dict[str, str]
    open_url: str
    health_url: str


@dataclass(frozen=True, slots=True)
class ProductSnapshot:
    product_id: str
    label: str
    installed: bool
    root: str
    runtime_state: str
    health_state: str
    detail: str


class ProductRegistry:
    """Declarative product registry used by both standalone and integrated dev flows."""

    def __init__(
        self,
        config: dict,
        backend_factory: Callable[[str], ExecutionBackend],
        supervisor: ProcessSupervisor,
        log: Callable[[str], None],
    ) -> None:
        self.config = config
        self.backend_factory = backend_factory
        self.supervisor = supervisor
        self.log = log
        self._resolved_roots: dict[str, str] = {}
        self._package_cache: dict[str, tuple[str, dict[str, str]]] = {}

    def reload(self, config: dict) -> None:
        self.config = config
        self.invalidate_cache()

    def invalidate_cache(self) -> None:
        self._resolved_roots.clear()
        self._package_cache.clear()

    def specs(self) -> list[ProductSpec]:
        raw = self.config.get("products", {})
        if not isinstance(raw, dict):
            return []
        result: list[ProductSpec] = []
        for product_id, value in raw.items():
            if not isinstance(value, dict):
                continue
            roots = value.get("roots", [])
            if isinstance(roots, str):
                roots = [roots]
            commands = value.get("commands", {}) if isinstance(value.get("commands", {}), dict) else {}
            environment = value.get("environment", {}) if isinstance(value.get("environment", {}), dict) else {}
            result.append(ProductSpec(
                product_id=str(product_id),
                label=str(value.get("label", product_id)),
                enabled=bool(value.get("enabled", True)),
                optional=bool(value.get("optional", True)),
                backend=str(value.get("backend", self.config.get("environment", {}).get("default_backend", "wsl"))),
                roots=tuple(str(item) for item in roots if str(item).strip()),
                marker=str(value.get("marker", "package.json")),
                commands={str(k): str(v) for k, v in commands.items() if str(v).strip()},
                environment={str(k): str(v) for k, v in environment.items()},
                open_url=str(value.get("open_url", "")).strip(),
                health_url=str(value.get("health_url", "")).strip(),
            ))
        return result

    def get(self, product_id: str) -> ProductSpec:
        for spec in self.specs():
            if spec.product_id == product_id:
                return spec
        raise KeyError(product_id)

    def enabled_ids(self) -> list[str]:
        return [spec.product_id for spec in self.specs() if spec.enabled]

    def _backend_path(self, spec: ProductSpec, raw: str) -> str:
        backend = self.backend_factory(spec.backend)
        if isinstance(backend, WslBackend) and _WINDOWS_ABSOLUTE.match(raw):
            return backend.windows_to_linux_path(raw)
        return backend.resolve_path(raw)

    def resolve_root(self, spec: ProductSpec, *, refresh: bool = False) -> str:
        if not refresh and spec.product_id in self._resolved_roots:
            return self._resolved_roots[spec.product_id]
        backend = self.backend_factory(spec.backend)
        for candidate in spec.roots:
            root = self._backend_path(spec, candidate)
            if not root:
                continue
            if isinstance(backend, WindowsBackend):
                marker = Path(root) / spec.marker if spec.marker else Path(root)
                exists = Path(root).is_dir() and marker.is_file()
            else:
                marker = f"{root.rstrip('/')}/{spec.marker}" if spec.marker else root
                result = backend.capture_shell(
                    f"test -d {shlex.quote(root)} && test -f {shlex.quote(marker)}",
                    timeout=15,
                )
                exists = result.code == 0
            if exists:
                self._resolved_roots[spec.product_id] = root
                return root
        self._resolved_roots.pop(spec.product_id, None)
        return ""

    def _package_scripts(self, spec: ProductSpec, root: str) -> tuple[str, dict[str, str]]:
        cached = self._package_cache.get(spec.product_id)
        if cached:
            return cached
        backend = self.backend_factory(spec.backend)
        if isinstance(backend, WindowsBackend):
            try:
                package_root = Path(root)
                value = json.loads((package_root / "package.json").read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return "", {}
            pm = str(value.get("packageManager", "")).partition("@")[0]
            if not pm:
                if (package_root / "pnpm-lock.yaml").exists() or (package_root / "pnpm-workspace.yaml").exists():
                    pm = "pnpm"
                elif (package_root / "yarn.lock").exists():
                    pm = "yarn"
                else:
                    pm = "npm"
            scripts = value.get("scripts", {})
            parsed = (pm, {str(k): str(v) for k, v in scripts.items()}) if isinstance(scripts, dict) else (pm, {})
            self._package_cache[spec.product_id] = parsed
            return parsed
        script = f"""cd {shlex.quote(root)} && python3 - <<'PY'\nimport json, pathlib\nroot = pathlib.Path.cwd()\np = root / 'package.json'\nvalue = json.loads(p.read_text(encoding='utf-8'))\npm = str(value.get('packageManager', '')).partition('@')[0]\nif not pm:\n    if (root / 'pnpm-lock.yaml').exists() or (root / 'pnpm-workspace.yaml').exists(): pm = 'pnpm'\n    elif (root / 'yarn.lock').exists(): pm = 'yarn'\n    else: pm = 'npm'\nprint(json.dumps({{'package_manager': pm, 'scripts': value.get('scripts', {{}})}}, separators=(',', ':')))\nPY"""
        result = backend.capture_shell(script, timeout=20)
        if result.code != 0:
            return "", {}
        for line in reversed([line.strip() for line in result.output.splitlines() if line.strip()]):
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            pm = str(value.get("package_manager", ""))
            scripts = value.get("scripts", {})
            if isinstance(scripts, dict):
                parsed = (pm, {str(k): str(v) for k, v in scripts.items()})
                self._package_cache[spec.product_id] = parsed
                return parsed
        return "", {}

    def command_for(self, spec: ProductSpec, action: str, root: str | None = None) -> str:
        explicit = spec.commands.get(action, "").strip()
        if explicit:
            return explicit
        root = root or self.resolve_root(spec)
        if not root:
            return ""
        package_manager, scripts = self._package_scripts(spec, root)
        if not package_manager:
            return ""
        for script_name in _SCRIPT_CANDIDATES.get(action, (action,)):
            if script_name in scripts:
                return f"{shlex.quote(package_manager)} run {shlex.quote(script_name)}"
        return ""

    def run_action(self, product_id: str, action: str, *, timeout: int = 1800) -> int:
        spec = self.get(product_id)
        root = self.resolve_root(spec, refresh=True)
        if not root:
            self.log(f"{spec.label}: NOT INSTALLED (none of the configured roots is usable)")
            return 78
        command = self.command_for(spec, action, root)
        if not command:
            self.log(f"{spec.label}: no '{action}' command declared or discoverable")
            return 78
        backend = self.backend_factory(spec.backend)
        if isinstance(backend, WindowsBackend):
            env = " ".join(f"$env:{key}={json.dumps(value)};" for key, value in spec.environment.items())
            script = f"{env} Set-Location -LiteralPath {json.dumps(root)}; {command}"
        else:
            env = " ".join(f"export {key}={shlex.quote(value)};" for key, value in spec.environment.items())
            script = f"{env} cd {shlex.quote(root)} && {command}"
        return backend.run_shell(script, f"{spec.label}: {action}", timeout=timeout)

    def start(self, product_id: str) -> bool:
        spec = self.get(product_id)
        root = self.resolve_root(spec, refresh=True)
        if not root:
            self.log(f"{spec.label}: NOT INSTALLED")
            return False
        health, detail = self.health(spec)
        if health == "READY" and self.supervisor.snapshot(spec.product_id).state != "RUNNING":
            self.log(f"{spec.label}: already reachable outside the supervisor ({detail}); reusing existing runtime")
            return True
        command = self.command_for(spec, "start", root)
        if not command:
            self.log(f"{spec.label}: no start/dev command declared or discoverable")
            return False
        backend = self.backend_factory(spec.backend)
        return self.supervisor.start_shell(spec.product_id, backend, root, command, environment=spec.environment)

    def stop(self, product_id: str) -> bool:
        return self.supervisor.stop(product_id)

    def health(self, spec: ProductSpec) -> tuple[str, str]:
        target = spec.health_url or spec.open_url
        if not target:
            return "UNKNOWN", "no health URL configured"
        try:
            request = urllib.request.Request(target, method="GET", headers={"User-Agent": "Koali-Control-Panel"})
            with urllib.request.urlopen(request, timeout=2.0) as response:
                code = int(getattr(response, "status", 200))
            return ("READY", f"HTTP {code}") if 200 <= code < 500 else ("FAILED", f"HTTP {code}")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return "UNAVAILABLE", str(exc)[:160]

    def snapshot(self, product_id: str) -> ProductSnapshot:
        spec = self.get(product_id)
        root = self.resolve_root(spec)
        installed = bool(root)
        runtime = self.supervisor.snapshot(product_id).state if installed else "NOT INSTALLED"
        health_state, health_detail = self.health(spec) if installed else ("UNKNOWN", "not installed")
        if runtime == "STOPPED" and health_state == "READY":
            runtime = "EXTERNAL"
        detail = root if root else "configure product roots in koali-control.json"
        if installed and health_state != "UNKNOWN":
            detail = f"{detail} · {health_detail}"
        return ProductSnapshot(spec.product_id, spec.label, installed, root, runtime, health_state, detail)

    def open(self, product_id: str) -> bool:
        spec = self.get(product_id)
        if not spec.open_url:
            self.log(f"{spec.label}: no open_url configured")
            return False
        return bool(webbrowser.open(spec.open_url))
