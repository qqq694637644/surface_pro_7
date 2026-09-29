# AI-assisted tuning loop

PowerLab deliberately separates observation, proposal, human review, application, and measurement.

## 1. Finish a baseline experiment

```bash
sp7-powerlab finish
```

This creates both the raw experiment summary and a compact Git-trackable record under `history/`.

## 2. Create a proposal template

```bash
sp7-powerlab proposal-template <experiment-id> --output next-proposal.json
```

Give `history/<experiment-id>.json` (or the experiment's `ai-summary.json`) plus `next-proposal.json` to the AI. The AI should fill exactly one primary change.

## 3. Validate and store the proposal

```bash
sp7-powerlab proposal-add next-proposal.json
sp7-powerlab proposal-list
sp7-powerlab proposal-show <proposal-id>
```

PowerLab classifies proposed parameters:

- `allowlisted-reversible`: EPP, CPU max frequency, turbo policy, brightness, Bluetooth, Wi-Fi power save, and selected browser power features.
- `sensitive-human-review-only`: kernel, suspend, PCI, USB, I2C, firmware, and linux-surface settings.
- `unknown-blocked`: anything not explicitly classified.

PowerLab v0.2 never applies a setting automatically, including allowlisted settings.

## 4. Review and apply the setting yourself

Use the relevant actuator (for example Power Options) and make sure the proposal's rollback instruction is practical before testing.

## 5. Start the candidate experiment linked to the proposal

```bash
sp7-powerlab start \
  --name candidate-epp-power \
  --proposal <proposal-id> \
  --profile web-epp-power \
  --config-file /path/to/reviewed/profile.toml

sp7-powerlab collect --interval 2 --duration 900
sp7-powerlab finish
```

When `--proposal` is supplied, PowerLab records the proposal hash and inherits its workload/hypothesis when those were not specified explicitly.

## 6. Read the result

The candidate experiment produces `proposal-evaluation.json` and the portable history record includes the same evaluation.

The evaluation reports measured power/temperature deltas and mechanical acceptance checks, but it deliberately leaves the final decision as:

`pending-human-review`

Record your actual experience before asking for the next proposal:

```bash
sp7-powerlab decision <candidate-experiment-id> accepted \
  --responsiveness 5 \
  --stability good \
  --suspend-wake good \
  --notes "No noticeable regression during normal web work."
```

Use `rejected` even when watts improved if the machine became unpleasant or unreliable. This prevents the AI from rediscovering a numerically efficient but practically bad setting.

Then build a compact context pack:

```bash
sp7-powerlab ai-pack --workload web --output ai-pack.json
```

The pack includes recent measurements, proposal outcomes, human accept/reject feedback, safety classifications, and a ready-to-fill next proposal template. It is the preferred input for the next AI tuning pass.

## Git strategy

Raw `experiments/` telemetry and the SQLite cache stay out of Git. Commit these compact files instead:

- `history/*.json`
- `proposals/*.json`
- reviewed `config/` profiles

That makes tuning history portable across reinstallations while avoiding a repository full of high-frequency CSV samples.
