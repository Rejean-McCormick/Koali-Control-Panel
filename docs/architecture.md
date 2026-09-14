# Architecture

## System boundary

Koali Control Panel is a Tkinter control surface over a set of orchestration/services classes. The UI initiates operations, but long-running behavior is delegated to dedicated components.

```text
ControlApp (UI)
├── ConfigStore
├── WorkspaceManager
├── ExecutionBackend
│   ├── WslBackend
│   ├── NativeLinuxBackend
│   └── WindowsBackend
├── Orchestrator
├── ProductRegistry
├── ProcessSupervisor
├── DevStackOrchestrator
│   └── KoaliSpacesIntegrationController
├── LevelUpDiagAdapter
├── DebugDiagnosticsRunner
└── QemuEnvironmentManager
```

## Authority model

```text
Koali Control Panel
├── prepares backend/workspace/toolchain context
├── invokes repository-owned commands
├── supervises development runtimes
├── stores/provides QEMU execution context
└── presents delegated diagnostics

kOA-Linux
├── owns repository contracts
├── owns profiles and admission rules
├── owns assembly/build-image behavior
└── owns release evidence

LevelUpDiag-Koali
├── owns diagnostic planning
├── invokes kOA-Linux validators/tests
└── owns findings and verdict semantics

Koali Spaces
└── owns Space composition/activation semantics and manifests
```

This separation is intentional. The Control Panel should not create substitute repository semantics merely to make a workflow pass.

## Module responsibilities

### `koali_control/app.py`

`ControlApp` constructs the Tkinter interface and wires UI actions to the underlying services. It exposes tabs/actions for home, workspaces, development, Dev Stack, diagnostics, build, system test, environment, settings, and logs.

The UI also coordinates asynchronous actions and event/log publication, but it is not the owner of long-running child processes.

### `koali_control/config.py`

Owns:

- supported profiles/renderers/overlays;
- schema-4 default configuration;
- configuration migrations and normalization;
- QEMU configuration-to-environment mapping;
- `ConfigStore.load()` / `ConfigStore.save()`.

Loaded configuration is deep-merged with defaults and normalized so older supported configuration shapes can continue to work.

### `koali_control/models.py`

Defines the small shared model layer:

- `CheckState`: `PASS`, `WARN`, `BLOCKED`, `FAIL`, `INFO`;
- `CheckResult`;
- `PreflightReport` with aggregate state rules;
- `Workspace` and its config conversion helpers.

### `koali_control/backends.py`

Defines the execution abstraction and concrete backends:

- `ExecutionBackend` base contract;
- `WslBackend` for WSL2 lifecycle, path translation, preflight, toolchain provisioning and repository build prerequisites;
- `NativeLinuxBackend`;
- `WindowsBackend`.

Backend methods provide shell execution, capture, path resolution, preflight, and shell/editor launch behavior.

### `koali_control/workspaces.py`

`WorkspaceManager` owns configured workspace lookup, repository marker checks, source freshness, preflight, and import of a Windows checkout into a Linux-filesystem WSL workspace.

Workspace refresh preserves the previous Linux workspace as a folder backup before replacing it. Source transfer intentionally avoids Git-state policy.

### `koali_control/orchestration.py`

`Orchestrator` owns one-shot development and build orchestration around the kOA repository surface. Important responsibilities include:

- development-environment convergence;
- repository setup/bootstrap invocation;
- effective-profile generation;
- assembly/build plan calls;
- public image-build invocation;
- component builds via the repository command module;
- optional diagnostic replay for a failing Rust component build.

### `koali_control/process.py`

`ProcessRunner` executes finite commands, captures output, streams logs, handles Windows output encodings, and can stop the active command.

### `koali_control/supervisor.py`

`ProcessSupervisor` owns persistent development processes. It tracks managed processes, launches backend-aware shell commands, stops one/all processes, and returns runtime snapshots.

### `koali_control/products.py`

`ProductRegistry` converts the `products` configuration into product/service specifications. It resolves installed roots, discovers commands when supported, runs one-shot product actions, starts/stops supervised services, checks health, and opens product URLs.

A product can be a single runtime or a composite of multiple persistent services under one product identity.

### `koali_control/devstack.py`

`DevStackOrchestrator` composes multiple products into a development stack. It runs integration gates, product preparation actions, Koali Spaces integration hooks, product startup, aggregate health checks, and reverse-order shutdown.

### `koali_control/spaces_integration.py`

`KoaliSpacesIntegrationController` is the generic boundary between Control Panel orchestration and Koali-owned Space activation. It supports `delegated` and `legacy_projection` modes and performs declarative verification without embedding product-specific Space semantics into the controller.

### `koali_control/spaces_pilot.py`

`LegacyKoaliSpacesPilotState` is the compatibility implementation for the explicit `legacy_projection` mode. It materializes/cleans the legacy development projection state.

### `koali_control/levelupdiag.py`

`LevelUpDiagAdapter` invokes configured LevelUpDiag campaigns/levels in the active backend, supplies QEMU context, and presents bounded evidence. It does not redefine LevelUpDiag verdict semantics.

### `koali_control/debugdiag.py`

`DebugDiagnosticsRunner` provides local read-only pipeline diagnosis. It is intentionally non-conformance-authoritative and can demote configured architecture formalities to warnings for DEBUG use.

### `koali_control/qemu.py`

`QemuEnvironmentManager` prepares QEMU infrastructure/context and preflights the execution environment. It can discover canonical repository-owned images/effective profiles and provision QEMU packages on WSL, but it does not invent missing semantic runtime markers.

## Command ownership

The main kOA command helper is constructed as:

```text
uv run --frozen python -m koa_tools.cli --repository-root $PWD ...
```

Component building is a deliberate exception: it invokes the repository module `koa_tools.commands.build_component` directly because that command is not exposed through the public `koa_tools.cli` command catalog.

## Data/control flow

A typical development action flows through:

```text
UI action
  -> selected Workspace model
  -> Orchestrator / DevStackOrchestrator / diagnostic adapter
  -> backend execution
  -> repository/product command
  -> log/result publication
  -> UI status refresh
```

Persistent runtime ownership flows through `ProductRegistry` -> `ProcessSupervisor`, rather than through the UI thread.
