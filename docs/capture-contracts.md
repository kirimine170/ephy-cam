# Capture and ingestion contracts

## Current state

The repository contains versioned camera authorization schemas，a small
fail-closed domain implementation，and one hardware-validated XIAO ESP32S3
Sense reference implementation．The reference transport is manual and bounded．
The contract implementation does not create an automatic production ingestion
path，scheduler，or network service．

The contracts follow the responsibility boundaries in the Ephy camera-context
design and ADRs．The JSON schemas provide structural validation．
`src/ephy_cam/contracts.py` provides chronological and other cross-field checks
that JSON Schema cannot express．Every public domain validator applies the full
schema before those checks，and `src/ephy_cam/capture_gate.py` enforces the
resolved permit at the gateway boundary．Callers cannot bypass structural
validation by invoking the domain API directly．

The domain validator's pinned runtime dependency is declared in
`requirements-contracts.txt`．Linux hosts may satisfy the same dependency with
the distribution-provided `python3-jsonschema` package documented by
`ephy-physical-ci`．

## `CaptureSource`

A capture source describes a logical input without embedding credentials or a
local absolute path．Its stable ID is repository-independent，and its
capabilities declare media kinds and supported operations．Device-specific
connection settings remain outside Git．

The reference source uses `xiao-esp32s3-sense-01` and emits only image media．

## Versioned contracts

### `CaptureRequest` v1

`CaptureRequest` carries a unique request and trace ID，a source，an explicit
purpose，a bounded time window，a capture profile，a retention intent，and an
opaque permit token．The token is resolved and authenticated outside this
repository．It must never be logged．A request may ask only for `ephemeral` or
`candidate` retention; successful capture cannot directly create accepted
media．

### `CapturePermit` v1

`CapturePermit` is the resolved，short-lived authority evaluated by a gateway．
It binds source and purpose，issue and expiry time，an opaque place assertion，
capture limit，cooldown，visible-indicator requirement，and policy revision．
This version requires `indicator_required: true`．Invalid，expired，revoked，
replayed，over-limit，or otherwise mismatched operations fail closed．

### `MediaEnvelope` v1 and v2

A media envelope carries capture identity，source ID，media type，capture time，
content hash，size，and a staging reference．Binary media is never embedded in
the envelope．Metadata must not contain unnecessary personal data．

`schemas/media-envelope.schema.json` remains the unchanged v1 contract for the
manual USB reference and existing consumers．Consumers must select a schema by
`schema_version`; a v1 document is not silently treated as v2．

`schemas/media-envelope-v2.schema.json` adds purpose，retention class，expiry，
dimensions，clock source，firmware version，sensor model，and trace ID．It is a
closed schema and does not admit coordinates，Wi-Fi identifiers，captions，
people or emotion inference，or embedded media bytes．A v1 consumer may keep
reading v1 while it is upgraded deliberately; a producer must not remove v1
support until every declared consumer supports v2．

### `PhotoAcceptance` v1

`PhotoAcceptance` records an explicit user or policy decision for a candidate
capture．A policy decision also names its policy revision．The schema lives here
as an integration contract，while the authoritative decision provider belongs
to `ephy-runtime`．Capture and JPEG validation alone never imply acceptance．

## Retention lifecycle

Every capture begins in `ephemeral`．The only supported forward path is:

```text
ephemeral -> candidate -> accepted -> diary-eligible -> committed -> purged
```

Earlier states may also transition directly to `purged` when rejected or
expired．`purged` is terminal．`MediaLifecycle` records an append-only，
time-monotonic event history and rejects unsupported transitions．The
`accepted` transition requires a matching `PhotoAcceptance` while the capture
is already a candidate．Diary eligibility and Karte commit are separate steps．

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
- A gateway validates the request and resolved permit before invoking a device．
- `ephy-runtime` may consume the staged reference only after policy checks and
  an explicit acceptance decision．
- Karte integration must not overwrite canonical content automatically．
- Retention，redaction，consent，and deletion policy must be decided before
  production deployment．

The current in-memory authorization ledger demonstrates deterministic
fail-closed behavior．Production token authentication，durable replay state，
transport，scheduler，retention worker，and runtime integration remain separate
implementation responsibilities．
