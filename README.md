# Koali Control Panel 4.1.1

Koali Control Panel 4.1.1 keeps the existing development orchestration while introducing the final-target **Koali Spaces integration boundary**. Product runtimes remain autonomous, removable, and independently runnable. The Control Panel may start, validate and observe products, but Koali owns Space composition, activation semantics, manifests, ACP interpretation and receipts.


## 4.1.1 build-workflow hardening

The final-target workflow now handles two WSL build-state edges discovered during live `sovereign-linux-node` assembly:

- **Generate Effective Profile** removes only an **untracked** `assembly/uv.lock` created transiently by UV. A tracked lockfile is never removed. This prevents the Control Panel from making the source worktree dirty immediately before the repository's strict component builder.
- **Build Component / Build All Components** first runs the canonical `uv sync --frozen --all-groups`, then verifies that Git is clean before entering the repository's deliberately offline component build. This restores build backends such as `setuptools`/`wheel` after a fresh workspace import.
- **DEBUG PRINCIPAL** now targets the configured final profile (normally `sovereign-linux-node`) rather than the development workspace profile.

The repository remains authoritative: the Control Panel does not relax the clean-worktree rule, fabricate component bundles, or change subsystem admission.

## Development orchestration

```text
BRING KOALI TO READY
  -> prepare WSL kOA-Linux workspace
  -> LevelUpDiag stabilization
  -> Koali <-> Konnaxion adapter gate
  -> discover/validate/test/build configured products
  -> start Konnaxion
  -> start Koali Spaces
  -> health-check both runtimes
  -> open Koali in the browser
```

The UI is not the orchestration engine. Long-running runtimes are owned by `ProcessSupervisor`; product behavior comes from the `products` registry; integrated startup comes from `DevStackOrchestrator`. The existing one-shot kOA workflows continue to use the repository-owned commands and validators.

Default development endpoints are `Konnaxion -> 127.0.0.1:4300` and `Koali Spaces -> 127.0.0.1:4173`. Product roots and commands remain editable in `koali-control.json`. Konnaxion commands are auto-discovered from its `package.json` when not explicitly configured.

## Current focus

```text
current focus        core_stabilization
qualification scope  koali_core_pre_subsystem
final profile         sovereign-linux-node

Konnaxion            placeholder_until_integration
Ariane               deferred_until_koali_integration_test
Orgo                 draft_not_admitted
Semantik Architect   deferred_not_admitted
```

A placeholder is a workflow/development state only. It is **not** a pinned source lock, activation authority, package resolution, or release claim.

## Responsibility split

```text
Koali Control Panel
├── backend/workspace lifecycle
├── WSL/native toolchain preparation
├── core stabilization orchestration
├── component build orchestration
├── final-profile assembly controls
├── QEMU infrastructure/context
└── diagnostics presentation/delegation
        └── LevelUpDiag-Koali
            └── kOA-Linux validators/tests
```

kOA-Linux remains authoritative for contracts, profile semantics, package/source admission, assembly, image construction, and release evidence. Koali Control Panel does not autonomously manage Git state.

## Primary workflow — stabilize first

Launch with `LAUNCH.cmd`, then use:

```text
STABILIZE KOALI CORE
→ repeat until environment/contracts/native core are stable
→ integrate/test independent subsystems later
```

`STABILIZE KOALI CORE` prepares the development environment and then delegates the LevelUpDiag `stabilization` campaign. The core campaign is intentionally pre-subsystem and does not claim final-profile conformance.

Useful core actions:

- **STABILIZE KOALI CORE** — PREPARE + core stabilization diagnostics.
- **DAILY CORE CYCLE** — development startup + core stabilization + editor.
- **CORE STABILITY CHECK** — environment preflight + core diagnostic campaign; no final-profile assembly.
- **CORE RUNTIME DIAGNOSTICS** — pre-subsystem runtime diagnostics once an image/QEMU context exists.
- **DEBUG PRINCIPAL** — broader diagnosis; final-target blockers remain visible but do not redefine the core-stabilization scope.

## Final target remains strict

The Build tab keeps a separate **Final profile assembly (strict / subsystem-aware)** section. Actions such as **Generate Effective Profile**, **Assemble --check**, **FINAL PROFILE ASSEMBLY**, **Build System Image**, VALIDATION and RELEASE retain full target semantics.

