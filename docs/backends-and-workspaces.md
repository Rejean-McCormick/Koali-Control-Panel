# Backends and workspaces

## Backend abstraction

`ExecutionBackend` defines the common operations used by higher-level orchestration:

- capture/run shell commands;
- start/preflight backend;
- resolve paths;
- open shell/editor;
- execute/capture a command in a workspace.

The concrete implementations are `WslBackend`, `NativeLinuxBackend`, and `WindowsBackend`.

## WSL backend

The supplied development configuration is centered on **WSL2 Ubuntu 24.04**.

`WslBackend` handles:

- WSL distribution discovery and registration checks;
- first-run/interactive preparation;
- runtime and systemd preflight;
- Windows-to-Linux path translation;
- profile tool probing/provisioning;
- repository-declared Rust toolchain checks/provisioning;
- native `cc` compile/link readiness;
- Cargo locked/offline cache readiness;
- shell/editor/explorer launch helpers.

### Provisioning boundary

There are two distinct layers:

1. **Profile/backend prerequisites** — configured by the Control Panel, such as base Ubuntu packages and `uv` support.
2. **Repository-specific requirements** — discovered from the active repository and provisioned to match repository contracts.

The Control Panel intentionally does not pin a repository Rust version in its own configuration.

## Native Linux backend

`NativeLinuxBackend` provides the same execution contract for a direct Linux host. It does not expose the WSL-specific automatic provisioning logic.

## Windows backend

`WindowsBackend` uses PowerShell and is used by configured Windows-hosted products such as Konnaxion/Koali Spaces in the supplied configuration.

## Workspace model

A `Workspace` contains:

```text
workspace_id
repository
backend
profile
root
windows_source
checkout_ref
bootstrap_on_start
assembly
services
```

`WorkspaceManager` resolves configured workspaces and owns workspace-specific preflight/import logic.

## WSL workspace strategy

The default model keeps the active kOA-Linux development workspace on the Linux filesystem:

```text
Windows checkout  C:\mycode\kOA-Linux\koa-linux
        |
        | source import/refresh
        v
WSL workspace     {home}/work/koa-linux
```

This avoids treating the Windows checkout path itself as the Linux build workspace while still allowing Windows-side editing/source management.

## Source freshness

`WorkspaceManager.source_sync_status()` compares the configured Windows source with the imported Linux workspace using repository/source fingerprints. It can report states such as current, legacy/source-changed, or unavailable.

When automatic refresh is enabled and refresh is required, the workspace is replaced from the Windows source and setup is forced again.

## Refresh safety

When replacing an existing Linux workspace, the implementation preserves the previous workspace as a sibling folder backup before installing the refreshed import.

The transfer excludes/transforms only what the implementation explicitly handles; it does **not** enforce Git branch/index/reset/stash/clean state. The Control Panel's job is source/workspace convergence, not source-control policy.

## Workspace preflight

Workspace preflight aggregates `CheckResult` values into a `PreflightReport`:

- required `FAIL` dominates;
- then required `BLOCKED`;
- then required `WARN`;
- otherwise `PASS`.

Environment preparation considers `BLOCKED`/`FAIL` fatal and permits a READY state with non-blocking warnings.

## Creating a new workspace configuration

Add an entry under `workspaces`, then set `environment.default_workspace` if it should become the default. At minimum, choose:

- repository id;
- backend;
- profile;
- backend-visible root;
- Windows source if using the import/sync model;
- assembly renderer/output.

Validate the resulting model with:

```powershell
python .\koali-control.pyw --self-test
```
