# Architecture

## Purpose

This repository owns camera-device adapters，capture contracts，and staged media
metadata for the Ephy ecosystem．

## Repository layers

- `.ephy/` contains project identity，relationships，and data policy．
- `schemas/` contains capture authorization，acceptance，source，and media
  envelope contracts．
- `src/ephy_cam/` contains cross-field validation，the fail-closed gateway gate，
  and the auditable media lifecycle domain．
- `reference/` contains device-owned reference implementations．
- `docs/` describes architecture，security，and long-lived decisions．
- `scripts/` and `tests/` validate repository and implementation invariants．

## XIAO ESP32S3 Sense reference

The device firmware owns camera pins，OV3660 sensor settings，resolution
switching，and USB framing．The host reference owns JPEG validation，temporary
staging，contract generation，and a bounded loopback preview．

The preview path requests individual VGA frames and retains only the newest
frame in memory．An explicit capture request switches the sensor to QXGA，
reapplies the fixed manual exposure，warms the sensor，returns one JPEG，and
returns to VGA．

## Responsibility boundaries

`ephy-physical-ci` may provision host packages and invoke generic build，flash，
and artifact-validation commands．It must not own camera pins，sensor profiles，
or device protocol implementation．`ephy-runtime` and Karte remain outside the
automatic path for this reference．

`ephy-runtime` owns permit issuance and photo-acceptance decisions．A camera
gateway owns permit enforcement，replay/cooldown/capture-limit state，device
invocation，and expiring staging．This repository owns the shared schemas and
device/gateway reference behavior，but does not own Karte canonical writes．

The contract domain intentionally accepts an already authenticated，resolved
permit．Permit-token cryptography，production transport，durable persistence，
and scheduling are not hidden inside the domain model．They are separate later
implementation slices and must preserve the same fail-closed checks．

## Implementation state and proposals

The XIAO reference and USB protocol are implemented and hardware validated．The
versioned contract schemas，synthetic fixtures，authorization gate，and media
lifecycle are implemented and locally validated．A production gateway，permit
provider，daemon，scheduler，retention service，and automatic policy ingestion
remain future proposals．
