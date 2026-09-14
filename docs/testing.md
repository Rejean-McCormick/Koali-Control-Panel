# Testing

The repository uses the Python standard-library `unittest` framework.

## Built-in application self-test

Run:

```powershell
python .\koali-control.pyw --self-test
```

This validates the checked-in schema/model assumptions. It is intentionally narrower than the full unit suite and does not qualify external repositories or runtimes.

## Full unit suite

From the repository root:

```powershell
python -m unittest discover -s tests -p "test_*.py"
```

The current suite is split across:

```text
tests/test_core.py
tests/test_dev_stack.py
tests/test_stabilization_workflow.py
```

`tests/test_dev_stack.py.tmp` is an empty temporary file and is not a test module matched by the command above.

## Coverage areas

### Core tests

`test_core.py` covers, among other behavior:

- configuration migration/schema writing;
- workspace round-tripping/import/freshness;
- repository command construction;
- Windows/WSL path and stream handling;
- WSL preflight and provisioning;
- repository Rust/native/Cargo prerequisites;
- LevelUpDiag delegation/evidence presentation;
- QEMU infrastructure/context/preflight;
- component build diagnostic replay.

### Dev Stack tests

`test_dev_stack.py` covers:

- schema-4 product registry defaults;
- command discovery and explicit overrides;
- process supervision environment/root behavior;
- external and composite runtime health;
- configuration upgrades for older product command shapes;
- generic Koali Spaces integration lifecycle;
- delegated-mode fail-closed behavior;
- same-origin verification routes;
- integration teardown ordering.

### Stabilization workflow tests

`test_stabilization_workflow.py` verifies the current core-first workflow metadata, modular products, LevelUpDiag campaign mapping, UI exposure of core-first actions, preservation of strict final assembly, and separation of development/final profiles.

## What to run after common changes

| Change | Minimum validation |
| --- | --- |
| Documentation only | Self-test + full unit suite recommended before packaging. |
| `koali-control.json` | `--self-test` + full unit suite. |
| Config migration/defaults | Full unit suite. |
| Backend/workspace code | `test_core.py` or full suite. |
| Product/supervisor/Dev Stack code | `test_dev_stack.py` + full suite. |
| Workflow/diagnostic mapping | stabilization workflow tests + relevant core tests. |
| QEMU/LevelUpDiag | relevant core tests + full suite. |

## Focused test execution

Examples:

```powershell
python -m unittest tests.test_core
python -m unittest tests.test_dev_stack
python -m unittest tests.test_stabilization_workflow
```

A passing local unit suite proves Control Panel logic under its test doubles/fixtures. It does not prove that WSL, QEMU, kOA-Linux, Konnaxion, Koali Spaces, or LevelUpDiag are installed and healthy on a particular workstation.
