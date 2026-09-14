# QEMU and diagnostics

## Responsibility split

The Control Panel owns QEMU **infrastructure and execution context**. LevelUpDiag-Koali and kOA-Linux own diagnostic sequencing, semantic validation, findings and verdicts.

```text
Koali Control Panel
  -> provision/discover QEMU context
  -> export KOA_QEMU_* values
  -> invoke LevelUpDiag

LevelUpDiag-Koali / kOA-Linux
  -> decide diagnostic plan
  -> run validators/tests
  -> produce findings/verdicts
```

## QEMU configuration

QEMU settings are stored at `system_test.qemu`. The Control Panel maps configuration keys to environment variables such as:

```text
KOA_QEMU_IMAGE
KOA_QEMU_IMAGE_FORMAT
KOA_QEMU_NETWORK
KOA_QEMU_EXPECTED_RELEASE_IDENTITY
KOA_QEMU_SESSION_READY_REGEX
KOA_QEMU_COMPOSITOR_READY_REGEX
KOA_QEMU_ACTIVE_PROFILE
KOA_QEMU_ACTIVE_RELEASE_SET
...
```

`image`, `active_profile`, and `active_release_set` are treated as path fields and resolved relative to the active workspace where appropriate.

## Infrastructure provisioning

On WSL, QEMU preparation can install configured Ubuntu packages when QEMU/UEFI support is missing. The supplied list is:

```text
qemu-system-x86
qemu-utils
ovmf
```

Provisioning can be disabled or changed in `system_test.qemu.provisioning`.

## Repository-aware context discovery

`QemuEnvironmentManager` can discover:

- repository image package status;
- canonical generated system image candidates backed by repository metadata;
- generated effective-profile JSON candidates.

It may auto-select a context only when it can do so unambiguously. Multiple candidates remain explicit rather than guessed.

If a configured structured `build.image --output` is absent and image build is enabled, QEMU preparation can request the repository's public image-build surface and then re-check for canonical metadata.

## No invented semantic context

QEMU preparation is allowed to provision infrastructure and discover real repository artifacts. It deliberately does not invent missing semantic values such as readiness regexes, active release sets, navigation context, or other diagnostic evidence.

A prepared QEMU installation can therefore still produce **NOT READY** when the execution/semantic context is incomplete.

## Scoped preflight

Preflight can be scoped for N08/N09/N10-style runtime validation contexts. Scoped checks only require context relevant to the requested scope; for example an N10-oriented check should not require unrelated N09 navigation context.

The preflight output is line-oriented and ends with `QEMU PREFLIGHT: READY` or `NOT READY` (including a scope suffix for scoped runs).

## LevelUpDiag integration

`LevelUpDiagAdapter` resolves the configured LevelUpDiag root, maps UI roles to campaign names, injects active backend/QEMU context, invokes campaigns, and presents their report/evidence.

The supplied campaign mapping is:

```text
stabilization         stabilization
stabilization_runtime stabilization-runtime
developer             debug
build                 stabilization
run_all               validation
release               release
delivery              delivery
```

The adapter is intentionally a delegation/presentation layer. It must not reinterpret a LevelUpDiag verdict to fit the Control Panel's current workflow focus.

## Local DEBUG diagnostics

`DebugDiagnosticsRunner` is separate from LevelUpDiag. It performs read-only local pipeline diagnosis and can treat configured architecture formalities as non-blocking warnings. It returns an error when the diagnostic itself cannot be read/executed, not merely because strict final architecture is incomplete.

## Recommended diagnostic sequence

For core development:

```text
PREPARE
  -> stabilization campaign
  -> DEBUG when investigation is needed
```

For runtime work, prepare an actual QEMU/image/profile context first, then run the scoped runtime diagnostic path. For final validation/release, use the strict campaigns without reclassifying expected subsystem/conformance blockers.
