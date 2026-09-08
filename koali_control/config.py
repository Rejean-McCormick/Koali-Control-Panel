from __future__ import annotations

import json
from pathlib import Path
from typing import Any


PROFILES = (
    "user-lightweight",
    "developer-linux-workstation",
    "developer-windows-wsl",
    "sovereign-linux-node",
    "sovereign-hub",
    "build-farm",
    "control-plane",
    "high-assurance",
    "sovereign-offline",
    "appliance-shell",
)
RENDERERS = ("systemd", "quadlet", "compose", "kubernetes", "image", "offline-bundle")
OVERLAYS = ("high-assurance", "sovereign-offline", "appliance-shell")


# Canonical Control Panel -> kOA-Linux QEMU execution-context boundary.
# The Control Panel stores/provides these values; LevelUpDiag and kOA-Linux own diagnostic
# planning, validation semantics, findings and verdicts.
QEMU_ENV_MAP: dict[str, str] = {
    "KOA_QEMU_IMAGE": "image",
    "KOA_QEMU_IMAGE_FORMAT": "image_format",
    "KOA_QEMU_NETWORK": "network",
    "KOA_QEMU_EXPECTED_RELEASE_IDENTITY": "expected_release_identity",
    "KOA_QEMU_SESSION_READY_REGEX": "session_ready_regex",
    "KOA_QEMU_COMPOSITOR_READY_REGEX": "compositor_ready_regex",
    "KOA_QEMU_CONFINEMENT_READY_REGEX": "confinement_ready_regex",
    "KOA_QEMU_GENERAL_SURFACE_DENIED_REGEX": "general_surface_denied_regex",
    "KOA_QEMU_PRIVILEGE_PATH_DENIED_REGEX": "privilege_path_denied_regex",
    "KOA_QEMU_ACTIVE_PROFILE": "active_profile",
    "KOA_QEMU_NAVIGATION_SURFACE_ID": "navigation_surface_id",
    "KOA_QEMU_NAVIGATION_READY_REGEX": "navigation_ready_regex",
    "KOA_QEMU_NAVIGATION_RESULT_REGEX": "navigation_result_regex",
    "KOA_QEMU_NAVIGATION_KEYS": "navigation_keys",
    "KOA_QEMU_MEDIATHEQUE_SELECTION": "mediatheque_selection",
    "KOA_QEMU_ACTIVE_RELEASE_SET": "active_release_set",
    "KOA_QEMU_MEDIATHEQUE_ARTIFACT_REF": "mediatheque_artifact_ref",
    "KOA_QEMU_MEDIATHEQUE_OFFLINE_REGEX": "mediatheque_offline_regex",
    "KOA_QEMU_SEMANTIK_SELECTION": "semantik_selection",
    "KOA_QEMU_SEMANTIK_READY_REGEX": "semantik_ready_regex",
}

QEMU_PATH_FIELDS = frozenset({"image", "active_profile", "active_release_set"})

QEMU_UI_FIELDS: tuple[tuple[str, str], ...] = (
    ("image", "QEMU image"),
    ("image_format", "Image format"),
    ("network", "Network"),
    ("expected_release_identity", "Expected release identity"),
    ("session_ready_regex", "Session ready regex"),
    ("compositor_ready_regex", "Compositor ready regex"),
    ("confinement_ready_regex", "Confinement ready regex"),
    ("general_surface_denied_regex", "General surface denied regex"),
    ("privilege_path_denied_regex", "Privilege path denied regex"),
    ("active_profile", "Active profile file"),
    ("navigation_surface_id", "Navigation surface id"),
    ("navigation_ready_regex", "Navigation ready regex"),
    ("navigation_result_regex", "Navigation result regex"),
    ("navigation_keys", "Navigation keys (qcodes)"),
    ("mediatheque_selection", "Mediatheque selection"),
    ("active_release_set", "Active release set file"),
    ("mediatheque_artifact_ref", "Mediatheque artifact ref"),
    ("mediatheque_offline_regex", "Mediatheque offline regex"),
    ("semantik_selection", "SemantiK selection"),
    ("semantik_ready_regex", "SemantiK ready regex"),
)


