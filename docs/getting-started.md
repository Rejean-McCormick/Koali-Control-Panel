# Getting started

## Requirements

The default configuration targets a Windows development host with:

- Python 3 able to launch the Tkinter application;
- WSL2 with the configured `Ubuntu-24.04` distribution;
- the kOA-Linux Windows checkout configured as the workspace source;
- optional external product repositories such as Konnaxion and Koali Spaces at paths declared in `koali-control.json`.

The WSL backend can provision its configured Ubuntu profile tools, the repository-declared Rust toolchain, native build prerequisites, and the locked Cargo dependency cache. Repository setup itself remains repository-owned.

## Launch

From the repository root on Windows:

```bat
LAUNCH.cmd
```

`LAUNCH.cmd` prefers `pyw.exe -3`, falls back to `pythonw.exe`, and finally uses `python` to execute `koali-control.pyw`.

For a console launch:

```powershell
python .\koali-control.pyw
```

## Validate the local Control Panel model

Run the built-in self-test before debugging external repositories:

```powershell
python .\koali-control.pyw --self-test
```

The self-test verifies the schema-4 configuration/model surface, including registered products, Koali Spaces integration, the default workspace, renderer/profile values, and stabilization campaign mapping. It does **not** prove that an external workspace, product runtime, or final system image is healthy.

To inspect the merged/normalized configuration:

```powershell
python .\koali-control.pyw --print-config
```

## Recommended first workflow

The default development path is core-first:

```text
Launch Control Panel
  -> select/confirm koa-linux-main
  -> PREPARE / STABILIZE KOALI CORE
  -> let backend and workspace preflights converge
  -> run LevelUpDiag stabilization
  -> integrate/start product runtimes only when needed
```

The primary UI action is **STABILIZE KOALI CORE**. It prepares the development environment and then delegates the configured `stabilization` campaign to LevelUpDiag-Koali.

## What PREPARE does

For the selected workspace, environment preparation is intentionally idempotent and follows this shape:

1. Start the selected backend.
2. Run backend preflight checks.
3. On WSL, provision missing configured profile tools when possible.
4. Create or refresh the Linux-filesystem workspace from the configured Windows source when required.
5. Read the repository Rust requirement and provision that exact toolchain if missing.
6. Prove the native compiler/linker prerequisite and provision configured Ubuntu build packages if required.
7. Prove the locked Cargo cache is available offline; prime it when configured and required.
8. Run canonical repository development setup when setup is missing or the workspace was refreshed.
9. Run workspace preflight and report READY, READY with warnings, or blocked/fail.

The Control Panel does not reproduce repository bootstrap semantics and does not inspect or mutate Git branch/index state as part of this convergence.

## Default workspace

The supplied configuration uses:

```text
workspace id   koa-linux-main
repository     koa-linux
backend        wsl
profile        developer-windows-wsl
Linux root     {home}/work/koa-linux
Windows source C:\mycode\kOA-Linux\koa-linux
renderer       systemd
```

Paths are configuration, not hard-coded requirements. Change them in `koali-control.json` to match the local machine.

## Next reading

- [Backends and workspaces](backends-and-workspaces.md)
- [Development workflows](development-workflows.md)
- [Configuration](configuration.md)
