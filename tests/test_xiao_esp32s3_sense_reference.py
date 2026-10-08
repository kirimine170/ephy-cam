from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "reference" / "xiao-esp32s3-sense"


class XiaoEsp32S3SenseReferenceTests(unittest.TestCase):
    def test_manifest_pins_reproducible_toolchain_and_policy(self) -> None:
        manifest = json.loads(
            (REFERENCE / "device-manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["board"]["fqbn"], "esp32:esp32:XIAO_ESP32S3")
        self.assertEqual(manifest["board"]["options"], {"PSRAM": "opi"})
        self.assertEqual(manifest["board"]["arduino_esp32_version"], "3.3.11")
        self.assertEqual(manifest["camera"]["expected_model"], "OV3660")
        self.assertEqual(manifest["camera"]["preview"]["persistence"], "memory-only")
        self.assertEqual(
            manifest["camera"]["capture"]["trigger"], "explicit-command-only"
        )
        self.assertEqual(manifest["policy"]["automatic_capture"], "disabled")
        self.assertEqual(manifest["policy"]["karte_write"], "disabled")

    def test_fixed_profile_matches_hardware_selected_values(self) -> None:
        profile = json.loads(
            (REFERENCE / "device-manifest.json").read_text(encoding="utf-8")
        )["camera"]["fixed_profile"]
        self.assertEqual(
            (
                profile["awb_red_gain"],
                profile["awb_green_gain"],
                profile["awb_blue_gain"],
            ),
            (1361, 1024, 2116),
        )
        self.assertEqual((profile["agc_gain"], profile["aec_value"]), (8, 1560))

    def test_firmware_exposes_only_explicit_preview_and_capture(self) -> None:
        firmware = (
            REFERENCE / "firmware" / "EphyCamReference" / "EphyCamReference.ino"
        ).read_text(encoding="utf-8")
        self.assertIn('command == "PREVIEW"', firmware)
        self.assertIn('command == "CAPTURE"', firmware)
        self.assertNotIn("SEQUENCE", firmware)
        self.assertNotIn("WiFi", firmware)
        self.assertIn("FRAMESIZE_VGA", firmware)
        self.assertIn("FRAMESIZE_QXGA", firmware)
        self.assertIn("set_aec_value(sensor, kAecValue)", firmware)

    def test_host_has_no_instance_specific_transport_or_network_binding(self) -> None:
        host = (REFERENCE / "host" / "ephy_cam_reference.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('LOOPBACK = "127.0.0.1"', host)
        self.assertNotIn("/dev/serial/by-id/usb-", host)
        self.assertNotIn("192.168.", host)
        self.assertNotIn("10.0.", host)
        self.assertIn('"staging_reference"', host)
        self.assertNotIn('"media_bytes"', host)
        self.assertIn("staging root must be outside the Git repository", host)

    def test_host_consumes_capture_payload_header_before_jpeg(self) -> None:
        host = (REFERENCE / "host" / "ephy_cam_reference.py").read_text(
            encoding="utf-8"
        )
        self.assertEqual(
            host.count('payload_marker=f"{PROTOCOL} FRAME"'),
            2,
        )
        self.assertIn(
            'ProtocolError("payload byte count differs from frame metadata")',
            host,
        )


if __name__ == "__main__":
    unittest.main()
