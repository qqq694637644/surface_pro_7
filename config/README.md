# PowerLab configuration

The configuration is deliberately Surface Pro 7 specific.

- `powerlab.toml`: sampling, retention, automation and service cadence.
- `machine.toml`: calibration output for this physical Surface Pro 7.
- `envelopes.toml`: candidate and verified HWP operating envelopes.
- `thermal.toml`: thermal model weights and safety state thresholds.

The repository ships with `calibration.valid = false`. This is intentional:
PowerLab starts read-only on a real machine until calibration is completed.

Changing `thermal.toml` is a high-risk engineering operation. Structured trial proposals do
not edit it; a Bash Agent may only change it as an explicit code/config change with review,
tests, and subsequent real-machine revalidation.
