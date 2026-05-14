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
