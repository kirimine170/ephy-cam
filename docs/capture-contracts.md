# Capture and ingestion contracts

## Current state

The camera subsystem is being redesigned around hardware constraints．This repository currently defines contracts and synthetic fixtures only．It contains no device driver，real capture，master image，or production Karte data．

## `CaptureSource`

A capture source describes a logical input without embedding credentials or a local absolute path．Its stable ID is repository-independent，and its capabilities declare media kinds and supported operations．Device-specific connection settings remain outside Git．

## `MediaEnvelope`

A media envelope carries capture identity，source ID，media type，capture time，content hash，size，and a storage reference．Binary media is not embedded in the envelope．Metadata must not contain unnecessary personal data．

## Ingestion boundary

- Capture adapters emit a validated envelope and place bytes in an authorized staging store．
- `ephy-runtime` may consume the staged reference after policy checks．
- Karte integration writes only to staging or outbox and never overwrites canonical content automatically．
- Retention，redaction，consent，and deletion policy are decided before any real device integration．
- Tests use visibly synthetic metadata and generated non-personal fixtures only．