For `sovereign-linux-node`, unresolved Konnaxion/Ariane/Orgo/Semantik Architect authority is therefore expected to block final-target qualification until each subsystem is genuinely integrated and admitted. 2.5.0 never rewrites their `source.lock.json` values merely to advance the core workflow.

The normal development workspace profile remains `developer-windows-wsl`; choosing a final build profile no longer overwrites that development context.

## Diagnostics

Recommended LevelUpDiag-Koali version: **2.2.0** at the canonical sibling path:

```text
C:\mycode\kOA-Linux\LevelUpDiag-Koali
```

Configured campaign roles:

```text
stabilization         -> stabilization
stabilization_runtime -> stabilization-runtime
developer             -> debug
build                 -> stabilization
run_all               -> validation
release               -> release
delivery              -> delivery
```

Strict validation/release can fail on canonical subsystem or conformance requirements. DEBUG/core stabilization do not convert those expected future-integration blockers into false core failures.

## Development environment closure already frozen

PREPARE retains the validated environment automation from 2.4.x:

- WSL2 / Ubuntu 24.04 checks;
- Python + `uv`;
- repository-declared Rust toolchain;
- Cargo locked offline cache preparation;
- native `cc` compile/link probe and Ubuntu `build-essential` provisioning when needed;
- canonical repository setup invocation;
- no autonomous Git branch/index/reset/stash/clean management.

## QEMU boundary

The System Test tab continues to own QEMU infrastructure/context only. LevelUpDiag and kOA-Linux own N08/N09/N10 diagnostic semantics and verdicts. Core runtime diagnostics are allowed before subsystem integration only when the required boot/runtime context actually exists; missing context remains visible rather than invented.

## Configuration

Primary configuration file: `koali-control.json`.

Relevant sections:

```text
environment
backends
workspaces
diagnostics.levelupdiag
workflow
products
dev_stack
build.image
system_test.qemu
```

`products` is the modular registry. A product declares roots, backend, commands/environment, health/browser URLs, and may declare multiple persistent `services` that are supervised under one product identity. `dev_stack` declares composition order, one-shot preparation actions, integration gates, and startup timeout. Removing or disabling a product does not require changing Control Panel Python code.

## Self-test

```powershell
python .\koali-control.pyw --self-test
```

The self-test verifies Control Panel configuration/model construction. It does not claim that the external kOA workspace or final target is qualified.

## Version 4.0.0 — Koali Spaces authority boundary

4.0.0 introduces a generic strangler boundary between development orchestration and Koali-owned Space semantics:

- configuration schema is `4`;
- `workflow.current_focus` replaces sequential `workflow.phase` terminology;
- `DevStackOrchestrator` depends on `KoaliSpacesIntegrationController`, not on the Konnaxion-specific pilot writer;
- `delegated` mode invokes repository-owned Koali product actions and verifies only public acceptance criteria;
- delegated activation fails closed when the Koali action is absent — Control Panel never falls back to fabricating a Space;
- verification is declarative and same-origin: required module IDs/routes live in configuration, not product branches in Python;
- `legacy_projection` is an explicit compatibility mode preserving the current Home+Konnaxion development pilot until the paired Koali Spaces repository exposes `SpaceActivationCompiler`/stable activation invocation;
- schema-3 `koali_spaces_pilot` settings migrate losslessly into the explicit legacy subsection;
- integration release/deactivation runs before managed products stop so a future Koali control endpoint remains available during shutdown.

### Final-target configuration

```json
{
  "dev_stack": {
    "koali_spaces_integration": {
      "enabled": true,
      "mode": "delegated",
      "product_id": "koali-spaces",
      "actions": {
        "activate": "<Koali-owned product action>",
        "deactivate": "<Koali-owned product action>"
      },
      "verify": {
        "modules": [
          {"module_id": "konnaxion", "required": true, "route": "/apps/konnaxion"}
        ]
      }
    }
  }
}
```

The action names are Control Panel orchestration configuration only. Their semantics and implementation must live in Koali Spaces.

## Version 3.0.6

3.0.6 completes the Konnaxion qualification and runtime-start fixes discovered by real Control Panel runs:

