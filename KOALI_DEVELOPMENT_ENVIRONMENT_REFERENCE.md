# Koali Development Environment Reference — Control Panel 4.0.0

## 1. Naming

- **Koali**: the broader product/development environment being built.
- **Koali Control Panel**: the current control surface used to prepare and operate development backends/workspaces.
- **kOA-Linux**: the technical operating-system/platform repository being developed.
- **LevelUpDiag-Koali**: the standalone diagnostic/validation appendix invoked by Koali Control Panel.

`Koali` and `Koali Control Panel` are not synonyms.

## 2. Stable architecture

```text
Koali
└── Development Environment
    ├── Workspace Management
    ├── Toolchain Management
    ├── Service Management
    ├── Build
    ├── System Test
    └── Execution Backend
        ├── WSL2 Linux
        ├── Native Linux
        └── QEMU / VM
```

Diagnostics are delegated:

```text
Koali Control Panel
└── diagnostics presentation/delegation
    └── LevelUpDiag-Koali
        └── kOA-Linux public validators/tests
```

## 2.1. Current qualification focus

The current non-sequential development focus is `core_stabilization` with scope `koali_core_pre_subsystem`. The final canonical profile remains `sovereign-linux-node`. The focus is descriptive workflow metadata, not a phase gate: integration, platform and product work may proceed whenever their actual dependencies are satisfied.

```text
Konnaxion          placeholder until real integration
Ariane             deferred until Koali integration testing
Orgo               draft / not admitted
Semantik Architect deferred / not admitted
```

These states are workflow metadata only. They never authorize fake source pins, fake package resolution, fake activation, or partial final-profile conformance.

## 3. Backend model

Ubuntu 24.04 under WSL2 is the current Windows-host execution backend. It is not Koali's architectural foundation.

```text
today: Koali Control Panel → WSL/Ubuntu → Dev Workspace
later: Koali Control Panel → Native Linux → Dev Workspace
```

The mutable workspace belongs in the Linux filesystem, for example `/home/<user>/work/koa-linux`, not under `/mnt/c`.

## 4. QEMU boundary

Koali Control Panel owns QEMU **infrastructure and execution context**. LevelUpDiag N08/N09/N10 own diagnostic execution/result/verdict/evidence using public kOA-Linux surfaces.

Koali Control Panel may operationally check QEMU/UEFI availability, image-file usability, context-path usability, and whether execution-context values required before delegation are present. It must not implement kOA-Linux QEMU test semantics itself.

## 5. Repository-aware QEMU convergence

The one-click operation is:

```text
PREPARE QEMU ENVIRONMENT
```

On Ubuntu WSL it can provision:

```text
qemu-system-x86
qemu-utils
ovmf
```

UEFI discovery accepts the Ubuntu 24.04/Noble 4M OVMF code image (`/usr/share/OVMF/OVMF_CODE_4M.fd`) and retains older/common OVMF/EDK2 code-image layouts as fallbacks.

Safe automatic discovery is restricted to repository-owned facts:

- `packaging/system/image.toml` package status, target profile and activation-ready flag;
- existence of the public `tools/src/koa_tools/commands/build_image.py` surface;
- generated system-image candidates whose sibling `<image>.build.json` confirms the canonical system-image metadata contract;
- generated effective-profile JSON files exposing `session_runtime.surfaces`;
- an explicitly configured structured `build.image.args --output`.

The QEMU `active_profile` is an **effective-profile JSON**, not `profiles/implementation-settings/<developer-profile>.toml`. Koali Control Panel 2.5.0 clears that obsolete 2.3.1 auto-derived TOML value during PREPARE.

Koali Control Panel must not invent:

- admitted kernel/rootfs/initramfs/boot/recovery inputs;
- disk-image backend executables;
- expected release identity;
- compositor/session/confinement ready regexes;
- denied-surface regexes;
- navigation markers/qcodes;
- Mediatheque semantic evidence values.

Those remain repository/runtime-owned inputs.

## 6. Scoped QEMU readiness

The public gates currently have distinct operational contexts:

```text
N08 Security
  image + release identity + confinement/denial markers

N09 Offline
  image + effective-profile JSON + navigation context
  + conditional Mediatheque context

N10 System
  image + release identity + compositor/session markers
```

**QEMU PREFLIGHT** checks N08+N09+N10 together. **RUN SYSTEM DIAGNOSTICS (N10)**, **BUILD + BOOT**, and the QEMU stage of **FULL CYCLE** use N10-only preflight so unrelated N09 fields cannot block N10 delegation.

`READY` means Koali Control Panel has enough operational context to delegate the requested level(s). `NOT READY` is not a kOA-Linux diagnostic verdict.

## 7. Image-build authority

