from __future__ import annotations

import json
from pathlib import Path, PureWindowsPath
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
    "schema_version": 4,
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
        "current_focus": "core_stabilization",
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
            "commands": {
                "prepare": "if (-not (Get-Command pnpm -ErrorAction SilentlyContinue)) { corepack enable; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE } }; if (-not (Test-Path 'frontend\\node_modules')) { Push-Location 'frontend'; pnpm install; $rc=$LASTEXITCODE; Pop-Location; if ($rc -ne 0) { exit $rc } }; if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { Write-Error 'uv is required on Windows for the Konnaxion backend'; exit 78 }; Push-Location 'backend'; if (-not (Test-Path '.venv\\Scripts\\python.exe')) { uv venv .venv --python 3.12; if ($LASTEXITCODE -ne 0) { Pop-Location; exit $LASTEXITCODE } }; uv pip install --python '.venv\\Scripts\\python.exe' -r 'requirements\\local.txt'; $rc=$LASTEXITCODE; Pop-Location; exit $rc",
                "migrate": "Push-Location 'backend'; & '.\\.venv\\Scripts\\python.exe' manage.py migrate; $rc=$LASTEXITCODE; Pop-Location; exit $rc",
                "validate": "Push-Location 'backend'; & '.\\.venv\\Scripts\\python.exe' manage.py check; $rc=$LASTEXITCODE; Pop-Location; if ($rc -ne 0) { exit $rc }; Push-Location 'frontend'; pnpm run typecheck; $rc=$LASTEXITCODE; Pop-Location; exit $rc",
                "test": "Push-Location 'backend'; & '.\\.venv\\Scripts\\python.exe' -m pytest -q --create-db; $rc=$LASTEXITCODE; Pop-Location; if ($rc -ne 0) { exit $rc }; Push-Location 'frontend'; pnpm exec cross-env FORCE_COLOR=1 jest --runInBand; $rc=$LASTEXITCODE; Pop-Location; exit $rc",
                "build": "Push-Location 'frontend'; pnpm run build; $rc=$LASTEXITCODE; Pop-Location; exit $rc"
            },
            "environment": {
                "API_PROXY_BASE": "http://127.0.0.1:8000/api",
                "NEXT_PUBLIC_API_BASE": "/api",
                "PORT": "4300"
            },
            "open_url": "http://127.0.0.1:4300/",
            "health_url": "",
            "services": {
                "api": {
                    "label": "API",
                    "backend": "windows",
                    "root": "backend",
                    "marker": "manage.py",
                    "command": "& '.\\.venv\\Scripts\\python.exe' -m uvicorn config.asgi:application --host 127.0.0.1 --port 8000 --reload",
                    "environment": {},
                    "health_url": "http://127.0.0.1:8000/"
                },
                "web": {
                    "label": "Web",
                    "backend": "windows",
                    "root": "frontend",
                    "marker": "package.json",
                    "command": "pnpm exec cross-env FORCE_COLOR=1 next dev --turbo --hostname 127.0.0.1 --port 4300",
                    "environment": {
                        "API_PROXY_BASE": "http://127.0.0.1:8000/api",
                        "NEXT_PUBLIC_API_BASE": "/api",
                        "PORT": "4300"
                    },
                    "health_url": "http://127.0.0.1:4300/"
                }
            }
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
                "start": "pnpm run start"
            },
            "environment": {
                "KOALI_SPACES_PORT": "4173",
                "KOALI_SPACES_FRAME_SRC": "http://127.0.0.1:4300",
                "KOALI_SPACES_STATE_ROOT": r"C:\mycode\kOA-Linux\.koali-control-runtime\koali-spaces",
                "KOALI_SPACES_SURFACE_REGISTRY": r"C:\mycode\kOA-Linux\.koali-control-runtime\koali-spaces\surface-runtime.json"
            },
            "open_url": "http://127.0.0.1:4173/",
            "health_url": "http://127.0.0.1:4173/health"
        },
        "konnaxion-capsule-manager": {
            "label": "Konnaxion Capsule Manager",
            "enabled": True,
            "optional": True,
            "backend": "windows",
            "roots": [r"C:\mycode\Konnaxion\Konnaxion_Capsule_Manager"],
            "marker": "pyproject.toml",
            "commands": {
                "validate": "uv run python -m compileall -q kx_shared kx_agent kx_manager kx_builder kx_cli",
                "test": "uv run pytest -q"
            },
            "environment": {
                "KX_ROOT": r"C:\mycode\Konnaxion\runtime",
                "KX_SOURCE_DIR": r"C:\mycode\Konnaxion\Konnaxion",
                "KX_AGENT_HOST": "127.0.0.1",
                "KX_AGENT_PORT": "8765",
                "KX_MANAGER_HOST": "127.0.0.1",
                "KX_MANAGER_PORT": "8714"
            },
            "open_url": "http://127.0.0.1:8714/ui",
            "health_url": "",
            "services": {
                "agent": {
                    "label": "Agent",
                    "backend": "windows",
                    "root": ".",
                    "marker": "pyproject.toml",
                    "command": "uv run kx-agent run",
                    "environment": {},
                    "health_url": "http://127.0.0.1:8765/v1/health"
                },
                "manager": {
                    "label": "Manager",
                    "backend": "windows",
                    "root": ".",
                    "marker": "pyproject.toml",
                    "command": "uv run kx-manager --host 127.0.0.1 --port 8714",
                    "environment": {},
                    "health_url": "http://127.0.0.1:8714/ui"
                }
            }
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
            "health_url": ""
        }
    },
    "dev_stack": {
        "products": ["konnaxion", "koali-spaces"],
        "default_product_actions": ["validate", "build"],
        "product_actions": {
            "konnaxion": ["prepare", "migrate", "validate", "test", "build"],
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
        "koali_spaces_integration": {
            "enabled": True,
            "mode": "legacy_projection",
            "product_id": "koali-spaces",
            "state_root": r"C:\mycode\kOA-Linux\.koali-control-runtime\koali-spaces",
            "actions": {
                "activate": "",
                "deactivate": ""
            },
            "verify": {
                "modules": [
                    {
                        "module_id": "konnaxion",
                        "required": True,
                        "route": "/apps/konnaxion"
                    }
                ]
            },
            "legacy_projection": {
                "konnaxion_embed_base": "http://127.0.0.1:4300"
            }
        },
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
            "current_focus": "core_stabilization",
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


def normalize_v4_workflow_focus(config: dict[str, Any]) -> None:
    """Replace sequential phase terminology with a non-sequential current focus."""
    workflow = config.setdefault("workflow", {})
    if not isinstance(workflow, dict):
        return
    legacy_phase = workflow.pop("phase", None)
    if legacy_phase and not workflow.get("current_focus"):
        workflow["current_focus"] = legacy_phase
    workflow.setdefault("current_focus", "core_stabilization")


def normalize_v4_spaces_integration(config: dict[str, Any]) -> None:
    """Move the v3 Koali/Konnaxion pilot behind the generic integration boundary.

    The migration is lossless and intentionally keeps legacy projection mode
    explicit until the paired Koali Spaces repository exposes its canonical
    delegated activation action.
    """
    dev_stack = config.setdefault("dev_stack", {})
    if not isinstance(dev_stack, dict):
        return

    old = dev_stack.pop("koali_spaces_pilot", None)
    integration = dev_stack.setdefault("koali_spaces_integration", {})
    if not isinstance(integration, dict):
        integration = {}
        dev_stack["koali_spaces_integration"] = integration

    integration.setdefault("enabled", True)
    integration.setdefault("mode", "legacy_projection")
    integration.setdefault("product_id", "koali-spaces")
    actions = integration.setdefault("actions", {})
    if isinstance(actions, dict):
        actions.setdefault("activate", "")
        actions.setdefault("deactivate", "")
    verify = integration.setdefault("verify", {})
    if isinstance(verify, dict):
        verify.setdefault(
            "modules",
            [{"module_id": "konnaxion", "required": True, "route": "/apps/konnaxion"}],
        )
    legacy = integration.setdefault("legacy_projection", {})
    if not isinstance(legacy, dict):
        legacy = {}
        integration["legacy_projection"] = legacy

    if isinstance(old, dict):
        if "enabled" in old:
            integration["enabled"] = bool(old["enabled"])
        if old.get("state_root"):
            integration["state_root"] = old["state_root"]
        if old.get("konnaxion_embed_base"):
            legacy["konnaxion_embed_base"] = old["konnaxion_embed_base"]
        module_id = str(old.get("module_id", "")).strip()
        if module_id and isinstance(verify, dict):
            verify["modules"] = [{"module_id": module_id, "required": True, "route": f"/apps/{module_id}"}]

    integration.setdefault("state_root", r"C:\mycode\kOA-Linux\.koali-control-runtime\koali-spaces")
    legacy.setdefault("konnaxion_embed_base", "http://127.0.0.1:4300")


def normalize_v3_product_orchestration(config: dict[str, Any]) -> None:
    """Upgrade the 3.0.0 Konnaxion action list without clobbering custom workflows."""
    actions = config.setdefault("dev_stack", {}).setdefault("product_actions", {})
    legacy = ["validate", "test", "build"]
    if actions.get("konnaxion") == legacy:
        actions["konnaxion"] = ["prepare", "migrate", "validate", "test", "build"]

    # 3.0.1 used the repository pytest default, which includes --reuse-db.
    # A stale reused database can omit tables introduced by current migrations.
    products = config.setdefault("products", {})
    konnaxion = products.get("konnaxion")
    if isinstance(konnaxion, dict):
        commands = konnaxion.get("commands")
        if isinstance(commands, dict):
            current = str(commands.get("test", ""))
            if "-m pytest -q;" in current and "--create-db" not in current:
                current = current.replace("-m pytest -q;", "-m pytest -q --create-db;", 1)
            # 3.0.2 forwarded Jest args through `pnpm run test -- ...`; with pnpm 10
            # the separator reaches Jest and makes --runInBand a file-pattern argument.
            if "pnpm run test -- --runInBand" in current:
                current = current.replace(
                    "pnpm run test -- --runInBand",
                    "pnpm exec cross-env FORCE_COLOR=1 jest --runInBand",
                    1,
                )
            commands["test"] = current

        services = konnaxion.get("services")
        if isinstance(services, dict):
            web = services.get("web")
            if isinstance(web, dict):
                # 3.0.3 forwarded Next CLI options through `pnpm run dev -- ...`;
                # the separator reaches Next 15 and makes --hostname a project path.
                legacy_web = "pnpm run dev -- --hostname 127.0.0.1 --port 4300"
                if str(web.get("command", "")) == legacy_web:
                    web["command"] = "pnpm exec cross-env FORCE_COLOR=1 next dev --turbo --hostname 127.0.0.1 --port 4300"
                # Konnaxion has no public dedicated frontend health route in this snapshot;
                # app/_api is a private Next.js folder, so readiness uses the real root route.
                if str(web.get("health_url", "")) in {"", "http://127.0.0.1:4300/health", "http://127.0.0.1:4300/_api/health"}:
                    web["health_url"] = "http://127.0.0.1:4300/"

    integration = config.setdefault("dev_stack", {}).setdefault("koali_spaces_integration", {})
    integration.setdefault("enabled", True)
    integration.setdefault("mode", "legacy_projection")
    integration.setdefault("product_id", "koali-spaces")
    integration.setdefault("state_root", r"C:\mycode\kOA-Linux\.koali-control-runtime\koali-spaces")
    actions_cfg = integration.setdefault("actions", {})
    if isinstance(actions_cfg, dict):
        actions_cfg.setdefault("activate", "")
        actions_cfg.setdefault("deactivate", "")
    verify_cfg = integration.setdefault("verify", {})
    if isinstance(verify_cfg, dict):
        verify_cfg.setdefault(
            "modules",
            [{"module_id": "konnaxion", "required": True, "route": "/apps/konnaxion"}],
        )
    legacy_cfg = integration.setdefault("legacy_projection", {})
    if isinstance(legacy_cfg, dict):
        legacy_cfg.setdefault("konnaxion_embed_base", "http://127.0.0.1:4300")

    spaces = products.get("koali-spaces")
    if isinstance(spaces, dict):
        # 3.0.5 started Koali Spaces through the development presentation server.
        # The shell CSP is production-strict and the browser can remain on SSR
        # initial state if Next development hydration needs eval/refresh support.
        # Since the dev stack already builds and smoke-validates dist/runtime, run
        # that validated packaged runtime instead of weakening the CSP.
        commands = spaces.setdefault("commands", {})
        if isinstance(commands, dict) and str(commands.get("start", "")) == "pnpm run dev":
            commands["start"] = "pnpm run start"

        environment = spaces.setdefault("environment", {})
        if isinstance(environment, dict):
            state_root = str(integration.get("state_root", "")).strip()
            if state_root:
                environment["KOALI_SPACES_STATE_ROOT"] = state_root
                environment["KOALI_SPACES_SURFACE_REGISTRY"] = (
                    str(PureWindowsPath(state_root) / "surface-runtime.json")
                    if len(state_root) >= 3 and state_root[1] == ":" and state_root[2] in "\\/"
                    else str(Path(state_root) / "surface-runtime.json")
                )


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
        merged["schema_version"] = 4
        normalize_levelupdiag_campaigns(merged)
        normalize_v4_workflow_focus(merged)
        normalize_v4_spaces_integration(merged)
        normalize_v3_product_orchestration(merged)
        if migrated != raw or merged != raw:
            self.save(merged)
        return merged

    def save(self, config: dict[str, Any]) -> None:
        self.path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
