from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from .products import ProductRegistry
from .spaces_pilot import LegacyKoaliSpacesPilotState


@dataclass(frozen=True, slots=True)
class KoaliSpacesIntegrationStatus:
    mode: str
    delegated: bool
    legacy_projection: bool


class KoaliSpacesIntegrationController:
    """Coordinate Koali Spaces integration without owning Koali composition semantics.

    Final-target mode is ``delegated``: Control Panel invokes repository-owned
    Koali actions and verifies the resulting public shell state. It never builds
    ModuleManifest, ACP, activation payload, receipt, or runtime-registration
    semantics itself.

    ``legacy_projection`` is an explicit strangler compatibility mode retained
    only while the Koali-owned SpaceActivationCompiler/invocation surface is not
    yet available in the currently paired Koali Spaces snapshot.
    """

    VALID_MODES = frozenset({"delegated", "legacy_projection"})

    def __init__(
        self,
        config: dict,
        products: ProductRegistry,
        log: Callable[[str], None],
    ) -> None:
        self.config = config
        self.products = products
        self.log = log
        self.legacy = LegacyKoaliSpacesPilotState(config, log)

    def reload(self, config: dict) -> None:
        self.config = config
        self.legacy.reload(config)

    def cfg(self) -> dict:
        value = self.config.get("dev_stack", {}).get("koali_spaces_integration", {})
        return value if isinstance(value, dict) else {}

    def enabled(self) -> bool:
        return bool(self.cfg().get("enabled", True))

    def mode(self) -> str:
        mode = str(self.cfg().get("mode", "delegated")).strip().lower()
        if mode not in self.VALID_MODES:
            raise RuntimeError(
                "dev_stack.koali_spaces_integration.mode must be 'delegated' or 'legacy_projection'"
            )
        return mode

    def status(self) -> KoaliSpacesIntegrationStatus:
        mode = self.mode()
        return KoaliSpacesIntegrationStatus(
            mode=mode,
            delegated=mode == "delegated",
            legacy_projection=mode == "legacy_projection",
        )

    def product_id(self) -> str:
        return str(self.cfg().get("product_id", "koali-spaces")).strip() or "koali-spaces"

    def timeout(self) -> int:
        raw = self.cfg().get("command_timeout_seconds")
        if raw is None:
            raw = self.config.get("dev_stack", {}).get("command_timeout_seconds", 1800)
        try:
            return max(30, int(raw))
        except (TypeError, ValueError):
            return 1800

    def _action(self, key: str) -> str:
        actions = self.cfg().get("actions", {})
        if not isinstance(actions, dict):
            return ""
        return str(actions.get(key, "")).strip()

    def _run_delegated_action(self, key: str, *, required: bool) -> bool:
        action = self._action(key)
        if not action:
            if required:
                self.log(
                    f"Koali Spaces integration: delegated '{key}' action is not configured; "
                    "Control Panel will not fabricate Koali state"
                )
                return False
            return True
        try:
            spec = self.products.get(self.product_id())
        except KeyError:
            self.log(f"Koali Spaces integration product is not registered: {self.product_id()}")
            return False
        if not spec.enabled:
            self.log(f"Koali Spaces integration product is disabled: {self.product_id()}")
            return False
        rc = self.products.run_action(self.product_id(), action, timeout=self.timeout())
        if rc != 0:
            self.log(f"Koali Spaces integration delegated action failed: {key} ({action}, rc={rc})")
            return False
        self.log(f"Koali Spaces integration delegated action completed: {key} ({action})")
        return True

    def before_start(self) -> bool:
        """Prepare integration state before managed runtimes start.

        Delegated mode intentionally does nothing here. The Koali-owned action is
        invoked after runtimes become ready, allowing either a local control API
        or a repository-owned CLI to own activation semantics.
        """
        if not self.enabled():
            return True
        if self.mode() == "legacy_projection":
            self.log(
                "Koali Spaces integration: LEGACY PROJECTION compatibility mode active; "
                "migrate to delegated mode when the Koali-owned activation surface is available"
            )
            return self.legacy.write("starting", "unknown")
        return True

    def mark_failed(self) -> bool:
        if not self.enabled():
            return True
        if self.mode() == "legacy_projection":
            return self.legacy.write("failed", "failed")
        return True

    def after_ready(self) -> bool:
        if not self.enabled():
            return True
        if self.mode() == "legacy_projection":
            if not self.legacy.write("ready", "ready"):
                return False
        else:
            if not self._run_delegated_action("activate", required=True):
                return False
        return self.verify()

    def stop(self) -> bool:
        if not self.enabled():
            return True
        if self.mode() == "legacy_projection":
            return self.legacy.clear()
        return self._run_delegated_action("deactivate", required=False)

    def _shell_base(self) -> str:
        explicit = str(self.cfg().get("shell_base_url", "")).strip()
        if explicit:
            return explicit.rstrip("/")
        products = self.config.get("products", {})
        spec = products.get(self.product_id(), {}) if isinstance(products, dict) else {}
        if isinstance(spec, dict):
            return str(spec.get("open_url", "http://127.0.0.1:4173/")).rstrip("/")
        return "http://127.0.0.1:4173"

    def _verification_modules(self) -> list[dict]:
        verify = self.cfg().get("verify", {})
        if not isinstance(verify, dict):
            return []
        modules = verify.get("modules", [])
        if isinstance(modules, str):
            modules = [{"module_id": modules}]
        if not isinstance(modules, list):
            return []
        result: list[dict] = []
        for item in modules:
            if isinstance(item, str):
                item = {"module_id": item}
            if not isinstance(item, dict):
                continue
            module_id = str(item.get("module_id", "")).strip()
            if not module_id:
                continue
            result.append(
                {
                    "module_id": module_id,
                    "required": bool(item.get("required", True)),
                    "route": str(item.get("route", f"/apps/{module_id}")).strip(),
                }
            )
        return result

    def verify(self) -> bool:
        """Check public shell and selected module readiness, not policy authority.

        The Koali-owned shell-state API exposes both the aggregate state and
        per-owner module_health. A route returning HTTP 200 is never sufficient
        to call a required backend ready. Legacy projection mode is explicitly
        exempt from the module_health requirement because its static pilot does
        not publish those observations.
        """
        if not self.enabled():
            return True
        base = self._shell_base()
        try:
            req = urllib.request.Request(f"{base}/api/shell-state", headers={"User-Agent": "Koali-Control-Panel"})
            with urllib.request.urlopen(req, timeout=5.0) as response:
                # Bound the untrusted HTTP response; JSON document validation
                # below is entirely local and has no activation side effects.
                body = response.read(2 * 1024 * 1024 + 1)
                if len(body) > 2 * 1024 * 1024:
                    self.log("Koali integration verification: shell state is oversized")
                    return False
                state = json.loads(body.decode("utf-8"))

            if not isinstance(state, dict) or state.get("state") != "ready":
                self.log("Koali integration verification: shell is not ready")
                return False
            modules = state.get("modules")
            active_space = state.get("active_space")
            if not isinstance(modules, list) or not isinstance(active_space, dict):
                self.log("Koali integration verification: shell structure is invalid")
                return False
            instances_list = active_space.get("module_instances")
            if not isinstance(instances_list, list):
                self.log("Koali integration verification: module instances are missing")
                return False
            manifests = {
                item.get("module_id")
                for item in modules
                if isinstance(item, dict) and isinstance(item.get("module_id"), str)
            }
            instances = {
                item.get("module_id")
                for item in instances_list
                if isinstance(item, dict) and item.get("enabled") is True
                and isinstance(item.get("module_id"), str)
            }
            health_rows = state.get("module_health", [])
            if not isinstance(health_rows, list):
                self.log("Koali integration verification: module health projection is invalid")
                return False
            health: dict[str, str] = {}
            for item in health_rows:
                if not isinstance(item, dict) or not isinstance(item.get("module_id"), str):
                    self.log("Koali integration verification: malformed module health entry")
                    return False
                module_id = item["module_id"]
                if module_id in health or not isinstance(item.get("state"), str):
                    self.log("Koali integration verification: duplicate or malformed module health entry")
                    return False
                health[module_id] = item["state"]

            for expected in self._verification_modules():
                module_id = expected["module_id"]
                admitted = module_id in manifests and module_id in instances
                if not admitted:
                    if expected["required"]:
                        self.log(f"Koali integration verification: required module not admitted: {module_id}")
                        return False
                    self.log(f"Koali integration verification: optional module not admitted: {module_id}")
                    continue

                observed = health.get(module_id)
                if self.mode() == "delegated" and observed != "ready":
                    if expected["required"]:
                        self.log(f"Koali integration verification: required module not ready: {module_id} ({observed or 'unknown'})")
                        return False
                    self.log(f"Koali integration verification: optional module not ready: {module_id} ({observed or 'unknown'})")
                    continue
                if observed is not None and observed != "ready":
                    if expected["required"]:
                        self.log(f"Koali integration verification: required module not ready: {module_id} ({observed})")
                        return False
                    continue

                route = expected["route"]
                if route and (not route.startswith("/") or route.startswith("//") or "\\" in route
                              or "?" in route or "#" in route or any(ord(ch) < 32 for ch in route)):
                    self.log(f"Koali integration verification: route must be a same-origin absolute path: {route}")
                    return False
                if route:
                    route_url = f"{base}/{route.lstrip('/')}"
                    route_req = urllib.request.Request(route_url, headers={"User-Agent": "Koali-Control-Panel"})
                    with urllib.request.urlopen(route_req, timeout=10.0) as response:
                        code = int(getattr(response, "status", 200))
                        redirected = response.geturl() if callable(getattr(response, "geturl", None)) else route_url
                    if urlsplit(redirected)[:2] != urlsplit(route_url)[:2]:
                        self.log(f"Koali integration verification: route escaped the configured shell origin: {route}")
                        return False
                    if not 200 <= code < 400:
                        self.log(f"Koali integration verification: {route} returned HTTP {code}")
                        return False
                    self.log(f"Koali integration verification: {module_id} ready; {route} resolved (HTTP {code})")

            self.log("Koali Spaces integration verification passed (readiness only; no policy grant)")
            return True
        except (OSError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, ValueError,
                UnicodeError, TypeError) as exc:
            self.log(f"Koali Spaces integration verification failed: {exc}")
            return False
