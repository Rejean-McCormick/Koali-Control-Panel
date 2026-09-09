from __future__ import annotations

import json
import os
import posixpath
import re
import shlex
import urllib.error
import urllib.request
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .backends import ExecutionBackend, WindowsBackend, WslBackend
from .supervisor import ProcessSupervisor


_WINDOWS_ABSOLUTE = re.compile(r"^[A-Za-z]:[\\/]")
_SCRIPT_CANDIDATES: dict[str, tuple[str, ...]] = {
    "validate": ("validate", "check", "verify", "typecheck"),
    "test": ("test", "test:ci"),
    "build": ("build",),
    "smoke": ("smoke:runtime", "smoke", "test:runtime", "smoke:gate"),
    "start": ("dev", "start"),
}


@dataclass(frozen=True, slots=True)
class ProductServiceSpec:
    service_id: str
    label: str
    backend: str
    root: str
    marker: str
    command: str
    environment: dict[str, str]
    health_url: str


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
    services: tuple[ProductServiceSpec, ...] = ()


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
    """Declarative product registry used by standalone and integrated dev flows.

    A product can expose a single persistent runtime (legacy/simple case) or a set
    of named services. Composite products keep a single UI/product identity while
    the supervisor manages each service independently. This is used by Konnaxion
    (API + Web) and the optional Capsule Manager (Agent + Manager GUI).
    """

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

    @staticmethod
    def _mapping_strings(value: object) -> dict[str, str]:
        if not isinstance(value, dict):
            return {}
        return {str(k): str(v) for k, v in value.items() if str(v).strip()}

    def specs(self) -> list[ProductSpec]:
        raw = self.config.get("products", {})
        if not isinstance(raw, dict):
            return []
        result: list[ProductSpec] = []
        default_backend = self.config.get("environment", {}).get("default_backend", "wsl")
        for product_id, value in raw.items():
            if not isinstance(value, dict):
                continue
            roots = value.get("roots", [])
            if isinstance(roots, str):
                roots = [roots]
            backend = str(value.get("backend", default_backend))
            services: list[ProductServiceSpec] = []
            raw_services = value.get("services", {})
            if isinstance(raw_services, dict):
                for service_id, service in raw_services.items():
                    if not isinstance(service, dict):
                        continue
                    services.append(ProductServiceSpec(
                        service_id=str(service_id),
                        label=str(service.get("label", service_id)),
                        backend=str(service.get("backend", backend)),
                        root=str(service.get("root", ".")).strip() or ".",
                        marker=str(service.get("marker", "")).strip(),
                        command=str(service.get("command", "")).strip(),
                        environment=self._mapping_strings(service.get("environment", {})),
                        health_url=str(service.get("health_url", "")).strip(),
                    ))
            result.append(ProductSpec(
                product_id=str(product_id),
                label=str(value.get("label", product_id)),
                enabled=bool(value.get("enabled", True)),
                optional=bool(value.get("optional", True)),
                backend=backend,
                roots=tuple(str(item) for item in roots if str(item).strip()),
                marker=str(value.get("marker", "package.json")),
                commands=self._mapping_strings(value.get("commands", {})),
                environment=self._mapping_strings(value.get("environment", {})),
                open_url=str(value.get("open_url", "")).strip(),
                health_url=str(value.get("health_url", "")).strip(),
                services=tuple(services),
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
                exists = Path(root).is_dir() and (marker.is_file() if spec.marker else True)
            else:
                marker = f"{root.rstrip('/')}/{spec.marker}" if spec.marker else root
                test = f"test -d {shlex.quote(root)}"
                if spec.marker:
                    test += f" && test -f {shlex.quote(marker)}"
                result = backend.capture_shell(test, timeout=15)
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
        script = f"""cd {shlex.quote(root)} && python3 - <<'PYI'\nimport json, pathlib\nroot = pathlib.Path.cwd()\np = root / 'package.json'\nvalue = json.loads(p.read_text(encoding='utf-8'))\npm = str(value.get('packageManager', '')).partition('@')[0]\nif not pm:\n    if (root / 'pnpm-lock.yaml').exists() or (root / 'pnpm-workspace.yaml').exists(): pm = 'pnpm'\n    elif (root / 'yarn.lock').exists(): pm = 'yarn'\n    else: pm = 'npm'\nprint(json.dumps({{'package_manager': pm, 'scripts': value.get('scripts', {{}})}}, separators=(',', ':')))\nPYI"""
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

    @staticmethod
    def _process_id(spec: ProductSpec, service: ProductServiceSpec | None = None) -> str:
        return spec.product_id if service is None else f"{spec.product_id}:{service.service_id}"

    def _service_root(self, spec: ProductSpec, service: ProductServiceSpec, product_root: str) -> str:
        backend = self.backend_factory(service.backend)
        raw = service.root
        if raw in {"", "."}:
            return product_root
        if isinstance(backend, WindowsBackend):
            if _WINDOWS_ABSOLUTE.match(raw):
                return backend.resolve_path(raw)
            return str((Path(product_root) / raw).resolve())
        if raw.startswith("/"):
            return backend.resolve_path(raw)
        return posixpath.normpath(posixpath.join(product_root, raw.replace("\\", "/")))

    def _service_installed(self, service: ProductServiceSpec, root: str) -> bool:
        backend = self.backend_factory(service.backend)
        if isinstance(backend, WindowsBackend):
            base = Path(root)
            if not base.is_dir():
                return False
            return (base / service.marker).is_file() if service.marker else True
        test = f"test -d {shlex.quote(root)}"
        if service.marker:
            marker = posixpath.join(root, service.marker)
            test += f" && test -f {shlex.quote(marker)}"
        return backend.capture_shell(test, timeout=15).code == 0

    @staticmethod
    def _merged_environment(spec: ProductSpec, service: ProductServiceSpec) -> dict[str, str]:
        result = dict(spec.environment)
        result.update(service.environment)
        return result

    def _probe_url(self, target: str) -> tuple[str, str]:
        if not target:
            return "UNKNOWN", "no health URL configured"
        try:
            request = urllib.request.Request(target, method="GET", headers={"User-Agent": "Koali-Control-Panel"})
            with urllib.request.urlopen(request, timeout=2.0) as response:
                code = int(getattr(response, "status", 200))
            return ("READY", f"HTTP {code}") if 200 <= code < 500 else ("FAILED", f"HTTP {code}")
        except urllib.error.HTTPError as exc:
            # A reachable local service returning a client-side 4xx still proves
            # that the process is accepting HTTP. Readiness URLs should normally
            # return 2xx, but this keeps generic service probes useful.
            return ("READY", f"HTTP {exc.code}") if 400 <= int(exc.code) < 500 else ("FAILED", f"HTTP {exc.code}")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return "UNAVAILABLE", str(exc)[:160]

    def _start_service(self, spec: ProductSpec, service: ProductServiceSpec, product_root: str) -> bool:
        root = self._service_root(spec, service, product_root)
        if not self._service_installed(service, root):
            self.log(f"{spec.label}/{service.label}: service root is not usable: {root}")
            return False
        pid = self._process_id(spec, service)
        health, detail = self._probe_url(service.health_url)
        if health == "READY" and self.supervisor.snapshot(pid).state != "RUNNING":
            self.log(f"{spec.label}/{service.label}: already reachable outside the supervisor ({detail}); reusing existing runtime")
            return True
        if not service.command:
            self.log(f"{spec.label}/{service.label}: no service start command declared")
            return False
        backend = self.backend_factory(service.backend)
        return self.supervisor.start_shell(pid, backend, root, service.command, environment=self._merged_environment(spec, service))

    def start(self, product_id: str) -> bool:
        spec = self.get(product_id)
        root = self.resolve_root(spec, refresh=True)
        if not root:
            self.log(f"{spec.label}: NOT INSTALLED")
            return False
        if spec.services:
            started: list[str] = []
            for service in spec.services:
                if not self._start_service(spec, service, root):
                    for process_id in reversed(started):
                        self.supervisor.stop(process_id)
                    return False
                process_id = self._process_id(spec, service)
                if self.supervisor.snapshot(process_id).state == "RUNNING":
                    started.append(process_id)
            return True
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
        spec = self.get(product_id)
        if spec.services:
            ok = True
            for service in reversed(spec.services):
                ok = self.supervisor.stop(self._process_id(spec, service)) and ok
            return ok
        return self.supervisor.stop(product_id)

    def health(self, spec: ProductSpec) -> tuple[str, str]:
        if spec.services:
            observations: list[str] = []
            states: list[str] = []
            for service in spec.services:
                state, detail = self._probe_url(service.health_url)
                states.append(state)
                observations.append(f"{service.label}={state} ({detail})")
            if states and all(state == "READY" for state in states):
                return "READY", "; ".join(observations)
            if any(state == "FAILED" for state in states):
                return "FAILED", "; ".join(observations)
            if any(state == "UNAVAILABLE" for state in states):
                return "UNAVAILABLE", "; ".join(observations)
            return "UNKNOWN", "; ".join(observations) or "no service health URLs configured"
        return self._probe_url(spec.health_url or spec.open_url)

    def runtime_state(self, spec: ProductSpec) -> str:
        if not spec.services:
            state = self.supervisor.snapshot(spec.product_id).state
            if state == "STOPPED" and self.health(spec)[0] == "READY":
                return "EXTERNAL"
            return state
        states = [self.supervisor.snapshot(self._process_id(spec, service)).state for service in spec.services]
        if any(state == "FAILED" for state in states):
            return "FAILED"
        if any(state == "RUNNING" for state in states):
            return "RUNNING"
        if self.health(spec)[0] == "READY":
            return "EXTERNAL"
        return "STOPPED"

    def snapshot(self, product_id: str) -> ProductSnapshot:
        spec = self.get(product_id)
        root = self.resolve_root(spec)
        installed = bool(root)
        runtime = self.runtime_state(spec) if installed else "NOT INSTALLED"
        health_state, health_detail = self.health(spec) if installed else ("UNKNOWN", "not installed")
        detail = root if root else "configure product roots in koali-control.json"
        if installed and spec.services:
            managed = []
            for service in spec.services:
                state = self.supervisor.snapshot(self._process_id(spec, service)).state
                managed.append(f"{service.label}:{state}")
            detail = f"{detail} · {' / '.join(managed)}"
        if installed and health_state != "UNKNOWN":
            detail = f"{detail} · {health_detail}"
        return ProductSnapshot(spec.product_id, spec.label, installed, root, runtime, health_state, detail)

    def open(self, product_id: str) -> bool:
        spec = self.get(product_id)
        if not spec.open_url:
            self.log(f"{spec.label}: no open_url configured")
            return False
        return bool(webbrowser.open(spec.open_url))
