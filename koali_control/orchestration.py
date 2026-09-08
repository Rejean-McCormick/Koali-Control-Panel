from __future__ import annotations

import shlex
from typing import Callable

from .backends import ExecutionBackend, WslBackend, shell_join
from .config import OVERLAYS
from .models import CheckState, Workspace
from .workspaces import WorkspaceManager


class Orchestrator:
    def __init__(
        self,
        config: dict,
        workspace_manager: WorkspaceManager,
        backend_factory: Callable[[str], ExecutionBackend],
        log: Callable[[str], None],
    ) -> None:
        self.config = config
        self.workspaces = workspace_manager
        self.backend_factory = backend_factory
        self.log = log

    def timeout(self) -> int:
        try:
            return max(30, int(self.config.get("app", {}).get("command_timeout_seconds", 1800)))
        except (TypeError, ValueError):
            return 1800

    def backend(self, workspace: Workspace) -> ExecutionBackend:
        return self.backend_factory(workspace.backend)

    @staticmethod
    def koa_cli(args: list[str]) -> str:
        parts = ["uv", "run", "--frozen", "python", "-m", "koa_tools.cli", "--repository-root", "$PWD", *args]
        return shell_join(parts).replace("'$PWD'", '"$PWD"')

    def run_koa(self, workspace: Workspace, label: str, args: list[str], *, timeout: int | None = None) -> int:
        return self.backend(workspace).execute_in_workspace(workspace, self.koa_cli(args), label, timeout=timeout or self.timeout())

    @staticmethod
    def component_build_cli(component: str, source_date_epoch: str) -> str:
        """Return the repository's actual component-builder invocation.

        build-component is intentionally not registered on koa_tools.cli's public
        command catalog. The repository exposes it as the Python command module
        koa_tools.commands.build_component, so the Control Panel must call that
        module directly instead of inventing a public CLI subcommand.
        """
        parts = [
            "uv", "run", "--frozen", "python", "-m",
            "koa_tools.commands.build_component",
            "--component", component,
            "--source-date-epoch", source_date_epoch,
        ]
        return shell_join(parts)

    def build_component(
        self,
        workspace: Workspace,
        component: str,
        source_date_epoch: str,
        *,
        timeout: int | None = None,
    ) -> int:
        label = f"Build Component: {component}"
        backend = self.backend(workspace)
        effective_timeout = timeout or max(self.timeout(), 900)
        rc = backend.execute_in_workspace(
            workspace,
            self.component_build_cli(component, source_date_epoch),
            label,
            timeout=effective_timeout,
        )
        if rc != 0 and component == "koa-node-agent" and isinstance(backend, WslBackend):
            self.log("Rust component build failed; replaying the canonical Cargo invocation in /tmp so the full compiler/offline-cache error is visible")
            backend.run_cargo_build_diagnostic(
                workspace,
                component,
                source_date_epoch,
                timeout=effective_timeout,
            )
        return rc

    def _log_report(self, report) -> None:
        for check in report.checks:
            self.log(f"{check.state.value:7} {check.label}: {check.detail}")

    def prepare_development_environment(self, workspace: Workspace, *, force_setup: bool = False) -> bool:
        """Converge the selected workspace toward a usable development state.

        This is intentionally idempotent. Backend/profile prerequisites are
        supplied before repository-owned bootstrap/setup is invoked. The Control Panel does
        not duplicate repository bootstrap semantics.
        """
        backend = self.backend(workspace)
        self.log(f"Preparing development environment: {workspace.workspace_id}")
        if backend.start() != 0:
            return False

        backend_checks = backend.preflight()
        hard_backend_block = [
            item for item in backend_checks
            if item.required and item.state in {CheckState.BLOCKED, CheckState.FAIL}
            and not item.key.startswith("backend_tool_")
        ]
        for check in backend_checks:
            self.log(f"{check.state.value:7} {check.label}: {check.detail}")
        if hard_backend_block:
            self.log("Development environment blocked before toolchain provisioning")
            return False

        missing_tools = [
            item for item in backend_checks
            if item.key.startswith("backend_tool_") and item.state in {CheckState.BLOCKED, CheckState.FAIL}
        ]
        if missing_tools:
            if isinstance(backend, WslBackend):
                self.log("Profile toolchain is incomplete; the Control Panel will provision the missing Ubuntu/WSL profile tools")
                if backend.provision_profile_toolchain(timeout=self.timeout()) != 0:
                    return False
            else:
                self.log("Profile toolchain is incomplete and this backend has no automatic provisioner")
                return False

        backend_checks = backend.preflight()
        if any(item.required and item.state in {CheckState.BLOCKED, CheckState.FAIL} for item in backend_checks):
            self.log("Profile toolchain provisioning did not produce a conformant backend")
            for check in backend_checks:
                self.log(f"{check.state.value:7} {check.label}: {check.detail}")
            return False

        prepare_cfg = self.config.get("environment", {}).get("prepare", {})
        auto_create = bool(prepare_cfg.get("auto_create_workspace", True))
        auto_refresh = bool(prepare_cfg.get("auto_refresh_workspace_from_windows", True))
        workspace_exists = self.workspaces.workspace_exists(workspace)
        if not workspace_exists:
            if not auto_create:
                self.log("Workspace is missing and automatic workspace creation is disabled")
                return False
            self.log("Workspace is missing or incomplete; importing the configured Windows checkout into the canonical Linux-filesystem workspace")
            if self.workspaces.create_from_windows_source(workspace, timeout=self.timeout()) != 0:
                return False
            force_setup = True
        elif isinstance(backend, WslBackend) and workspace.windows_source.strip():
            sync_state, sync_detail = self.workspaces.source_sync_status(workspace)
            self.log(f"Workspace source sync: {sync_state} — {sync_detail}")
            refresh_needed = sync_state in {"legacy", "source_changed"}
            if refresh_needed and auto_refresh:
                self.log("Refreshing the Linux workspace from the current Windows checkout; the previous workspace will be preserved as a folder backup")
                if self.workspaces.create_from_windows_source(workspace, timeout=self.timeout(), replace_existing=True) != 0:
                    return False
                force_setup = True
            elif refresh_needed:
                self.log("Workspace source refresh is needed but automatic refresh is disabled")
            elif sync_state == "unavailable":
                self.log(f"WARN: could not verify Windows-source freshness: {sync_detail}")

        # Resolve repository-owned native toolchains only after the current workspace
        # exists/refreshed. Versions come from repository contracts, never Control
        # Panel defaults, and no Git state is inspected.
        if isinstance(backend, WslBackend):
            rust_checks = backend.repository_rust_toolchain_preflight(workspace)
            for check in rust_checks:
                self.log(f"{check.state.value:7} {check.label}: {check.detail}")
            rust_blocked = [
                item for item in rust_checks
                if item.required and item.state in {CheckState.BLOCKED, CheckState.FAIL}
            ]
            if rust_blocked:
                self.log("Repository Rust toolchain is incomplete; PREPARE will provision exactly rust-toolchain.toml")
                if backend.provision_repository_rust_toolchain(workspace, timeout=self.timeout()) != 0:
                    return False
                rust_checks = backend.repository_rust_toolchain_preflight(workspace)
                for check in rust_checks:
                    self.log(f"{check.state.value:7} {check.label}: {check.detail}")
                if any(item.required and item.state in {CheckState.BLOCKED, CheckState.FAIL} for item in rust_checks):
                    self.log("Repository Rust toolchain provisioning did not satisfy rust-toolchain.toml")
                    return False

            native_checks = backend.repository_native_build_toolchain_preflight(workspace)
            for check in native_checks:
                self.log(f"{check.state.value:7} {check.label}: {check.detail}")
            native_blocked = [
                item for item in native_checks
                if item.required and item.state in {CheckState.BLOCKED, CheckState.FAIL}
            ]
            if native_blocked:
                self.log("Repository native compiler/linker is incomplete; PREPARE will provision the configured Ubuntu native build package set")
                if backend.provision_repository_native_build_toolchain(workspace, timeout=self.timeout()) != 0:
                    return False
                native_checks = backend.repository_native_build_toolchain_preflight(workspace)
                for check in native_checks:
                    self.log(f"native-toolchain: {check.state.value} {check.label}: {check.detail}")
                if any(item.required and item.state in {CheckState.BLOCKED, CheckState.FAIL} for item in native_checks):
                    self.log("Native compiler/linker provisioning did not satisfy the repository Rust build prerequisite")
                    return False

            cargo_checks = backend.repository_cargo_cache_preflight(workspace)
            for check in cargo_checks:
                self.log(f"{check.state.value:7} {check.label}: {check.detail}")
            cargo_blocked = [
                item for item in cargo_checks
                if item.required and item.state in {CheckState.BLOCKED, CheckState.FAIL}
            ]
            if cargo_blocked:
                self.log("Repository Cargo cache is incomplete; PREPARE will fetch exactly Cargo.lock dependencies, then verify offline availability")
                if backend.prime_repository_cargo_cache(workspace, timeout=self.timeout()) != 0:
                    return False
                cargo_checks = backend.repository_cargo_cache_preflight(workspace)
                for check in cargo_checks:
                    self.log(f"cargo-cache: {check.state.value} {check.label}: {check.detail}")
                if any(item.required and item.state in {CheckState.BLOCKED, CheckState.FAIL} for item in cargo_checks):
                    self.log("Cargo cache provisioning did not make Cargo.lock available offline")
                    return False

        run_setup = bool(prepare_cfg.get("run_repository_setup", True))
        setup_needed = self.workspaces.setup_needed(workspace)
        if run_setup and (force_setup or setup_needed):
            self.log("Running canonical repository development setup (bootstrap + frozen UV sync + local hooks)")
            if self.setup_development(workspace) != 0:
                return False
        elif setup_needed:
            self.log("Workspace setup is incomplete and automatic repository setup is disabled")
            return False
        else:
            self.log("Repository development setup is already present; no bootstrap repetition needed")

        report = self.workspaces.preflight(workspace)
        self._log_report(report)
        if report.state in {CheckState.BLOCKED, CheckState.FAIL}:
            self.log(f"Development environment remains blocked: {report.state.value}")
            return False
        if report.state == CheckState.WARN:
            self.log("Development environment READY with non-blocking warnings")
        else:
            self.log("Development environment READY")
        return True

    def refresh_workspace_from_windows_source(self, workspace: Workspace) -> int:
        """Replace the Linux workspace from Windows without managing Git state.

        When a workspace already exists, create_from_windows_source preserves the
        complete previous directory as a sibling backup before installing the new import.
        """
        if not self.workspaces.workspace_exists(workspace):
            return self.workspaces.create_from_windows_source(workspace, timeout=self.timeout())
        return self.workspaces.create_from_windows_source(
            workspace, timeout=self.timeout(), replace_existing=True
        )

    def start_development(self, workspace: Workspace, *, open_shell: bool = True) -> bool:
        if not self.prepare_development_environment(workspace):
            self.log("Development start blocked because environment preparation did not complete")
            return False
        backend = self.backend(workspace)
        if workspace.bootstrap_on_start:
            if backend.execute_in_workspace(workspace, "bash tools/scripts/bootstrap.sh", "Bootstrap", timeout=self.timeout()) != 0:
                return False
        if open_shell:
            backend.open_shell(workspace)
        self.log(f"Development session ready: {workspace.workspace_id}")
        return True

    @staticmethod
    def effective_profile_output(profile: str) -> str:
        """Return the canonical generated projection path for one profile."""
        return f"generated/profiles/{profile.replace('-', '_')}/effective-profile.json"

    @classmethod
    def effective_profile_cli(
        cls,
        profile: str,
        overlays: tuple[str, ...] = (),
    ) -> str:
        """Build the repository-owned effective-profile generation command.

        The Control Panel does not compose profile authority itself. It invokes
        koa_assembly resolve-profile, which owns validation and deterministic
        projection semantics. This action writes only under generated/.
        """
        parts = [
            "uv", "run", "--project", "assembly", "python", "-m", "koa_assembly",
            "resolve-profile",
            "--profile", profile,
            "--output", cls.effective_profile_output(profile),
        ]
        for overlay in overlays:
            if overlay in OVERLAYS:
                parts.extend(("--overlay", overlay))
        return shell_join(parts)

    def generate_effective_profile(self, workspace: Workspace) -> int:
        overlays = tuple(
            str(value) for value in workspace.assembly.get("overlays", [])
            if str(value) in OVERLAYS
        )
        return self.backend(workspace).execute_in_workspace(
            workspace,
            self.effective_profile_cli(workspace.profile, overlays),
            f"Generate Effective Profile: {workspace.profile}",
            timeout=self.timeout(),
        )

    def assembly_args(self, workspace: Workspace, *, check: bool = False) -> list[str]:
        assembly = workspace.assembly
        profile = workspace.profile
        renderer = str(assembly.get("renderer", "systemd"))
        output = str(assembly.get("output", f"generated/koali/{workspace.workspace_id}-{renderer}"))
        args = ["assemble", "--profile", profile, "--renderer", renderer, "--output", output]
        for overlay in assembly.get("overlays", []):
            if overlay in OVERLAYS:
                args += ["--overlay", overlay]
        if check:
            args.append("--check")
        return args

    def build_plan(self, workspace: Workspace, *, check: bool = False) -> int:
        return self.run_koa(workspace, "Assemble deployment plan", self.assembly_args(workspace, check=check))

    def build_image(self, workspace: Workspace) -> int:
        cfg = self.config.get("build", {}).get("image", {})
        if not cfg.get("enabled", False):
            self.log("Build Image is not configured/enabled")
            return 78
        args = cfg.get("args", [])
        if isinstance(args, list) and args:
            return self.run_koa(workspace, "Build system image", ["build-image", *[str(x) for x in args]], timeout=max(self.timeout(), 3600))
        custom = str(cfg.get("custom_command", "")).strip()
        if custom:
            return self.backend(workspace).execute_in_workspace(workspace, custom, "Build system image", timeout=max(self.timeout(), 3600))
        self.log("Build Image is enabled but no structured args are configured")
        return 78

    def bootstrap(self, workspace: Workspace, *, offline: bool = False) -> int:
        command = "bash tools/scripts/bootstrap.sh" + (" --offline" if offline else "")
        return self.backend(workspace).execute_in_workspace(workspace, command, "Bootstrap" + (" offline" if offline else ""), timeout=self.timeout())

    def setup_development(self, workspace: Workspace) -> int:
        return self.backend(workspace).execute_in_workspace(workspace, "bash tools/scripts/setup-development.sh", "Setup development", timeout=self.timeout())

    def services_command(self, workspace: Workspace, action: str) -> int:
        cfg = workspace.services
        mode = str(cfg.get("mode", "disabled"))
        backend = self.backend(workspace)
        if mode == "disabled":
            self.log("Development services are disabled for this workspace")
            return 78
        if mode == "custom":
            command = str(cfg.get("custom", {}).get(f"{action}_command", "")).strip()
            if not command:
                self.log(f"Custom services action is not configured: {action}")
                return 78
            return backend.execute_in_workspace(workspace, command, f"Services {action}", timeout=self.timeout())
        if mode != "compose":
            self.log(f"Unknown services mode: {mode}")
            return 78
        engine = str(cfg.get("engine", "docker"))
        compose_file = str(cfg.get("compose_file", "dev/local-services/compose.yaml"))
        profile = str(cfg.get("compose_profile", "local-services"))
        env = cfg.get("environment", {})
        exports = " ".join(f"{key}={shlex.quote(str(value))}" for key, value in env.items() if str(value))
        base = [engine, "compose", "-f", compose_file]
        if profile:
            base += ["--profile", profile]
        if action == "start":
            cmd = shell_join(base + ["up", "-d"])
        elif action == "status":
            cmd = shell_join(base + ["ps"])
        elif action == "stop":
            cmd = shell_join(base + ["down"])
        else:
            return 78
        if exports:
            cmd = f"env {exports} {cmd}"
        return backend.execute_in_workspace(workspace, cmd, f"Services {action}", timeout=self.timeout())
