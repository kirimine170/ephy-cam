from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ephy_cam.inspection_bundle import (BundleError, MAX_IMAGE_BYTES, MAX_JSON_BYTES,
    canonical, create_bundle, digest, read_regular, relative_path, replay_bundle, strict_json)
from ephy_cam.replay import main

FIXTURE = ROOT / "tests" / "fixtures" / "synthetic" / "inspection-session-v1.json"


class InspectionBundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.capture = self.root / "source"
        self.capture.mkdir()
        self.session = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.session_path = self.root / "session.json"
        self.expected = {key: self.session[key] for key in ("session_id", "task_id", "sample_id")}
        # Deliberately marker-only, synthetic bytes; replay never claims decode.
        self.image = b"\xff\xd8\xffsynthetic-observation-fixture\xff\xd9"
        self.envelope = {"schema_version": 1, "capture_id": self.session["capture"]["capture_id"],
                         "source_id": self.session["capture"]["source_id"], "media_type": "image/jpeg",
                         "captured_at": "2026-01-01T00:00:00Z", "sha256": digest(self.image),
                         "size_bytes": len(self.image), "staging_reference": "staging://{source_id}/{capture_id}/{image_path}".format(**self.session["capture"])}
        self.validation = {"jpeg_magic": "valid", "decode": "valid", "width": 2048, "height": 1536,
                           "sha256": digest(self.image), "size_bytes": len(self.image),
                           "capture_duration_ms": -1, "transfer_duration_ms": -1,
                           "device_metadata": {"aec_value": "1560"}}
        self.write_inputs()

    def write_inputs(self):
        self.session_path.write_bytes(canonical(self.session))
        image_path = self.capture / self.session["capture"]["image_path"]
        image_path.parent.mkdir(parents=True, exist_ok=True)
        image_path.write_bytes(self.image)
        (self.capture / "media-envelope.json").write_bytes(canonical(self.envelope))
        (self.capture / "validation.json").write_bytes(canonical(self.validation))

    def build(self, name="bundle"):
        return create_bundle(self.session_path, self.capture, self.root / name, expected_identity=self.expected)

    def reject(self, reason=None):
        with self.assertRaises(BundleError) as caught:
            self.build()
        if reason:
            self.assertEqual(str(caught.exception), reason)
        self.assertFalse((self.root / "bundle").exists())
        self.assertEqual(list(self.root.glob(".inspection-*")), [])

    def test_deterministic_byte_identical_snapshots_and_independent_replay(self):
        report = self.build("a")
        self.assertEqual(report, self.build("b"))
        for file in (self.root / "a").rglob("*"):
            if file.is_file():
                self.assertEqual(file.read_bytes(), (self.root / "b" / file.relative_to(self.root / "a")).read_bytes())
        for source, snapshot in ((self.session_path, "session.json"), (self.capture / "capture.jpg", "capture/capture.jpg"),
                                 (self.capture / "media-envelope.json", "capture/media-envelope.json"), (self.capture / "validation.json", "capture/validation.json")):
            self.assertEqual(source.read_bytes(), (self.root / "a" / snapshot).read_bytes())
        self.assertEqual(report, replay_bundle(self.root / "a", expected_identity=self.expected, expected_manifest_sha256=report["manifest_sha256"]))
        (self.capture / "capture.jpg").write_bytes(b"changed source after snapshot")
        self.assertEqual(report, replay_bundle(self.root / "a", expected_identity=self.expected))

    def test_every_snapshot_one_byte_mutation_rejects(self):
        self.build()
        root = self.root / "bundle"
        for name in ("session.json", "capture/capture.jpg", "capture/media-envelope.json", "capture/validation.json", "capture-manifest.json", "replay-report.json", "evidence-refs.json"):
            with self.subTest(name=name):
                path = root / name
                original = path.read_bytes()
                path.write_bytes(original[:-1] + b"X")
                with self.assertRaises(BundleError):
                    replay_bundle(root, expected_identity=self.expected)
                path.write_bytes(original)

    def test_source_image_or_validation_digest_mismatch(self):
        (self.capture / "capture.jpg").write_bytes(self.image[:-3] + b"X\xff\xd9")
        self.reject("image_binding_mismatch")
        self.write_inputs()
        self.validation["sha256"] = "0" * 64
        self.write_inputs()
        self.reject("validation_binding_mismatch")

    def test_expected_identity_is_independent_and_required(self):
        for field in self.expected:
            with self.subTest(field=field):
                wrong = {**self.expected, field: "different"}
                with self.assertRaisesRegex(BundleError, "identity_mismatch"):
                    create_bundle(self.session_path, self.capture, self.root / "bundle", expected_identity=wrong)
        with self.assertRaises(TypeError):
            create_bundle(self.session_path, self.capture, self.root / "bundle")
        for invalid in ({}, {**self.expected, "extra": "x"}, {**self.expected, "sample_id": "unknown"}):
            with self.assertRaises(BundleError):
                create_bundle(self.session_path, self.capture, self.root / "bundle", expected_identity=invalid)
        self.build()
        with self.assertRaisesRegex(BundleError, "identity_mismatch"):
            replay_bundle(self.root / "bundle", expected_identity={**self.expected, "sample_id": "different"})

    def test_coherent_relabeling_requires_external_manifest_anchor(self):
        report = self.build("first")
        self.session["sample_id"] = "other-sample"
        self.expected["sample_id"] = "other-sample"
        self.write_inputs()
        self.build("second")
        with self.assertRaisesRegex(BundleError, "manifest_digest_mismatch"):
            replay_bundle(self.root / "second", expected_identity=self.expected, expected_manifest_sha256=report["manifest_sha256"])

    def test_strict_json_rejects_duplicate_nonfinite_and_deep_inputs(self):
        for raw in (b'{"a":1,"a":2}', b'{"a":{"b":1,"b":2}}', b'{"a":NaN}', b'{"a":Infinity}', b'{"a":-Infinity}', b'{"a":1e999}', b'[]', b'{}\xff', b'', b'[' * 2000 + b']' * 2000):
            with self.subTest(raw=raw[:60]), self.assertRaises(BundleError):
                strict_json(raw)

    def test_all_document_json_is_strict(self):
        for path in (self.session_path, self.capture / "media-envelope.json", self.capture / "validation.json"):
            original = path.read_bytes()
            for suffix in (b',"duplicate":NaN}', b',"duplicate":1,"duplicate":2}'):
                path.write_bytes(original.rstrip()[:-1] + suffix)
                self.reject()
            path.write_bytes(original)

    def test_session_unknown_fields_versions_and_nested_fields_reject(self):
        for version in (True, 1.0, 0, 2, "1"):
            self.session["schema_version"] = version
            self.write_inputs()
            self.reject()
        self.session["schema_version"] = 1
        self.session["measurement"] = 1
        self.write_inputs()
        self.reject()
        del self.session["measurement"]
        self.session["fixture"]["extra"] = "x"
        self.write_inputs()
        self.reject()

    def test_envelope_unknown_fields_and_versions_reject(self):
        for version in (True, 1.0, 0, 3, "1"):
            self.envelope["schema_version"] = version
            self.write_inputs()
            self.reject()
        self.envelope["schema_version"] = 1
        self.envelope["sample_id"] = "not-an-envelope-field"
        self.write_inputs()
        self.reject()

    def test_manifest_unknown_fields_version_semantics_and_paths_reject(self):
        self.build()
        root = self.root / "bundle"
        path = root / "capture-manifest.json"
        original = strict_json(path.read_bytes())
        for alter in (lambda m: m.update(extra="x"), lambda m: m.update(schema_version=2),
                      lambda m: m["capture_time"].update(meaning="envelope_declared_time_unverified"),
                      lambda m: m["bindings"]["image"].update(path="../source/capture.jpg"),
                      lambda m: m["bindings"]["image"].update(path="session.json")):
            modified = copy.deepcopy(original)
            alter(modified)
            path.write_bytes(canonical(modified))
            with self.assertRaises(BundleError):
                replay_bundle(root, expected_identity=self.expected)
        path.write_bytes(canonical(original))

    def test_capture_identity_and_staging_reference_reject(self):
        for field in ("source_id", "capture_id", "staging_reference"):
            original = self.envelope[field]
            self.envelope[field] = "other-source" if field == "source_id" else "33333333-3333-4333-8333-333333333333" if field == "capture_id" else "staging://other/capture.jpg"
            self.write_inputs()
            self.reject()
            self.envelope[field] = original

    def test_relative_paths_reject_cross_platform_aliases_and_devices(self):
        for name in ("../capture.jpg", "a/../b.jpg", "/a.jpg", "a//b.jpg", "a/./b.jpg", "C:/a.jpg", "a\\b.jpg", "a.jpg:stream", "NUL", "con.jpg", "com1.jpg", "foo.", "a\x00b", "", "a/PRN"):
            with self.subTest(name=name), self.assertRaises(BundleError):
                relative_path(name)
        for name in ("../capture.jpg", "a/../capture.jpg", "a//capture.jpg", "NUL", "media-envelope.json"):
            self.session["capture"]["image_path"] = name
            self.session_path.write_bytes(canonical(self.session))
            self.reject()

    def test_nested_image_relative_path_supported(self):
        self.session["capture"]["image_path"] = "view/capture.jpg"
        self.envelope["staging_reference"] = "staging://{source_id}/{capture_id}/{image_path}".format(**self.session["capture"])
        self.write_inputs()
        report = self.build()
        self.assertIn("capture/view/capture.jpg", [r["path"] for r in report["evidence_refs"]])

    def test_symlink_input_file_and_parent_reject(self):
        link = self.root / "linked-source"
        try:
            link.symlink_to(self.capture, target_is_directory=True)
        except OSError:
            self.skipTest("host does not grant symlink creation")
        with self.assertRaisesRegex(BundleError, "symlink_or_reparse_point"):
            create_bundle(self.session_path, link, self.root / "bundle", expected_identity=self.expected)
        image = self.capture / "capture.jpg"
        image.unlink()
        image.symlink_to(self.session_path)
        self.reject("symlink_or_reparse_point")

    def test_hardlink_input_rejects(self):
        image = self.capture / "capture.jpg"
        image.unlink()
        os.link(self.session_path, image)
        self.reject("multiply_linked_file")

    @unittest.skipUnless(hasattr(os, "mkfifo"), "POSIX FIFO unavailable")
    def test_fifo_and_device_reject_without_open(self):
        image = self.capture / "capture.jpg"
        image.unlink()
        os.mkfifo(image)
        with patch("ephy_cam.inspection_bundle.os.open", side_effect=AssertionError("must reject before open")):
            with self.assertRaisesRegex(BundleError, "regular_file_required"):
                read_regular(image, MAX_IMAGE_BYTES)
            with self.assertRaisesRegex(BundleError, "regular_file_required"):
                read_regular(Path("/dev/null"), MAX_IMAGE_BYTES)

    def test_empty_oversize_directory_and_missing_inputs_reject(self):
        image = self.capture / "capture.jpg"
        image.write_bytes(b"")
        self.reject("file_size_out_of_bounds")
        with image.open("wb") as stream:
            stream.truncate(MAX_IMAGE_BYTES + 1)
        self.reject("file_size_out_of_bounds")
        image.unlink()
        image.mkdir()
        self.reject("regular_file_required")
        image.rmdir()
        self.reject("input_unavailable")
        self.write_inputs()
        self.session_path.write_bytes(b" " * (MAX_JSON_BYTES + 1))
        self.reject("file_size_out_of_bounds")

    def test_existing_output_even_empty_is_untouched(self):
        output = self.root / "bundle"
        output.mkdir()
        with self.assertRaisesRegex(BundleError, "output_already_exists"):
            self.build()
        marker = output / "operator-owned"
        marker.write_bytes(b"preserve")
        with self.assertRaisesRegex(BundleError, "output_already_exists"):
            self.build()
        self.assertEqual(marker.read_bytes(), b"preserve")

    def test_publication_failure_removes_only_owned_stage(self):
        with patch("ephy_cam.inspection_bundle._publish", side_effect=OSError("simulated")):
            self.reject("publication_failed")
        def race(stage, output):
            output.mkdir()
            (output / "race-owner").write_bytes(b"preserve")
            raise FileExistsError()
        with patch("ephy_cam.inspection_bundle._publish", side_effect=race):
            with self.assertRaisesRegex(BundleError, "output_already_exists"):
                self.build()
        self.assertEqual((self.root / "bundle/race-owner").read_bytes(), b"preserve")
        self.assertEqual(list(self.root.glob(".inspection-*")), [])

    def test_atomic_publish_never_replaces_existing_directory(self):
        from ephy_cam.inspection_bundle import _publish
        stage, output = self.root / "stage", self.root / "destination"
        stage.mkdir()
        output.mkdir()
        with self.assertRaises(OSError):
            _publish(stage, output)
        self.assertTrue(stage.is_dir())
        self.assertTrue(output.is_dir())

    def test_output_checkout_overlap_and_missing_parent_reject(self):
        (self.root / ".git").mkdir()
        self.reject("output_must_be_outside_git_checkout")
        (self.root / ".git").rmdir()
        with self.assertRaisesRegex(BundleError, "output_overlaps_source"):
            create_bundle(self.session_path, self.capture, self.capture / "bundle", expected_identity=self.expected)
        with self.assertRaisesRegex(BundleError, "input_unavailable"):
            create_bundle(self.session_path, self.capture, self.root / "missing/bundle", expected_identity=self.expected)

    def test_validation_unknown_fields_types_and_hashes_reject(self):
        original = copy.deepcopy(self.validation)
        for changes in ({"extra": "x"}, {"width": True}, {"width": 1.0}, {"height": 0}, {"size_bytes": 1},
                        {"decode": "failed"}, {"device_metadata": {"aec_value": 1560}}, {"device_metadata": {"value": "x" * 257}}):
            self.validation = {**original, **changes}
            self.write_inputs()
            self.reject()

    def test_explicit_unknown_provenance_and_calibration_do_not_qualify_metrology(self):
        for field in ("design_job_ref", "manufacturing_job_ref", "design_revision", "cad_sha256", "parameter_sha256", "requirement_sha256"):
            self.session[field] = "unknown"
        self.session["calibration"] = {"status": "operator_declared", "reference": "synthetic-calibration", "revision": "1"}
        self.write_inputs()
        report = self.build()
        self.assertFalse(report["metrology_eligible"])
        self.assertEqual(strict_json((self.root / "bundle/session.json").read_bytes()), self.session)

    def test_read_detects_file_replacement(self):
        real_open = os.open
        def replace(path, flags):
            path.unlink()
            path.write_bytes(self.image)
            return real_open(path, flags)
        with patch("ephy_cam.inspection_bundle.os.open", side_effect=replace):
            with self.assertRaisesRegex(BundleError, "source_changed_during_read"):
                read_regular(self.capture / "capture.jpg", MAX_IMAGE_BYTES)

    def test_unknown_calibration_legacy_time_and_no_measurements(self):
        report = self.build()
        self.assertEqual(report["capture_time"], {"captured_at": self.envelope["captured_at"], "meaning": "host_artifact_write_after_jpeg_receive", "exposure_time": "unknown"})
        self.assertFalse(report["metrology_eligible"])
        self.assertFalse(report["adopted"])
        self.assertEqual(report["physical_validation"], "not_performed")
        self.assertEqual(report["identity_basis"], "operator_assertion")
        for field in ("measurement", "tolerance", "material", "uncertainty", "value"):
            self.assertNotIn(field, report)
        self.assertEqual(strict_json((self.root / "bundle/capture/validation.json").read_bytes())["device_metadata"]["aec_value"], "1560")

    def test_v2_import_preserves_closed_envelope(self):
        self.envelope.update(schema_version=2, purpose="physical-ci-test", retention_class="candidate", expires_at="2026-01-02T00:00:00Z", width=2048, height=1536,
                             clock_source="gateway-utc", firmware_version="synthetic-v1", sensor_model="synthetic", trace_id="22222222-2222-4222-8222-222222222222")
        self.write_inputs()
        report = self.build()
        self.assertEqual(report["capture_time"]["meaning"], "envelope_declared_time_unverified")
        self.assertEqual((self.capture / "media-envelope.json").read_bytes(), (self.root / "bundle/capture/media-envelope.json").read_bytes())
        self.envelope["sample_id"] = "not-allowed"
        self.write_inputs()
        with self.assertRaises(BundleError):
            self.build("invalid")
        del self.envelope["sample_id"]
        self.envelope["retention_class"] = "accepted"
        self.write_inputs()
        with self.assertRaisesRegex(BundleError, "accepted_media_out_of_scope"):
            self.build("accepted")

    def test_no_network_serial_hardware_or_ledger_side_effect(self):
        with patch.object(socket, "socket", side_effect=AssertionError("offline only")), patch.object(subprocess, "Popen", side_effect=AssertionError("no hardware process")):
            report = self.build()
            self.assertEqual(report, replay_bundle(self.root / "bundle", expected_identity=self.expected))
        for path in (ROOT / "src/ephy_cam/inspection_bundle.py", ROOT / "src/ephy_cam/replay.py"):
            text = path.read_text(encoding="utf-8")
            for module in ("import serial", "import requests", "import socket", "import ephy_cam_reference", "from .lifecycle", "import karte"):
                self.assertNotIn(module, text)

    def test_cli_import_replay_and_errors(self):
        identity_args = [item for key, value in self.expected.items() for item in ("--expect-" + key.replace("_", "-"), value)]
        import_args = ["--session", str(self.session_path), "--capture-dir", str(self.capture), "--output", str(self.root / "bundle"), *identity_args]
        with patch("sys.stdout"):
            self.assertEqual(main(import_args), 0)
            self.assertEqual(main(["--bundle", str(self.root / "bundle"), *identity_args]), 0)
        with patch("sys.stderr"):
            self.assertEqual(main(import_args), 2)
            with self.assertRaises(SystemExit):
                main(["--bundle", str(self.root / "bundle")])

    def test_evidence_refs_match_inspect_length_shape_and_bytes(self):
        report = self.build()
        self.assertLessEqual(len(report["evidence_refs"]), 16)
        for ref in report["evidence_refs"]:
            self.assertEqual(set(ref), {"path", "sha256"})
            self.assertFalse(Path(ref["path"]).is_absolute())
            self.assertEqual(ref["sha256"], digest((self.root / "bundle" / ref["path"]).read_bytes()))


if __name__ == "__main__":
    unittest.main()
