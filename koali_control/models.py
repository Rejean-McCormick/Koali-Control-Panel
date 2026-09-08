from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class CheckState(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    BLOCKED = "BLOCKED"
    FAIL = "FAIL"
    INFO = "INFO"


@dataclass(frozen=True, slots=True)
class CheckResult:
    key: str
    label: str
    state: CheckState
    detail: str
    required: bool = True


@dataclass(frozen=True, slots=True)
class PreflightReport:
    checks: tuple[CheckResult, ...]

    @property
    def state(self) -> CheckState:
        required = [item for item in self.checks if item.required]
        if any(item.state == CheckState.FAIL for item in required):
            return CheckState.FAIL
        if any(item.state == CheckState.BLOCKED for item in required):
            return CheckState.BLOCKED
        if any(item.state == CheckState.WARN for item in required):
            return CheckState.WARN
        return CheckState.PASS

    def by_key(self) -> dict[str, CheckResult]:
        return {item.key: item for item in self.checks}


@dataclass(slots=True)
class Workspace:
    workspace_id: str
    repository: str
    backend: str
    profile: str
    root: str
    windows_source: str = ""
    checkout_ref: str = "current"
    bootstrap_on_start: bool = False
    assembly: dict[str, Any] = field(default_factory=dict)
    services: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_config(cls, workspace_id: str, data: dict[str, Any]) -> "Workspace":
        return cls(
            workspace_id=workspace_id,
            repository=str(data.get("repository", "koa-linux")),
            backend=str(data.get("backend", "wsl")),
            profile=str(data.get("profile", "developer-windows-wsl")),
            root=str(data.get("root", "{home}/work/koa-linux")),
            windows_source=str(data.get("windows_source", "")),
            checkout_ref=str(data.get("checkout_ref", "current")),
            bootstrap_on_start=bool(data.get("bootstrap_on_start", False)),
            assembly=dict(data.get("assembly", {})),
            services=dict(data.get("services", {})),
        )

    def to_config(self) -> dict[str, Any]:
        return {
            "repository": self.repository,
            "backend": self.backend,
            "profile": self.profile,
            "root": self.root,
            "windows_source": self.windows_source,
            "checkout_ref": self.checkout_ref,
            "bootstrap_on_start": self.bootstrap_on_start,
            "assembly": self.assembly,
            "services": self.services,
        }
