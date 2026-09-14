# Configuration

The primary configuration file is [`../koali-control.json`](../koali-control.json). The current schema is **4**.

`ConfigStore.load()` deep-merges the file with `DEFAULT_CONFIG` and then applies compatibility normalization/migrations. This means omitted values normally inherit defaults, while explicitly configured values override them.

## Top-level sections

```text
schema_version
app
environment
backends
workspaces
diagnostics
workflow
products
dev_stack
build
system_test
```

## `app`

| Key | Default | Meaning |
| --- | --- | --- |
| `terminal_exe` | `wt.exe` | Terminal executable used by UI/backend helpers. |
| `editor_exe` | `code` | Editor executable. |
| `command_timeout_seconds` | `1800` | Base timeout for finite commands. |
| `open_shell_on_start` | `true` | Whether development start may open a shell after preparation. |

## `environment`

Important values:

- `default_backend`: default backend identifier.
- `default_workspace`: workspace selected by default.
- `prepare.auto_create_workspace`: allow creation/import of a missing workspace.
- `prepare.auto_refresh_workspace_from_windows`: refresh a WSL workspace when the configured Windows source changed.
- `prepare.run_repository_setup`: allow canonical repository setup when needed.

## `backends`

### WSL

The default WSL backend expects:

```text
distribution             Ubuntu-24.04
expected_wsl_version     2
expected_distribution_id ubuntu
expected_release         24.04
require_systemd           true
shell                     bash
```

`toolchain_provisioning` controls host/profile prerequisites such as Ubuntu packages, `uv`, Rust provisioning support, native build packages, and Cargo cache priming.

Repository-specific Rust versions are **not** declared here. They are read from repository contracts such as `rust-toolchain.toml`.

### Native Linux / Windows

Both are enabled by default. Native Linux uses `bash`; Windows uses `powershell.exe`.

## `workspaces`

A workspace maps a repository checkout to an execution backend.

Example shape:

```json
{
  "workspaces": {
    "koa-linux-main": {
      "repository": "koa-linux",
      "backend": "wsl",
      "profile": "developer-windows-wsl",
      "root": "{home}/work/koa-linux",
      "windows_source": "C:\\mycode\\kOA-Linux\\koa-linux",
      "checkout_ref": "current",
      "bootstrap_on_start": false,
      "assembly": {
        "renderer": "systemd",
        "overlays": [],
        "output": "generated/koali/developer-windows-wsl-systemd"
      },
      "services": {}
    }
  }
}
```

### Supported profile values

The code recognizes:

```text
user-lightweight
developer-linux-workstation
developer-windows-wsl
sovereign-linux-node
sovereign-hub
build-farm
control-plane
high-assurance
sovereign-offline
appliance-shell
```

### Supported renderers

```text
systemd
quadlet
compose
kubernetes
image
offline-bundle
```

Supported overlay identifiers are `high-assurance`, `sovereign-offline`, and `appliance-shell`.

## `diagnostics`

`diagnostics.debug` configures local debug behavior.

`diagnostics.levelupdiag` configures the external LevelUpDiag-Koali root and named campaign roles. The supplied mapping is:

```text
stabilization         -> stabilization
stabilization_runtime -> stabilization-runtime
developer             -> debug
build                 -> stabilization
run_all               -> validation
release               -> release
delivery              -> delivery
```

## `workflow`

The schema-4 workflow is descriptive, not a sequential phase machine.

Default values include:

```text
current_focus       core_stabilization
qualification_scope koali_core_pre_subsystem
final_profile       sovereign-linux-node
```

`external_subsystems` records workflow/admission status metadata. These values do not grant source locks, package admission, or final-profile conformance.

## `products`

Each product can declare:

| Key | Meaning |
| --- | --- |
| `label` | Human-readable name. |
| `enabled` | Whether the product participates. |
| `optional` | Whether a missing/start-failing product may be skipped by supported flows. |
| `backend` | Execution backend for product commands. |
| `roots` | Candidate installation roots. |
| `marker` | File used to recognize a valid root. |
| `commands` | Named one-shot/start actions. |
| `environment` | Product-level environment variables. |
| `open_url` | Browser URL. |
| `health_url` | Product-level health URL. |
| `services` | Optional persistent services under one product identity. |

The supplied config registers `konnaxion`, `koali-spaces`, `konnaxion-capsule-manager`, and disabled optional `orgo`.

### Composite services

A service can override backend/root/marker/command/environment/health URL. Konnaxion, for example, is modeled as `api` + `web`; the capsule manager as `agent` + `manager`.

## `dev_stack`

Important keys:

- `products`: ordered product composition;
- `default_product_actions`: preparation actions used when no per-product override exists;
- `product_actions`: per-product action order;
- `gates`: finite commands that must pass before product preparation/start;
- `startup_timeout_seconds`;
- `command_timeout_seconds`;
- `koali_spaces_integration`.

A gate can specify `id`, `label`, `workspace`, `command`, and `enabled`.

## `dev_stack.koali_spaces_integration`

Supported modes are:

- `delegated`: invoke Koali-owned configured product actions and then verify public acceptance criteria;
- `legacy_projection`: use the explicit compatibility projection implementation.

See [Koali Spaces integration](koali-spaces-integration.md).

## `build`

`build.image` controls optional public image-build invocation:

```json
{
  "enabled": false,
  "args": [],
  "custom_command": ""
}
```

`full_cycle_requires_image` controls whether a full UI cycle requires an image step.

## `system_test.qemu`

This section stores QEMU infrastructure and execution context, including image path/format, network mode, expected identity, readiness regexes, effective profile/release set paths, and N09/N10-related values.

The code maps these settings to `KOA_QEMU_*` environment variables when delegating diagnostics. Path fields are resolved relative to the workspace when appropriate.

Default WSL QEMU provisioning uses Ubuntu packages:

```text
qemu-system-x86
qemu-utils
ovmf
```

See [QEMU and diagnostics](qemu-and-diagnostics.md).

## Editing configuration safely

After editing `koali-control.json`:

```powershell
python .\koali-control.pyw --self-test
python .\koali-control.pyw --print-config
```

The first catches core model/configuration inconsistencies. The second shows the normalized configuration actually loaded by the application.
