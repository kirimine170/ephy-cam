# Changelog

All notable changes to this repository are documented in this file．

## Unreleased

### Added

- CaptureRequest v1，CapturePermit v1，MediaEnvelope v2，and PhotoAcceptance v1
  JSON schemas with synthetic fixtures．
- Fail-closed capture authorization checks for time bounds，permit scope，
  revocation，indicator readiness，capture limits，cooldown，and replay．
- Auditable media lifecycle from `ephemeral` through `purged`，with explicit
  candidate acceptance and terminal purge semantics．
- MediaEnvelope v1 compatibility and camera-contract validation in CI．
- Full schema enforcement at capture-authorization and photo-acceptance domain
  boundaries．
- XIAO ESP32S3 Sense reference firmware with fixed OV3660 color calibration．
- Bounded loopback VGA preview and explicit QXGA USB capture host tool．
- Git-external staging with JPEG and schema validation．
- Initial language-independent Ephy repository template．
- Project metadata schema，initializer，validator，tests，and GitHub templates．
