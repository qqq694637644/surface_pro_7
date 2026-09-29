# Surface Pro 7 PowerLab

A small, auditable experiment layer for improving Surface Pro 7 battery life on Linux without reinventing the underlying power-management stack.

## Design goals

- Treat whole-device battery discharge as the primary objective.
- Keep every experiment reproducible and comparable.
- Store configuration, telemetry, summaries, and notes in Git-friendly formats.
- Let external tools such as Power Options, powerstat, PowerJoular, powertop, and thermald do the low-level work.
- Keep risky hardware/kernel changes out of automatic tuning.
- Make it easy for an AI assistant to inspect history and propose one controlled change at a time.

## Current v0.2

The first version provides a Python CLI that can:

- inspect the current Surface/Linux power state;
- create an experiment directory with immutable metadata;
- identify a Surface Pro 7 through DMI data;
- sample whole-device battery discharge, CPU policy/load, brightness, thermal, Wi-Fi, and CPU idle telemetry into CSV;
- snapshot reviewed profile/config files with SHA-256 hashes;
- write a machine-readable experiment summary;
- write a compact ai-summary.json with safety guardrails and comparison context;
- generate, validate, classify, and store single-variable tuning proposals;
- link candidate experiments to proposals without applying settings automatically;
- evaluate proposal acceptance criteria after measurement while leaving the final decision to human review;
- archive compact experiment/proposal outcomes under history/ for Git-portable long-term learning;
- compare two completed experiments;
- maintain a SQLite index of experiment results.

No privileged power-setting changes are performed yet.

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

sp7-powerlab doctor
sp7-powerlab start --name baseline-web --profile baseline --workload web
# keep the workload running, then in another terminal:
sp7-powerlab collect --interval 2 --duration 900
sp7-powerlab finish
sp7-powerlab list
```

See docs/FIRST_RUN.md for the recommended first baseline on the actual Surface Pro 7.
See docs/AI_LOOP.md for the proposal -> review -> experiment -> archive loop.

Raw data is stored under `experiments/` by default and intentionally ignored by Git. Every finished run also writes a compact record under `history/`; tuning proposals live under `proposals/`. Those small JSON files are intended to be committed so an AI can continue learning from prior experiments after a reinstall or on another checkout.

## Proposal loop

```bash
sp7-powerlab proposal-template <baseline-id> --output next-proposal.json
# let AI/user fill exactly one primary change
sp7-powerlab proposal-add next-proposal.json
sp7-powerlab proposal-list

# review and apply the setting using Power Options or another actuator
sp7-powerlab start \
  --name candidate \
  --proposal <proposal-id> \
  --profile reviewed-profile \
  --config-file /path/to/profile.toml

sp7-powerlab collect --interval 2 --duration 900
sp7-powerlab finish
sp7-powerlab decision <candidate-id> accepted --responsiveness 5 --stability good
sp7-powerlab ai-pack --workload web --output ai-pack.json
sp7-powerlab history-list
```

## Planned integration

The intended stack is:

```text
Power Options / thermald / browser settings
                |
          configuration
                |
          sp7-powerlab
        /       |       \
 battery    powerstat   PowerJoular
 power_now              / RAPL
        \       |       /
           experiment
             history
                |
          AI proposal
                |
      human-approved patch
```

The AI layer proposes changes; PowerLab v0.2 does not silently mutate any system setting. Even allowlisted reversible changes require review and are applied through an external actuator.

## Safety policy

The proposal allowlist currently covers reversible parameters such as CPU EPP, CPU frequency caps, turbo policy, selected browser power features, radios, and display policy.

Kernel command line, suspend internals, PCI/runtime-PM, USB autosuspend, I2C devices, firmware, and Surface kernel changes require explicit review.
