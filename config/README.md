# PowerLab configuration

This directory contains the version-controlled policy surface for SP7 PowerLab.

## Files

- `powerlab.toml` — collector, LLM cadence, experiment gates, automation, storage and actuator settings.
- `contexts.toml` — deterministic application/process groups used by the local context engine.
- `policy.toml` — default scene → profile mapping. Promoted SQLite context policies take precedence.
- `profiles/*.toml` — reviewed profile definitions.

## Profile lifecycle

A profile can be:

```text
experimental
verified
needs_revalidation
deprecated
blocked
```

Static TOML describes the reproducible profile definition. Runtime verification status, validation evidence and promoted scene mappings are kept in SQLite and exported to `history/continuous/`; reloading a TOML profile does not erase those learned states.

The built-in `safe-baseline` is a verified no-op profile. Other bundled profiles are experimental until validated on the actual Surface Pro 7.

## Single-writer rule

`policy.actuator = "auto"` chooses one writer:

```text
Power Options
→ power-profiles-daemon
→ direct sysfs
```

Do not run multiple tools that continuously write the same EPP/frequency parameters.

## Git

Commit:

- reviewed config changes;
- promoted/validated profile definitions when you choose to materialize them;
- `history/continuous/` knowledge snapshots;
- reviewed context-rule proposals.

Do not commit `runtime/`.