- retains `pytest --create-db` so the isolated Django test database is recreated with current migrations;
- runs the Konnaxion frontend Jest suite as `pnpm exec cross-env FORCE_COLOR=1 jest --runInBand`;
- starts Next.js directly as `pnpm exec cross-env FORCE_COLOR=1 next dev --turbo --hostname 127.0.0.1 --port 4300`, avoiding the pnpm `--` separator that Next 15 interpreted as a project directory;
- probes the actual public root route for Konnaxion Web readiness because `app/_api` is a private Next.js folder and does not publish a health route;
- automatically upgrades the canonical 3.0.1/3.0.2/3.0.3 Konnaxion commands and readiness URL while leaving custom commands untouched.

## Version 3.0.1

3.0.1 binds the orchestrator to the actual Konnaxion repository layout and adds composite-product supervision:

- Konnaxion remains one product in the UI but starts two supervised services: Django API on `127.0.0.1:8000` and Next.js Web on `127.0.0.1:4300`;
- `BRING KOALI TO READY` now prepares the Konnaxion frontend/backend, applies Django migrations, runs backend checks, backend tests, frontend tests/typecheck, and the frontend build before startup;
- Koali Spaces continues to use `127.0.0.1:4173` and frames Konnaxion at `http://127.0.0.1:4300`;
- the Konnaxion Capsule Manager is registered as an optional composite product with Agent `:8765` and Manager UI `:8714`; it is visible/manageable but intentionally excluded from the normal Koali dev stack;
- composite health and runtime status are aggregated under the parent product instead of exposing implementation services as separate products;
- STOP for a composite product terminates only the service processes owned by this Control Panel.

## Version 3.0.0

3.0.0 turns the Control Panel into a development orchestrator without turning products into hard dependencies:

- adds native-Windows product execution alongside the existing WSL/native-Linux backends;
- adds a declarative product registry for Konnaxion, Koali Spaces, and future optional products;
- auto-discovers common `package.json` scripts when a product command is not hard-coded;
- adds a persistent process supervisor with start/stop/status and Windows child-process-tree cleanup;
- adds the **Dev Stack** tab with product status, health, start/stop/open controls;
- adds **START KOALI DEV STACK** and **BRING KOALI TO READY** end-to-end workflows;
- runs the existing Koali/Konnaxion adapter test gate before integrated startup;
- validates/builds products before starting them and waits for health readiness;
- safely reuses already-running external product servers instead of starting duplicates;
- keeps Orgo disabled/optional until it is admitted and configured;
- preserves all 2.5 core stabilization, DEBUG, assembly, QEMU, validation, and release workflows;
- keeps LevelUpDiag diagnostic-only and leaves persistent runtime ownership to the Control Panel.

## Version 2.5.0

2.5.0 historically introduced a pre-subsystem core-stabilization workflow boundary (4.0.0 replaces sequential phase terminology with `current_focus`):

- adds explicit core stabilization and core-runtime LevelUpDiag campaign roles;
- makes **STABILIZE KOALI CORE** the primary environment progression path;
- separates core build checks from strict final-profile assembly;
- keeps Konnaxion as a declared placeholder workflow state, not a fabricated admitted source;
- defers Ariane, Orgo and Semantik Architect from core qualification while preserving their strict final-profile requirements;
- keeps final `sovereign-linux-node` validation/assembly available as a separate strict path;
- preserves the development workspace profile when selecting a final assembly target;
- integrates directly with LevelUpDiag-Koali 2.2.0 structured summaries, including qualification scope and deferred-subsystem metadata;
- preserves the no-autonomous-Git-management policy.

## Version 2.4.6

2.4.6 freezes effective-profile projection generation into the graphical Build workflow:

- the Build tab adds **Generate Effective Profile** for the currently selected profile;
- the action invokes the repository-owned `koa_assembly resolve-profile` command rather than reimplementing profile composition in the Control Panel;
- the generated path follows the repository convention `generated/profiles/<profile_id_with_underscores>/effective-profile.json`;
- configured canonical overlays are forwarded to the repository resolver;
- **Assemble --check** remains non-mutating and strict: it still reports generated effective-profile drift instead of silently rewriting the projection;
- **Generate All** remains the repository's existing `koa generate all` action and is not misrepresented as effective-profile generation;
- no canonical profile authority, Git state, component bundle, package lock, or release evidence is modified by this Control Panel action.

This closes the concrete assembly preflight blocker observed after 8/8 component bundle closure, where `Assemble --check` correctly reported drift in `generated/profiles/sovereign_linux_node/effective-profile.json`.

