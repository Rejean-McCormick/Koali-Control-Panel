# Koali Spaces integration

## Boundary

Koali Control Panel may coordinate **when** Koali Spaces integration is activated/deactivated and verify declared public acceptance criteria. Koali Spaces owns the semantics of Space composition, activation, manifests, ACP interpretation, and receipts.

The generic integration boundary is implemented by `KoaliSpacesIntegrationController`.

## Configuration

The integration lives under:

```text
dev_stack.koali_spaces_integration
```

Core fields:

```json
{
  "enabled": true,
  "mode": "legacy_projection",
  "product_id": "koali-spaces",
  "state_root": "...",
  "actions": {
    "activate": "",
    "deactivate": ""
  },
  "verify": {
    "modules": [
      {
        "module_id": "konnaxion",
        "required": true,
        "route": "/apps/konnaxion"
      }
    ]
  }
}
```

## Modes

### `delegated`

Delegated mode invokes configured **Koali-owned product actions** through `ProductRegistry`. The Control Panel does not implement the meaning of those actions.

A missing required activation action fails closed: delegated mode must not silently fall back to generating a legacy/fabricated Space.

The intended final-target shape is:

```json
{
  "mode": "delegated",
  "actions": {
    "activate": "<Koali-owned product action>",
    "deactivate": "<Koali-owned product action>"
  }
}
```

### `legacy_projection`

This is an explicit compatibility mode. It delegates to `LegacyKoaliSpacesPilotState` to materialize the current development projection and clean it during teardown.

Because this mode is explicit, it does not weaken the final authority boundary: moving to delegated activation requires a real Koali-owned action.

## Lifecycle

The controller participates in Dev Stack startup/shutdown through:

```text
before_start()
after_ready()
stop()
```

`before_start()` prepares/activates integration according to mode. `after_ready()` verifies the declared acceptance criteria once products are ready. `stop()` releases/deactivates integration.

On startup failure after integration preparation, the Dev Stack marks the integration failed and applies the appropriate cleanup path.

## Verification

Verification is configuration-driven. Declared modules contain a `module_id`, `required` flag and same-origin route.

The controller validates routes rather than hard-coding Konnaxion-specific manifest generation. Tests also enforce same-origin route expectations and the absence of product-manifest fabrication in the generic controller.

## State-root projection

The integration `state_root` is also projected into the configured Koali Spaces environment in the supplied schema-4 configuration. The current environment includes a surface-registry path ending in `surface-runtime.json`.

This makes the runtime/state location explicit while keeping the activation authority with Koali Spaces.

## Migration

Schema-3 pilot configuration is normalized into the schema-4 `koali_spaces_integration` shape without losing the legacy projection settings. This preserves compatibility while making the authority mode explicit.
