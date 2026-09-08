from __future__ import annotations

import json
from typing import Callable

from .backends import ExecutionBackend, shell_join
from .models import Workspace


class DebugDiagnosticsRunner:
    """Read-only local debug diagnostics.

    DEBUG answers whether the local system is observable/testable. Repository
    architecture/conformance findings are preserved in the report but presented as
    non-blocking warnings. Strict validation remains owned by LevelUpDiag.
    """

    def __init__(
        self,
        config: dict,
        backend_factory: Callable[[str], ExecutionBackend],
        log: Callable[[str], None],
        on_report: Callable[[str], None] | None = None,
    ) -> None:
        self.config = config
        self.backend_factory = backend_factory
        self.log = log
        self.on_report = on_report
        self._last_report = ""

    def _config(self) -> dict:
        return self.config.get("diagnostics", {}).get("debug", {})

    def enabled(self) -> bool:
        return bool(self._config().get("enabled", True))

    def status(self, workspace: Workspace) -> tuple[bool, str]:
        if not self.enabled():
            return False, "DEBUG diagnostics disabled"
        backend = self.backend_factory(workspace.backend)
        probe = backend.capture_in_workspace(
            workspace,
            "uv run --frozen python -m koa_tools.cli diagnose --help >/dev/null 2>&1",
            timeout=30,
        )
        return (probe.code == 0, "ready" if probe.code == 0 else "koa diagnose unavailable")

    @staticmethod
    def _extract_json(text: str) -> dict | None:
        text = text.strip()
        if not text:
            return None
        try:
            value = json.loads(text)
            return value if isinstance(value, dict) else None
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                try:
                    value = json.loads(text[start : end + 1])
                    return value if isinstance(value, dict) else None
                except json.JSONDecodeError:
                    return None
        return None

    @staticmethod
    def _pipeline_lines(pipeline: object) -> list[str]:
        if not isinstance(pipeline, dict):
            return ["Pipeline: no structured pipeline data returned"]
        lines = ["Pipeline findings (functional blockers remain visible):"]
        emitted = 0
        for key, value in pipeline.items():
            if isinstance(value, dict):
                status = value.get("status") or value.get("readiness") or value.get("state")
                if status is not None:
                    lines.append(f"  {key}: {status}")
                    emitted += 1
                for field in ("blocked_reasons", "missing", "missing_inputs", "missing_paths", "reasons"):
                    item = value.get(field)
                    if item:
                        if isinstance(item, list):
                            for entry in item[:20]:
                                lines.append(f"    - {entry}")
                        else:
                            lines.append(f"    - {field}: {item}")
            elif key in {"status", "readiness", "profile", "outcome"}:
                lines.append(f"  {key}: {value}")
                emitted += 1
        if not emitted:
            lines.append("  See raw JSON below for pipeline details.")
        return lines

    def _format(self, data: dict, process_code: int, profile: str) -> str:
        architecture = str(data.get("architecture_readiness", "unknown"))
        checks = data.get("checks", []) if isinstance(data.get("checks"), list) else []
        lines = [
            "DEBUG PRINCIPAL — EXECUTION COMPLETE",
            f"Profile: {profile}",
            f"Diagnostic process exit: {process_code} (non-zero readiness is not a DEBUG execution failure)",
            "",
        ]
        if architecture.lower() in {"blocked", "fail", "failed", "error"}:
            lines.append(f"Architecture/conformance: WARN in DEBUG (underlying result: {architecture})")
            lines.append("Formal repository-layout findings do not stop local debugging.")
        else:
            lines.append(f"Architecture/conformance: {architecture}")
        lines.append("")
        for check in checks:
            if not isinstance(check, dict):
                continue
            cid = str(check.get("check_id", "check"))
            status = str(check.get("status", "unknown"))
            counts = check.get("counts", {}) if isinstance(check.get("counts"), dict) else {}
            errors = int(counts.get("errors", 0) or 0)
            warnings = int(counts.get("warnings", 0) or 0)
            presented = "WARN" if errors else status.upper()
            lines.append(f"{presented:7} {cid}: errors={errors} warnings={warnings}")
            if errors:
                findings = check.get("findings", []) if isinstance(check.get("findings"), list) else []
                for finding in findings[:8]:
                    if isinstance(finding, dict):
                        path = finding.get("path")
                        msg = finding.get("message") or finding.get("code") or "finding"
                        lines.append(f"        - {msg}" + (f" [{path}]" if path else ""))
                if len(findings) > 8:
                    lines.append(f"        ... {len(findings)-8} more formal finding(s)")
        lines.append("")
        lines.extend(self._pipeline_lines(data.get("pipeline")))
        lines += ["", "Raw read-only diagnostic JSON:", json.dumps(data, indent=2, ensure_ascii=False)]
        return "\n".join(lines)

    def run(self, workspace: Workspace, *, profile: str | None = None, timeout: int = 1800) -> int:
        if not self.enabled():
            self.log("DEBUG PRINCIPAL is disabled")
            return 78
        profile = str(profile or workspace.profile).strip()
        command = shell_join([
            "uv", "run", "--frozen", "python", "-m", "koa_tools.cli",
            "--repository-root", "$PWD", "diagnose", "--pipeline", "--profile", profile, "--json",
        ]).replace("'$PWD'", '"$PWD"')
        backend = self.backend_factory(workspace.backend)
        result = backend.capture_in_workspace(workspace, command, timeout=timeout)
        data = self._extract_json(result.output)
        if data is None:
            text = (
                "DEBUG PRINCIPAL — EXECUTION ERROR\n"
                f"Process exit: {result.code}\n\n{result.output.strip()}"
            )
            self._last_report = text
            self.log(text)
            if self.on_report:
                self.on_report(text)
            return result.code or 70
        text = self._format(data, result.code, profile)
        self._last_report = text
        self.log("DEBUG PRINCIPAL complete; formal conformance findings are non-blocking in DEBUG")
        if self.on_report:
            self.on_report(text)
        return 0

    def last_report(self) -> str:
        return self._last_report