Koali Control Panel may invoke the public kOA-Linux `build-image` command when `build.image.args` already supplies its explicit inputs. It does not infer or fabricate missing build arguments and does not implement an alternate disk-image backend.

When `packaging/system/image.toml` reports a blocked system-image package and no canonical generated image exists, Koali Control Panel surfaces that repository-owned status as the image-readiness blocker.

## 8. One-click principle

Repeated shell commands are implementation details. Stable operations belong behind Koali Control Panel actions where they can be deterministic and self-diagnosing.

```text
STABILIZE KOALI CORE
PREPARE DEVELOPMENT ENVIRONMENT
CORE STABILITY CHECK
CORE RUNTIME DIAGNOSTICS
DEBUG PRINCIPAL
PREPARE QEMU ENVIRONMENT
QEMU PREFLIGHT
RUN SYSTEM DIAGNOSTICS (N10)
FINAL PROFILE ASSEMBLY
BUILD + BOOT
```

## 9. Current source organization

```text
koali-control.pyw                 entry point
koali-control.json                configuration
koali_control/app.py              GUI/control surface
koali_control/backends.py         execution backends
koali_control/workspaces.py       workspace lifecycle
koali_control/orchestration.py    development/build orchestration
koali_control/levelupdiag.py      LevelUpDiag adapter
koali_control/qemu.py             QEMU infrastructure/context manager
koali_control/config.py           configuration schema/defaults
tests/test_core.py                Koali Control Panel self-tests
```


## Core-first workflow (2.5.0)

Core stabilization and final-target qualification are separate operations. `STABILIZE KOALI CORE` and `CORE STABILITY CHECK` delegate LevelUpDiag's pre-subsystem `stabilization` campaign and do not invoke final-profile assembly. `VALIDATION (FINAL TARGET)` and `FINAL PROFILE ASSEMBLY` remain strict and are allowed to block on unresolved independent subsystems.

The active development workspace remains `developer-windows-wsl`. Selecting `sovereign-linux-node` in the strict Build section creates a build-oriented workspace view rather than persisting that target over the development workspace profile.

The Control Panel must never edit external subsystem source locks to manufacture readiness.


## Windows source synchronization (2.4.0)

For `developer-windows-wsl`, the Windows checkout remains an import source and the Linux-filesystem workspace remains the mutable development workspace. PREPARE records a content fingerprint of the filtered Windows source outside the repository. PREPARE does not inspect or manage Git state. When a legacy or changed source is refreshed, the complete prior Linux workspace is first preserved as a sibling folder backup, then the filtered Windows source is imported atomically.

The import boundary excludes clearly local runtime/cache state such as `.levelupdiag/`, `.venv/`, `node_modules/`, Python bytecode and tool caches. Generic `build/`, `dist/`, and `*.egg-info` paths are preserved because they may be tracked repository content. PREPARE does not use Git cleanliness, branch, or remote synchronization as automatic DEBUG readiness gates.


## DEBUG versus strict validation (2.4.0)

**DEBUG PRINCIPAL** uses the repository's read-only `koa diagnose --pipeline --json` surface. A valid diagnostic report means DEBUG execution completed, even if formal architecture checks report errors. Those formal findings remain visible as warnings so they do not hide functional pipeline blockers.

**VALIDATION** and **RELEASE** remain strict LevelUpDiag operations. They may fail on architecture, ownership, evidence, clean-source, or other conformance requirements appropriate to validation/finalization.


## Effective-profile generation (2.4.6)

The effective profile is a deterministic, non-authoritative projection owned by kOA-Linux assembly semantics. The Control Panel must not compose or repair it itself.

The Build tab therefore exposes **Generate Effective Profile** for the selected canonical profile. The action delegates directly to:

```text
uv run --project assembly python -m koa_assembly resolve-profile ...
```

and writes only the repository-defined generated projection path. **Assemble --check** remains read-only and is expected to block on drift until this explicit generation action has synchronized the projection.

This preserves the distinction between generation and validation: a check never changes the artifact it is checking.

## Native compiler/linker readiness (2.4.5)

Rust and Cargo can be present while the host linker driver is still absent. kOA's Rust build therefore needs one additional host-development prerequisite: a working native `cc` compile/link path. PREPARE now verifies this whenever `rust-toolchain.toml` is present.

The sequence is:

1. locate `cc`;
2. compile and link a minimal C program entirely under `/tmp`;
3. if the probe is blocked, provision the configured Ubuntu native build package set (default: `build-essential`) as the distro root user;
4. repeat the temporary compile/link probe and require PASS before continuing;
5. leave the repository, `Cargo.lock`, generated artifacts, and Git state untouched.

This host prerequisite is prepared before the Cargo offline-cache proof and before repository setup. It is a development-environment concern only; it does not relax the strict kOA component builder or release requirements.

