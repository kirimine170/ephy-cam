# XIAO ESP32S3 Sense reference device

This directory owns the camera-specific reference implementation for
`xiao-esp32s3-sense-01`．It is intentionally separate from the Physical CI host
configuration．

## Verified behavior

- Arduino FQBN: `esp32:esp32:XIAO_ESP32S3`
- board option: `PSRAM=opi`
- Arduino-ESP32 core: `3.3.11`
- detected camera: OV3660
- preview: explicit VGA JPEG frames，kept in memory only
- capture: one QXGA JPEG after an explicit `CAPTURE` command
- transport: USB CDC with length-delimited binary framing

The fixed profile records the visually selected F-series calibration．The
OV3660 limits the requested long exposure while it is in VGA mode，so the
firmware reapplies `aec_value=1560` only after changing to QXGA．

## Build and flash

Run on `hil-01` or another Linux host with the pinned Arduino core already
installed．Use the stable serial path recorded in the local Physical CI
inventory．Do not put that path in Git．

```bash
reference/xiao-esp32s3-sense/scripts/build-flash.sh build /tmp/ephy-cam-build
reference/xiao-esp32s3-sense/scripts/build-flash.sh \
  flash /dev/serial/by-id/LOCAL_DEVICE /tmp/ephy-cam-build
```

## One-shot capture

The staging directory must be outside the repository．The command writes
`capture.jpg`，`capture-source.json`，`media-envelope.json`，and
`validation.json` under a generated capture ID．Binary media is referenced by
the envelope and is never embedded in it．

```bash
python3 reference/xiao-esp32s3-sense/host/ephy_cam_reference.py \
  capture /dev/serial/by-id/LOCAL_DEVICE /var/tmp/ephy-cam-staging
```

## Bounded preview

The server binds only to `127.0.0.1` and stops after 30 minutes by default．The
VGA stream is not persisted．The page's button triggers exactly one QXGA
capture．Forward the loopback port over SSH when viewing it from Windows．

```bash
reference/xiao-esp32s3-sense/scripts/run-preview.sh \
  /dev/serial/by-id/LOCAL_DEVICE /var/tmp/ephy-cam-staging
```

Automatic capture，continuous capture，Wi-Fi，microSD，Karte writes，and
automatic runtime ingestion remain disabled．Captured images and host-specific
logs must stay outside Git．
