# Architecture

## Purpose

This repository owns camera-device adapters，capture contracts，and staged media
metadata for the Ephy ecosystem．

## Repository layers

- `.ephy/` contains project identity，relationships，and data policy．
- `schemas/` contains `CaptureSource` and `MediaEnvelope` contracts．
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

## Implementation state and proposals

The XIAO reference and USB protocol are implemented and hardware validated．A
daemon，scheduler，retention service，and automatic policy ingestion remain
future proposals．
