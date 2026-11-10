# Branch 08 — Koali Spaces readiness as an observable boundary

**Status:** tested local source/consumer alignment, not an authorization or deployment claim. **Product owner:** Koali Control Panel. **Producer:** independently owned Koali Spaces.

## Implemented consumer behavior

`koali_control/spaces_integration.py::KoaliSpacesIntegrationController.verify` consumes `GET /api/shell-state` from the selected local Koali Spaces shell. It requires `state == ready` and validates a structured `modules` list, active Space module instances and (when supplied) a unique per-module `module_health` projection. In the final `delegated` mode, every **required** configured module must have `module_health.state == ready`. An optional degraded module may remain visible without falsely changing the shell to degraded. A route responding HTTP 200 does **not** override a degraded owner runtime, and a redirect away from the configured shell origin does not count as ready. The remote JSON body is bounded to 2 MiB.

This verification is **readiness observation only**. It does not mint a capability, authorize a task, claim a signed release, activate a Space or produce privileged host credentials. Product-specific required API and frontend probes belong to the actual runtime owner and Koali's source-qualification path.

## Compatibility

`legacy_projection` remains an explicit compatibility mode. Its legacy static pilot does not publish `module_health`; this mode still requires a valid ready shell and a declared, admitted module, but does not fabricate per-module health. `delegated` never silently falls back to legacy and must call a configured Koali-owned activation action.

The current configured required presentation modules are `orgo` and `orgo_worlds` (`koali-control.json`). The bilateral synthetic fixture under `tests/test_branch08_shell_readiness.py` verifies the semantics using a running Koali Spaces source compiler when `KOALI_SPACES_SOURCE` points to that checkout; it is **not** a production traffic test.

## Verification

```console
python -m unittest discover -s tests -q
KOALI_SPACES_SOURCE=/path/to/koali-spaces python -m unittest tests.test_branch08_shell_readiness -v
```

Required negative fixtures include: shell degraded/unavailable, required module health missing/degraded, duplicate/malformed module health, oversized payload, redirect to another origin, and incompatible status projection. Optional degradation must not trigger a false global outage.

## Open gates

- Test against an actual installed Koali Spaces runtime and product-specific health probes, not only compiled synthetic state.
- Confirm allowed origins and HTTP redirect behavior at a real host deployment; a rejected final URL is not a substitute for dedicated network egress policy.
- Record host/runtime and actual owner permission receipts separately from UI readiness. This documentation is not a production or deployment qualification.
