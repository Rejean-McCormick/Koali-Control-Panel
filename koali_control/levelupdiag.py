from __future__ import annotations

import json
import os
from dataclasses import replace
import re
import shlex
from typing import Callable

from .backends import ExecutionBackend, WindowsBackend, WslBackend, shell_join
from .config import QEMU_ENV_MAP, QEMU_PATH_FIELDS
from .models import Workspace


DEFAULT_CAMPAIGNS: dict[str, str] = {
    "koali_system": "koali-system",
    "store": "store",
    "stabilization": "stabilization",
    "stabilization_runtime": "stabilization-runtime",
    "developer": "debug",
    "build": "stabilization",
    "run_all": "validation",
    "release": "release",
    "delivery": "delivery",
}

_WINDOWS_ABSOLUTE = re.compile(r"^[A-Za-z]:[\\/]")

_EVIDENCE_UI_MAX_CHARS = 16000
_EVIDENCE_LOG_MAX_CHARS = 4000


class LevelUpDiagAdapter:
    """Control Panel boundary to the standalone LevelUpDiag-Koali diagnostic engine.

    The Control Panel supplies execution context (backend, active workspace and optional QEMU
    parameters). LevelUpDiag remains the sole owner of diagnostic levels,
    dependency planning, campaigns, verdicts, findings and diagnostic reports.

    The Control Panel may *present* the structured report produced by LevelUpDiag, but never
    recalculates or remaps its verdicts.
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
        self._last_report_text = ""

    def _config(self) -> dict:
        return self.config.get("diagnostics", {}).get("levelupdiag", {})

    def enabled(self) -> bool:
        return bool(self._config().get("enabled", True))

    def routed_workspace(self, workspace: Workspace, selection: str) -> Workspace:
        if selection not in {"koali-system", "koali-system-debug", "store", "N12", "N13"}:
            return workspace
        backend = self._config().get("campaign_backends", {}).get(selection, "native")
        if backend == "native":
            backend = "windows" if os.name == "nt" else "native_linux"
        if backend not in {"windows", "native_linux", "wsl"}:
            raise ValueError(f"Unsupported diagnostic backend: {backend}")
        # Empty root means LevelUpDiag owns the campaign target, including store.root.
        return replace(workspace, backend=backend, root="")

    @staticmethod
    def _ps_quote(value: str) -> str:
        return "'" + str(value).replace("'", "''") + "'"

    def _script(self, workspace, root, argv, env):
        if isinstance(self.backend(workspace), WindowsBackend):
            q = self._ps_quote
            prefix = " ".join(f"$env:{key}={q(value)};" for key, value in env.items())
            return (f"$ErrorActionPreference='Stop'; {prefix} Set-Location -LiteralPath {q(root)}; "
                    + "& " + " ".join(q(arg) for arg in argv) + "; exit $LASTEXITCODE")
        return f"cd {shlex.quote(root)} && {self._env_prefix(env)} {shell_join(argv)}"

    def backend(self, workspace: Workspace) -> ExecutionBackend:
        return self.backend_factory(workspace.backend)

    def root(self, workspace: Workspace) -> str:
        raw = str(self._config().get("roots", {}).get(workspace.backend) or self._config().get("root", "")).strip()
        if not raw:
            return ""
        backend = self.backend(workspace)
        if isinstance(backend, WslBackend) and _WINDOWS_ABSOLUTE.match(raw):
            return backend.windows_to_linux_path(raw)
        return backend.resolve_path(raw)

    def target_root(self, workspace: Workspace) -> str:
        return self.backend(workspace).resolve_path(workspace.root)

    def campaign_name(self, role: str) -> str:
        configured = self._config().get("campaigns", {})
        value = str(configured.get(role, DEFAULT_CAMPAIGNS.get(role, ""))).strip()
        if not value:
            raise RuntimeError(f"LevelUpDiag campaign is not configured for role: {role}")
        return value

    def last_report(self) -> str:
        """Return the last structured LevelUpDiag report presented by the Control Panel."""

        return self._last_report_text

    def _available(self, workspace: Workspace) -> tuple[bool, str]:
        if not self.enabled():
            return False, "LevelUpDiag integration is disabled"
        root = self.root(workspace)
        if not root:
            return False, "LevelUpDiag root is not configured"
        if isinstance(self.backend(workspace), WindowsBackend):
            source = "from pathlib import Path; import sys; p=Path('.'); sys.exit(0 if p.is_dir() and (p/'levelupdiag.py').is_file() and (p/'levelupdiag_manifest.json').is_file() else 1)"
            script = self._script(workspace, root, ["python", "-c", source], {})
        else:
            script = " && ".join([f"test -d {shlex.quote(root)}", f"test -f {shlex.quote(root + '/levelupdiag.py')}", f"test -f {shlex.quote(root + '/levelupdiag_manifest.json')}"])
        probe = self.backend(workspace).capture_shell(script, timeout=20)
        if probe.code != 0:
            return False, f"LevelUpDiag is not usable at {root}"
        return True, root

    def status(self, workspace: Workspace) -> tuple[bool, str]:
        return self._available(workspace)

    def _qemu_value(self, workspace: Workspace, config_name: str, raw: object) -> str:
        value = str(raw or "").strip()
        if not value or config_name not in QEMU_PATH_FIELDS:
            return value
        backend = self.backend(workspace)
        if isinstance(backend, WslBackend) and _WINDOWS_ABSOLUTE.match(value):
            return backend.windows_to_linux_path(value)
        if value.startswith("/"):
            return value
        root = backend.resolve_path(workspace.root).rstrip("/")
        return f"{root}/{value.lstrip('/')}"

    def qemu_environment(self, workspace: Workspace) -> dict[str, str]:
        """Return Control Panel-owned QEMU context in the public kOA-Linux env contract."""

        qemu = self.config.get("system_test", {}).get("qemu", {})
        environment: dict[str, str] = {}
        for env_name, config_name in QEMU_ENV_MAP.items():
            value = self._qemu_value(workspace, config_name, qemu.get(config_name, ""))
            if value:
                environment[env_name] = value
        return environment

    def _execution_env(self, workspace: Workspace, *, include_qemu: bool) -> dict[str, str]:
        env = {
            "LEVELUPDIAG_TARGET_REPO_ROOT": self.target_root(workspace) if workspace.root else "",
            "LEVELUPDIAG_APP_NAME": "kOA-Linux",
        }
        if include_qemu and workspace.root:
            env.update(self.qemu_environment(workspace))
        return env

    @staticmethod
    def _env_prefix(env: dict[str, str]) -> str:
        exports = " ".join(
            f"{name}={shlex.quote(value)}"
            for name, value in env.items()
        )
        return f"env {exports}".rstrip()

    def _levelupdiag_python(
        self,
        workspace: Workspace,
        root: str,
        source: str,
        *,
        include_qemu: bool,
        timeout: int = 20,
    ):
        env = self._execution_env(workspace, include_qemu=include_qemu)
        script = self._script(workspace, root, ["python", "-c", source], env)
        return self.backend(workspace).capture_shell(script, timeout=timeout)

    def _report_marker(self, workspace, root, selection, level, include_qemu):
        target = self.target_root(workspace) if workspace.root else None
        source = (
            "from pathlib import Path; import json,re; from levelupdiag_core.config import load_config; "
            f"target={target!r}; selection={selection!r}; tool=Path.cwd(); cfg=load_config(tool,target); "
            "mapped=cfg.get('campaign_targets',{}).get(selection) if target is None else None; "
            "cfg=load_config(tool,mapped) if mapped else cfg; "
            "control=Path(cfg['_control_root']); p=control/'latest'/'summary.json'; "
            "d=json.loads(p.read_text(encoding='utf-8')) if p.is_file() else {}; "
            "rid=str(d.get('run_id','')); "
            "valid=d.get('selection')==selection and bool(re.fullmatch(r'[A-Za-z0-9_-]+',rid)); "
            "p=control/'runs'/rid/'summary.json' if valid else p; "
            + (f"p=p.parent/'levels'/{level!r}/'result.json'; " if level else "")
            + "st=p.stat() if valid and p.is_file() else None; "
            "print((str(p)+'\t'+str(st.st_mtime_ns)+'\t'+str(st.st_size)) if st else '')"
        )
        result = self._levelupdiag_python(workspace, root, source, include_qemu=include_qemu)
        if result.code:
            self.log("Cannot locate LevelUpDiag report: " + result.output[-2000:])
        return result.output.strip() if result.code == 0 else ""

    def _campaign_marker(self, workspace, root, campaign, *, include_qemu):
        return self._report_marker(workspace, root, campaign, None, include_qemu)

    def _level_marker(self, workspace, root, level, *, include_qemu):
        return self._report_marker(workspace, root, level, level, include_qemu)

    def _read_text(self, workspace, path):
        if isinstance(self.backend(workspace), WindowsBackend):
            source = f"from pathlib import Path; print(Path({path!r}).read_text(encoding='utf-8'))"
            return self._levelupdiag_python(workspace, self.root(workspace), source, include_qemu=False)
        return self.backend(workspace).capture_shell(f"cat -- {shlex.quote(path)}", timeout=20)

    def _marker(
        self,
        workspace: Workspace,
        root: str,
        report_kind: str,
        report_key: str,
        *,
        include_qemu: bool,
    ) -> str:
        if report_kind == "campaign":
            return self._campaign_marker(
                workspace, root, report_key, include_qemu=include_qemu
            )
        if report_kind == "level":
            return self._level_marker(
                workspace, root, report_key, include_qemu=include_qemu
            )
        return ""

    def _read_marker_json(self, workspace: Workspace, marker: str) -> dict | None:
        path = marker.split("\t", 1)[0].strip()
        if not path:
            return None
        result = self._read_text(workspace, path)
        if result.code != 0 or not result.output.strip():
            return None
        try:
            data = json.loads(result.output)
        except json.JSONDecodeError as exc:
            self.log(f"LevelUpDiag structured report is unreadable: {exc}")
            return None
        if not isinstance(data, dict):
            return None

        # Campaign summaries intentionally stay compact. Enrich presentation from the
        # LevelUpDiag-owned latest per-level result files without changing verdicts.
        if data.get("schema") == "levelupdiag.campaign-summary.v2" and isinstance(data.get("levels"), list):
            latest_dir = path.replace("\\", "/").rsplit("/", 1)[0]
            enriched: list[dict] = []
            for row in data["levels"]:
                if not isinstance(row, dict):
                    continue
                item = dict(row)
                level_id = str(item.get("id", "")).strip()
                if level_id and re.fullmatch(r"N\d{2}", level_id):
                    detail_path = f"{latest_dir}/levels/{level_id}/result.json" if data.get("run_id") else f"{latest_dir}/{level_id}/result.json"
                    detail = self._read_text(workspace, detail_path)
                    if detail.code == 0 and detail.output.strip():
                        try:
                            level_data = json.loads(detail.output)
                        except json.JSONDecodeError:
                            level_data = None
                        if isinstance(level_data, dict) and (not data.get("run_id") or level_data.get("run_id") == data["run_id"]):
                            item["findings"] = level_data.get("findings", [])
                            item["metrics"] = level_data.get("metrics", {})
                enriched.append(item)
            data["levels"] = enriched
        return data

    @staticmethod
    def _clip_evidence(value: object, limit: int) -> str:
        """Bound presentation of LevelUpDiag-owned evidence without interpreting it."""

        text = str(value or "").strip()
        if not text or limit <= 0 or len(text) <= limit:
            return text
        half = max(1, limit // 2)
        omitted = len(text) - (half * 2)
        return (
            text[:half].rstrip()
            + f"\n... {omitted} evidence character(s) omitted by Control Panel presentation ...\n"
            + text[-half:].lstrip()
        )

    @classmethod
    def _format_finding(cls, finding: dict) -> list[str]:
        severity = str(finding.get("severity") or finding.get("verdict") or "INFO").strip() or "INFO"
        message = str(finding.get("message", "")).strip() or "Unnamed finding"
        lines = [f"    [{severity}] {message}"]
        path = str(finding.get("path") or "").strip()
        if path:
            lines.append(f"      Path: {path}")
        recommendation = str(finding.get("recommendation") or "").strip()
        if recommendation:
            lines.append(f"      Recommendation: {recommendation}")
        evidence = cls._clip_evidence(finding.get("evidence"), _EVIDENCE_UI_MAX_CHARS)
        if evidence:
            lines.append("      Evidence (LevelUpDiag):")
            lines.extend(f"        {line}" for line in evidence.splitlines())
        return lines

    @classmethod
    def _format_level(cls, level: dict) -> list[str]:
        level_id = str(level.get("level") or level.get("level_id") or level.get("id") or "?").strip() or "?"
        name = str(level.get("name", "")).strip()
        verdict = str(level.get("verdict", "?")).strip() or "?"
        title = f"{level_id} — {name}: {verdict}" if name else f"{level_id}: {verdict}"
        lines = [title]
        findings = level.get("findings", [])
        if isinstance(findings, list):
            for finding in findings:
                if isinstance(finding, dict):
                    lines.extend(cls._format_finding(finding))
        return lines

    @classmethod
    def _format_report(cls, data: dict) -> str:
        levels = data.get("levels")
        if isinstance(levels, list):
            campaign = str(data.get("selection") or data.get("campaign") or "campaign").strip() or "campaign"
            verdict = str(data.get("verdict", "?")).strip() or "?"
            lines = [f"LevelUpDiag campaign — {campaign}: {verdict}"]
            scope = str(data.get("qualification_scope") or "").strip()
            if scope:
                lines.append(f"Qualification scope: {scope}")
            deferred = data.get("deferred_subsystems", [])
            if isinstance(deferred, list) and deferred:
                lines.append("Deferred subsystems: " + ", ".join(str(x) for x in deferred))
            states = data.get("deferred_subsystem_states", {})
            if isinstance(states, dict) and states:
                lines.append("Subsystem states: " + ", ".join(f"{name}={state}" for name, state in states.items()))
            lines.append("")
            for level in levels:
                if isinstance(level, dict):
                    lines.extend(cls._format_level(level))
            return "\n".join(lines).rstrip() + "\n"
        return "\n".join(cls._format_level(data)).rstrip() + "\n"

    def _publish_report(self, data: dict) -> None:
        text = self._format_report(data)
        self._last_report_text = text
        if self.on_report is not None:
            self.on_report(text)

        # The CLI already logs level verdicts. Add the missing structured reason
        # here so WARN/BLOCKED/FAIL never require opening JSON by hand.
        levels = data.get("levels")
        items = levels if isinstance(levels, list) else [data]
        for level in items:
            if not isinstance(level, dict):
                continue
            level_id = str(level.get("level") or level.get("level_id") or level.get("id") or "?").strip() or "?"
            findings = level.get("findings", [])
            if not isinstance(findings, list):
                continue
            for finding in findings:
                if not isinstance(finding, dict):
                    continue
                severity = str(finding.get("severity") or finding.get("verdict") or "INFO").strip() or "INFO"
                if severity == "PASS":
                    continue
                message = str(finding.get("message", "")).strip() or "Unnamed finding"
                self.log(f"  {level_id} [{severity}] {message}")
                recommendation = str(finding.get("recommendation") or "").strip()
                if recommendation:
                    self.log(f"    recommendation: {recommendation}")
                evidence = self._clip_evidence(
                    finding.get("evidence"), _EVIDENCE_LOG_MAX_CHARS
                )
                if evidence:
                    self.log("    evidence (LevelUpDiag):")
                    for evidence_line in evidence.splitlines():
                        self.log(f"      {evidence_line}")

    def _publish_missing_report(self, label: str) -> None:
        text = (
            f"{label}\n\n"
            "No fresh structured LevelUpDiag report was produced for this execution.\n"
            "See the activity log for process/configuration errors.\n"
        )
        self._last_report_text = text
        if self.on_report is not None:
            self.on_report(text)

    def _run(
        self,
        workspace: Workspace,
        args: list[str],
        label: str,
        *,
        include_qemu: bool = False,
        timeout: int | None = None,
        report_kind: str,
        report_key: str,
    ) -> int:
        workspace = self.routed_workspace(workspace, report_key)
        self.log(f"Diagnostic context: {workspace.backend}; target={workspace.root or 'LevelUpDiag campaign configuration'}")
        available, detail = self._available(workspace)
        if not available:
            self.log(detail)
            self._publish_missing_report(label)
            return 78

        root = detail
        before = self._marker(
            workspace,
            root,
            report_kind,
            report_key,
            include_qemu=include_qemu,
        )
        env = self._execution_env(workspace, include_qemu=include_qemu)
        if args[:1] == ["--campaign"] and len(args) >= 2:
            selection = args[1]
        elif args[:1] == ["--all"]:
            selection = "release"
        else:
            selection = args[0] if args else "stabilization"
        argv = ["python", "levelupdiag.py"]
        if workspace.root:
            argv += ["--target", self.target_root(workspace)]
        argv += ["run", selection]
        script = self._script(workspace, root, argv, env)
        code = self.backend(workspace).run_shell(script, label, timeout=timeout)

        after = self._marker(
            workspace,
            root,
            report_kind,
            report_key,
            include_qemu=include_qemu,
        )
        if after and after != before:
            data = self._read_marker_json(workspace, after)
            if data is not None and (report_kind != "campaign" or data.get("selection") == selection):
                self._publish_report(data)
                return code
        self._publish_missing_report(label)
        return code

    def run_level(self, workspace: Workspace, level_id: str, *, timeout: int | None = None) -> int:
        level = str(level_id).strip().upper()
        if not re.fullmatch(r"N\d{2}", level):
            raise ValueError(f"Invalid LevelUpDiag level id: {level_id!r}")
        return self._run(
            workspace,
            [level, "--console"],
            f"LevelUpDiag {level}",
            include_qemu=(level in {"N08", "N09", "N10"}),
            timeout=timeout,
            report_kind="level",
            report_key=level,
        )

    def run_campaign(self, workspace: Workspace, campaign: str, *, timeout: int | None = None) -> int:
        name = str(campaign).strip()
        if not name:
            raise ValueError("LevelUpDiag campaign name is required")
        return self._run(
            workspace,
            ["--campaign", name, "--console"],
            f"LevelUpDiag campaign: {name}",
            include_qemu=True,
            timeout=timeout,
            report_kind="campaign",
            report_key=name,
        )

    def run_configured_campaign(self, workspace: Workspace, role: str, *, timeout: int | None = None) -> int:
        return self.run_campaign(workspace, self.campaign_name(role), timeout=timeout)

    def run_all_enabled(self, workspace: Workspace, *, timeout: int | None = None) -> int:
        return self._run(
            workspace,
            ["--all", "--console"],
            "LevelUpDiag: all enabled levels",
            include_qemu=True,
            timeout=timeout,
            report_kind="campaign",
            report_key="release",
        )

    def run_system(self, workspace: Workspace, *, timeout: int | None = None) -> int:
        return self.run_level(workspace, "N10", timeout=timeout)
