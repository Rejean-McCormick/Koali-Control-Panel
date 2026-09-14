# Products and Dev Stack

## Modular product registry

Products are data-driven through the `products` section of `koali-control.json`. Python code does not need a branch for every ordinary product registration.

`ProductRegistry` is responsible for:

- parsing product/service specs;
- resolving an installed root from configured candidates;
- discovering a supported command when applicable;
- executing named product actions;
- starting/stopping supervised runtimes;
- aggregate health/runtime state;
- opening configured browser URLs.

## Product specification

A product may be single-process or composite.

For a single-process product, the product-level `commands.start`, `health_url`, and `open_url` can be sufficient.

For a composite product, `services` contains multiple persistent processes. Each service can define its own backend, relative root, marker, command, environment and health URL.

The product is considered healthy only when its required configured runtime health checks are ready.

## Supplied products

The checked-in configuration defines:

- **Konnaxion** — enabled, required, Windows backend, composite `api` + `web` services;
- **Koali Spaces** — enabled, required, Windows backend, packaged runtime start command and health endpoint;
- **Konnaxion Capsule Manager** — enabled but optional, composite `agent` + `manager` services;
- **Orgo** — disabled and optional in the current configuration.

The default Dev Stack composition contains `konnaxion` then `koali-spaces`.

## Process ownership

Persistent child processes are owned by `ProcessSupervisor`, not by the Tkinter UI.

The supervisor launches backend-aware shell commands, stores managed process metadata, streams output through callbacks, and can stop individual/all managed processes.

## Dev Stack preparation

`DevStackOrchestrator.start()` can perform four stages before reporting readiness:

```text
optional core environment preparation
  -> integration gates
  -> product preparation actions
  -> Koali Spaces before_start hook
  -> start products
  -> wait for health/runtime readiness
  -> Koali Spaces after_ready verification
```

Failures are reported by stage through `DevStackResult`.

## Gates

`dev_stack.gates` runs finite commands before product startup. The supplied gate is the Koali/Konnaxion adapter test in the kOA workspace:

```text
uv run --frozen pytest -q integrations/konnaxion/tests
```

A non-zero gate result blocks the Dev Stack.

## Product preparation actions

The supplied action order is:

```text
Konnaxion:   prepare -> migrate -> validate -> test -> build
Koali Spaces: validate -> build -> smoke
```

If a declared preparation action has no available command, it is skipped as an undeclared optional action. If a present command fails, preparation fails.

## Startup and readiness

Products start in configured order. Required products must be installed and start successfully; optional products may be skipped/continued in supported failure paths.

Readiness waits until the configured startup deadline. For products with health/open URLs or composite services, `ProductRegistry.health()` is authoritative for aggregate product readiness.

A healthy already-running external runtime can satisfy readiness even if the supervisor does not own that process; logs distinguish `managed` from `external` readiness.

## Shutdown

Shutdown runs in two important phases:

1. release/deactivate Koali Spaces integration while its owning runtime may still be available;
2. stop configured products in reverse order.

This ordering preserves the ability of delegated integration teardown to call the Koali runtime before it is stopped.

## Adding a product

A new ordinary product normally requires configuration only:

1. add a `products.<id>` object;
2. configure roots/marker/backend/actions and optional services;
3. add the product id to `dev_stack.products` if it belongs in the integrated stack;
4. optionally add `dev_stack.product_actions.<id>`;
5. run the Control Panel self-test and the unit suite.

Only behavior that cannot be expressed through the generic registry should require new Python logic.
