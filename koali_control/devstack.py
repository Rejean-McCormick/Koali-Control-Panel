from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from .backends import ExecutionBackend
from .models import Workspace
from .orchestration import Orchestrator
from .products import ProductRegistry
from .supervisor import ProcessSupervisor
from .workspaces import WorkspaceManager


@dataclass(frozen=True, slots=True)
class DevStackResult:
    ready: bool
    failed_step: str | None = None


class DevStackOrchestrator:
    """Orchestrate the repeatable path from a stable core to browsable Koali."""

    def __init__(
        self,
        config: dict,
        workspace_manager: WorkspaceManager,
        backend_factory: Callable[[str], ExecutionBackend],
        core: Orchestrator,
        products: ProductRegistry,
        supervisor: ProcessSupervisor,
        log: Callable[[str], None],
    ) -> None:
        self.config = config
        self.workspaces = workspace_manager
        self.backend_factory = backend_factory
        self.core = core
        self.products = products
        self.supervisor = supervisor
        self.log = log

    def reload(self, config: dict) -> None:
        self.config = config
        self.products.reload(config)

    def cfg(self) -> dict:
        value = self.config.get("dev_stack", {})
        return value if isinstance(value, dict) else {}

    def timeout(self) -> int:
        try:
            return max(30, int(self.cfg().get("command_timeout_seconds", self.core.timeout())))
        except (TypeError, ValueError):
            return self.core.timeout()

    def startup_timeout(self) -> float:
        try:
            return max(2.0, float(self.cfg().get("startup_timeout_seconds", 30)))
        except (TypeError, ValueError):
            return 30.0

    def _gate_workspace(self, gate: dict, fallback: Workspace) -> Workspace:
        workspace_id = str(gate.get("workspace", fallback.workspace_id)).strip() or fallback.workspace_id
        try:
            return self.workspaces.get(workspace_id)
        except Exception:
            return fallback

    def run_gates(self, workspace: Workspace) -> bool:
        gates = self.cfg().get("gates", [])
        if not isinstance(gates, list):
            return True
        for index, gate in enumerate(gates):
            if not isinstance(gate, dict) or not bool(gate.get("enabled", True)):
                continue
            label = str(gate.get("label", gate.get("id", f"gate-{index + 1}")))
            command = str(gate.get("command", "")).strip()
            if not command:
                continue
            target = self._gate_workspace(gate, workspace)
            backend = self.backend_factory(target.backend)
            rc = backend.execute_in_workspace(target, command, f"Dev Stack Gate: {label}", timeout=self.timeout())
            if rc != 0:
                self.log(f"DEV STACK blocked by gate: {label}")
                return False
        return True

    def configured_products(self) -> list[str]:
        ids = self.cfg().get("products", self.products.enabled_ids())
        if isinstance(ids, str):
            ids = [ids]
        return [str(item) for item in ids if str(item).strip()]

    def prepare_products(self) -> bool:
        action_map = self.cfg().get("product_actions", {})
        default_actions = self.cfg().get("default_product_actions", ["validate", "build"])
        if not isinstance(default_actions, list):
            default_actions = ["validate", "build"]
        for product_id in self.configured_products():
            try:
                spec = self.products.get(product_id)
            except KeyError:
                self.log(f"DEV STACK product is not registered: {product_id}")
                return False
            if not spec.enabled:
                continue
            root = self.products.resolve_root(spec, refresh=True)
            if not root:
                if spec.optional:
                    self.log(f"{spec.label}: optional product not installed; skipping")
                    continue
                self.log(f"{spec.label}: required dev-stack product is NOT INSTALLED")
                return False
            actions = action_map.get(product_id, default_actions) if isinstance(action_map, dict) else default_actions
            if isinstance(actions, str):
                actions = [actions]
            for action in actions:
                action = str(action).strip()
                if not action:
                    continue
                command = self.products.command_for(spec, action, root)
                if not command:
                    self.log(f"{spec.label}: '{action}' not available; skipping undeclared optional action")
                    continue
                if self.products.run_action(product_id, action, timeout=self.timeout()) != 0:
                    return False
        return True

    def _wait_ready(self, product_ids: list[str]) -> bool:
        deadline = time.monotonic() + self.startup_timeout()
        pending = set(product_ids)
        while pending and time.monotonic() < deadline:
            for product_id in list(pending):
                spec = self.products.get(product_id)
                proc_state = self.supervisor.snapshot(product_id).state
                if proc_state == "FAILED":
                    self.log(f"{spec.label}: process failed during startup")
                    return False
                # A product may already be running outside this Control Panel.
                # ProductRegistry.start() deliberately reuses such a runtime; health
                # therefore remains authoritative even when the supervisor owns no PID.
                if not (spec.health_url or spec.open_url):
                    if proc_state == "RUNNING":
                        pending.remove(product_id)
                    continue
                health, detail = self.products.health(spec)
                if health == "READY":
                    ownership = "managed" if proc_state == "RUNNING" else "external"
                    self.log(f"{spec.label}: READY ({detail}; {ownership})")
                    pending.remove(product_id)
            if pending:
                time.sleep(0.5)
        if pending:
            names = ", ".join(self.products.get(pid).label for pid in sorted(pending))
            self.log(f"DEV STACK startup timeout waiting for: {names}")
            return False
        return True

    def start(self, workspace: Workspace, *, prepare_core: bool = False, run_gates: bool = True, prepare_products: bool = True) -> DevStackResult:
        if prepare_core and not self.core.prepare_development_environment(workspace):
            return DevStackResult(False, "environment")
        if run_gates and not self.run_gates(workspace):
            return DevStackResult(False, "gates")
        if prepare_products and not self.prepare_products():
            return DevStackResult(False, "products")
        started: list[str] = []
        for product_id in self.configured_products():
            spec = self.products.get(product_id)
            if not spec.enabled:
                continue
            if not self.products.resolve_root(spec):
                if spec.optional:
                    continue
                return DevStackResult(False, f"product:{product_id}:missing")
            if not self.products.start(product_id):
                if spec.optional:
                    self.log(f"{spec.label}: optional product could not start; continuing")
                    continue
                self.stop()
                return DevStackResult(False, f"product:{product_id}:start")
            started.append(product_id)
        if not self._wait_ready(started):
            return DevStackResult(False, "health")
        self.log("KOALI DEV STACK READY")
        return DevStackResult(True)

    def stop(self) -> bool:
        ok = True
        for product_id in reversed(self.configured_products()):
            ok = self.products.stop(product_id) and ok
        self.log("KOALI DEV STACK STOPPED" if ok else "KOALI DEV STACK stop completed with errors")
        return ok
