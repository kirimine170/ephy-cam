# Capture and ingestion contracts

## Current state

The repository contains schemas plus one hardware-validated XIAO ESP32S3 Sense
reference implementation．The implementation is manual，bounded，and does not
constitute an automatic production ingestion path．

## `CaptureSource`

A capture source describes a logical input without embedding credentials or a
local absolute path．Its stable ID is repository-independent，and its
capabilities declare media kinds and supported operations．Device-specific
connection settings remain outside Git．

The reference source uses `xiao-esp32s3-sense-01` and emits only image media．

## `MediaEnvelope`

A media envelope carries capture identity，source ID，media type，capture time，
content hash，size，and a staging reference．Binary media is never embedded in
the envelope．Metadata must not contain unnecessary personal data．

## Reference data flow

1. A local operator explicitly sends `CAPTURE` over USB CDC．
2. The device returns a length-delimited QXGA JPEG．
3. The host validates JPEG magic，decode，dimensions，size，and SHA-256．
4. The host writes the JPEG and validated JSON documents to Git-external
   temporary staging．

VGA preview uses the same request/response framing but remains memory-only．
Wi-Fi，microSD，automatic capture，continuous capture，Karte writes，and
automatic `ephy-runtime` ingestion are disabled．

## Ingestion boundary

- Capture adapters emit a validated envelope and place bytes in an authorized
  staging store．
- `ephy-runtime` may consume the staged reference only after future policy
  checks．
- Karte integration must not overwrite canonical content automatically．
- Retention，redaction，consent，and deletion policy must be decided before
  production deployment．
