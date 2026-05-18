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
- `fault_installer.build_fault_injector_script()` random source switched
  from `od -An -N2 -tu2 /dev/urandom` to `sha256sum` over `dd` bytes from
  `/dev/urandom`. BGW720 / prplOS BusyBox lacks `od`, so the previous
  implementation silently returned 0 from `get_random` on every call,
  collapsing the 10% gate into 100% and pinning the fault-type selector
  to type 0. With the new random source the gate and type cycle both
  function as intended.
- `fault_installer.build_init_script()` now emits an OpenWrt/procd-style
  init script: shebang `#!/bin/sh /etc/rc.common`, a `START=50` priority,
  and a `start()` function. The previous plain SysV `case "$1" in start)`
  template was never executed by OpenWrt/prplOS boot — procd ignores
  non-rc.common scripts even when the `/etc/rc.d/SNN<name>` symlink is
  present. Consequence on BGW720 over a 58 h soak: fault injector
  installed but never invoked at boot, so types 1–3 (link reset, process
  abort, kernel panic) injected zero events. The brcm-therm rule still
  fired only because the legit `bcm_thermal_drv` driver init prints the
  same `Trip 0: threshold=…` text that the fault injector echoes.
- Event handler reads the minicom capture with `errors='ignore'` instead of
  strict UTF-8, so UART noise (e.g. stray `0xff` bytes during boot) no longer
  raises `UnicodeDecodeError` and aborts the handler. Prior to this fix, the
  first fire on a fresh capture would succeed but every subsequent fire crashed
  with exit=1, so the markdown summary stopped updating even though events
  kept matching.
- `CommandRunner` injects `--timeout 30` ahead of every `serialwrap` subcommand
  (unless the caller already specified `--timeout` or `--endpoint`). The CLI
  default of 5 s is not enough when the daemon is under sustained load — eth
  link bouncing during reboot churn keeps the daemon's RPC queue busy, so the
  first call after a quiet period would time out with rc=2, surfacing as a
  spurious "serialwrap daemon not running" / "Failed to run session recover"
  in the reboot controller.
- Reboot controller registers event rules with an absolute handler path
  (`SERIALWRAP_EVENT_HANDLER` env var, defaulting to
  `<project_root>/bin/serialwrap-event-handler`). Previously the rule stored
  the bare name `serialwrap-event-handler`, which `serialwrap` runs via
  `subprocess.Popen` without a shell — every fire failed with
  `FileNotFoundError`, so the event markdown report was never generated even
  though hundreds of events matched.
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
