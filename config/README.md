# PowerLab v2 configuration

The v2 configuration is deliberately small and Surface Pro 7 specific.

- `powerlab.toml`: sampling, retention, automation and service cadence.
- `machine.toml`: calibration output for this physical Surface Pro 7.
- `envelopes.toml`: candidate and verified HWP operating envelopes.
- `thermal.toml`: thermal model weights and safety state thresholds.

The repository ships with `calibration.valid = false`. This is intentional:
PowerLab starts read-only on a real machine until calibration is completed.

Changing `thermal.toml` is a high-risk manual operation. LLM decisions never edit it.
