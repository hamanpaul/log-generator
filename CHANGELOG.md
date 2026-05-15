# Changelog

All notable changes to this project are documented in this file.

Format follows [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/)
and hamanpaul project policy v1.0.0.

## [Unreleased]

### Added
- Bootstrap `hamanpaul/log-generator` with the serialwrap reboot log test
  toolkit and paulsha-conventions policy skeleton.

### Changed
- Update serialwrap reboot-test event rules and handler payload parsing for the
  current serialwrap EventEngine schema while preserving legacy payload support.
- Align the policy check workflow with the branch protection required
  `policy-check` status context.

### Fixed
- Reboot controller startup waits up to 15 s for the async marker echo to land
  in the minicom capture file before declaring "No active minicom log found",
  closing a race against `serialwrap cmd submit --mode line` which returns
  before the echo reaches the target.
- Reboot controller honours a 90 s post-reboot boot guard before re-probing.
  `decide_reboot_action` returns `{"type":"wait"}` while the guard is active
  so that self-test (`echo __READY__<nonce>`), `session recover`, and the
  raw recovery commands never push bytes into u-boot's autoboot countdown
  window — which previously aborted autoboot and stranded the target at
  `=>` until the next manual recovery. Guard duration lives on the
  controller (`boot_guard_seconds`) for future per-target tuning.
- Align reboot controller's serialwrap CLI usage with the current daemon:
  `event add --file <path>` (was `--rule <json>`), `event rm <rule_id>`
  positional (was `--name`), `session list` reads each session's `com` field
  (legacy `selector` field is also still accepted), `event status` recognises
  both the current `{"coms": [...]}` schema and the legacy
  `{"selectors": {...}}` schema, and the recovery `raw_reset` / `raw_reboot`
  paths route through `cmd submit` (the legacy `broker raw` subcommand was
  removed). Tests updated to cover both old and new shapes.
