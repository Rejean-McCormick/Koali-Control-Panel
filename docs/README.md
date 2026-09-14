# Koali Control Panel documentation

This directory is the maintained technical documentation for **Koali Control Panel 4.0.0**.

Koali Control Panel is a local development control surface. It prepares execution backends and workspaces, delegates repository-owned operations to kOA-Linux, supervises configured development products, and presents diagnostics delegated to LevelUpDiag-Koali. It does **not** replace repository contracts, invent subsystem admission, or own diagnostic verdict semantics.

## Documentation map

| Document | Purpose |
| --- | --- |
| [Getting started](getting-started.md) | Launch, self-test, first-run expectations, and the shortest path to a ready development environment. |
| [Architecture](architecture.md) | Component boundaries, module responsibilities, and authority model. |
| [Configuration](configuration.md) | `koali-control.json` schema, major sections, profiles, products, Dev Stack, and QEMU settings. |
| [Backends and workspaces](backends-and-workspaces.md) | WSL2, native Linux, Windows, workspace import/sync, and toolchain preparation. |
| [Development workflows](development-workflows.md) | Core stabilization, daily development, build/final-profile paths, and one-click orchestration. |
| [Products and Dev Stack](products-and-dev-stack.md) | Modular products/services, gates, health checks, startup and shutdown. |
| [Koali Spaces integration](koali-spaces-integration.md) | Delegated vs legacy projection modes and the Space authority boundary. |
| [QEMU and diagnostics](qemu-and-diagnostics.md) | QEMU execution context, readiness, LevelUpDiag delegation, and diagnostic scope. |
| [Testing](testing.md) | Self-test, unit-test suite, test areas, and safe validation commands. |

## Existing repository references

The following root-level documents remain useful historical/reference material:

- [`../README.md`](../README.md) — project overview, workflow summary, and version history.
- [`../KOALI_DEVELOPMENT_ENVIRONMENT_REFERENCE.md`](../KOALI_DEVELOPMENT_ENVIRONMENT_REFERENCE.md) — frozen development-environment reference.
- [`../UPDATE_KCP4_KOALI_SPACES_INTEGRATION.md`](../UPDATE_KCP4_KOALI_SPACES_INTEGRATION.md) — 4.0 Koali Spaces integration update notes.

## Core invariants

1. **kOA-Linux is authoritative** for repository contracts, profile semantics, source/package admission, assembly, image construction, and release evidence.
2. **LevelUpDiag-Koali is authoritative** for diagnostic planning, findings, and verdicts.
3. **Koali Control Panel orchestrates** backend/workspace preparation, repository command invocation, product supervision, QEMU context, and presentation.
4. **Git state is not autonomously managed** by the Control Panel. Workspace import/sync copies source content without branch/index/reset/stash/clean policy.
5. **Core stabilization is not final-target qualification.** The current focus is `core_stabilization`; the strict final profile remains `sovereign-linux-node`.