## Cargo locked dependency cache (2.4.4)

A freshly provisioned Rust toolchain does not imply that the dependencies named by `Cargo.lock` are present locally. kOA component builds intentionally run with `--locked --offline`, so PREPARE now treats offline Cargo dependency availability as part of development-environment readiness whenever `rust-toolchain.toml` is present.

The sequence is:

1. verify the exact repository Rust toolchain;
2. test `cargo fetch --locked --offline --manifest-path Cargo.toml`;
3. only if that test is blocked, run one networked `cargo fetch --locked --manifest-path Cargo.toml`;
4. immediately repeat the offline fetch as proof that the lock can be satisfied without network access;
5. keep the actual component build strict and offline.

This writes only to the normal user Cargo cache. It does not edit the repository, `Cargo.lock`, branch/index state, or release evidence. If the Rust component build subsequently fails, the Control Panel performs a temporary offline Cargo replay under `/tmp` to surface the full compiler/error output in the graphical log, then removes that temporary target directory.

## Repository-declared Rust toolchain (2.4.3)

When the active workspace contains `rust-toolchain.toml`, `PREPARE DEVELOPMENT ENVIRONMENT` treats that file as the sole authority for the Rust channel, profile, and components. On Ubuntu WSL it provisions `rustup` when needed, installs the declared toolchain under the normal Linux user, verifies `rustc`, `cargo`, and declared components, and makes `~/.cargo/bin` available to subsequent graphical actions. This provisioning does not inspect or modify Git state.


## Product and Dev Stack orchestration (4.0.0)

The kOA-Linux core remains prepared and qualified in WSL. Browser-facing products can be managed by a separate backend; the default schema-4 configuration runs Konnaxion and Koali Spaces with the native Windows Node/pnpm toolchain while preserving WSL for kOA-Linux.

`products` is declarative. Each product supplies candidate roots, an installation marker, optional explicit commands, environment, health URL, and open URL. A product may also declare named persistent `services`; the Control Panel supervises those services independently while reporting one aggregate product state. Common package scripts are auto-discovered when commands are omitted. `dev_stack` composes those products and defines integration gates and preparation actions.

The default Konnaxion product is composite: `api` runs Django/Uvicorn from `backend/` on port 8000 and `web` runs Next.js from `frontend/` on port 4300. The optional Konnaxion Capsule Manager is also composite: `agent` runs on 8765 and `manager` on 8714. Capsule Manager is not part of the normal Koali dev-stack product list.

`BRING KOALI TO READY` is the high-level development workflow. It prepares the core, runs LevelUpDiag stabilization, runs the Koali/Konnaxion adapter gate, validates/builds the products, starts the managed runtimes, waits for health, and opens Koali. This workflow does not change source locks or claim final-profile admission.

Long-running product processes are supervised independently of `ProcessRunner`, so a dev server does not block one-shot Control Panel commands. STOP DEV STACK terminates only processes owned by the panel; an already-running external server may be reused and is not killed by the panel.


### Konnaxion test database freshness

The Control Panel qualification gate invokes the Konnaxion backend tests with `pytest --create-db`. Konnaxion itself retains `--reuse-db` for normal developer loops, but integrated qualification must rebuild the isolated test database so current migrations are always materialized.

The same qualification action invokes the frontend Jest suite directly as `pnpm exec cross-env FORCE_COLOR=1 jest --runInBand`. Do not insert a standalone `--` before `--runInBand`: Jest treats arguments after that separator as test-path patterns, which can produce a false `No tests found` result.

## Koali Spaces integration authority (4.0.0)

`KoaliSpacesIntegrationController` is the only Dev Stack boundary for Koali Space admission orchestration. It has two explicit modes:

- `delegated` — final target. Control Panel invokes actions implemented by the Koali Spaces product and verifies public shell/module behavior. It does not compose manifests, ACPs, activation payloads, receipts or runtime policy.
- `legacy_projection` — compatibility-only strangler path for the current paired snapshot. It preserves the historical Home+Konnaxion development projection until Koali exposes its canonical compiler/invocation surface.

The legacy implementation remains isolated in `koali_control/spaces_pilot.py`. No new product integration may be added there. A second app must use owner-owned integration artifacts plus the Koali-owned generic path, not another `_product_manifest()` function.

Verification configuration may name expected module IDs and same-origin routes. These are acceptance criteria only; health/readiness and menu visibility never grant authority.

On shutdown, integration deactivation/release runs before managed product processes are stopped so delegated control actions may still reach Koali.

Koali Spaces launch policy: after the source validation/build/smoke gates pass, Control Panel runs the packaged production presentation runtime (`pnpm run start`) rather than the Next development presentation server. This avoids weakening the shell CSP solely for development hydration.