DEFAULT_CONFIG: dict[str, Any] = {
    "schema_version": 3,
    "app": {
        "terminal_exe": "wt.exe",
        "editor_exe": "code",
        "command_timeout_seconds": 1800,
        "open_shell_on_start": True,
    },
    "environment": {
        "default_backend": "wsl",
        "default_workspace": "koa-linux-main",
        "prepare": {
            "auto_create_workspace": True,
            "auto_refresh_workspace_from_windows": True,
            "run_repository_setup": True,
        },
    },
    "backends": {
        "wsl": {
            "distribution": "Ubuntu-24.04",
            "expected_wsl_version": 2,
            "expected_distribution_id": "ubuntu",
            "expected_release": "24.04",
            "require_systemd": True,
            "required_systemd_units": [],
            "ignored_systemd_units": ["console-getty.service", "getty@tty1.service"],
            "shell": "bash",
            "toolchain_provisioning": {
                "enabled": True,
                "provider": "ubuntu_apt_pipx",
                "apt_packages": ["git", "python3", "python-is-python3", "pipx"],
                "uv_pipx_package": "uv",
                "rustup_apt_package": "rustup",
                "native_build_apt_packages": ["build-essential"],
                "prime_cargo_cache": True,
            },
        },
        "native_linux": {
            "enabled": True,
            "shell": "bash",
        },
        "windows": {
            "enabled": True,
            "shell": "powershell.exe",
        },
    },
    "workspaces": {
        "koa-linux-main": {
            "repository": "koa-linux",
            "backend": "wsl",
            "profile": "developer-windows-wsl",
            "root": "{home}/work/koa-linux",
            "windows_source": r"C:\mycode\kOA-Linux\koa-linux",
            "checkout_ref": "current",
            "bootstrap_on_start": False,
            "assembly": {
                "renderer": "systemd",
                "overlays": [],
                "output": "generated/koali/developer-windows-wsl-systemd",
            },
            "services": {
                "mode": "disabled",
                "engine": "docker",
                "compose_file": "dev/local-services/compose.yaml",
                "compose_profile": "local-services",
                "environment": {},
            },
        }
    },
    "diagnostics": {
        "debug": {
            "enabled": True,
            "architecture_formalities": "warn",
            "description": "Read-only local pipeline diagnosis; conformance findings never block DEBUG execution.",
        },
        "levelupdiag": {
            "enabled": True,
            "root": r"C:\mycode\kOA-Linux\LevelUpDiag-Koali",
            "campaigns": {
                "stabilization": "stabilization",
                "stabilization_runtime": "stabilization-runtime",
                "developer": "debug",
                "build": "stabilization",
                "run_all": "validation",
                "release": "release",
                "delivery": "delivery",
            },
        }
    },
    "workflow": {
        "phase": "core_stabilization",
        "qualification_scope": "koali_core_pre_subsystem",
        "final_profile": "sovereign-linux-node",
        "external_subsystems": {
            "konnaxion": "placeholder_until_integration",
            "ariane": "deferred_until_koali_integration_test",
            "orgo": "draft_not_admitted",
            "semantik_architect": "deferred_not_admitted",
        },
        "policy": "stabilize Koali environment and native core before subsystem integration; never fabricate source admission",
    },
    "products": {
        "konnaxion": {
            "label": "Konnaxion",
            "enabled": True,
            "optional": False,
            "backend": "windows",
            "roots": [r"C:\mycode\Konnaxion\Konnaxion", r"C:\mycode\kOA-Linux\Konnaxion"],
            "marker": "package.json",
            "commands": {},
            "environment": {"PORT": "4300"},
            "open_url": "http://127.0.0.1:4300/",
            "health_url": "http://127.0.0.1:4300/",
        },
        "koali-spaces": {
            "label": "Koali Spaces",
            "enabled": True,
            "optional": False,
            "backend": "windows",
            "roots": [r"C:\mycode\kOA-Linux\koali-spaces"],
            "marker": "package.json",
            "commands": {
                "validate": "pnpm run validate",
                "build": "pnpm run build",
                "smoke": "pnpm run smoke:runtime",
                "start": "pnpm run dev",
            },
            "environment": {
                "KOALI_SPACES_PORT": "4173",
                "KOALI_SPACES_FRAME_SRC": "http://127.0.0.1:4300",
            },
            "open_url": "http://127.0.0.1:4173/",
            "health_url": "http://127.0.0.1:4173/health",
        },
        "orgo": {
            "label": "Orgo",
            "enabled": False,
            "optional": True,
            "backend": "wsl",
            "roots": [],
            "marker": "package.json",
            "commands": {},
            "environment": {},
            "open_url": "",
            "health_url": "",
        },
    },
    "dev_stack": {
        "products": ["konnaxion", "koali-spaces"],
        "default_product_actions": ["validate", "build"],
        "product_actions": {
            "konnaxion": ["validate", "test", "build"],
            "koali-spaces": ["validate", "build", "smoke"],
        },
        "gates": [
            {
                "id": "konnaxion-koali-adapter",
                "label": "Koali ↔ Konnaxion adapter",
                "workspace": "koa-linux-main",
                "command": "uv run --frozen pytest -q integrations/konnaxion/tests",
                "enabled": True,
            }
        ],
        "startup_timeout_seconds": 45,
        "command_timeout_seconds": 1800,
    },
    "build": {
        "image": {
            "enabled": False,
            "args": [],
            "custom_command": "",
        },
        "full_cycle_requires_image": False,
    },
    "system_test": {
        "qemu": {
            "image": "",
            "image_format": "raw",
            "network": "off",
            "expected_release_identity": "",
            "session_ready_regex": "",
            "compositor_ready_regex": "",
            "confinement_ready_regex": "",
            "general_surface_denied_regex": "",
            "privilege_path_denied_regex": "",
            "active_profile": "",
            "navigation_surface_id": "",
            "navigation_ready_regex": "",
            "navigation_result_regex": "",
            "navigation_keys": "",
            "mediatheque_selection": "",
            "active_release_set": "",
            "mediatheque_artifact_ref": "",
            "mediatheque_offline_regex": "",
            "semantik_selection": "selected",
            "semantik_ready_regex": "",
            "provisioning": {
                "enabled": True,
                "provider": "ubuntu_apt",
                "apt_packages": ["qemu-system-x86", "qemu-utils", "ovmf"],
            },
        }
    },
}


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = json.loads(json.dumps(base))
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def migrate_v1(data: dict[str, Any]) -> dict[str, Any]:
    """Losslessly map the old mono-workspace launcher config into schema v2."""
    if int(data.get("schema_version", 1)) >= 2:
        return data
    workspace_id = "koa-linux-main"
    service_data = dict(data.get("services", {}))
    workspace = {
        "repository": "koa-linux",
        "backend": "wsl",
        "profile": data.get("profile", "developer-windows-wsl"),
        "root": data.get("repo_wsl", "{home}/work/koa-linux"),
        "windows_source": data.get("repo_windows", r"C:\mycode\kOA-Linux\koa-linux"),
        "checkout_ref": "current",
        "bootstrap_on_start": bool(data.get("bootstrap_on_start", False)),
        "assembly": dict(data.get("assembly", {})),
        "services": {
            "mode": "custom" if any(service_data.values()) else "disabled",
            "custom": service_data,
            "engine": "docker",
            "compose_file": "dev/local-services/compose.yaml",
            "compose_profile": "local-services",
            "environment": {},
        },
    }
    return {
        "schema_version": 2,
        "app": {
            "terminal_exe": data.get("terminal_exe", "wt.exe"),
            "editor_exe": "code",
            "command_timeout_seconds": data.get("command_timeout_seconds", 1800),
            "open_shell_on_start": True,
        },
        "environment": {
            "default_backend": "wsl",
            "default_workspace": workspace_id,
            "prepare": {"auto_create_workspace": True, "auto_refresh_workspace_from_windows": True, "run_repository_setup": True},
        },
        "backends": {
            "wsl": {
                "distribution": data.get("wsl_distro", "Ubuntu-24.04"),
                "expected_wsl_version": 2,
                "expected_distribution_id": "ubuntu",
                "expected_release": "24.04",
                "require_systemd": True,
                "required_systemd_units": [],
                "ignored_systemd_units": ["console-getty.service", "getty@tty1.service"],
                "shell": "bash",
                "toolchain_provisioning": {
                    "enabled": True,
                    "provider": "ubuntu_apt_pipx",
                    "apt_packages": ["git", "python3", "python-is-python3", "pipx"],
                    "uv_pipx_package": "uv",
                    "rustup_apt_package": "rustup",
                    "native_build_apt_packages": ["build-essential"],
                    "prime_cargo_cache": True,
                },
            },
            "native_linux": {"enabled": True, "shell": "bash"},
        },
        "workspaces": {workspace_id: workspace},
        "diagnostics": {
            "debug": {"enabled": True, "architecture_formalities": "warn"},
            "levelupdiag": {
                "enabled": True,
                "root": r"C:\mycode\kOA-Linux\LevelUpDiag-Koali",
                "campaigns": {
                    "stabilization": "stabilization",
                    "stabilization_runtime": "stabilization-runtime",
                    "developer": "debug",
                    "build": "stabilization",
                    "run_all": "validation",
                    "release": "release",
                    "delivery": "delivery",
                },
            }
        },
        "workflow": {
            "phase": "core_stabilization",
            "qualification_scope": "koali_core_pre_subsystem",
            "final_profile": "sovereign-linux-node",
            "external_subsystems": {
                "konnaxion": "placeholder_until_integration",
                "ariane": "deferred_until_koali_integration_test",
                "orgo": "draft_not_admitted",
                "semantik_architect": "deferred_not_admitted",
            },
            "policy": "stabilize Koali environment and native core before subsystem integration; never fabricate source admission",
        },
        "build": {
            "image": {
                "enabled": bool(str(data.get("build_image_command", "")).strip()),
                "args": [],
                "custom_command": str(data.get("build_image_command", "")),
            },
            "full_cycle_requires_image": False,
        },
        "system_test": {"qemu": dict(data.get("qemu", {}))},
    }


