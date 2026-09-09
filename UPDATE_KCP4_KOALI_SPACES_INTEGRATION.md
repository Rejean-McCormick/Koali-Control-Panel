# Control Panel 4.0.0 — Koali Spaces Integration Boundary

## Purpose

This update removes the Koali/Konnaxion development pilot from the main Dev Stack orchestration path and introduces the final-target delegation boundary.

Control Panel remains responsible for development orchestration: workspace/product preparation, build/test invocation, process supervision, health observation, and acceptance verification. Koali Spaces remains responsible for Space composition, Manifest/ACP/runtime semantics, activation, rollback, and receipts.

## Architecture

```text
DevStackOrchestrator
        |
        v
KoaliSpacesIntegrationController
        |
        +-- delegated          -> Koali-owned product action -> public verification
        |
        `-- legacy_projection  -> isolated compatibility writer
```

`delegated` is the final-target mode. It contains no Home/Konnaxion manifest construction and no activation-payload logic. If its Koali-owned activation action is absent, the operation fails closed.

`legacy_projection` exists only because the paired Koali Spaces KS-2 snapshot does not yet expose the planned `SpaceActivationCompiler` invocation surface on Windows. It preserves current development usability while keeping the debt isolated and removable.

## Main changes

- configuration schema bumped to `4`;
- `workflow.phase` replaced with non-sequential `workflow.current_focus`;
- new `koali_control/spaces_integration.py`;
- `DevStackOrchestrator` now talks only to `KoaliSpacesIntegrationController`;
- delegated actions are repository-owned product actions;
- verification modules/routes are declarative configuration, not Python product branches;
- verification routes are constrained to same-origin absolute paths;
- integration release/deactivation occurs before product shutdown;
- schema-3 `koali_spaces_pilot` configuration migrates losslessly;
- `spaces_pilot.py` is now compatibility-only and no longer owns navigation verification;
- no new `_product_manifest()` pattern is introduced.

## Current compatibility mode

The shipped configuration intentionally remains:

```json
"mode": "legacy_projection"
```

because the paired Koali Spaces code does not yet provide the delegated activation action required by the final architecture.

Switching to `delegated` requires all of the following:

1. Koali Spaces implements the Koali-owned `SpaceActivationCompiler`/stable invocation surface.
2. Konnaxion owns its canonical ModuleManifest and real ACP/evidence package.
3. Koali can build the runtime registration/activation inputs without Control Panel semantics.
4. The configured Koali product exposes activation/deactivation actions.
5. Generic shell/module verification passes through the delegated path.
6. The legacy Home+Konnaxion writer is then deleted.

## Validation

Executed from this snapshot:

```text
python -m unittest discover -s tests -p "test_*.py" -q
84 tests PASS

python koali-control.pyw --self-test
PASS — schema 4 / Control Panel 4.0.0
```

The tests include explicit proof that delegated mode does not fall back to state fabrication when activation is unavailable.