## Version 2.4.5

2.4.5 freezes the native compiler/linker prerequisite into the graphical PREPARE workflow:

- when `rust-toolchain.toml` is present, `PREPARE DEVELOPMENT ENVIRONMENT` verifies that `cc` exists and can compile **and link** a temporary C probe under `/tmp`;
- if that native linker probe is blocked on Ubuntu WSL, PREPARE provisions the configured native build package set, which defaults to `build-essential`;
- the probe is repeated immediately after provisioning and PREPARE stops if the host compiler/linker is still unusable;
- the probe and provisioning do not edit repository source, generated outputs, `Cargo.lock`, or Git state;
- the validated 2.4.4 Rust toolchain, Cargo offline-cache preparation, strict `cargo build --locked --offline`, and detailed Cargo diagnostic replay are preserved unchanged.

This closes the concrete **`linker cc not found`** blocker surfaced by the 2.4.4 graphical Cargo diagnostic without introducing a manual terminal installation step.

## Version 2.4.4

2.4.4 freezes the Cargo dependency-cache step into the graphical PREPARE/build workflow:

- after the exact repository Rust toolchain is ready, `PREPARE DEVELOPMENT ENVIRONMENT` verifies that `Cargo.lock` can be satisfied with `cargo fetch --locked --offline`;
- when the locked dependency graph is not yet cached, PREPARE performs one networked `cargo fetch --locked` under the normal WSL user, then immediately proves the same lock is available offline;
- the repository source tree, `Cargo.lock`, and Git state are not modified by this cache preparation; Cargo downloads are stored only in the normal user Cargo cache;
- the actual kOA component builder remains strict and unchanged: `cargo build --locked --offline`;
- when `koa-node-agent` still fails, the Control Panel automatically replays the canonical Cargo build in a temporary `/tmp` target directory so the complete Cargo/compiler error is visible in the GUI log;
- the temporary diagnostic build is removed automatically and does not become a kOA artifact.

This preserves the validated 2.4.3 Rust provisioning and makes offline Rust component builds reproducible from the interface without a manual terminal cache step.

## Version 2.4.3

2.4.3 freezes the repository Rust toolchain into the graphical PREPARE workflow:

- `PREPARE DEVELOPMENT ENVIRONMENT` reads `rust-toolchain.toml` from the active Linux workspace after source refresh;
- when Rust is declared, PREPARE verifies `rustup`, the exact toolchain channel, `rustc`, `cargo`, and every declared component;
- on Ubuntu/WSL, missing `rustup` is provisioned through the distro package manager and the exact channel/profile/components are installed with `rustup`;
- the Control Panel does not choose or hard-code the repository Rust version: `rust-toolchain.toml` remains authoritative;
- `~/.cargo/bin` is part of every WSL command PATH so graphical component builds can find Cargo;
- no Git branch/index/cleanliness/remote state is inspected or modified by PREPARE.

This makes the exact Rust compiler/toolchain available from the Build tab after PREPARE. Version 2.4.4 adds the missing locked Cargo dependency-cache preparation required by the repository's offline builder.

## Version 2.4.2

2.4.2 fixes component builds from the graphical Build tab:

- `Build Component` now invokes the repository-owned `koa_tools.commands.build_component` module directly.
- `Build All Components` uses the same canonical component-builder path.
- The Control Panel no longer invents a `build-component` subcommand on `koa_tools.cli`, where that command is not registered.

## Version 2.4.1

2.4.1 fixes QEMU/UEFI discovery on current Ubuntu WSL environments:

- Ubuntu 24.04/Noble `ovmf` firmware is detected at `/usr/share/OVMF/OVMF_CODE_4M.fd`;
- legacy/common OVMF and EDK2 code-image paths remain supported as fallbacks;
- QEMU PREPARE/PREFLIGHT no longer reports a false `OVMF/EDK2 code image missing` solely because the distro uses the 4M firmware filename;
- no repository semantic values, release identities, runtime regexes, or image inputs are invented by this change.

## Version 2.4.0

2.4.0 separates the development debug loop from strict conformance gates:

