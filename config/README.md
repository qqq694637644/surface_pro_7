# Power profiles

This directory is reserved for reviewed, version-controlled power-management profiles.

The initial project does not write to sysfs or mutate Power Options automatically. When Power Options is adopted on the target Surface Pro 7, keep copies or generated patches of the active profile here so every experiment can be tied to a reproducible configuration.

Recommended naming:

- `baseline.toml`
- `web-balanced.toml`
- `web-longlife.toml`
- `video.toml`

Do not put secrets or machine credentials in profile files.