_LEGACY_LEVELUPDIAG_CAMPAIGNS = {
    "developer-fast": "debug",
    "bundle-validation": "stabilization",
    "nightly": "validation",
    "release-preparation": "release",
    "delivery-check": "delivery",
}


def normalize_levelupdiag_campaigns(config: dict[str, Any]) -> None:
    campaigns = config.setdefault("diagnostics", {}).setdefault("levelupdiag", {}).setdefault("campaigns", {})
    for role, value in list(campaigns.items()):
        mapped = _LEGACY_LEVELUPDIAG_CAMPAIGNS.get(str(value))
        if mapped:
            campaigns[role] = mapped
    campaigns.setdefault("stabilization", "stabilization")
    campaigns.setdefault("stabilization_runtime", "stabilization-runtime")



class ConfigStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            self.save(DEFAULT_CONFIG)
            return json.loads(json.dumps(DEFAULT_CONFIG))
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Invalid Control Panel config {self.path}: {exc}") from exc
        migrated = migrate_v1(raw)
        merged = deep_merge(DEFAULT_CONFIG, migrated)
        merged["schema_version"] = 3
        normalize_levelupdiag_campaigns(merged)
        if migrated != raw or merged != deep_merge(DEFAULT_CONFIG, raw):
            self.save(merged)
        return merged

    def save(self, config: dict[str, Any]) -> None:
        self.path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
