# Development workflows

## Current focus

The current workflow metadata is:

```text
current focus        core_stabilization
qualification scope  koali_core_pre_subsystem
final profile         sovereign-linux-node
```

This is intentionally **non-sequential**. It describes the current qualification focus; it does not prohibit independent integration/product work when real dependencies are satisfied.

## Core stabilization

The primary development action is **STABILIZE KOALI CORE**.

Conceptually:

```text
prepare development environment
  -> backend/workspace/toolchain convergence
  -> repository development setup if needed
  -> workspace preflight
  -> LevelUpDiag stabilization campaign
```

This path is pre-subsystem and must not be interpreted as final-profile qualification.

## Daily core cycle

The UI also exposes **DAILY CORE CYCLE**, intended to combine normal development startup/core stabilization with opening the development editor/shell workflow.

The exact repository operations remain delegated through `Orchestrator`/LevelUpDiag rather than being duplicated inside the UI.

## Core stability check

**CORE STABILITY CHECK** performs environment preflight plus the core diagnostic campaign without claiming final-profile assembly.

## Core runtime diagnostics

Once a real image/QEMU execution context exists, **CORE RUNTIME DIAGNOSTICS** can exercise the pre-subsystem runtime diagnostic path. Missing QEMU context remains a visible blocker; the Control Panel does not synthesize semantic runtime values to make the diagnostic runnable.

## Debug

**DEBUG PRINCIPAL** is broader diagnosis. The local debug layer is designed to remain useful during incomplete architecture/final-target work, while still surfacing expected blockers instead of reclassifying them as success.

## Development start

`Orchestrator.start_development()` depends on successful environment preparation. It can then apply configured startup behavior for the active workspace and open the backend shell when requested.

## Repository command delegation

Finite kOA operations use repository-owned command surfaces. The standard helper uses:

```text
uv run --frozen python -m koa_tools.cli --repository-root $PWD ...
```

Examples of orchestration responsibilities in the code include:

- repository bootstrap/setup;
- services command delegation;
- effective-profile generation;
- assembly plan/check;
- image build;
- component build.

The Control Panel assembles arguments and context; kOA-Linux owns the actual repository semantics.

## Effective profile and final assembly

Final-target operations are deliberately separate from core stabilization. The UI keeps actions for effective-profile generation, assembly check, strict final assembly, image building, validation, and release-oriented diagnostics.

Selecting a final build profile is not supposed to rewrite the normal development workspace profile (`developer-windows-wsl`).

For the canonical `sovereign-linux-node` target, unresolved external subsystem admission can legitimately block final qualification.

## Component builds

Component builds call:

```text
uv run --frozen python -m koa_tools.commands.build_component \
  --component <id> \
  --source-date-epoch <epoch>
```

This direct module call is intentional because `build-component` is not represented as a public subcommand of `koa_tools.cli`.

If the `koa-node-agent` Rust build fails under WSL, the orchestrator can replay the canonical Cargo build in a diagnostic temporary context so the compiler/offline-cache error is visible.

## Bring Koali to ready

The higher-level integrated development flow represented in the project documentation is:

```text
prepare WSL kOA-Linux workspace
  -> core stabilization / diagnostics
  -> integration gate(s)
  -> validate/test/build configured products
  -> start product runtimes
  -> verify product health
  -> apply/verify Koali Spaces integration
  -> open Koali surface
```

The product/runtime part of that sequence is implemented by `DevStackOrchestrator`; see [Products and Dev Stack](products-and-dev-stack.md).
