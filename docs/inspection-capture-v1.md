# Offline inspection capture v1

This MVP imports one existing capture into a self-contained observation bundle．
It does not capture hardware，open serial or network connections，write Karte or
Physical CI ledgers，operate CAD，or produce dimensions or pass/fail judgments．

## Independent session

Prepare a session using
[`inspection-session-v1.schema.json`](../schemas/inspection-session-v1.schema.json)．
The [synthetic example](../tests/fixtures/synthetic/inspection-session-v1.json)
contains session，task and sample IDs；design and manufacturing job references；
design revision and CAD/parameter/requirement SHA256 values；fixture，mount，
lighting and background IDs/revisions；and explicit calibration status．
Use the literal `unknown` for unavailable provenance．Session，task and sample
IDs must be known．Hashes bind declared artifacts；they never establish which
physical sample was in view．Identity is an `operator_assertion`．

The session's capture ID，source ID and relative image path must match the
envelope's `staging://SOURCE/CAPTURE/IMAGE` reference．No path is inferred from
the image hash or fetched from that URI．The capture directory contains the
image，`media-envelope.json` and `validation.json` in the reference host format．
Optional `capture-source.json` and unrelated files are not imported．

Both existing v1 and closed v2 envelopes remain unchanged．V2 imports validate
the existing cross-field contract and reject already accepted media．Import is
not a capture permit，retention authorization or an adoption decision．The
caller remains responsible for lawful retention and private storage，including
v2 expiry；offline replay makes no current-time authorization judgment．

## Import and replay

Install `requirements-contracts.txt` and set `PYTHONPATH=src`．The additional
expected IDs must come from the caller's independent task/sample selection，
not be copied programmatically from the session being checked．For example：

```sh
PYTHONPATH=src python -m ephy_cam.replay \
  --session tests/fixtures/synthetic/inspection-session-v1.json \
  --capture-dir /private/staging/existing-capture \
  --output /private/evidence/new-bundle \
  --expect-session-id synthetic-session-01 \
  --expect-task-id synthetic-task-01 \
  --expect-sample-id synthetic-sample-01
```

Use an existing trusted output parent outside Git checkouts．The output must
not exist，even as an empty directory．The importer first reads and checks all
bounded inputs，builds a private sibling staging directory，replays the snapshot，
and atomically publishes the complete directory without replacing a destination．
Failures remove only the uniquely owned stage．Publication supports Windows
rename and Linux `renameat2(RENAME_NOREPLACE)`；unsupported hosts fail closed．
This is publication atomicity，not a power-loss durability guarantee．

Output：

```text
new-bundle/
  session.json
  capture/<session image_path>
  capture/media-envelope.json
  capture/validation.json
  capture-manifest.json
  replay-report.json
  evidence-refs.json
```

Session，image，envelope and validation snapshots are byte-identical to their
inputs，including JSON whitespace．The separate
[`capture-manifest-v1.schema.json`](../schemas/capture-manifest-v1.schema.json)
binds each path，byte size and SHA256．The report is deterministic，with no new
wall-clock timestamp．Save the report's `manifest_sha256` independently when
accepting a bundle for later integrity checks：

```sh
PYTHONPATH=src python -m ephy_cam.replay \
  --bundle /private/evidence/new-bundle \
  --manifest-sha256 <independently_saved_64_character_digest> \
  --expect-session-id synthetic-session-01 \
  --expect-task-id synthetic-task-01 \
  --expect-sample-id synthetic-sample-01
```

Replay is read-only．It rechecks source schemas，all byte bindings，capture
identity，manifest semantics，and derived report/reference contents．Without an
external digest it proves internal consistency only；a coherent rewrite of
files and hashes cannot be detected by hashes stored in that same bundle．No
signature，chain of custody or physical sample authenticity is implied．Errors
return exit code 2 and a reason code without printing private paths or input
contents．

## Observation evidence boundary

Every report has `metrology_eligible=false`，
`physical_validation="not_performed"` and `adopted=false`，including sessions
with operator-declared calibration．Unknown calibration supports observation
evidence．No calibration evaluation or measurement algorithm is implemented．
No measurement，tolerance，material property or uncertainty is generated．

For the legacy XIAO reference v1 source `xiao-esp32s3-sense-01`，the original
`captured_at` is host artifact-write time after JPEG receipt，not exposure or
device time．The sidecar records `host_artifact_write_after_jpeg_receive` and
`exposure_time="unknown"`．Other source/v2 timestamps remain unverified envelope
declarations．Raw AEC values and other device metadata are preserved in the
validation snapshot without unit conversion．The importer checks JPEG boundary
markers，hashes and byte counts．The source's decode/dimension claims are bound
records；JPEG decoding is explicitly `not_performed` during replay．

`evidence-refs.json` contains `{"evidence_refs": [{"path": "capture/capture.jpg",
"sha256": "..."}, ...]}` using the existing Physical CI `inspect-length`
`{path, sha256}` format．There are five references：the four snapshots and the
manifest．Place a separately authored human measurement JSON at the bundle
root and copy the reference array into it，or prefix every reference with the
bundle's relative directory name if the measurement JSON is in its parent．
References resolve relative to the measurement JSON's directory．Match the
human measurement's sample/job fields to the independently selected Physical
CI subject and supply its independent requirement．The existing `inspect-length`
implementation handles human values and uncertainty；this importer does not
rewrite it or supply a measurement record．

## Input bounds and operating assumptions

Each JSON input is limited to 64 KiB；the image is limited to 16 MiB．Reject empty
files，duplicate JSON keys，nonfinite numbers，unknown fields/versions，path
traversal，absolute/drive paths，path aliases，Windows device/stream names，
symlinks/reparse points，multiply linked files，FIFOs，devices and directories．
Reads check descriptor type，file identity，size and timestamps before/after
bounded reads．Sources must be private and quiescent，and the output parent must
be trusted．This portable MVP does not defend against a malicious concurrent
process that can replace ancestor directories or edit bytes while restoring
metadata．Use OS access controls to enforce exclusive ownership．A published
bundle remains mutable；replay detects changes against saved bindings or an
independently pinned manifest．

Only synthetic fixtures belong in Git．Tests use deliberately synthetic JPEG
marker bytes and make no decode or hardware-validation claims．Real images，
session identifiers and production metadata remain in external private staging．

The next separate step is a read-only integration test with an independently
authored human measurement and existing `inspect-length`．Live ledger/Karte
writes and camera/CAD tooling remain separate work．
