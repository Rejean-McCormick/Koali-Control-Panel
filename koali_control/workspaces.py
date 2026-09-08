from __future__ import annotations

from pathlib import Path
import re
import shlex
from typing import Callable

from .backends import ExecutionBackend, WslBackend
from .models import CheckResult, CheckState, PreflightReport, Workspace


REQUIRED_MARKERS = ("pyproject.toml", "uv.lock", ".python-version")
REQUIRED_TOOLS = ("git", "python", "uv")

# Windows -> Linux import boundary. These are mutable/runtime/build artifacts and
# must never be copied into the canonical Linux workspace from the Windows source.
IMPORT_EXCLUDES = (
    "./.venv",
    "./.levelupdiag",
    "./node_modules",
    "./.git/index.lock",
    "*/__pycache__",
    "*/.pytest_cache",
    "*/.mypy_cache",
    "*/.ruff_cache",
    "*/.tox",
    "*/.nox",
    "*.pyc",
)

# Git metadata is copied during import, but excluded from the source-content
# fingerprint because repository metadata changes independently of checkout data.
FINGERPRINT_EXCLUDES = ("./.git", *IMPORT_EXCLUDES)


class WorkspaceManager:
    def __init__(self, config: dict, backend_factory: Callable[[str], ExecutionBackend]) -> None:
        self.config = config
        self.backend_factory = backend_factory

    def ids(self) -> list[str]:
        return sorted(self.config.get("workspaces", {}))

    def get(self, workspace_id: str) -> Workspace:
        data = self.config.get("workspaces", {}).get(workspace_id)
        if not isinstance(data, dict):
            raise KeyError(f"Unknown workspace: {workspace_id}")
        return Workspace.from_config(workspace_id, data)

    def default_id(self) -> str:
        configured = str(self.config.get("environment", {}).get("default_workspace", "")).strip()
        if configured in self.config.get("workspaces", {}):
            return configured
        ids = self.ids()
        if not ids:
            raise RuntimeError("No Control Panel workspace is configured")
        return ids[0]

    def workspace_exists(self, workspace: Workspace) -> bool:
        """Return True for a structurally usable imported workspace.

        PREPARE does not inspect or manage Git state. It only checks the directory
        and repository markers required by the configured development workflow.
        """
        backend = self.backend_factory(workspace.backend)
        root = backend.resolve_path(workspace.root)
        probe = backend.capture_shell(
            f"test -d {shlex.quote(root)} && "
            f"test -e {shlex.quote(root + '/.git')} && "
            f"test -f {shlex.quote(root + '/pyproject.toml')} && "
            f"test -f {shlex.quote(root + '/uv.lock')} && "
            f"test -f {shlex.quote(root + '/.python-version')}",
            timeout=15,
        )
        return probe.code == 0

    def setup_needed(self, workspace: Workspace) -> bool:
        backend = self.backend_factory(workspace.backend)
        root = backend.resolve_path(workspace.root)
        probe = backend.capture_shell(
            f"cd {shlex.quote(root)} 2>/dev/null || exit 2; "
            "test -d .venv && test -x .venv/bin/python && test -x .git/hooks/pre-commit",
            timeout=15,
        )
        return probe.code != 0

    @staticmethod
    def _state_slug(workspace_id: str) -> str:
        return re.sub(r"[^A-Za-z0-9_.-]+", "_", workspace_id).strip("._") or "workspace"

    @staticmethod
    def _exclude_args(patterns: tuple[str, ...]) -> str:
        return " \\\n  ".join(f"--exclude={shlex.quote(pattern)}" for pattern in patterns)

    @classmethod
    def _fingerprint_function(cls) -> str:
        excludes = cls._exclude_args(FINGERPRINT_EXCLUDES)
        return f"""fingerprint_tree() {{
  ROOT_TO_HASH=\"$1\"
  test -d \"$ROOT_TO_HASH\" || return 1
  tar -C \"$ROOT_TO_HASH\" \\
  --sort=name \\
  --mtime='UTC 1970-01-01' \\
  --owner=0 --group=0 --numeric-owner \\
  {excludes} \\
  -cf - . | sha256sum | awk '{{print $1}}'
}}"""

    def source_sync_status(self, workspace: Workspace) -> tuple[str, str]:
        """Compare the current Windows import source with the last imported snapshot.

        Returns one of: current, source_changed, legacy, unavailable. No Git command
        is executed against the Windows-mounted source checkout.
        """
        backend = self.backend_factory(workspace.backend)
        if not isinstance(backend, WslBackend):
            return "unavailable", "source synchronization is only applicable to WSL workspaces"
        source = workspace.windows_source.strip()
        if not source:
            return "unavailable", "workspace.windows_source is empty"
        source_linux = backend.windows_to_linux_path(source)
        if not source_linux:
            return "unavailable", f"cannot translate Windows source path: {source}"
        target = backend.resolve_path(workspace.root)
        parent = str(Path(target).parent).replace("\\", "/")
        state_dir = f"{parent}/.koali-control-state"
        state_file = f"{state_dir}/{self._state_slug(workspace.workspace_id)}.source.sha256"
        script = f"""set -euo pipefail
SRC={shlex.quote(source_linux)}
STATE={shlex.quote(state_file)}
{self._fingerprint_function()}

test -d \"$SRC\" || {{ echo 'status=unavailable'; echo 'detail=configured Windows source directory does not exist'; exit 0; }}
SOURCE_FP=$(fingerprint_tree \"$SRC\")
if test ! -f \"$STATE\"; then
  echo 'status=legacy'
  echo \"source=$SOURCE_FP\"
  echo 'detail=no prior Control Panel source fingerprint; one safe refresh is required'
  exit 0
fi
IMPORTED_FP=$(tr -d '[:space:]' < \"$STATE\")
if test -n \"$IMPORTED_FP\" && test \"$SOURCE_FP\" = \"$IMPORTED_FP\"; then
  echo 'status=current'
  echo \"source=$SOURCE_FP\"
  echo 'detail=Windows source matches the last imported snapshot'
else
  echo 'status=source_changed'
  echo \"source=$SOURCE_FP\"
  echo \"imported=$IMPORTED_FP\"
  echo 'detail=Windows source changed since the last imported snapshot'
fi
"""
        probe = backend.capture_shell(script, timeout=120)
        if probe.code != 0:
            return "unavailable", probe.output.strip() or f"source fingerprint failed with exit {probe.code}"
        status = "unavailable"
        detail = "source synchronization status unavailable"
        for line in probe.output.splitlines():
            if line.startswith("status="):
                status = line.partition("=")[2].strip()
            elif line.startswith("detail="):
                detail = line.partition("=")[2].strip()
        return status, detail

    def preflight(self, workspace: Workspace) -> PreflightReport:
        backend = self.backend_factory(workspace.backend)
        checks = list(backend.preflight())
        if any(item.state in {CheckState.BLOCKED, CheckState.FAIL} and item.required for item in checks):
            return PreflightReport(tuple(checks))
        root = backend.resolve_path(workspace.root)
        probe = backend.capture_shell(
            f"""set +e
ROOT={shlex.quote(root)}
printf 'exists='; test -d "$ROOT" && echo yes || echo no
if test -d "$ROOT"; then
  cd "$ROOT" || exit 2
  test -e .git && echo 'repometa=ok' || echo 'repometa=missing'
  for f in pyproject.toml uv.lock .python-version; do test -f "$f" && echo "marker:$f=ok" || echo "marker:$f=missing"; done
  test -x .venv/bin/python && echo 'venv=ok' || echo 'venv=missing'
  test -x .git/hooks/pre-commit && echo 'precommit=ok' || echo 'precommit=missing'
fi
""",
            timeout=30,
        )
        out = probe.output
        exists = "exists=yes" in out
        checks.append(CheckResult("workspace", "Workspace root", CheckState.PASS if exists else CheckState.BLOCKED, root))
        if not exists:
            return PreflightReport(tuple(checks))
        if isinstance(backend, WslBackend) and workspace.profile == "developer-windows-wsl":
            linux_fs = not root.startswith("/mnt/")
            checks.append(CheckResult("filesystem", "Workspace filesystem", CheckState.PASS if linux_fs else CheckState.BLOCKED, "WSL Linux filesystem" if linux_fs else "Windows mount is not the canonical mutable workspace"))
        repometa = "repometa=ok" in out
        checks.append(CheckResult("repository_metadata", "Repository metadata", CheckState.PASS if repometa else CheckState.BLOCKED, ".git metadata present" if repometa else ".git metadata missing"))
        for marker in REQUIRED_MARKERS:
            ok = f"marker:{marker}=ok" in out
            checks.append(CheckResult(f"marker_{marker}", marker, CheckState.PASS if ok else CheckState.BLOCKED, "present" if ok else "missing"))
        venv_ok = "venv=ok" in out
        checks.append(CheckResult("venv", "Workspace .venv", CheckState.PASS if venv_ok else CheckState.BLOCKED, "workspace-local environment ready" if venv_ok else "missing/not initialized — PREPARE DEVELOPMENT ENVIRONMENT will run repository setup"))
        hook_ok = "precommit=ok" in out
        checks.append(CheckResult("precommit", "Pre-commit hook", CheckState.PASS if hook_ok else CheckState.WARN, "installed" if hook_ok else "not installed — repository setup will install it", required=False))
        # PREPARE intentionally does not inspect branch, index, staged files,
        # untracked files, or working-tree cleanliness. Git state belongs to the user.
        return PreflightReport(tuple(checks))

    def create_from_windows_source(
        self,
        workspace: Workspace,
        *,
        timeout: int = 1800,
        replace_existing: bool = False,
    ) -> int:
        """Import a Windows checkout into the canonical Linux workspace.

        The Windows-mounted checkout is treated strictly as an import source.
        PREPARE does not inspect branch, index, staged files, untracked files, or
        working-tree cleanliness. Git state belongs to the user.

        The Control Panel transfers the source tree as data into a staging directory,
        filters mutable/runtime/build artifacts, validates structural repository
        markers, and atomically installs the staged workspace. On refresh, the entire
        prior workspace is preserved as a sibling directory backup before replacement.
        """
        backend = self.backend_factory(workspace.backend)
        if not isinstance(backend, WslBackend):
            raise RuntimeError("Windows-source import is only supported by the WSL backend")
        source = workspace.windows_source.strip()
        if not source:
            raise RuntimeError("workspace.windows_source is empty")
        source_linux = backend.windows_to_linux_path(source)
        if not source_linux:
            raise RuntimeError(f"Cannot translate Windows source path: {source}")
        target = backend.resolve_path(workspace.root)
        excludes = self._exclude_args(IMPORT_EXCLUDES)
        fingerprint = self._fingerprint_function()
        replace = "yes" if replace_existing else "no"
        slug = self._state_slug(workspace.workspace_id)
        script = f"""set -euo pipefail
SRC={shlex.quote(source_linux)}
DST={shlex.quote(target)}
REPLACE={shlex.quote(replace)}
PARENT=$(dirname \"$DST\")
BASE=$(basename \"$DST\")
STAGE=\"$PARENT/.koali-import-${{BASE}}-$$\"
BACKUP_ROOT=\"$PARENT/.koali-control-backups\"
BACKUP=\"$BACKUP_ROOT/${{BASE}}-$(date -u +%Y%m%dT%H%M%SZ)-$$\"
STATE_DIR=\"$PARENT/.koali-control-state\"
STATE_FILE=\"$STATE_DIR/{slug}.source.sha256\"
{fingerprint}

cleanup_stage() {{
  rc=$?
  if test -d \"$STAGE\"; then rm -rf -- \"$STAGE\"; fi
  if test -d \"$BACKUP\" && test ! -e \"$DST\"; then mv -- \"$BACKUP\" \"$DST\"; fi
  exit $rc
}}
trap cleanup_stage EXIT INT TERM

test -d \"$SRC\" || {{ echo 'BLOCKED: configured Windows source directory does not exist'; exit 70; }}
test -e \"$SRC/.git\" || {{ echo 'BLOCKED: Windows source does not contain .git metadata'; exit 71; }}
if test -f \"$SRC/.git\"; then
  echo 'BLOCKED: linked Git worktree source is not supported for direct checkout import yet'
  exit 72
fi
SOURCE_DIGEST=$(fingerprint_tree \"$SRC\")

if test -e \"$DST\"; then
  if test \"$REPLACE\" = yes; then
    test -d \"$DST\" || {{ echo 'BLOCKED: existing workspace path is not a directory'; exit 73; }}
  elif test -d \"$DST\" && test -z \"$(find \"$DST\" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)\"; then
    echo 'Removing empty incomplete workspace from an earlier failed import'
    rm -rf -- \"$DST\"
  else
    echo 'BLOCKED: target workspace already exists; use Refresh from Windows source'
    exit 73
  fi
fi

mkdir -p -- \"$PARENT\"
rm -rf -- \"$STAGE\"
mkdir -p -- \"$STAGE\"

# Transfer the checkout as data. Only clearly local runtime/cache state is
# filtered. Build/dist/egg-info paths are preserved because they may be tracked
# repository content; DEBUG policy, not the importer, decides their severity.
tar -C \"$SRC\" \\
  {excludes} \\
  -cf - . | tar -C \"$STAGE\" -xf -

# Structural validation only. PREPARE does not inspect Git state.
test -e \"$STAGE/.git\" || {{ echo 'BLOCKED: imported source is missing .git metadata'; exit 74; }}
for f in pyproject.toml uv.lock .python-version; do
  test -f \"$STAGE/$f\" || {{ echo \"BLOCKED: imported source is missing $f\"; exit 75; }}
done

if test \"$REPLACE\" = yes && test -e \"$DST\"; then
  mkdir -p -- \"$BACKUP_ROOT\"
  mv -- \"$DST\" \"$BACKUP\"
  mv -- \"$STAGE\" \"$DST\"
  echo \"Previous workspace preserved: $BACKUP\"
else
  mv -- \"$STAGE\" \"$DST\"
fi
mkdir -p -- \"$STATE_DIR\"
printf '%s\\n' \"$SOURCE_DIGEST\" > \"$STATE_FILE\"
trap - EXIT INT TERM
echo \"Workspace imported: $DST\"
echo \"Source fingerprint recorded: $SOURCE_DIGEST\"
"""
        label = ("Refresh" if replace_existing else "Import") + f" workspace {workspace.workspace_id}"
        return backend.run_shell(script, label, timeout=timeout)
