from __future__ import annotations

import json
from pathlib import Path
import sys

from koali_control import __version__
from koali_control.config import ConfigStore, PROFILES, RENDERERS
from koali_control.models import Workspace

APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "koali-control.json"


def self_test() -> int:
    cfg = ConfigStore(CONFIG_PATH).load()
    assert cfg.get("schema_version") == 3
    assert "products" in cfg and "konnaxion" in cfg["products"] and "koali-spaces" in cfg["products"]
    assert "dev_stack" in cfg and cfg["dev_stack"].get("products")
    default = cfg["environment"]["default_workspace"]
    assert default in cfg["workspaces"]
    ws = Workspace.from_config(default, cfg["workspaces"][default])
    assert ws.profile in PROFILES
    assert ws.assembly.get("renderer", "systemd") in RENDERERS
    assert ws.backend in cfg["backends"]
    assert "{home}" in ws.root or ws.root.startswith("/")
    workflow = cfg.get("workflow", {})
    assert workflow.get("phase") == "core_stabilization"
    assert workflow.get("qualification_scope") == "koali_core_pre_subsystem"
    assert workflow.get("final_profile") == "sovereign-linux-node"
    campaigns = cfg.get("diagnostics", {}).get("levelupdiag", {}).get("campaigns", {})
    assert campaigns.get("stabilization") == "stabilization"
    assert campaigns.get("stabilization_runtime") == "stabilization-runtime"
    print(f"Koali Control Panel {__version__} self-test: PASS")
    return 0


def print_config() -> int:
    print(json.dumps(ConfigStore(CONFIG_PATH).load(), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        raise SystemExit(self_test())
    if "--print-config" in sys.argv:
        raise SystemExit(print_config())
    from koali_control.app import ControlApp
    app = ControlApp(APP_DIR)
    app.mainloop()
