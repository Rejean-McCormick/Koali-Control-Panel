from __future__ import annotations

from dataclasses import dataclass
import json
import re
import shlex
from typing import Callable, Iterable

from .backends import ExecutionBackend, WslBackend
from .config import QEMU_ENV_MAP, QEMU_PATH_FIELDS
from .models import Workspace


_WINDOWS_ABSOLUTE = re.compile(r"^[A-Za-z]:[\\/]")
_DISK_PROTOCOL = "koa.disk-image-backend.v1"

_SCOPE_CONTEXT_FIELDS: dict[str, tuple[str, ...]] = {
    "N08": (
        "expected_release_identity",
        "confinement_ready_regex",
        "general_surface_denied_regex",
        "privilege_path_denied_regex",
    ),
    "N09": (
        "active_profile",
        "navigation_surface_id",
        "navigation_ready_regex",
        "navigation_result_regex",
        "navigation_keys",
        "mediatheque_selection",
    ),
    "N10": (
        "expected_release_identity",
        "compositor_ready_regex",
        "session_ready_regex",
    ),
}

_MEDIATHEQUE_SELECTED_FIELDS: tuple[str, ...] = (
    "active_release_set",
    "mediatheque_artifact_ref",
    "mediatheque_offline_regex",
)


@dataclass(frozen=True, slots=True)
class QemuPreflightResult:
    ready: bool
    lines: tuple[str, ...]

    def text(self) -> str:
        return "\n".join(self.lines)


