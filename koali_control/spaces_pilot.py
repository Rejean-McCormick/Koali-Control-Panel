from __future__ import annotations

import json
from pathlib import Path
from typing import Callable


class LegacyKoaliSpacesPilotState:
    """Compatibility-only local Koali+Konnaxion projection used by the strangler path.

    The files produced here are development-only presentation/runtime inputs for
    Koali Spaces. They do not grant kOA capabilities and are intentionally kept
    outside the source repositories.
    """

    def __init__(self, config: dict, log: Callable[[str], None]) -> None:
        self.config = config
        self.log = log

    def reload(self, config: dict) -> None:
        self.config = config

    def cfg(self) -> dict:
        dev_stack = self.config.get("dev_stack", {})
        if not isinstance(dev_stack, dict):
            return {}
        integration = dev_stack.get("koali_spaces_integration", {})
        if isinstance(integration, dict):
            legacy = integration.get("legacy_projection", {})
            if isinstance(legacy, dict):
                merged = dict(legacy)
                if "state_root" not in merged and integration.get("state_root"):
                    merged["state_root"] = integration.get("state_root")
                return merged
        value = dev_stack.get("koali_spaces_pilot", {})
        return value if isinstance(value, dict) else {}

    def enabled(self) -> bool:
        return bool(self.cfg().get("enabled", True))

    def state_root(self) -> Path:
        raw = str(self.cfg().get("state_root", "")).strip()
        if not raw:
            raise RuntimeError("Koali Spaces legacy projection state_root is required")
        return Path(raw)

    @staticmethod
    def _route(route_id: str, path: str, label: str, entrypoint: str) -> dict:
        return {
            "route_id": route_id,
            "module_id": "space_home",
            "path": path,
            "page_ref": f"koa-spaces://page/{entrypoint}",
            "default_label": label,
            "label_key": route_id,
            "availability": "always",
            "offline_behavior": "available" if route_id in {"space_home.home", "space_home.offline"} else "cached_read_only",
            "deep_link_allowed": True,
            "safe_fallback_route_id": None if route_id == "space_home.home" else "space_home.home",
            "aliases": [],
            "capability_policy": {"required_capabilities": [], "denied_behavior": "access_denied"},
            "surface": {"kind": "local_shell_page", "origin_policy": "same_origin", "entrypoint": entrypoint},
        }

    @classmethod
    def _home_manifest(cls) -> dict:
        routes = [
            cls._route("space_home.home", "/", "Home", "home"),
            cls._route("space_home.search", "/search", "Search", "search"),
            cls._route("space_home.tasks", "/tasks", "Tasks", "tasks"),
            cls._route("space_home.offline", "/offline", "Offline", "offline"),
            cls._route("space_home.health", "/health", "Interface health", "health"),
            cls._route("space_home.settings", "/settings", "Settings", "settings"),
        ]
        return {
            "manifest_id": "koa-spaces.global-widgets",
            "manifest_version": "1.0.0",
            "module_id": "space_home",
            "public_name": "Home",
            "home_route_id": "space_home.home",
            "required_capabilities": [],
            "routes": routes,
            "sidebar": {
                "module_id": "space_home",
                "visible_depth": 2,
                "items": [
                    {"item_id": "space_home.home", "label": "Home", "label_key": "space_home.home", "order": 0, "route_id": "space_home.home"},
                    {"item_id": "space_home.search", "label": "Search", "label_key": "space_home.search", "order": 10, "route_id": "space_home.search"},
                    {"item_id": "space_home.tasks", "label": "Tasks", "label_key": "space_home.tasks", "order": 20, "route_id": "space_home.tasks"},
                    {
                        "item_id": "space_home.system",
                        "label": "System",
                        "label_key": "space_home.system",
                        "order": 30,
                        "children": [
                            {"item_id": "space_home.offline", "label": "Offline", "label_key": "space_home.offline", "order": 0, "route_id": "space_home.offline"},
                            {"item_id": "space_home.health", "label": "Interface health", "label_key": "space_home.health", "order": 10, "route_id": "space_home.health"},
                            {"item_id": "space_home.settings", "label": "Settings", "label_key": "space_home.settings", "order": 20, "route_id": "space_home.settings"},
                        ],
                    },
                ],
            },
            "topbar_widgets": [],
            "localization_refs": [],
            "offline_behavior": {"module_state": "available", "fallback_route_id": "space_home.home"},
            "authority_boundary": {
                "presentation_only": True,
                "may_grant_capabilities": False,
                "direct_domain_writes": False,
                "menu_visibility_is_authorization": False,
            },
        }

    @staticmethod
    def _konnaxion_manifest() -> dict:
        return {
            "manifest_id": "konnaxion.interface",
            "manifest_version": "1.0.0",
            "module_id": "konnaxion",
            "public_name": "Konnaxion",
            "home_route_id": "konnaxion.home",
            "required_capabilities": [],
            "routes": [
                {
                    "route_id": "konnaxion.home",
                    "module_id": "konnaxion",
                    "path": "/",
                    "page_ref": "konnaxion://home",
                    "default_label": "Konnaxion",
                    "label_key": "konnaxion.home",
                    "availability": "always",
                    "offline_behavior": "degraded",
                    "deep_link_allowed": True,
                    "safe_fallback_route_id": None,
                    "aliases": [],
                    "capability_policy": {"required_capabilities": [], "denied_behavior": "access_denied"},
                    "surface": {"kind": "local_module_surface", "origin_policy": "registered_local_origin"},
                }
            ],
            "sidebar": {"module_id": "konnaxion", "visible_depth": 2, "items": []},
            "topbar_widgets": [],
            "localization_refs": [],
            "offline_behavior": {"module_state": "degraded", "fallback_route_id": "konnaxion.home"},
            "authority_boundary": {
                "presentation_only": True,
                "may_grant_capabilities": False,
                "direct_domain_writes": False,
                "menu_visibility_is_authorization": False,
            },
        }

    @classmethod
    def shell_state(cls) -> dict:
        return {
            "state": "ready",
            "network_state": "online",
            "active_space_id": "koali_dev",
            "active_space": {
                "space_id": "koali_dev",
                "title": "Koali Development",
                "version": "1.0.0",
                "default_module_id": "space_home",
                "module_instances": [
                    {"module_id": "space_home", "manifest_ref": "global-widgets.json", "enabled": True, "required": True, "order": 0, "public_label": "Home"},
                    {"module_id": "konnaxion", "manifest_ref": "konnaxion.json", "enabled": True, "required": False, "order": 10, "public_label": "Konnaxion"},
                ],
                "global_topbar": [],
                "appearance": {
                    "theme_ref": "themes/default.json",
                    "density": "comfortable",
                    "design_system_id": "koali.ant5",
                    "theme_version": "1.0.0",
                },
                "offline_policy": {
                    "shell_available": True,
                    "retain_last_validated_definition": True,
                    "unavailable_module_behavior": "show_declared_fallback",
                    "network_state_indicator": True,
                    "public_cdn_required": False,
                    "remote_runtime_assets_required": False,
                },
                "authority_boundary": {
                    "presentation_only": True,
                    "may_grant_capabilities": False,
                    "contains_business_state": False,
                    "contains_executable_extension": False,
                },
            },
            "active_theme": {
                "theme_id": "koa_spaces.default",
                "version": "1.0.0",
                "design_system_id": "koali.ant5",
                "tokens": {
                    "primary_accent": "#1e6864",
                    "density": "comfortable",
                    "radius_scale": "v1",
                    "spacing_scale": "v1",
                    "typography_scale": "v1",
                    "focus_style": "visible",
                    "surface_family": "neutral",
                },
            },
            "modules": [cls._home_manifest(), cls._konnaxion_manifest()],
            "active_module_id": "space_home",
            "active_route_id": "space_home.home",
            "capabilities": [],
            "reason": "Control Panel local development pilot; non-authoritative presentation projection",
        }

    def surface_registry(self, runtime_state: str = "starting", health_state: str = "unknown") -> dict:
        embed_base = str(self.cfg().get("konnaxion_embed_base", "http://127.0.0.1:4300")).rstrip("/")
        return {
            "schemaVersion": 1,
            "runtimeRegistrations": [
                {
                    "registrationId": "konnaxion.web",
                    "moduleId": "konnaxion",
                    "adapter": "web_app",
                    "runtimeRef": "service:konnaxion-web",
                    "healthRef": "health:konnaxion-web",
                    "transportProfileRef": "transport:konnaxion",
                    "offlineClass": "local_optional",
                    "embedPolicy": "supported",
                }
            ],
            "presentationPolicies": [
                {
                    "moduleId": "konnaxion",
                    "allowedModes": ["framed", "immersive"],
                    "defaultMode": "framed",
                    "chromeProfile": "minimal",
                    "accentTokenRef": "module.konnaxion",
                }
            ],
            "resolvedTargets": [
                {
                    "transportProfileRef": "transport:konnaxion",
                    "moduleId": "konnaxion",
                    "embedBase": embed_base,
                    "iframeTitle": "Konnaxion",
                    "sandboxTokens": ["allow-scripts", "allow-forms", "allow-same-origin"],
                    "browserPermissions": [],
                }
            ],
            "runtimeObservations": [{"runtimeRef": "service:konnaxion-web", "state": runtime_state}],
            "healthObservations": [{"healthRef": "health:konnaxion-web", "state": health_state}],
        }

    def write(self, runtime_state: str = "starting", health_state: str = "unknown") -> bool:
        if not self.enabled():
            return True
        try:
            root = self.state_root()
            root.mkdir(parents=True, exist_ok=True)
            (root / "active-state.json").write_text(
                json.dumps(self.shell_state(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            (root / "surface-runtime.json").write_text(
                json.dumps(self.surface_registry(runtime_state, health_state), indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            self.log(f"Koali pilot state: Home + Konnaxion admitted ({runtime_state}/{health_state})")
            return True
        except OSError as exc:
            self.log(f"Koali pilot state could not be written: {exc}")
            return False


    def clear(self) -> bool:
        if not self.enabled():
            return True
        ok = True
        try:
            root = self.state_root()
            for name in ("active-state.json", "surface-runtime.json"):
                try:
                    (root / name).unlink()
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    self.log(f"Koali pilot cleanup failed for {name}: {exc}")
                    ok = False
            if ok:
                self.log("Koali pilot state cleared; Spaces falls back to Home-only development state")
        except OSError as exc:
            self.log(f"Koali pilot cleanup failed: {exc}")
            return False
        return ok


# Backward-compatible import name for external scripts/tests during the strangler migration.
KoaliSpacesPilotState = LegacyKoaliSpacesPilotState
