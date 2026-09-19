from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import shlex
import subprocess
import threading
import time
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk
from tkinter.scrolledtext import ScrolledText

from . import __version__
from .backends import ExecutionBackend, NativeLinuxBackend, WindowsBackend, WslBackend
from .config import ConfigStore, OVERLAYS, PROFILES, QEMU_UI_FIELDS, RENDERERS
from .levelupdiag import LevelUpDiagAdapter
from .debugdiag import DebugDiagnosticsRunner
from .models import CheckState, PreflightReport, Workspace
from .orchestration import Orchestrator
from .process import ProcessRunner
from .products import ProductRegistry
from .supervisor import ProcessSupervisor
from .devstack import DevStackOrchestrator
from .qemu import QemuEnvironmentManager
from .workspaces import WorkspaceManager


APP_NAME = f"Koali Control Panel {__version__}"


class ControlApp(tk.Tk):
    def __init__(self, app_dir: Path) -> None:
        super().__init__()
        self.app_dir = app_dir
        self.config_path = app_dir / "koali-control.json"
        self.log_dir = app_dir / "logs"
        self.log_path = self.log_dir / "koali-control.log"
        self.store = ConfigStore(self.config_path)
        self.config_data = self.store.load()
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.runner = ProcessRunner(self.log)
        self.supervisor = ProcessSupervisor(self.log)
        self.backends: dict[str, ExecutionBackend] = {}
        self.workspace_manager = WorkspaceManager(self.config_data, self.backend_factory)
        self.orchestrator = Orchestrator(self.config_data, self.workspace_manager, self.backend_factory, self.log)
        self.products = ProductRegistry(self.config_data, self.backend_factory, self.supervisor, self.log)
        self.dev_stack = DevStackOrchestrator(
            self.config_data, self.workspace_manager, self.backend_factory, self.orchestrator,
            self.products, self.supervisor, self.log,
        )
        self.levelupdiag = LevelUpDiagAdapter(
            self.config_data,
            self.backend_factory,
            self.log,
            on_report=lambda text: self.events.put(("diagnostics_detail", text)),
        )
        self.debugdiag = DebugDiagnosticsRunner(
            self.config_data,
            self.backend_factory,
            self.log,
            on_report=lambda text: self.events.put(("diagnostics_detail", text)),
        )
        self.qemu_manager = QemuEnvironmentManager(
            self.config_data,
            self.backend_factory,
            self.log,
            build_image=self.orchestrator.build_image,
        )
        self.busy = False
        self._status_refresh_lock = threading.Lock()
        self._dev_refresh_lock = threading.Lock()
        self.status_vars: dict[str, tk.StringVar] = {}
        self.setting_vars: dict[str, tk.Variable] = {}
        self.last_preflight: PreflightReport | None = None
        self.title(APP_NAME)
        self.geometry("1240x860")
        self.minsize(1020, 720)
        self._styles()
        self._build_ui()
        self.log(f"Koali Control Panel {__version__} started")
        self.after(100, self._drain_events)
        self.after(300, self.refresh_status_async)

    def backend_factory(self, backend_id: str) -> ExecutionBackend:
        if backend_id in self.backends:
            return self.backends[backend_id]
        app_cfg = self.config_data.get("app", {})
        if backend_id == "wsl":
            backend = WslBackend(self.config_data.get("backends", {}).get("wsl", {}), self.runner, app_cfg)
        elif backend_id == "native_linux":
            backend = NativeLinuxBackend(self.config_data.get("backends", {}).get("native_linux", {}), self.runner, app_cfg)
        elif backend_id == "windows":
            backend = WindowsBackend(self.config_data.get("backends", {}).get("windows", {}), self.runner, app_cfg)
        else:
            raise RuntimeError(f"Unknown execution backend: {backend_id}")
        self.backends[backend_id] = backend
        return backend

    def _styles(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure("Title.TLabel", font=("Segoe UI", 16, "bold"))
        style.configure("Section.TLabel", font=("Segoe UI", 11, "bold"))
        style.configure("Hero.TButton", font=("Segoe UI", 10, "bold"), padding=(13, 11))
        style.configure("Action.TButton", padding=(9, 7))
        style.configure("Status.TLabel", font=("Segoe UI", 9, "bold"))

    def _build_ui(self) -> None:
        pane = ttk.Panedwindow(self, orient=tk.VERTICAL)
        pane.pack(fill=tk.BOTH, expand=True)
        top, bottom = ttk.Frame(pane), ttk.Frame(pane)
        pane.add(top, weight=5)
        pane.add(bottom, weight=2)
        self.notebook = ttk.Notebook(top)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=8, pady=(8, 4))
        self._build_home()
        self._build_workspaces()
        self._build_development()
        self._build_dev_stack()
        self._build_diagnostics()
        self._build_build()
        self._build_system_test()
        self._build_environment()
        self._build_settings()
        self._build_log(bottom)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(1500, self._refresh_dev_stack_status)

    def _tab(self, title: str) -> ttk.Frame:
        f = ttk.Frame(self.notebook, padding=14)
        self.notebook.add(f, text=title)
        return f

    def _button_grid(self, frame: ttk.Frame, actions: list[tuple[str, object]], columns: int = 3) -> None:
        for idx, (label, command) in enumerate(actions):
            r, c = divmod(idx, columns)
            ttk.Button(frame, text=label, command=command, style="Action.TButton").grid(row=r, column=c, sticky="ew", padx=4, pady=4)
        for c in range(columns):
            frame.columnconfigure(c, weight=1)

    def _workspace_ids(self) -> list[str]:
        return self.workspace_manager.ids()

    def active_workspace_id(self) -> str:
        if hasattr(self, "workspace_var") and self.workspace_var.get() in self._workspace_ids():
            return self.workspace_var.get()
        return self.workspace_manager.default_id()

    def active_workspace(self) -> Workspace:
        return self.workspace_manager.get(self.active_workspace_id())

    def workflow_focus(self) -> str:
        return str(self.config_data.get("workflow", {}).get("current_focus", "core_stabilization"))

    def workflow_scope(self) -> str:
        return str(self.config_data.get("workflow", {}).get("qualification_scope", "koali_core_pre_subsystem"))

    def final_profile(self) -> str:
        value = str(self.config_data.get("workflow", {}).get("final_profile", "sovereign-linux-node"))
        return value if value in PROFILES else "sovereign-linux-node"

    def deferred_subsystems_text(self) -> str:
        states = self.config_data.get("workflow", {}).get("external_subsystems", {})
        if not isinstance(states, dict):
            return ""
        return ", ".join(f"{name}={state}" for name, state in states.items())

    def _build_home(self) -> None:
        f = self._tab("Home")
        ttk.Label(f, text=f"Koali Control Panel · v{__version__}", style="Title.TLabel").grid(row=0, column=0, columnspan=4, sticky="w")
        ttk.Label(
            f,
            text="Core-first stabilization. Stabilize Koali environment/contracts/native components before integrating independent subsystems; final-target VALIDATION/RELEASE remain strict.",
            wraplength=1100,
        ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(2, 12))
        meta = [
            ("STABILIZE KOALI CORE", self.stabilize_core, "Prepare the development environment, then run the LevelUpDiag pre-subsystem stabilization campaign."),
            ("START DEVELOPMENT", self.start_development, "Prepare if needed, activate the workspace, then open the shell if configured."),
            ("BRING KOALI TO READY", self.bring_koali_to_ready, "Stabilize the core, qualify the Koali↔Konnaxion boundary, build products, start the dev stack, then open Koali."),
            ("START KOALI DEV STACK", self.start_dev_stack, "Run integration gates, validate/build configured products, start managed runtimes and health-check them."),
            ("DAILY CORE CYCLE", self.daily_cycle, "Start Development -> core stabilization diagnostics -> open editor."),
            ("DEBUG PRINCIPAL", self.run_debug, "Broader read-only pipeline diagnosis. Final-profile subsystem blockers stay visible but do not redefine core stabilization."),
            ("CORE RUNTIME DIAGNOSTICS", self.run_stabilization_runtime, "Run the pre-subsystem runtime campaign when image/QEMU context exists."),
            ("VALIDATION (FINAL TARGET)", self.run_all, "Strict non-release validation of the complete declared target, including subsystem requirements."),
            ("CORE BUILD CHECK", self.core_build_check, "Environment preflight + LevelUpDiag stabilization. Does not invoke final-profile assembly."),
            ("FINAL PROFILE ASSEMBLY", self.build_meta, "Strict validation followed by deterministic assembly of the selected final profile. Subsystem blockers are expected until integrated."),
            ("RELEASE DIAGNOSTICS", self.full_gate, "Strict release/finalization diagnostics; not part of core stabilization."),
            ("STOP SESSION", self.stop_session, "Stop workspace services and current command. Does not shutdown all WSL."),
        ]
        for idx, (title, command, tip) in enumerate(meta):
            r, c = 2 + idx // 2, idx % 2
            cell = ttk.Frame(f, padding=5)
            cell.grid(row=r, column=c, sticky="nsew", padx=4, pady=4)
            ttk.Button(cell, text=title, command=command, style="Hero.TButton").pack(fill=tk.X)
            ttk.Label(cell, text=tip, wraplength=470).pack(anchor="w", pady=(4, 0))
        f.columnconfigure(0, weight=1)
        f.columnconfigure(1, weight=1)
        status_row = 2 + (len(meta) + 1) // 2
        status = ttk.LabelFrame(f, text="Current development context", padding=10)
        status.grid(row=status_row, column=0, columnspan=2, sticky="nsew", pady=(14, 4))
        for i, name in enumerate(("Phase", "Environment", "Backend", "Workspace", "Profile", "LevelUpDiag", "Validation", "QEMU")):
            var = tk.StringVar(value="...")
            self.status_vars[name] = var
            ttk.Label(status, text=name + ":", style="Status.TLabel").grid(row=i//4, column=(i%4)*2, sticky="e", padx=(6,3), pady=4)
            ttk.Label(status, textvariable=var).grid(row=i//4, column=(i%4)*2+1, sticky="w", padx=(0,20), pady=4)
        ttk.Label(status, text="Deferred subsystems:", style="Status.TLabel").grid(row=2, column=0, sticky="e", padx=(6,3), pady=4)
        ttk.Label(status, text=self.deferred_subsystems_text(), wraplength=900).grid(row=2, column=1, columnspan=7, sticky="w", pady=4)
        ttk.Button(status, text="Refresh", command=self.refresh_status_async).grid(row=3, column=0, sticky="w", pady=(8,0))

    def _build_workspaces(self) -> None:
        f = self._tab("Workspaces")
        ttk.Label(f, text="Development workspaces", style="Section.TLabel").grid(row=0, column=0, columnspan=4, sticky="w")
        self.workspace_var = tk.StringVar(value=self.workspace_manager.default_id())
        ttk.Label(f, text="Active workspace").grid(row=1, column=0, sticky="w", pady=6)
        self.workspace_combo = ttk.Combobox(f, textvariable=self.workspace_var, values=self._workspace_ids(), state="readonly")
        self.workspace_combo.grid(row=1, column=1, columnspan=2, sticky="ew", pady=6)
        self.workspace_combo.bind("<<ComboboxSelected>>", self._on_workspace_selected)
        ttk.Button(f, text="Set default", command=self.set_default_workspace).grid(row=1, column=3, sticky="ew", padx=4)
        box = ttk.Frame(f)
        box.grid(row=2, column=0, columnspan=4, sticky="ew", pady=8)
        self._button_grid(box, [
            ("Preflight", self.preflight),
            ("Refresh from Windows source", self.create_workspace),
            ("Open Shell", self.open_shell),
            ("Open Editor", self.open_editor),
            ("Open Explorer", self.open_explorer),
            ("Add Workspace", self.add_workspace),
            ("Remove Config Entry", self.remove_workspace_config),
        ], columns=4)
        self.workspace_detail = ScrolledText(f, height=18, font=("Consolas", 9), wrap="word")
        self.workspace_detail.grid(row=3, column=0, columnspan=4, sticky="nsew", pady=8)
        ttk.Label(f, text="Workspace removal here removes only Koali Control Panel configuration. It never deletes source or mutable data automatically.", wraplength=960).grid(row=4, column=0, columnspan=4, sticky="w")
        for c in range(4): f.columnconfigure(c, weight=1)
        f.rowconfigure(3, weight=1)

    def _build_development(self) -> None:
        f = self._tab("Development")
        ttk.Label(f, text="Development session", style="Section.TLabel").pack(anchor="w")
        box = ttk.Frame(f); box.pack(fill=tk.X, pady=10)
        self._button_grid(box, [
            ("PREPARE DEVELOPMENT ENVIRONMENT", self.prepare_development_environment),
            ("START DEVELOPMENT", self.start_development),
            ("Bootstrap", self.bootstrap),
            ("Bootstrap Offline", self.bootstrap_offline),
            ("Setup Development", self.setup_development),
            ("Open Shell", self.open_shell),
            ("Open Editor", self.open_editor),
            ("Start Services", self.services_start),
            ("Services Status", self.services_status),
            ("Stop Services", self.services_stop),
            ("STOP SESSION", self.stop_session),
        ])
        ttk.Label(f, text="Heavy services remain task-activated. STOP SESSION is workspace-scoped and does not globally shutdown WSL.", wraplength=950).pack(anchor="w", pady=8)

    def _build_dev_stack(self) -> None:
        f = self._tab("Dev Stack")
        ttk.Label(f, text="Integrated Koali development stack", style="Section.TLabel").grid(row=0, column=0, columnspan=6, sticky="w")
        ttk.Label(
            f,
            text="The Control Panel orchestrates products declaratively. Products remain standalone and removable; missing optional products do not break the host.",
            wraplength=1100,
        ).grid(row=1, column=0, columnspan=6, sticky="w", pady=(2, 8))
        actions = ttk.Frame(f)
        actions.grid(row=2, column=0, columnspan=6, sticky="ew", pady=(2, 10))
        self._button_grid(actions, [
            ("BRING KOALI TO READY", self.bring_koali_to_ready),
            ("START DEV STACK", self.start_dev_stack),
            ("STOP DEV STACK", self.stop_dev_stack),
            ("OPEN KOALI", lambda: self.open_product("koali-spaces")),
            ("REFRESH", self.refresh_dev_stack),
        ], columns=5)

        headers = ("Product", "Installed", "Runtime", "Health", "Location", "Actions")
        for col, text in enumerate(headers):
            ttk.Label(f, text=text, style="Status.TLabel").grid(row=3, column=col, sticky="w", padx=4, pady=(2, 6))
        self.product_status_vars: dict[str, dict[str, tk.StringVar]] = {}
        row = 4
        for spec in self.products.specs():
            if not spec.enabled and spec.product_id != "orgo":
                continue
            values = {name: tk.StringVar(value="...") for name in ("installed", "runtime", "health", "detail")}
            self.product_status_vars[spec.product_id] = values
            ttk.Label(f, text=spec.label).grid(row=row, column=0, sticky="w", padx=4, pady=4)
            ttk.Label(f, textvariable=values["installed"]).grid(row=row, column=1, sticky="w", padx=4)
            ttk.Label(f, textvariable=values["runtime"]).grid(row=row, column=2, sticky="w", padx=4)
            ttk.Label(f, textvariable=values["health"]).grid(row=row, column=3, sticky="w", padx=4)
            ttk.Label(f, textvariable=values["detail"], wraplength=360).grid(row=row, column=4, sticky="w", padx=4)
            cell = ttk.Frame(f)
            cell.grid(row=row, column=5, sticky="ew", padx=4)
            ttk.Button(cell, text="Check", command=lambda pid=spec.product_id: self.product_check(pid)).pack(side=tk.LEFT, padx=2)
            ttk.Button(cell, text="Start", command=lambda pid=spec.product_id: self.start_product(pid)).pack(side=tk.LEFT, padx=2)
            ttk.Button(cell, text="Stop", command=lambda pid=spec.product_id: self.stop_product(pid)).pack(side=tk.LEFT, padx=2)
            ttk.Button(cell, text="Open", command=lambda pid=spec.product_id: self.open_product(pid)).pack(side=tk.LEFT, padx=2)
            row += 1
        self.dev_stack_detail = ScrolledText(f, height=10, font=("Consolas", 9), wrap="word")
        self.dev_stack_detail.grid(row=row, column=0, columnspan=6, sticky="nsew", pady=(10, 0))
        self.dev_stack_detail.insert("1.0", "Dev Stack is idle. BRING KOALI TO READY performs the complete development path without invoking final release gates.\n")
        for col in range(6):
            f.columnconfigure(col, weight=1 if col in {4, 5} else 0)
        f.rowconfigure(row, weight=1)

    def _build_diagnostics(self) -> None:
        f = self._tab("Diagnostics")
        ttk.Label(f, text="Koali System / Store / Core / Final target", style="Section.TLabel").pack(anchor="w")
        ttk.Label(
            f,
            text="Koali System checks the live shell. Store / N13 checks koa-linux store sources; Linux session checks require configuration in LevelUpDiag. Core and QEMU campaigns use the active workspace. Verdicts remain owned by LevelUpDiag.",
            wraplength=1050,
        ).pack(anchor="w", pady=(4, 8))
        box = ttk.Frame(f); box.pack(fill=tk.X, pady=10)
        self._button_grid(box, [
            ("CORE STABILIZATION", self.run_stabilization),
            ("CORE RUNTIME", self.run_stabilization_runtime),
            ("DEBUG PRINCIPAL", self.run_debug),
            ("VALIDATION (FINAL TARGET)", self.run_all),
            ("Structure Strict", lambda: self.run_diagnostic_campaign("structure")),
            ("Release Preparation", self.full_gate),
            ("Koali System", lambda: self.run_diagnostic_role("koali_system")),
            ("Store / N13", lambda: self.run_diagnostic_role("store")),
            ("System N10", self.system_diagnostics),
            ("All Strict Levels", self.run_all_diagnostic_levels),
        ], columns=4)
        self.diagnostics_detail = ScrolledText(f, height=15, font=("Consolas", 9), wrap="word")
        self.diagnostics_detail.pack(fill=tk.BOTH, expand=True, pady=8)
        self.diagnostics_detail.insert("1.0", "CORE STABILIZATION delegates N00/N01/N04/N05 to LevelUpDiag. External subsystem admission is deferred, never fabricated.\n")

    def _build_build(self) -> None:
        f = self._tab("Build")
        ttk.Label(f, text="Koali core stabilization", style="Section.TLabel").grid(row=0, column=0, columnspan=4, sticky="w")
        ttk.Label(
            f,
            text="Current focus: stabilize the Koali environment and native components while integration work proceeds independently. Core checks do not require Konnaxion/Ariane/Orgo/Semantik Architect admission and never rewrite their source locks.",
            wraplength=1100,
        ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(2, 6))
        core_box = ttk.Frame(f); core_box.grid(row=2, column=0, columnspan=4, sticky="ew", pady=6)
        self._button_grid(core_box, [
            ("CORE STABILITY CHECK", self.core_build_check),
            ("CORE RUNTIME DIAGNOSTICS", self.run_stabilization_runtime),
        ], columns=2)

        ttk.Separator(f).grid(row=3, column=0, columnspan=4, sticky="ew", pady=12)
        ttk.Label(f, text="Final profile assembly (strict / subsystem-aware)", style="Section.TLabel").grid(row=4, column=0, columnspan=4, sticky="w")
        ttk.Label(f, text="This section preserves the canonical final-target pipeline. It is expected to remain BLOCKED until required independent subsystems and upstream package evidence are genuinely integrated.", wraplength=1100).grid(row=5, column=0, columnspan=4, sticky="w", pady=(2, 6))
        self.build_profile = tk.StringVar(value=self.final_profile())
        self.build_renderer = tk.StringVar(value=str(self.active_workspace().assembly.get("renderer", "systemd")))
        self.build_output = tk.StringVar(value=str(self.active_workspace().assembly.get("output", "generated/koali/plan")))
        ttk.Label(f, text="Profile").grid(row=6, column=0, sticky="w", pady=4)
        ttk.Combobox(f, textvariable=self.build_profile, values=PROFILES, state="readonly").grid(row=6, column=1, sticky="ew", pady=4)
        ttk.Label(f, text="Renderer").grid(row=6, column=2, sticky="w", padx=(12,0))
        ttk.Combobox(f, textvariable=self.build_renderer, values=RENDERERS, state="readonly").grid(row=6, column=3, sticky="ew")
        ttk.Label(f, text="Output").grid(row=7, column=0, sticky="w", pady=4)
        ttk.Entry(f, textvariable=self.build_output).grid(row=7, column=1, columnspan=3, sticky="ew")
        box = ttk.Frame(f); box.grid(row=8, column=0, columnspan=4, sticky="ew", pady=8)
        self._button_grid(box, [
            ("Assemble", self.assemble),
            ("Assemble --check", self.assemble_check),
            ("Generate All", self.generate_all),
            ("Generate Effective Profile", self.generate_effective_profile),
            ("Generate Indexes", self.generate_indexes),
            ("FINAL PROFILE ASSEMBLY", self.build_meta),
            ("Build System Image", self.build_image),
        ])
        ttk.Separator(f).grid(row=9, column=0, columnspan=4, sticky="ew", pady=12)
        ttk.Label(f, text="Native Koali component candidates", style="Section.TLabel").grid(row=10, column=0, columnspan=4, sticky="w")
        self.component_var = tk.StringVar(value="")
        self.source_epoch_var = tk.StringVar(value="")
        ttk.Label(f, text="Component").grid(row=11, column=0, sticky="w", pady=4)
        self.component_combo = ttk.Combobox(f, textvariable=self.component_var, values=(), state="readonly")
        self.component_combo.grid(row=11, column=1, sticky="ew", pady=4)
        ttk.Button(f, text="Discover", command=self.discover_components).grid(row=11, column=2, sticky="ew", padx=4)
        ttk.Button(f, text="Build Component", command=self.build_component).grid(row=11, column=3, sticky="ew", padx=4)
        ttk.Label(f, text="SOURCE_DATE_EPOCH").grid(row=12, column=0, sticky="w", pady=4)
        ttk.Entry(f, textvariable=self.source_epoch_var).grid(row=12, column=1, sticky="ew", pady=4)
        ttk.Button(f, text="Use Git Commit Epoch", command=self.use_git_epoch).grid(row=12, column=2, sticky="ew", padx=4)
        ttk.Button(f, text="Build All Components", command=self.build_all_components).grid(row=12, column=3, sticky="ew", padx=4)
        ttk.Separator(f).grid(row=13, column=0, columnspan=4, sticky="ew", pady=12)
        ttk.Label(f, text="Offline bundle (final target)", style="Section.TLabel").grid(row=14, column=0, columnspan=4, sticky="w")
        self.bundle_output_var = tk.StringVar(value="generated/koali/offline-bundle")
        ttk.Label(f, text="Output").grid(row=15, column=0, sticky="w", pady=4)
        ttk.Entry(f, textvariable=self.bundle_output_var).grid(row=15, column=1, columnspan=2, sticky="ew", pady=4)
        ttk.Button(f, text="Build Offline Bundle", command=self.build_bundle).grid(row=15, column=3, sticky="ew", padx=4)
        ttk.Label(f, text="System-image inputs remain structured and explicit. Core stabilization never fabricates subsystem source locks, package plans, release evidence, kernel/rootfs/recovery/provenance inputs.", wraplength=1050).grid(row=16, column=0, columnspan=4, sticky="w", pady=8)
        for c in range(4): f.columnconfigure(c, weight=1)

    def _build_system_test(self) -> None:
        f = self._tab("System Test")
        ttk.Label(f, text="QEMU execution context", style="Section.TLabel").grid(row=0, column=0, columnspan=4, sticky="w")
        ttk.Label(
            f,
            text="Koali Control Panel owns QEMU/image context and lifecycle. LevelUpDiag N10 owns the system diagnostic verdict and calls the public kOA-Linux system-validation surface.",
            wraplength=950,
        ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(2, 8))
        qemu = self.config_data.get("system_test", {}).get("qemu", {})
        self.qemu_vars: dict[str, tk.StringVar] = {}
        choices = {
            "image_format": ("raw", "qcow2"),
            "network": ("off", "on"),
            "mediatheque_selection": ("", "selected", "not_selected"),
            "semantik_selection": ("", "selected", "not_selected"),
        }
        for idx, (key, label) in enumerate(QEMU_UI_FIELDS):
            row = 2 + idx // 2
            group = idx % 2
            label_col = group * 2
            value_col = label_col + 1
            var = tk.StringVar(value=str(qemu.get(key, "")))
            self.qemu_vars[key] = var
            ttk.Label(f, text=label).grid(row=row, column=label_col, sticky="w", padx=(0, 6), pady=3)
            if key in choices:
                widget = ttk.Combobox(f, textvariable=var, values=choices[key], state="readonly")
            else:
                widget = ttk.Entry(f, textvariable=var)
            widget.grid(row=row, column=value_col, sticky="ew", padx=(0, 12), pady=3)

        action_row = 2 + (len(QEMU_UI_FIELDS) + 1) // 2
        box = ttk.Frame(f); box.grid(row=action_row, column=0, columnspan=4, sticky="ew", pady=10)
        self._button_grid(box, [
            ("PREPARE QEMU ENVIRONMENT", self.prepare_qemu_environment),
            ("QEMU PREFLIGHT", self.qemu_preflight),
            ("RUN SYSTEM DIAGNOSTICS (N10)", self.system_diagnostics),
            ("BUILD + BOOT", self.build_and_boot),
            ("Save QEMU Context", self.save_qemu_settings),
        ], columns=2)
        ttk.Label(
            f,
            text="Koali Control Panel owns QEMU infrastructure/context only. PREPARE installs the WSL validation backend when needed, derives safe repository facts, and never invents kOA-Linux runtime markers. LevelUpDiag owns N10 and the verdict.",
            wraplength=1100,
        ).grid(row=action_row + 1, column=0, columnspan=4, sticky="w", pady=(0, 6))
        self.system_test_detail = ScrolledText(f, height=10, font=("Consolas", 9), wrap="word")
        self.system_test_detail.grid(row=action_row + 2, column=0, columnspan=4, sticky="nsew", pady=(2, 0))
        self.system_test_detail.insert("1.0", "Run PREPARE QEMU ENVIRONMENT, then QEMU PREFLIGHT. N10 is delegated only when the operational context is READY.\n")
        for c in range(4): f.columnconfigure(c, weight=1)
        f.rowconfigure(action_row + 2, weight=1)

    def _build_environment(self) -> None:
        f = self._tab("Environment")
        ttk.Label(f, text="Execution backend", style="Section.TLabel").pack(anchor="w")
        box = ttk.Frame(f); box.pack(fill=tk.X, pady=10)
        self._button_grid(box, [
            ("PREPARE DEVELOPMENT ENVIRONMENT", self.prepare_development_environment),
            ("Environment Preflight", self.preflight),
            ("Start Backend", self.start_backend),
            ("Backend Status", self.backend_status),
            ("PREPARE WSL BACKEND", self.prepare_wsl_backend),
            ("Terminate Configured Distro", self.terminate_distro),
            ("Stop Active Command", self.runner.stop_active),
            ("Shutdown ALL WSL (Advanced)", self.shutdown_all_wsl),
        ])
        self.environment_detail = ScrolledText(f, height=18, font=("Consolas", 9), wrap="word")
        self.environment_detail.pack(fill=tk.BOTH, expand=True, pady=8)
        ttk.Label(f, text="Global WSL shutdown is intentionally isolated here because it can affect Debian, Docker Desktop and other workspaces.", wraplength=950).pack(anchor="w")

    def _build_settings(self) -> None:
        f = self._tab("Settings")
        ttk.Label(f, text="Stable facts and defaults", style="Section.TLabel").grid(row=0, column=0, columnspan=3, sticky="w")
        wsl = self.config_data.get("backends", {}).get("wsl", {})
        app = self.config_data.get("app", {})
        ws = self.active_workspace()
        diag = self.config_data.get("diagnostics", {}).get("levelupdiag", {})
        campaigns = diag.get("campaigns", {})
        settings = [
            ("wsl_distribution", "WSL distribution", str(wsl.get("distribution", "Ubuntu-24.04"))),
            ("terminal_exe", "Terminal executable", str(app.get("terminal_exe", "wt.exe"))),
            ("editor_exe", "Editor executable", str(app.get("editor_exe", "code"))),
            ("timeout", "Command timeout (seconds)", str(app.get("command_timeout_seconds", 1800))),
            ("workspace_root", "Active workspace root", ws.root),
            ("windows_source", "Active workspace Windows source", ws.windows_source),
            ("levelupdiag_root", "LevelUpDiag root (Windows or backend path)", str(diag.get("root", ""))),
            ("diag_developer", "Diagnostics campaign: developer", str(campaigns.get("developer", "debug"))),
            ("diag_build", "Diagnostics campaign: build", str(campaigns.get("build", "stabilization"))),
            ("diag_run_all", "Diagnostics campaign: run", str(campaigns.get("run_all", "validation"))),
            ("diag_release", "Diagnostics campaign: release", str(campaigns.get("release", "release"))),
            ("diag_delivery", "Diagnostics campaign: delivery", str(campaigns.get("delivery", "delivery"))),
        ]
        row = 1
        for key, label, value in settings:
            var = tk.StringVar(value=value); self.setting_vars[key] = var
            ttk.Label(f, text=label).grid(row=row, column=0, sticky="w", pady=4)
            ttk.Entry(f, textvariable=var).grid(row=row, column=1, columnspan=2, sticky="ew", pady=4)
            row += 1
        open_shell = tk.BooleanVar(value=bool(app.get("open_shell_on_start", True))); self.setting_vars["open_shell_on_start"] = open_shell
        ttk.Checkbutton(f, text="Open shell after START DEVELOPMENT", variable=open_shell).grid(row=row, column=0, columnspan=3, sticky="w", pady=4); row += 1
        diagnostics_enabled = tk.BooleanVar(value=bool(diag.get("enabled", True))); self.setting_vars["levelupdiag_enabled"] = diagnostics_enabled
        ttk.Checkbutton(f, text="Enable LevelUpDiag diagnostics integration", variable=diagnostics_enabled).grid(row=row, column=0, columnspan=3, sticky="w", pady=4); row += 1
        prepare_cfg = self.config_data.get("environment", {}).get("prepare", {})
        auto_create = tk.BooleanVar(value=bool(prepare_cfg.get("auto_create_workspace", True))); self.setting_vars["auto_create_workspace"] = auto_create
        ttk.Checkbutton(f, text="PREPARE: create missing Linux workspace automatically", variable=auto_create).grid(row=row, column=0, columnspan=3, sticky="w", pady=4); row += 1
        auto_refresh = tk.BooleanVar(value=bool(prepare_cfg.get("auto_refresh_workspace_from_windows", True))); self.setting_vars["auto_refresh_workspace_from_windows"] = auto_refresh
        ttk.Checkbutton(f, text="PREPARE: refresh WSL workspace when Windows source changed (folder backup; no Git state management)", variable=auto_refresh).grid(row=row, column=0, columnspan=3, sticky="w", pady=4); row += 1
        auto_setup = tk.BooleanVar(value=bool(prepare_cfg.get("run_repository_setup", True))); self.setting_vars["run_repository_setup"] = auto_setup
        ttk.Checkbutton(f, text="PREPARE: run canonical repository setup when needed", variable=auto_setup).grid(row=row, column=0, columnspan=3, sticky="w", pady=4); row += 1
        provision_cfg = wsl.get("toolchain_provisioning", {})
        auto_tools = tk.BooleanVar(value=bool(provision_cfg.get("enabled", True))); self.setting_vars["toolchain_provisioning"] = auto_tools
        ttk.Checkbutton(f, text="PREPARE: provision missing WSL profile tools automatically", variable=auto_tools).grid(row=row, column=0, columnspan=3, sticky="w", pady=4); row += 1
        image = self.config_data.get("build", {}).get("image", {})
        image_enabled = tk.BooleanVar(value=bool(image.get("enabled", False))); self.setting_vars["image_enabled"] = image_enabled
        ttk.Checkbutton(f, text="Enable system-image build", variable=image_enabled).grid(row=row, column=0, columnspan=3, sticky="w", pady=4); row += 1
        ttk.Label(f, text="build-image args (JSON array, excludes command name)").grid(row=row, column=0, sticky="nw", pady=4)
        self.build_args_text = tk.Text(f, height=6, wrap="word"); self.build_args_text.insert("1.0", json.dumps(image.get("args", []), indent=2)); self.build_args_text.grid(row=row, column=1, columnspan=2, sticky="nsew", pady=4); row += 1
        actions = ttk.Frame(f); actions.grid(row=row, column=0, columnspan=3, sticky="ew", pady=10)
        self._button_grid(actions, [("Save Settings", self.save_settings), ("Open Config", self.open_config), ("Reload Config", self.reload_config)], columns=3)
        f.columnconfigure(1, weight=1); f.columnconfigure(2, weight=1); f.rowconfigure(row-1, weight=1)

    def _build_log(self, frame: ttk.Frame) -> None:
        head = ttk.Frame(frame); head.pack(fill=tk.X, padx=8, pady=(4,0))
        ttk.Label(head, text="Activity log", style="Section.TLabel").pack(side=tk.LEFT)
        ttk.Button(head, text="Open logs", command=self.open_logs).pack(side=tk.RIGHT, padx=4)
        ttk.Button(head, text="Clear", command=lambda: self.log_text.delete("1.0", tk.END)).pack(side=tk.RIGHT)
        self.log_text = ScrolledText(frame, height=11, font=("Consolas", 9), wrap="word")
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=8, pady=(3,8))

    def log(self, message: str) -> None:
        self.events.put(("log", message))

    def _append_logs(self, messages) -> None:
        if not messages:
            return
        stamp = time.strftime('%H:%M:%S')
        text = ''.join(f"[{stamp}] {message}\n" for message in messages)
        self.log_text.insert(tk.END, text)
        lines = int(self.log_text.index('end-1c').split('.')[0])
        if lines > 5000:
            self.log_text.delete('1.0', f'{lines - 5000}.0')
        self.log_text.see(tk.END)
        try:
            self.log_dir.mkdir(exist_ok=True)
            if self.log_path.exists() and self.log_path.stat().st_size > 5 * 1024 * 1024:
                self.log_path.replace(self.log_path.with_suffix('.log.1'))
            with self.log_path.open('a', encoding='utf-8') as handle:
                handle.write(text)
        except OSError:
            pass

    def _append_log(self, message: str) -> None:
        self._append_logs([message])

    def _drain_events(self) -> None:
        logs = []
        deadline = time.monotonic() + 0.02
        try:
            for _ in range(200):
                if time.monotonic() >= deadline:
                    break
                kind, payload = self.events.get_nowait()
                if kind == "log": logs.append(str(payload))
                elif kind == "refresh_status": self.refresh_status_async()
                elif kind == "busy": self.busy = bool(payload)
                elif kind == "status":
                    key, value = payload
                    if key in self.status_vars: self.status_vars[key].set(str(value))
                elif kind == "workspace_detail":
                    self.workspace_detail.delete("1.0", tk.END); self.workspace_detail.insert("1.0", str(payload))
                elif kind == "environment_detail":
                    self.environment_detail.delete("1.0", tk.END); self.environment_detail.insert("1.0", str(payload))
                elif kind == "diagnostics_detail" and hasattr(self, "diagnostics_detail"):
                    self.diagnostics_detail.delete("1.0", tk.END); self.diagnostics_detail.insert("1.0", str(payload))
                elif kind == "system_test_detail" and hasattr(self, "system_test_detail"):
                    self.system_test_detail.delete("1.0", tk.END); self.system_test_detail.insert("1.0", str(payload))
                elif kind == "dev_stack_detail" and hasattr(self, "dev_stack_detail"):
                    self.dev_stack_detail.delete("1.0", tk.END); self.dev_stack_detail.insert("1.0", str(payload))
                elif kind == "product_status" and hasattr(self, "product_status_vars"):
                    product_id, values = payload
                    targets = self.product_status_vars.get(str(product_id), {})
                    for key, value in dict(values).items():
                        if key in targets:
                            targets[key].set(str(value))
                elif kind == "qemu_context" and hasattr(self, "qemu_vars"):
                    for key, value in dict(payload).items():
                        if key in self.qemu_vars:
                            self.qemu_vars[key].set(str(value))
        except queue.Empty:
            pass
        self._append_logs(logs)
        self.after(25 if not self.events.empty() else 100, self._drain_events)

    def async_action(self, label: str, fn) -> None:
        if self.busy:
            self.log("Another action is already running")
            return
        self.busy = True
        def worker() -> None:
            self.events.put(("busy", True))
            try:
                fn()
            except Exception as exc:
                self.log(f"ERROR {label}: {exc}")
            finally:
                self.events.put(("busy", False))
                self.events.put(("refresh_status", None))
        threading.Thread(target=worker, daemon=True, name=f"koali-{label}").start()

    def _report_text(self, report: PreflightReport) -> str:
        lines = [f"Overall: {report.state.value}", ""]
        lines += [f"{item.state.value:7} {item.label:24} {item.detail}" for item in report.checks]
        return "\n".join(lines)

    def refresh_status_async(self) -> None:
        ws = self.active_workspace()
        if not self._status_refresh_lock.acquire(blocking=False):
            return
        def work() -> None:
            try:
                report = self.workspace_manager.preflight(ws)
                self.last_preflight = report
                checks = report.by_key()
                self.events.put(("status", ("Focus", self.workflow_focus())))
                self.events.put(("status", ("Environment", report.state.value)))
                self.events.put(("status", ("Backend", f"{ws.backend}: {checks.get('distro', checks.get('backend')).detail if checks.get('distro', checks.get('backend')) else 'unknown'}")))
                self.events.put(("status", ("Workspace", ws.workspace_id)))
                self.events.put(("status", ("Profile", ws.profile)))
                debug_ok, debug_detail = self.debugdiag.status(ws)
                diag_ok, diag_detail = self.levelupdiag.status(ws)
                self.events.put(("status", ("LevelUpDiag", "ready" if diag_ok else "not ready")))
                self.events.put(("status", ("Validation", "available" if diag_ok else "not ready")))
                qemu_img = str(self.config_data.get("system_test", {}).get("qemu", {}).get("image", ""))
                self.events.put(("status", ("QEMU", "configured" if qemu_img else "not configured")))
                text = self._report_text(report)
                self.events.put(("workspace_detail", text))
                self.events.put(("environment_detail", text))
            except Exception as exc:
                self.events.put(("status", ("Environment", "BLOCKED")))
                self.events.put(("environment_detail", str(exc)))
            finally:
                self._status_refresh_lock.release()
        threading.Thread(target=work, daemon=True, name="koali-status").start()

    # Home / meta
    def prepare_development_environment(self) -> None:
        ws = self.active_workspace()
        self.async_action(
            "PREPARE DEVELOPMENT ENVIRONMENT",
            lambda: self.orchestrator.prepare_development_environment(ws),
        )

    def start_development(self) -> None:
        ws = self.active_workspace()
        open_shell = bool(self.config_data.get("app", {}).get("open_shell_on_start", True))
        self.async_action("START DEVELOPMENT", lambda: self.orchestrator.start_development(ws, open_shell=open_shell))

    def stabilize_core(self) -> None:
        ws = self.active_workspace()
        def work() -> None:
            if not self.orchestrator.prepare_development_environment(ws):
                return
            self.levelupdiag.run_configured_campaign(
                ws, "stabilization", timeout=max(self.orchestrator.timeout(), 1800)
            )
        self.async_action("STABILIZE KOALI CORE", work)

    def _refresh_dev_stack_status(self, *, schedule: bool = True) -> None:
        if hasattr(self, "product_status_vars") and self._dev_refresh_lock.acquire(blocking=False):
            product_ids = list(self.product_status_vars)
            def work() -> None:
                try:
                    lines = []
                    for product_id in product_ids:
                        try:
                            snap = self.products.snapshot(product_id)
                        except Exception as exc:
                            self.events.put(("product_status", (product_id, {"installed": "ERROR", "runtime": "ERROR", "health": "ERROR", "detail": str(exc)})))
                            continue
                        values = {
                            "installed": "YES" if snap.installed else "NO",
                            "runtime": snap.runtime_state,
                            "health": snap.health_state,
                            "detail": snap.detail,
                        }
                        self.events.put(("product_status", (product_id, values)))
                        lines.append(f"{snap.label:16} installed={'yes' if snap.installed else 'no':3} runtime={snap.runtime_state:12} health={snap.health_state}")
                    if hasattr(self, "dev_stack_detail") and lines:
                        self.events.put(("dev_stack_detail", "\n".join(lines)))
                finally:
                    self._dev_refresh_lock.release()
            threading.Thread(target=work, daemon=True, name="koali-dev-stack-status").start()
        if schedule:
            self.after(2500, self._refresh_dev_stack_status)

    def refresh_dev_stack(self) -> None:
        self.products.invalidate_cache()
        self._refresh_dev_stack_status(schedule=False)

    def product_check(self, product_id: str) -> None:
        def work() -> None:
            spec = self.products.get(product_id)
            root = self.products.resolve_root(spec, refresh=True)
            if not root:
                self.log(f"{spec.label}: NOT INSTALLED")
                return
            available = [action for action in ("prepare", "migrate", "validate", "test", "build", "smoke", "start") if self.products.command_for(spec, action, root)]
            health, detail = self.products.health(spec)
            self.log(f"{spec.label}: root={root}; actions={','.join(available) or 'none'}; health={health} ({detail})")
        self.async_action(f"Check {product_id}", work)

    def start_product(self, product_id: str) -> None:
        self.async_action(f"Start {product_id}", lambda: self.products.start(product_id))

    def stop_product(self, product_id: str) -> None:
        self.async_action(f"Stop {product_id}", lambda: self.products.stop(product_id))

    def open_product(self, product_id: str) -> None:
        try:
            self.products.open(product_id)
        except KeyError:
            self.log(f"Unknown product: {product_id}")

    def start_dev_stack(self) -> None:
        ws = self.active_workspace()
        def work() -> None:
            result = self.dev_stack.start(ws, prepare_core=False, run_gates=True, prepare_products=True)
            if result.ready:
                self.products.open("koali-spaces")
            else:
                self.log(f"DEV STACK NOT READY; failed step: {result.failed_step}")
        self.async_action("START KOALI DEV STACK", work)

    def stop_dev_stack(self) -> None:
        self.async_action("STOP KOALI DEV STACK", self.dev_stack.stop)

    def bring_koali_to_ready(self) -> None:
        ws = self.active_workspace()
        def work() -> None:
            if not self.orchestrator.prepare_development_environment(ws):
                return
            if self.levelupdiag.run_configured_campaign(ws, "stabilization", timeout=max(self.orchestrator.timeout(), 1800)) != 0:
                self.log("BRING KOALI TO READY stopped by core stabilization")
                return
            result = self.dev_stack.start(ws, prepare_core=False, run_gates=True, prepare_products=True)
            if not result.ready:
                self.log(f"BRING KOALI TO READY stopped at: {result.failed_step}")
                return
            self.products.open("koali-spaces")
            self.log("BRING KOALI TO READY: COMPLETE")
        self.async_action("BRING KOALI TO READY", work)

    def _on_close(self) -> None:
        try:
            self.dev_stack.stop()
        finally:
            self.destroy()

    def run_stabilization(self) -> None:
        ws = self.active_workspace()
        self.async_action(
            "CORE STABILIZATION",
            lambda: self.levelupdiag.run_configured_campaign(
                ws, "stabilization", timeout=max(self.orchestrator.timeout(), 1800)
            ),
        )

    def run_stabilization_runtime(self) -> None:
        ws = self.active_workspace()
        self.async_action(
            "CORE RUNTIME",
            lambda: self.levelupdiag.run_configured_campaign(
                ws, "stabilization_runtime", timeout=max(self.orchestrator.timeout(), 1800)
            ),
        )

    def daily_cycle(self) -> None:
        ws = self.active_workspace()
        def work() -> None:
            if not self.orchestrator.start_development(ws, open_shell=False):
                return
            self.levelupdiag.run_configured_campaign(
                ws, "stabilization", timeout=max(self.orchestrator.timeout(), 1800)
            )
            self.backend_factory(ws.backend).open_editor(ws)
        self.async_action("DAILY CORE CYCLE", work)

    def run_debug(self) -> None:
        ws = self.active_workspace()
        self.async_action(
            "DEBUG PRINCIPAL",
            lambda: self.debugdiag.run(ws, profile=self.final_profile(), timeout=max(self.orchestrator.timeout(), 1800)),
        )

    def run_all(self) -> None:
        ws = self.active_workspace()
        self.async_action(
            "VALIDATION",
            lambda: self.levelupdiag.run_configured_campaign(ws, "run_all", timeout=max(self.orchestrator.timeout(), 1800)),
        )

    def full_gate(self) -> None:
        ws = self.active_workspace()
        self.async_action(
            "RELEASE DIAGNOSTICS",
            lambda: self.levelupdiag.run_configured_campaign(ws, "release", timeout=max(self.orchestrator.timeout(), 3600)),
        )

    def core_build_check(self) -> None:
        ws = self.active_workspace()
        def work() -> None:
            report = self.workspace_manager.preflight(ws)
            if report.state in {CheckState.BLOCKED, CheckState.FAIL}:
                self.log("CORE BUILD CHECK blocked by environment preflight")
                return
            self.levelupdiag.run_configured_campaign(
                ws, "stabilization", timeout=max(self.orchestrator.timeout(), 1800)
            )
        self.async_action("CORE BUILD CHECK", work)

    def build_meta(self) -> None:
        ws = self._sync_build_to_workspace()
        def work() -> None:
            report = self.workspace_manager.preflight(self.active_workspace())
            if report.state in {CheckState.BLOCKED, CheckState.FAIL}:
                self.log("FINAL PROFILE ASSEMBLY blocked by environment preflight")
                return
            if self.levelupdiag.run_configured_campaign(
                self.active_workspace(), "run_all", timeout=max(self.orchestrator.timeout(), 1800)
            ) != 0:
                self.log("FINAL PROFILE ASSEMBLY stopped by full-target validation")
                return
            self.orchestrator.build_plan(ws)
        self.async_action("FINAL PROFILE ASSEMBLY", work)

    def build_and_boot(self) -> None:
        self._sync_qemu_to_config()
        ws = self._sync_build_to_workspace()
        def work() -> None:
            if self.orchestrator.build_image(ws) != 0:
                return
            result = self.qemu_manager.prepare(ws, scopes="N10")
            self.store.save(self.config_data)
            self.events.put(("qemu_context", dict(self.config_data.get("system_test", {}).get("qemu", {}))))
            self.events.put(("system_test_detail", result.text()))
            if not result.ready:
                self.log("BUILD + BOOT stopped: QEMU operational context is NOT READY")
                return
            self.levelupdiag.run_system(ws, timeout=max(self.orchestrator.timeout(), 1800))
        self.async_action("BUILD + BOOT", work)

    def full_cycle(self) -> None:
        self._sync_qemu_to_config()
        ws = self._sync_build_to_workspace()
        def work() -> None:
            if not self.orchestrator.start_development(ws, open_shell=False):
                return
            if self.levelupdiag.run_configured_campaign(ws, "run_all", timeout=max(self.orchestrator.timeout(), 1800)) != 0:
                self.log("FULL CYCLE stopped by full-target validation")
                return
            if self.orchestrator.build_plan(ws) != 0:
                return
            image_cfg = self.config_data.get("build", {}).get("image", {})
            require_image = bool(self.config_data.get("build", {}).get("full_cycle_requires_image", False))
            if image_cfg.get("enabled", False):
                if self.orchestrator.build_image(ws) != 0:
                    return
                qemu_result = self.qemu_manager.prepare(ws, scopes="N10")
                self.store.save(self.config_data)
                self.events.put(("qemu_context", dict(self.config_data.get("system_test", {}).get("qemu", {}))))
                self.events.put(("system_test_detail", qemu_result.text()))
                if not qemu_result.ready:
                    self.log("FULL CYCLE blocked: QEMU operational context is NOT READY")
                    return
                if self.levelupdiag.run_system(ws, timeout=max(self.orchestrator.timeout(), 1800)) != 0:
                    return
            elif require_image:
                self.log("FULL CYCLE blocked: image build is required but not configured")
                return
            if self.levelupdiag.run_configured_campaign(ws, "release", timeout=max(self.orchestrator.timeout(), 3600)) != 0:
                return
            self.log("FULL CYCLE complete")
        self.async_action("FULL CYCLE", work)

    def stop_session(self) -> None:
        ws = self.active_workspace()
        self.runner.stop_active()
        def work() -> None:
            # Best-effort workspace-scoped teardown only.
            if str(ws.services.get("mode", "disabled")) != "disabled":
                self.orchestrator.services_command(ws, "stop")
            self.log(f"Session stopped for workspace {ws.workspace_id}; backend remains available")
        threading.Thread(target=work, daemon=True, name="koali-stop-session").start()

    # Workspace
    def preflight(self) -> None:
        ws = self.active_workspace()
        def work() -> None:
            report = self.workspace_manager.preflight(ws)
            text = self._report_text(report)
            self.log(text)
            self.events.put(("workspace_detail", text)); self.events.put(("environment_detail", text))
        self.async_action("Environment Preflight", work)

    def create_workspace(self) -> None:
        ws = self.active_workspace()
        exists = self.workspace_manager.workspace_exists(ws)
        action = "Refresh" if exists else "Import"
        if not messagebox.askyesno(
            APP_NAME,
            f"{action} {ws.workspace_id} from its configured Windows checkout?\n\n"
            "Koali Control Panel copies source content into the Linux filesystem while excluding "
            ".levelupdiag, dependency environments, caches and build artifacts. On refresh, the "
            "complete previous Linux workspace is preserved as a folder backup. Git state is not inspected or modified.",
        ):
            return
        self.async_action(
            f"{action} workspace",
            lambda: self.orchestrator.refresh_workspace_from_windows_source(ws),
        )

    def open_shell(self) -> None: self.backend_factory(self.active_workspace().backend).open_shell(self.active_workspace())
    def open_editor(self) -> None: self.backend_factory(self.active_workspace().backend).open_editor(self.active_workspace())
    def open_explorer(self) -> None:
        b = self.backend_factory(self.active_workspace().backend)
        if isinstance(b, WslBackend): b.open_explorer(self.active_workspace())
        else: self.open_shell()

    def git_status(self) -> None:
        ws = self.active_workspace(); self.async_action("Git Status", lambda: self.backend_factory(ws.backend).execute_in_workspace(ws, "git status --short --branch", "Git Status", timeout=60))

    def set_default_workspace(self) -> None:
        self.config_data.setdefault("environment", {})["default_workspace"] = self.active_workspace_id(); self.store.save(self.config_data); self.log("Default workspace saved")

    def add_workspace(self) -> None:
        workspace_id = simpledialog.askstring(APP_NAME, "Workspace ID (e.g. koa-linux-feature-x):", parent=self)
        if not workspace_id: return
        workspace_id = workspace_id.strip()
        if not workspace_id or workspace_id in self.config_data.get("workspaces", {}):
            messagebox.showerror(APP_NAME, "Workspace ID is empty or already exists."); return
        base = self.active_workspace()
        root = simpledialog.askstring(APP_NAME, "Linux workspace root template:", initialvalue=f"{{home}}/work/{workspace_id}", parent=self)
        if not root: return
        self.config_data.setdefault("workspaces", {})[workspace_id] = {
            "repository": base.repository,
            "backend": base.backend,
            "profile": base.profile,
            "root": root.strip(),
            "windows_source": base.windows_source,
            "checkout_ref": "current",
            "bootstrap_on_start": False,
            "assembly": dict(base.assembly),
            "services": dict(base.services),
        }
        self.store.save(self.config_data); self._reload_runtime(); self.workspace_var.set(workspace_id); self._refresh_workspace_combo(); self.log(f"Workspace config added: {workspace_id}")

    def remove_workspace_config(self) -> None:
        wid = self.active_workspace_id()
        if len(self._workspace_ids()) <= 1:
            messagebox.showerror(APP_NAME, "At least one workspace configuration must remain."); return
        if not messagebox.askyesno(APP_NAME, f"Remove Koali Control Panel configuration for {wid}?\n\nNo files will be deleted."): return
        del self.config_data["workspaces"][wid]
        if self.config_data.get("environment", {}).get("default_workspace") == wid:
            self.config_data["environment"]["default_workspace"] = sorted(self.config_data["workspaces"])[0]
        self.store.save(self.config_data); self._reload_runtime(); self.workspace_var.set(self.workspace_manager.default_id()); self._refresh_workspace_combo(); self.log(f"Workspace config removed: {wid}")

    def _refresh_workspace_combo(self) -> None:
        self.workspace_combo.configure(values=self._workspace_ids())

    def _on_workspace_selected(self, _event=None) -> None:
        ws = self.active_workspace()
        self.build_profile.set(self.final_profile())
        self.build_renderer.set(str(ws.assembly.get("renderer", "systemd")))
        self.build_output.set(str(ws.assembly.get("output", f"generated/koali/{ws.workspace_id}-systemd")))
        if "workspace_root" in self.setting_vars:
            self.setting_vars["workspace_root"].set(ws.root)
        if "windows_source" in self.setting_vars:
            self.setting_vars["windows_source"].set(ws.windows_source)
        self.refresh_status_async()

    # Development
    def bootstrap(self) -> None:
        ws=self.active_workspace(); self.async_action("Bootstrap", lambda: self.orchestrator.bootstrap(ws))
    def bootstrap_offline(self) -> None:
        ws=self.active_workspace(); self.async_action("Bootstrap Offline", lambda: self.orchestrator.bootstrap(ws, offline=True))
    def setup_development(self) -> None:
        ws=self.active_workspace(); self.async_action("Setup Development", lambda: self.orchestrator.setup_development(ws))
    def services_start(self) -> None:
        ws=self.active_workspace(); self.async_action("Services Start", lambda: self.orchestrator.services_command(ws, "start"))
    def services_status(self) -> None:
        ws=self.active_workspace(); self.async_action("Services Status", lambda: self.orchestrator.services_command(ws, "status"))
    def services_stop(self) -> None:
        ws=self.active_workspace(); self.async_action("Services Stop", lambda: self.orchestrator.services_command(ws, "stop"))

    # Diagnostics — DEBUG is local/read-only; LevelUpDiag owns strict validation/release semantics.
    def run_diagnostic_role(self, role: str) -> None:
        ws = self.active_workspace()
        campaign = self.levelupdiag.campaign_name(role)
        self.async_action(
            f"LevelUpDiag {campaign}",
            lambda: self.levelupdiag.run_campaign(ws, campaign, timeout=max(self.orchestrator.timeout(), 1800)),
        )

    def run_diagnostic_campaign(self, campaign: str) -> None:
        ws = self.active_workspace()
        self.async_action(
            f"LevelUpDiag {campaign}",
            lambda: self.levelupdiag.run_campaign(ws, campaign, timeout=max(self.orchestrator.timeout(), 1800)),
        )

    def run_all_diagnostic_levels(self) -> None:
        ws = self.active_workspace()
        self.async_action(
            "LevelUpDiag all enabled levels",
            lambda: self.levelupdiag.run_all_enabled(ws, timeout=max(self.orchestrator.timeout(), 3600)),
        )

    def system_diagnostics(self) -> None:
        self._sync_qemu_to_config()
        ws = self.active_workspace()
        def work() -> None:
            result = self.qemu_manager.preflight(ws, scopes="N10")
            self.events.put(("system_test_detail", result.text()))
            if not result.ready:
                self.log("N10 delegation blocked by Koali Control Panel QEMU preflight: NOT READY")
                return
            self.levelupdiag.run_system(ws, timeout=max(self.orchestrator.timeout(), 1800))
        self.async_action("LevelUpDiag N10 System", work)

    # Repository-owned build/generation commands remain direct Control Panel operations.
    def _koa_action(self, label: str, args: list[str], timeout: int | None = None) -> None:
        ws=self.active_workspace(); self.async_action(label, lambda: self.orchestrator.run_koa(ws, label, args, timeout=timeout))

    # Build
    def _sync_build_to_workspace(self) -> Workspace:
        # Final-target build selection is intentionally separate from the active
        # development workspace profile used during core stabilization.
        base = self.active_workspace()
        ws = Workspace.from_config(base.workspace_id, base.to_config())
        ws.profile = self.build_profile.get().strip() or self.final_profile()
        ws.assembly["renderer"] = self.build_renderer.get().strip() or "systemd"
        ws.assembly["output"] = self.build_output.get().strip() or f"generated/koali/{ws.workspace_id}"
        return ws
    def assemble(self) -> None:
        ws=self._sync_build_to_workspace(); self.async_action("Assemble", lambda: self.orchestrator.build_plan(ws))
    def assemble_check(self) -> None:
        ws=self._sync_build_to_workspace(); self.async_action("Assemble Check", lambda: self.orchestrator.build_plan(ws, check=True))
    def generate_all(self) -> None: self._koa_action("Generate All", ["generate", "all"])
    def generate_effective_profile(self) -> None:
        ws = self._sync_build_to_workspace()
        self.async_action(
            "Generate Effective Profile",
            lambda: self.orchestrator.generate_effective_profile(ws),
        )
    def generate_indexes(self) -> None: self._koa_action("Generate Indexes", ["generate", "indexes"])
    def build_image(self) -> None:
        ws=self._sync_build_to_workspace(); self.async_action("Build System Image", lambda: self.orchestrator.build_image(ws))

    def discover_components(self) -> None:
        ws = self.active_workspace()
        def work() -> None:
            result = self.backend_factory(ws.backend).capture_in_workspace(ws, "for f in packaging/components/*.toml; do test -f \"$f\" && basename \"$f\" .toml; done | sort", timeout=30)
            if result.code != 0:
                self.log("Component discovery failed")
                return
            items = [line.strip() for line in result.output.splitlines() if line.strip()]
            def update() -> None:
                self.component_combo.configure(values=items)
                if items and self.component_var.get() not in items:
                    self.component_var.set(items[0])
            self.after(0, update)
            self.log(f"Discovered {len(items)} component build targets")
        self.async_action("Discover Components", work)

    def use_git_epoch(self) -> None:
        ws = self.active_workspace()
        def work() -> None:
            result = self.backend_factory(ws.backend).capture_in_workspace(ws, "git log -1 --format=%ct", timeout=30)
            value = result.output.strip()
            if result.code == 0 and value.isdigit():
                self.after(0, lambda: self.source_epoch_var.set(value))
                self.log(f"SOURCE_DATE_EPOCH = {value}")
            else:
                self.log("Cannot derive SOURCE_DATE_EPOCH from Git")
        self.async_action("Git Commit Epoch", work)

    def _source_epoch(self) -> str:
        value = self.source_epoch_var.get().strip()
        if not value.isdigit():
            raise RuntimeError("SOURCE_DATE_EPOCH is required; use 'Use Git Commit Epoch'")
        return value

    def build_component(self) -> None:
        component = self.component_var.get().strip()
        if not component:
            messagebox.showinfo(APP_NAME, "Discover and select a component first.")
            return
        try: epoch = self._source_epoch()
        except RuntimeError as exc: messagebox.showinfo(APP_NAME, str(exc)); return
        ws = self.active_workspace()
        def work() -> None:
            timeout = max(self.orchestrator.timeout(), 900)
            if not self.orchestrator.prepare_component_build_environment(ws, timeout=timeout):
                self.log(f"Build Component blocked before {component}: component build environment is not ready")
                return
            self.orchestrator.build_component(ws, component, epoch, timeout=timeout)
        self.async_action(f"Build Component: {component}", work)

    def build_all_components(self) -> None:
        items = list(self.component_combo.cget("values"))
        if not items:
            messagebox.showinfo(APP_NAME, "Discover component targets first.")
            return
        try: epoch = self._source_epoch()
        except RuntimeError as exc: messagebox.showinfo(APP_NAME, str(exc)); return
        ws = self.active_workspace()
        def work() -> None:
            timeout = max(self.orchestrator.timeout(), 900)
            if not self.orchestrator.prepare_component_build_environment(ws, timeout=timeout):
                self.log("Build All Components stopped: component build environment is not ready")
                return
            for component in items:
                if self.orchestrator.build_component(
                    ws, str(component), epoch, timeout=timeout
                ) != 0:
                    self.log(f"Build All Components stopped at {component}")
                    return
            self.log("Build All Components complete")
        self.async_action("Build All Components", work)

    def build_bundle(self) -> None:
        output = self.bundle_output_var.get().strip()
        if not output:
            messagebox.showinfo(APP_NAME, "Offline bundle output is required.")
            return
        try: epoch = self._source_epoch()
        except RuntimeError as exc: messagebox.showinfo(APP_NAME, str(exc)); return
        ws = self.active_workspace()
        args = ["build-bundle", "--profile", ws.profile, "--output", output, "--source-date-epoch", epoch]
        for overlay in ws.assembly.get("overlays", []):
            if overlay in OVERLAYS: args += ["--overlay", overlay]
        self._koa_action("Build Offline Bundle", args, timeout=max(self.orchestrator.timeout(), 1800))

    # System test
    def _sync_qemu_to_config(self) -> None:
        q = self.config_data.setdefault("system_test", {}).setdefault("qemu", {})
        for key, var in getattr(self, "qemu_vars", {}).items():
            q[key] = var.get().strip()

    def _publish_qemu_result(self, text: str) -> None:
        self.events.put(("system_test_detail", text))
        self.log(text)

    def prepare_qemu_environment(self) -> None:
        self._sync_qemu_to_config()
        ws = self.active_workspace()
        def work() -> None:
            result = self.qemu_manager.prepare(ws)
            self.store.save(self.config_data)
            qemu = dict(self.config_data.get("system_test", {}).get("qemu", {}))
            self.events.put(("qemu_context", qemu))
            self.events.put(("system_test_detail", result.text()))
            self.log("QEMU environment READY" if result.ready else "QEMU environment prepared but NOT READY")
        self.async_action("PREPARE QEMU ENVIRONMENT", work)

    def qemu_preflight(self) -> None:
        self._sync_qemu_to_config()
        ws = self.active_workspace()
        def work() -> None:
            result = self.qemu_manager.preflight(ws)
            self.events.put(("system_test_detail", result.text()))
            for line in result.lines:
                if line:
                    self.log(line)
        self.async_action("QEMU PREFLIGHT", work)

    def save_qemu_settings(self) -> None:
        self._sync_qemu_to_config()
        self.store.save(self.config_data)
        self.log("System Test settings saved")
        self.refresh_status_async()

    # Environment
    def start_backend(self) -> None:
        b=self.backend_factory(self.active_workspace().backend); self.async_action("Start Backend", b.start)
    def backend_status(self) -> None:
        ws=self.active_workspace(); b=self.backend_factory(ws.backend)
        def work() -> None:
            checks=b.preflight(); text="\n".join(f"{x.state.value:7} {x.label}: {x.detail}" for x in checks); self.log(text); self.events.put(("environment_detail",text))
        self.async_action("Backend Status", work)
    def prepare_wsl_backend(self) -> None:
        ws=self.active_workspace(); b=self.backend_factory(ws.backend)
        if not isinstance(b,WslBackend):
            messagebox.showinfo(APP_NAME,"Active workspace does not use WSL.")
            return
        def work() -> None:
            mode = b.prepare_interactive()
            if mode != "ready":
                self.log("Koali Control Panel is watching the WSL backend and will refresh automatically when first-run setup completes.")
                threading.Thread(target=self._watch_wsl_readiness, args=(b,), daemon=True, name="koali-wsl-readiness").start()
        self.async_action("PREPARE WSL BACKEND", work)

    def _watch_wsl_readiness(self, backend: WslBackend) -> None:
        for _ in range(150):
            time.sleep(4)
            result = backend.runtime_probe(timeout=3)
            if result.code == 0 and "KOALI_WSL_READY" in result.output:
                self.log(f"WSL backend ready: {backend.distro}")
                self.events.put(("refresh_status", None))
                return
        self.log(f"WSL backend {backend.distro} is still not command-ready. Re-open PREPARE WSL BACKEND if the first-run console was closed early.")

    # Compatibility alias for older UI bindings/config documentation.
    def install_wsl_distro(self) -> None:
        self.prepare_wsl_backend()
    def terminate_distro(self) -> None:
        ws=self.active_workspace(); b=self.backend_factory(ws.backend)
        if not isinstance(b,WslBackend): return
        if messagebox.askyesno(APP_NAME,f"Terminate only {b.distro}?"): self.async_action("Terminate Distro", b.terminate)
    def shutdown_all_wsl(self) -> None:
        if not messagebox.askyesno(APP_NAME,"Shutdown ALL WSL distributions? This can affect Debian, Docker Desktop and unrelated workspaces."): return
        self.async_action("Shutdown All WSL", lambda:self.runner.run(["wsl.exe","--shutdown"],"Shutdown ALL WSL",timeout=60))

    # Settings
    def save_settings(self) -> None:
        try: timeout=max(30,int(str(self.setting_vars["timeout"].get()).strip()))
        except ValueError: messagebox.showerror(APP_NAME,"Command timeout must be an integer."); return
        try: args=json.loads(self.build_args_text.get("1.0",tk.END).strip() or "[]")
        except json.JSONDecodeError as exc: messagebox.showerror(APP_NAME,f"build-image args must be valid JSON: {exc}"); return
        if not isinstance(args,list): messagebox.showerror(APP_NAME,"build-image args must be a JSON array."); return
        self.config_data["app"]["terminal_exe"]=str(self.setting_vars["terminal_exe"].get()).strip()
        self.config_data["app"]["editor_exe"]=str(self.setting_vars["editor_exe"].get()).strip()
        self.config_data["app"]["command_timeout_seconds"]=timeout
        self.config_data["app"]["open_shell_on_start"]=bool(self.setting_vars["open_shell_on_start"].get())
        prepare_cfg=self.config_data.setdefault("environment",{}).setdefault("prepare",{})
        prepare_cfg["auto_create_workspace"]=bool(self.setting_vars["auto_create_workspace"].get())
        prepare_cfg["auto_refresh_workspace_from_windows"]=bool(self.setting_vars["auto_refresh_workspace_from_windows"].get())
        prepare_cfg["run_repository_setup"]=bool(self.setting_vars["run_repository_setup"].get())
        self.config_data["backends"]["wsl"]["distribution"]=str(self.setting_vars["wsl_distribution"].get()).strip()
        self.config_data["backends"]["wsl"].setdefault("toolchain_provisioning",{})["enabled"]=bool(self.setting_vars["toolchain_provisioning"].get())
        ws=self._sync_build_to_workspace(); ws.root=str(self.setting_vars["workspace_root"].get()).strip(); ws.windows_source=str(self.setting_vars["windows_source"].get()).strip(); self.config_data["workspaces"][ws.workspace_id]=ws.to_config()
        diag=self.config_data.setdefault("diagnostics",{}).setdefault("levelupdiag",{})
        diag["enabled"]=bool(self.setting_vars["levelupdiag_enabled"].get())
        diag["root"]=str(self.setting_vars["levelupdiag_root"].get()).strip()
        campaigns=diag.setdefault("campaigns",{})
        campaigns["developer"]=str(self.setting_vars["diag_developer"].get()).strip()
        campaigns["build"]=str(self.setting_vars["diag_build"].get()).strip()
        campaigns["run_all"]=str(self.setting_vars["diag_run_all"].get()).strip()
        campaigns["release"]=str(self.setting_vars["diag_release"].get()).strip()
        campaigns["delivery"]=str(self.setting_vars["diag_delivery"].get()).strip()
        self.config_data["build"]["image"]["enabled"]=bool(self.setting_vars["image_enabled"].get()); self.config_data["build"]["image"]["args"]=args
        self._sync_qemu_to_config(); self.store.save(self.config_data); self._reload_runtime(); self.log("Settings saved"); self.refresh_status_async()
    def _reload_runtime(self) -> None:
        self.supervisor.stop_all()
        self.backends.clear()
        self.workspace_manager = WorkspaceManager(self.config_data, self.backend_factory)
        self.orchestrator = Orchestrator(self.config_data, self.workspace_manager, self.backend_factory, self.log)
        self.products = ProductRegistry(self.config_data, self.backend_factory, self.supervisor, self.log)
        self.dev_stack = DevStackOrchestrator(self.config_data, self.workspace_manager, self.backend_factory, self.orchestrator, self.products, self.supervisor, self.log)
        self.levelupdiag = LevelUpDiagAdapter(self.config_data, self.backend_factory, self.log, on_report=lambda text: self.events.put(("diagnostics_detail", text)))
        self.debugdiag = DebugDiagnosticsRunner(self.config_data, self.backend_factory, self.log, on_report=lambda text: self.events.put(("diagnostics_detail", text)))
        self.qemu_manager = QemuEnvironmentManager(self.config_data, self.backend_factory, self.log, build_image=self.orchestrator.build_image)
    def reload_config(self) -> None:
        self.config_data=self.store.load(); self._reload_runtime(); self.log("Config reloaded; reopen panel if you changed fields not currently bound in the UI"); self.refresh_status_async()
    def open_config(self) -> None:
        try: os.startfile(self.config_path)  # type: ignore[attr-defined]
        except Exception as exc: self.log(f"Cannot open config: {exc}")
    def open_logs(self) -> None:
        self.log_dir.mkdir(exist_ok=True)
        try: subprocess.Popen(["explorer.exe",str(self.log_dir)])
        except OSError as exc: self.log(f"Cannot open logs: {exc}")
