# ephy-cam

## Overview

Camera and visual-ingestion interfaces for Ephy and Karte．

## Role in the Ephy ecosystem

This repository is an Ephy `extension` project．Its status is `design` and its
intended visibility is `public`．Repository relationships are declared in
`.ephy/project.yaml`．

## Goals

- Define capture-source and media-envelope contracts．
- Provide privacy-preserving reference implementations for supported devices．
- Keep device code separate from Physical CI host provisioning．

## Non-goals

- Do not store master images or production Karte content in Git．
- Do not implement Physical CI scheduling or host configuration here．
- Do not distribute `ephy-private` to capture devices．

## Current status

The current implementation status is `design`．Versioned camera authorization
contracts and a fail-closed media lifecycle domain are implemented under
`schemas/` and `src/ephy_cam/`．A hardware-validated reference implementation
for XIAO ESP32S3 Sense is available under
[`reference/xiao-esp32s3-sense`](reference/xiao-esp32s3-sense/README.md)．It is
a bounded reference and not yet a production capture service．

## Architecture

The repository defines `CaptureSource`，`CaptureRequest`，`CapturePermit`，
`MediaEnvelope`，`PhotoAcceptance`，and a policy-controlled staging boundary．
The reference device provides USB CDC VGA preview and explicit QXGA one-shot
capture．See
[capture and ingestion contracts](docs/capture-contracts.md) and the JSON
schemas under `schemas/`．

## Repository relationships

- Parent project: `ephy`
- Direct dependencies: none declared．
- Integration peers: `ephy-runtime` and `karte`．
- Runtime platforms: none declared．

Declare only parent and direct relationships．Do not use Git submodules as an
ecosystem relationship model．See
[docs/repository-relations.md](docs/repository-relations.md)．

## Getting started

The reference device requires Linux，Arduino-ESP32 `3.3.11`，and the host
packages documented by `ephy-physical-ci`．Use a Git-ignored staging directory
for every real capture．

## Testing

```bash
python3 -m pip install -r requirements-test.txt
python3 -m unittest discover -s tests -v
python3 scripts/validate_repository.py --check-sensitive-patterns
python3 -m py_compile \
  src/ephy_cam/*.py \
  reference/xiao-esp32s3-sense/host/ephy_cam_reference.py
```

Firmware compilation is performed on a Linux host with the pinned Arduino core
using the reference build script．

## Security and data handling

The data classification is `restricted`．Do not commit secrets，unnecessary
personal data，raw conversation history，production Karte data，master camera
images，raw training data，or model weights．See
[docs/security-and-data.md](docs/security-and-data.md)．

## Documentation

- [Architecture](docs/architecture.md)
- [Capture contracts](docs/capture-contracts.md)
- [Repository relationships](docs/repository-relations.md)
- [Security and data handling](docs/security-and-data.md)
- [Architecture Decision Records](docs/adr/README.md)

## License

No license has been selected．Determine visibility and licensing explicitly
before distribution，then add the license file and update this section．