class QemuEnvironmentManager:
    """Own QEMU infrastructure/context convergence for Koali Control Panel.

    This class deliberately does not run kOA-Linux diagnostic tests. LevelUpDiag
    remains the diagnostic executor and verdict owner. Koali Control Panel only
    prepares QEMU infrastructure, stores/discovers repository-owned execution
    context, and reports operational readiness before delegation.
    """

    def __init__(
        self,
        config: dict,
        backend_factory: Callable[[str], ExecutionBackend],
        log: Callable[[str], None],
        build_image: Callable[[Workspace], int] | None = None,
    ) -> None:
        self.config = config
        self.backend_factory = backend_factory
        self.log = log
        self.build_image = build_image

    def backend(self, workspace: Workspace) -> ExecutionBackend:
        return self.backend_factory(workspace.backend)

    def qemu_config(self) -> dict:
        return self.config.setdefault("system_test", {}).setdefault("qemu", {})

    def _workspace_root(self, workspace: Workspace) -> str:
        return self.backend(workspace).resolve_path(workspace.root).rstrip("/")

    def _resolve_value(self, workspace: Workspace, key: str, raw: object) -> str:
        value = str(raw or "").strip()
        if not value or key not in QEMU_PATH_FIELDS:
            return value
        backend = self.backend(workspace)
        if isinstance(backend, WslBackend) and _WINDOWS_ABSOLUTE.match(value):
            return backend.windows_to_linux_path(value)
        if value.startswith("/"):
            return value
        return f"{self._workspace_root(workspace)}/{value.lstrip('/')}"

    def environment(self, workspace: Workspace) -> dict[str, str]:
        qemu = self.qemu_config()
        result: dict[str, str] = {}
        for env_name, config_name in QEMU_ENV_MAP.items():
            value = self._resolve_value(workspace, config_name, qemu.get(config_name, ""))
            if value:
                result[env_name] = value
        return result

    def _probe_runtime(self, workspace: Workspace) -> tuple[bool, bool, str]:
        backend = self.backend(workspace)
        script = r'''set +e
if command -v qemu-system-x86_64 >/dev/null 2>&1; then
  echo qemu=ok
else
  echo qemu=missing
fi
uefi=""
# Ubuntu 24.04/Noble ships 4M OVMF firmware names; keep older/common
# distro layouts as fallbacks so the preflight remains portable.
for p in \
  /usr/share/OVMF/OVMF_CODE_4M.fd \
  /usr/share/OVMF/OVMF_CODE.fd \
  /usr/share/OVMF/OVMF_CODE_4M.secboot.fd \
  /usr/share/OVMF/OVMF_CODE_4M.ms.fd \
  /usr/share/qemu/OVMF.fd \
  /usr/share/ovmf/OVMF.fd \
  /usr/share/qemu/OVMF_CODE.fd \
  /usr/share/edk2/x64/OVMF_CODE.fd \
  /usr/share/edk2/ovmf/OVMF_CODE.fd; do
  if [ -f "$p" ]; then uefi="$p"; break; fi
done
if [ -n "$uefi" ]; then
  echo "uefi=$uefi"
else
  echo uefi=missing
fi
'''
        result = backend.capture_shell(script, timeout=30)
        qemu_ok = any(line.strip() == "qemu=ok" for line in result.output.splitlines())
        uefi_line = next((line.strip() for line in result.output.splitlines() if line.startswith("uefi=")), "")
        uefi_ok = bool(uefi_line and uefi_line != "uefi=missing")
        return qemu_ok, uefi_ok, uefi_line.partition("=")[2] if uefi_ok else ""

    def _probe_regular_files(self, workspace: Workspace, paths: dict[str, str]) -> dict[str, bool]:
        if not paths:
            return {}
        backend = self.backend(workspace)
        commands = []
        for key, value in paths.items():
            commands.append(
                f"if test -f {shlex.quote(value)} && test ! -L {shlex.quote(value)}; "
                f"then echo {shlex.quote(key)}=ok; else echo {shlex.quote(key)}=missing; fi"
            )
        result = backend.capture_shell("\n".join(commands), timeout=30)
        observed: dict[str, bool] = {key: False for key in paths}
        for line in result.output.splitlines():
            key, sep, value = line.strip().partition("=")
            if sep and key in observed:
                observed[key] = value == "ok"
        return observed

    @staticmethod
    def _normalize_scopes(scopes: str | Iterable[str] | None) -> tuple[str, ...]:
        if scopes is None or scopes == "combined":
            return ("N08", "N09", "N10")
        if isinstance(scopes, str):
            values = (scopes.upper(),)
        else:
            values = tuple(str(item).upper() for item in scopes)
        if not values or any(value not in _SCOPE_CONTEXT_FIELDS for value in values):
            raise ValueError("QEMU preflight scopes must be N08, N09 and/or N10")
        return tuple(dict.fromkeys(values))

    def _repository_json(self, workspace: Workspace, body: str, *, timeout: int = 45) -> object | None:
        root = self._workspace_root(workspace)
        script = f"cd {shlex.quote(root)} && python3 - <<'PY'\n{body}\nPY\n"
        result = self.backend(workspace).capture_shell(script, timeout=timeout)
        if result.code != 0:
            return None
        candidates = [line.strip() for line in result.output.splitlines() if line.strip()]
        for line in reversed(candidates):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
        return None

    def image_package_info(self, workspace: Workspace) -> dict[str, object]:
        body = r'''import json, pathlib, tomllib
root = pathlib.Path.cwd()
path = root / "packaging/system/image.toml"
out = {"present": path.is_file(), "public_build_surface": (root / "tools/src/koa_tools/commands/build_image.py").is_file()}
if path.is_file():
    try:
        value = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
    else:
        out.update({
            "status": value.get("status", ""),
            "artifact_class_key": value.get("artifact_class_key", ""),
            "release_channel": value.get("release_channel", ""),
            "profile_contract": value.get("profile_contract", ""),
            "activation_ready": value.get("activation_ready"),
        })
        pipeline = value.get("build_pipeline")
        if isinstance(pipeline, dict):
            out["disk_backend_protocol"] = pipeline.get("disk_backend_protocol", "")
            out["activation_side_effects"] = pipeline.get("activation_side_effects")
            out["all_outputs_staged_inactive"] = pipeline.get("all_outputs_staged_inactive")
        else:
            out["build_pipeline"] = "missing"
print(json.dumps(out, separators=(",", ":"), sort_keys=True))'''
        value = self._repository_json(workspace, body)
        return value if isinstance(value, dict) else {}

    def canonical_image_candidates(self, workspace: Workspace) -> list[dict[str, object]]:
        body = rf'''import json, pathlib
root = pathlib.Path.cwd()
generated = root / "generated"
items = []
if generated.is_dir():
    for metadata in sorted(generated.rglob("*.build.json")):
        if not metadata.is_file() or metadata.is_symlink():
            continue
        try:
            value = json.loads(metadata.read_text(encoding="utf-8"))
        except Exception:
            continue
        backend = value.get("backend")
        if not isinstance(backend, dict):
            continue
        if value.get("artifact_class") != "system_image":
            continue
        if value.get("release_channel") != "system":
            continue
        if value.get("activation_authorized") is not False:
            continue
        if backend.get("protocol") != "{_DISK_PROTOCOL}":
            continue
        suffix = ".build.json"
        image = metadata.with_name(metadata.name[:-len(suffix)])
        if not image.is_file() or image.is_symlink() or image.stat().st_size <= 0:
            continue
        image_info = value.get("image") if isinstance(value.get("image"), dict) else {{}}
        items.append({{
            "image": image.resolve().as_posix(),
            "metadata": metadata.resolve().as_posix(),
            "profile_id": image_info.get("profile_id", ""),
            "image_version": image_info.get("image_version", ""),
            "backend_id": backend.get("backend_id", ""),
        }})
print(json.dumps(items, separators=(",", ":"), sort_keys=True))'''
        value = self._repository_json(workspace, body, timeout=60)
        return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []

    def effective_profile_candidates(self, workspace: Workspace) -> list[str]:
        body = r'''import json, pathlib
root = pathlib.Path.cwd()
generated = root / "generated"
items = []
if generated.is_dir():
    for path in sorted(generated.rglob("*.json")):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(value, dict):
            continue
        runtime = value.get("session_runtime")
        if not isinstance(runtime, dict):
            continue
        surfaces = runtime.get("surfaces")
        if not isinstance(surfaces, list) or not surfaces:
            continue
        if not all(isinstance(item, dict) and str(item.get("surface_id", "")).strip() for item in surfaces):
            continue
        items.append(path.resolve().as_posix())
print(json.dumps(items, separators=(",", ":")))'''
        value = self._repository_json(workspace, body, timeout=60)
        return [str(item) for item in value if isinstance(item, str)] if isinstance(value, list) else []

    def _repository_readiness_lines(self, workspace: Workspace, *, image_available: bool) -> list[str]:
        info = self.image_package_info(workspace)
        if not info:
            return ["INFO    repository image package: status unavailable"]
        lines: list[str] = []
        if info.get("present") is not True:
            lines.append("BLOCKED repository image package: packaging/system/image.toml missing")
            return lines
        status = str(info.get("status", "")).strip() or "unspecified"
        profile = str(info.get("profile_contract", "")).strip()
        activation_ready = info.get("activation_ready")
        blocked_status = status.startswith("blocked")
        prefix = "INFO   " if image_available or not blocked_status else "BLOCKED"
        lines.append(f"{prefix} repository image package: {status}")
        if profile:
            lines.append(f"INFO    repository image target profile: {profile}")
        if activation_ready is not None:
            lines.append(f"INFO    repository image activation_ready: {str(bool(activation_ready)).lower()}")
        if info.get("public_build_surface") is True:
            lines.append("PASS    public build-image surface: present")
        else:
            lines.append("BLOCKED public build-image surface: missing")
        if info.get("build_pipeline") == "missing":
            lines.append("INFO    repository image build_pipeline: not declared in packaging/system/image.toml")
        return lines

    def preflight(
        self,
        workspace: Workspace,
        scopes: str | Iterable[str] | None = None,
    ) -> QemuPreflightResult:
        selected_scopes = self._normalize_scopes(scopes)
        qemu = self.qemu_config()
        env = self.environment(workspace)
        lines: list[str] = []
        ready = True

        qemu_ok, uefi_ok, uefi_path = self._probe_runtime(workspace)
        if qemu_ok:
            lines.append("PASS    qemu-system-x86_64: available")
        else:
            lines.append("BLOCKED qemu-system-x86_64: missing")
            ready = False
        if uefi_ok:
            lines.append(f"PASS    UEFI firmware: {uefi_path}")
        else:
            lines.append("BLOCKED UEFI firmware: OVMF/EDK2 code image missing")
            ready = False

        image = env.get("KOA_QEMU_IMAGE", "")
        active_profile = env.get("KOA_QEMU_ACTIVE_PROFILE", "")
        release_set = env.get("KOA_QEMU_ACTIVE_RELEASE_SET", "")
        path_checks: dict[str, str] = {}
        if image:
            path_checks["image"] = image
        if "N09" in selected_scopes and active_profile:
            path_checks["active_profile"] = active_profile
        if (
            "N09" in selected_scopes
            and str(qemu.get("mediatheque_selection", "")).strip() == "selected"
            and release_set
        ):
            path_checks["active_release_set"] = release_set
        observed = self._probe_regular_files(workspace, path_checks)

        if not image:
            lines.append("BLOCKED QEMU image: not configured")
            ready = False
        elif observed.get("image", False):
            lines.append(f"PASS    QEMU image: {image}")
        else:
            lines.append(f"BLOCKED QEMU image: file not found: {image}")
            ready = False

        image_format = str(qemu.get("image_format", "raw")).strip().lower()
        if image_format in {"raw", "qcow2"}:
            lines.append(f"PASS    image format: {image_format}")
        else:
            lines.append(f"BLOCKED image format: {image_format or 'empty'}")
            ready = False

        network = str(qemu.get("network", "off")).strip().lower()
        if network in {"on", "off"}:
            lines.append(f"PASS    QEMU network policy: {network}")
        else:
            lines.append(f"BLOCKED QEMU network policy: {network or 'empty'}")
            ready = False

        fields: list[str] = []
        for scope in selected_scopes:
            for key in _SCOPE_CONTEXT_FIELDS[scope]:
                if key not in fields:
                    fields.append(key)
        for key in fields:
            value = str(qemu.get(key, "")).strip()
            if not value:
                lines.append(f"BLOCKED context.{key}: not configured")
                ready = False
                continue
            if key == "active_profile":
                if observed.get("active_profile", False):
                    lines.append(f"PASS    context.{key}: {active_profile}")
                else:
                    lines.append(f"BLOCKED context.{key}: file not found: {active_profile}")
                    ready = False
            else:
                lines.append(f"PASS    context.{key}: configured")

        if "N09" in selected_scopes:
            selection = str(qemu.get("mediatheque_selection", "")).strip()
            if selection and selection not in {"selected", "not_selected"}:
                lines.append(f"BLOCKED context.mediatheque_selection: invalid value {selection!r}")
                ready = False
            if selection == "selected":
                for key in _MEDIATHEQUE_SELECTED_FIELDS:
                    value = str(qemu.get(key, "")).strip()
                    if not value:
                        lines.append(f"BLOCKED context.{key}: required when Mediatheque is selected")
                        ready = False
                    elif key == "active_release_set":
                        if observed.get("active_release_set", False):
                            lines.append(f"PASS    context.{key}: {release_set}")
                        else:
                            lines.append(f"BLOCKED context.{key}: file not found: {release_set}")
                            ready = False
                    else:
                        lines.append(f"PASS    context.{key}: configured")

        repo_lines = self._repository_readiness_lines(
            workspace,
            image_available=bool(image and observed.get("image", False)),
        )
        lines.extend(repo_lines)
        if not image and any(line.startswith("BLOCKED repository image package:") for line in repo_lines):
            ready = False
        if any(line.startswith("BLOCKED public build-image surface:") for line in repo_lines):
            # The runtime can still be operationally usable with an existing image.
            if not image:
                ready = False

        lines.append("")
        label = "+".join(selected_scopes)
        suffix = "" if selected_scopes == ("N08", "N09", "N10") else f" [{label}]"
        lines.append(f"QEMU PREFLIGHT{suffix}: {'READY' if ready else 'NOT READY'}")
        return QemuPreflightResult(ready=ready, lines=tuple(lines))

    @staticmethod
    def _structured_build_output(config: dict) -> str:
        image_cfg = config.get("build", {}).get("image", {})
        args = image_cfg.get("args", [])
        if not isinstance(args, list):
            return ""
        values = [str(item) for item in args]
        for index, value in enumerate(values):
            if value == "--output" and index + 1 < len(values):
                return values[index + 1].strip()
            if value.startswith("--output="):
                return value.partition("=")[2].strip()
        return ""

    @staticmethod
    def _looks_like_obsolete_implementation_profile(value: str) -> bool:
        normalized = value.replace("\\", "/").lower()
        return (normalized.startswith("profiles/implementation-settings/") or "/profiles/implementation-settings/" in normalized) and normalized.endswith(".toml")

    def _auto_profile(self, workspace: Workspace) -> bool:
        qemu = self.qemu_config()
        current = str(qemu.get("active_profile", "")).strip()
        changed = False
        if current and self._looks_like_obsolete_implementation_profile(current):
            qemu["active_profile"] = ""
            current = ""
            changed = True
            self.log(
                "QEMU context: cleared obsolete implementation-settings TOML active_profile; "
                "N09 requires a generated effective-profile JSON"
            )
        if current:
            return changed

        candidates = self.effective_profile_candidates(workspace)
        if len(candidates) == 1:
            qemu["active_profile"] = candidates[0]
            self.log(f"QEMU context: effective profile detected: {candidates[0]}")
            return True
        if len(candidates) > 1:
            self.log(
                "QEMU context: multiple generated effective-profile JSON candidates found; "
                "active_profile remains explicit"
            )
        else:
            self.log("QEMU context: no generated effective-profile JSON with session_runtime.surfaces found")
        return changed

    def _resolve_build_output(self, workspace: Workspace, output: str) -> str:
        if output.startswith("/"):
            return output
        return f"{self._workspace_root(workspace)}/{output.lstrip('/')}"

    def _canonical_candidate_for(self, workspace: Workspace, image: str) -> dict[str, object] | None:
        target = image.rstrip("/")
        for item in self.canonical_image_candidates(workspace):
            if str(item.get("image", "")).rstrip("/") == target:
                return item
        return None

    def _set_image_from_candidate(self, candidate: dict[str, object]) -> bool:
        image = str(candidate.get("image", "")).strip()
        if not image:
            return False
        qemu = self.qemu_config()
        qemu["image"] = image
        if image.lower().endswith(".qcow2"):
            qemu["image_format"] = "qcow2"
        elif not str(qemu.get("image_format", "")).strip():
            qemu["image_format"] = "raw"
        self.log(f"QEMU context: canonical system image detected: {image}")
        return True

    def _auto_image(self, workspace: Workspace) -> tuple[bool, int]:
        qemu = self.qemu_config()
        if str(qemu.get("image", "")).strip():
            return False, 0

        output = self._structured_build_output(self.config)
        if output:
            image = self._resolve_build_output(workspace, output)
            candidate = self._canonical_candidate_for(workspace, image)
            if candidate is not None:
                return self._set_image_from_candidate(candidate), 0

            image_cfg = self.config.get("build", {}).get("image", {})
            if bool(image_cfg.get("enabled", False)) and self.build_image is not None:
                self.log("QEMU context: configured canonical image output is absent; invoking public build-image")
                rc = self.build_image(workspace)
                if rc != 0:
                    return False, rc
                candidate = self._canonical_candidate_for(workspace, image)
                if candidate is not None:
                    return self._set_image_from_candidate(candidate), 0
                self.log(
                    "QEMU context: build completed but the configured output is not backed by canonical "
                    "<image>.build.json system-image metadata"
                )

        candidates = self.canonical_image_candidates(workspace)
        if len(candidates) == 1:
            return self._set_image_from_candidate(candidates[0]), 0
        if len(candidates) > 1:
            self.log(
                "QEMU context: multiple canonical generated system images found; "
                "configure build.image --output or QEMU image explicitly"
            )
            return False, 0

        info = self.image_package_info(workspace)
        status = str(info.get("status", "")).strip()
        profile = str(info.get("profile_contract", "")).strip()
        if status:
            self.log(f"QEMU context: repository image package status: {status}")
        if profile:
            self.log(f"QEMU context: repository image target profile: {profile}")
        if not output:
            self.log(
                "QEMU context: no canonical generated system image exists and no structured "
                "build.image --output is configured"
            )
        return False, 0

    def _provision_wsl_qemu(self, workspace: Workspace) -> int:
        backend = self.backend(workspace)
        if not isinstance(backend, WslBackend):
            return 0
        qemu_ok, uefi_ok, _ = self._probe_runtime(workspace)
        if qemu_ok and uefi_ok:
            self.log("QEMU infrastructure already available")
            return 0

        provisioning = self.qemu_config().get("provisioning", {})
        if not bool(provisioning.get("enabled", True)):
            self.log("QEMU provisioning is disabled")
            return 78
        provider = str(provisioning.get("provider", "ubuntu_apt")).strip()
        if provider != "ubuntu_apt":
            self.log(f"Unsupported QEMU provisioner: {provider}")
            return 78
        packages = [
            str(item).strip()
            for item in provisioning.get(
                "apt_packages", ["qemu-system-x86", "qemu-utils", "ovmf"]
            )
            if str(item).strip()
        ]
        if not packages:
            self.log("QEMU provisioning has no configured apt packages")
            return 78
        script = (
            "set -euo pipefail; export DEBIAN_FRONTEND=noninteractive; "
            "apt-get update -qq; "
            "apt-get install -y -qq -o=Dpkg::Use-Pty=0 "
            + " ".join(shlex.quote(package) for package in packages)
        )
        return backend.run_root_shell(script, "Provision QEMU validation backend", timeout=1800)

    def prepare(
        self,
        workspace: Workspace,
        scopes: str | Iterable[str] | None = None,
    ) -> QemuPreflightResult:
        rc = self._provision_wsl_qemu(workspace)
        if rc != 0:
            self.log(f"QEMU environment preparation stopped during infrastructure provisioning (exit {rc})")
            return self.preflight(workspace, scopes=scopes)

        self._auto_profile(workspace)
        _changed, image_rc = self._auto_image(workspace)
        if image_rc != 0:
            self.log(f"QEMU environment preparation stopped during image build (exit {image_rc})")
            return self.preflight(workspace, scopes=scopes)

        result = self.preflight(workspace, scopes=scopes)
        if not result.ready:
            self.log(
                "QEMU infrastructure is prepared, but repository/runtime execution context is still incomplete. "
                "Koali Control Panel will not invent kOA-Linux semantic values."
            )
        return result