- **DEBUG PRINCIPAL** is now the primary diagnostic action. It runs the read-only local `koa diagnose --pipeline --json` surface inside the active workspace;
- a completed JSON diagnostic is considered a successful DEBUG execution even when architecture/conformance readiness is blocked;
- file-layout, ownership, generated-content and similar formal findings remain visible but are presented as WARN in DEBUG instead of stopping the debug loop;
- **VALIDATION** and **RELEASE** remain strict and are still delegated to LevelUpDiag;
- DAILY CYCLE uses DEBUG PRINCIPAL, then opens the editor without requiring strict conformance to be green;
- PREPARE continues to avoid autonomous Git-state management; Git status/branch/cleanliness are not part of the automatic debug readiness decision;
- Windows→WSL import no longer strips generic `build/`, `dist/`, or `*.egg-info` paths because such paths may be tracked repository content. Only clearly local runtime/cache state is filtered.

The operating rule is: **if a formality does not materially prevent observing or executing the local system, it cannot block DEBUG.**

## Version 2.3.4

2.3.4 removes autonomous Git-state management from PREPARE and workspace refresh:

- PREPARE does not run `git status`, inspect the branch, or require commit/stash before refresh;
- source freshness is based only on the Control Panel content fingerprint;
- when a refresh replaces an existing WSL workspace, the complete previous workspace is preserved under `~/work/.koali-control-backups/` (same parent as the workspace; exact path is logged);
- the 2.3.4 importer filtered several mutable-looking paths; 2.4.0 narrows that filter so potentially tracked build/package paths are preserved;
- explicit Git actions remain user-invoked only.

## Version 2.3.3

2.3.3 fixes Windows-source to WSL workspace convergence:

- PREPARE fingerprints the configured Windows checkout without running Git against `/mnt/c`;
- an existing legacy workspace receives one safe refresh so stale source copies cannot be silently reused;
- when the Windows source changes later, the WSL workspace can be refreshed from the source;
- 2.3.3 used Git cleanliness as a refresh guard; 2.3.4 supersedes that behavior with folder-level backup and no autonomous Git-state inspection;
- Windows-local runtime/cache state is excluded from import; 2.4.0 preserves generic build/package paths because they may be tracked content;
- the Workspaces action is **Refresh from Windows source**; 2.3.4 preserves the prior workspace directory before replacement;
- the source fingerprint is stored outside the repository under the workspace parent `.koali-control-state/` directory.

## Version 2.3.2

2.3.2 tightens the QEMU boundary and repository-aware convergence:

- visible application identity is **Koali Control Panel 2.3.2**;
- repository-aware canonical system-image discovery added;
- repository image-package status/target-profile reporting added;
- incorrect implementation-settings TOML active-profile auto-detection removed;
- generated effective-profile JSON discovery added with `session_runtime.surfaces` shape validation;
- N08/N09/N10 operational preflight scopes separated;
- N10 is no longer blocked by unrelated N09 navigation context;
- QEMU package provisioning output is quieter;
- no LevelUpDiag level/campaign semantics moved into Koali Control Panel.

## Koali Spaces integration strangler

The current paired Koali Spaces snapshot does not yet expose the final Koali-owned `SpaceActivationCompiler` invocation surface on Windows. For continuity, configuration therefore defaults explicitly to `mode: "legacy_projection"`. Only that compatibility mode uses `koali_control/spaces_pilot.py` to materialize the historical Home+Konnaxion development projection.

This path is intentionally isolated behind `KoaliSpacesIntegrationController`. New orchestration code must not add product-specific manifest builders to it. When Koali exposes the canonical activation action and Konnaxion owns its real manifest/ACP package, switch configuration to `delegated`; the legacy writer can then be deleted without changing `DevStackOrchestrator`.

`delegated` mode never writes `active-state.json` or `surface-runtime.json` itself. It invokes Koali and verifies `/api/shell-state` plus configured same-origin module routes.

### Koali Spaces runtime mode

After `validate`, `build`, and `smoke:runtime`, the dev stack launches Koali Spaces with `pnpm run start` from the packaged `dist/runtime` artifact. This preserves the production-strict CSP while Konnaxion may continue to run in hot-reload development mode. The browser shell must hydrate and then consume `/api/shell-state` before the stack is considered visually usable.

## Version 4.1 — Koali System et diagnostic du store

L'onglet Diagnostics ajoute **Koali System** et **Store / N13**, avec routage natif
ou workspace adapté, rapports par exécution et amélioration des rafraîchissements,
journaux et arrêts. Voir [installation, réglages et limites](UPDATE_KCP4_1_DIAGNOSTICS.md).
