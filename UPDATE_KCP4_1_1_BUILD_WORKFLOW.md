# Koali Control Panel 4.1.1 — build workflow hardening

## Problem observed

A fresh WSL workspace could enter a self-blocking sequence:

1. `Generate Effective Profile` invoked `uv run --project assembly ...`.
2. UV created an untracked `assembly/uv.lock`.
3. `koa_tools.commands.build_component` correctly rejected the now-dirty worktree.
4. After manually removing that lock, a freshly imported workspace could still fail the Python `uv build --offline --no-build-isolation` step because the root `.venv` had not yet been synchronized with all dependency groups.

## Fix

- Effective-profile generation now removes `assembly/uv.lock` only when Git reports it as untracked.
- Component build actions automatically run `uv sync --frozen --all-groups` before the strict offline build and then verify Git cleanliness.
- Strict repository semantics remain unchanged: any other source change still blocks the component build.
- `DEBUG PRINCIPAL` now diagnoses the configured final profile instead of the normal development workspace profile.
- Python component failures emit a small build-backend probe to improve diagnosis without weakening the repository build command.

## Safety properties

- No tracked lockfile is deleted.
- No Git reset/stash/clean is performed.
- No subsystem `source.lock.json`, package-resolution, deployment plan, image input, or release evidence is fabricated.
- Component bundles remain produced only by the repository-owned `koa_tools.commands.build_component`.
