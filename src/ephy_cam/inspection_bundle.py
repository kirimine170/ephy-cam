"""Bounded offline snapshots; file integrity is not physical sample identity."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tempfile
from typing import Any, Mapping

from .contracts import ContractViolation, validate_structure, validate_media_envelope_v2

MAX_JSON_BYTES = 64 * 1024
MAX_IMAGE_BYTES = 16 * 1024 * 1024
IDENTITY_FIELDS = ("session_id", "task_id", "sample_id")
TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,255}\Z")
STATUS = {"metrology_eligible": False, "physical_validation": "not_performed", "adopted": False}


class BundleError(ValueError):
    """A stable reason code; errors never contain input bytes or private paths."""


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise BundleError("duplicate_json_key")
        result[key] = value
    return result


def _constant(value):
    raise BundleError("nonfinite_json_number")


def _float(token):
    value = float(token)
    if not math.isfinite(value):
        raise BundleError("nonfinite_json_number")
    return value


def strict_json(raw: bytes) -> dict[str, Any]:
    if not raw or len(raw) > MAX_JSON_BYTES:
        raise BundleError("json_size_out_of_bounds")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique,
                           parse_constant=_constant, parse_float=_float)
    except BundleError:
        raise
    except (ValueError, UnicodeError, RecursionError, OverflowError) as exc:
        raise BundleError("invalid_json") from exc
    if not isinstance(value, dict):
        raise BundleError("json_object_required")
    return value


def canonical(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def relative_path(name: str) -> str:
    # Reject normalized aliases, Windows devices/ADS, drive paths and traversal.
    if not isinstance(name, str) or len(name) > 512 or not name:
        raise BundleError("unsafe_relative_path")
    parts = name.split("/")
    for part in parts:
        if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", part)
                or part.endswith(".") or part in (".", "..")
                or re.fullmatch(r"(?i:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part)):
            raise BundleError("unsafe_relative_path")
    return name


def _stat(path: Path):
    try:
        value = path.lstat()
    except (OSError, ValueError) as exc:
        raise BundleError("input_unavailable") from exc
    if stat.S_ISLNK(value.st_mode) or getattr(value, "st_file_attributes", 0) & 0x400:
        raise BundleError("symlink_or_reparse_point")
    return value


def _checked_path(path: Path, *, directory: bool = False) -> tuple[Path, Any]:
    path = Path(os.path.abspath(path))
    for ancestor in reversed(path.parents):
        if not stat.S_ISDIR(_stat(ancestor).st_mode):
            raise BundleError("directory_required")
    value = _stat(path)
    if not (stat.S_ISDIR(value.st_mode) if directory else stat.S_ISREG(value.st_mode)):
        raise BundleError("directory_required" if directory else "regular_file_required")
    return path, value


def _signature(value):
    # Python 3.12 Windows path stat and descriptor stat expose different
    # ctime semantics. File ID, size and mtime are comparable on both APIs.
    signature = (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)
    return signature if os.name == "nt" else (*signature, value.st_ctime_ns)


def read_regular(path: Path, limit: int) -> bytes:
    path, before = _checked_path(path)
    if before.st_nlink != 1:
        raise BundleError("multiply_linked_file")
    if not 0 < before.st_size <= limit:
        raise BundleError("file_size_out_of_bounds")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = None
    try:
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or _signature(before) != _signature(opened):
            raise BundleError("source_changed_during_read")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            raw = stream.read(limit + 1)
            after = os.fstat(stream.fileno())
        if _signature(opened) != _signature(after) or _signature(after) != _signature(_stat(path)):
            raise BundleError("source_changed_during_read")
    except OSError as exc:
        raise BundleError("input_unavailable") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if not raw or len(raw) > limit:
        raise BundleError("file_size_out_of_bounds")
    return raw


def _structure(value, schema):
    try:
        validate_structure(value, schema_name=schema, violation_code="invalid_" + schema.split(".")[0])
    except ContractViolation as exc:
        raise BundleError(exc.code) from exc
    # JSON Schema considers 1.0 an integer; contract versions are JSON integers.
    if type(value.get("schema_version")) is not int:
        raise BundleError("integer_schema_version_required")


def validate_identity(expected: Mapping[str, Any]) -> dict[str, str]:
    if not isinstance(expected, Mapping) or set(expected) != set(IDENTITY_FIELDS):
        raise BundleError("independent_expected_identity_required")
    result = dict(expected)
    for value in result.values():
        if not isinstance(value, str) or not TOKEN.fullmatch(value) or value == "unknown":
            raise BundleError("invalid_expected_identity")
    return result


def _session(raw, expected):
    session = strict_json(raw)
    _structure(session, "inspection-session-v1.schema.json")
    identity = validate_identity(expected)
    if any(session[field] != identity[field] for field in IDENTITY_FIELDS):
        raise BundleError("identity_mismatch")
    relative_path(session["capture"]["image_path"])
    if session["capture"]["image_path"] in ("media-envelope.json", "validation.json", "capture-source.json"):
        raise BundleError("image_path_collision")
    return session


def _validate_source(session, image, envelope_raw, validation_raw):
    envelope = strict_json(envelope_raw)
    version = envelope.get("schema_version")
    if type(version) is not int or version not in (1, 2):
        raise BundleError("unsupported_envelope_version")
    _structure(envelope, "media-envelope.schema.json" if version == 1 else "media-envelope-v2.schema.json")
    if version == 2:
        if any(type(envelope[field]) is not int for field in ("width", "height")):
            raise BundleError("integer_dimensions_required")
        try:
            validate_media_envelope_v2(envelope)
        except ContractViolation as exc:
            raise BundleError(exc.code) from exc
        # Import is not retention authorization or adoption.
        if envelope["retention_class"] == "accepted":
            raise BundleError("accepted_media_out_of_scope")
    capture = session["capture"]
    if any(envelope[field] != capture[field] for field in ("capture_id", "source_id")):
        raise BundleError("capture_binding_mismatch")
    reference = "staging://{source_id}/{capture_id}/{image_path}".format(**capture)
    if envelope["staging_reference"] != reference:
        raise BundleError("staging_reference_mismatch")
    if envelope["media_type"] != "image/jpeg":
        raise BundleError("jpeg_required")
    if not image.startswith(b"\xff\xd8\xff") or not image.endswith(b"\xff\xd9"):
        raise BundleError("invalid_jpeg_markers")
    if type(envelope["size_bytes"]) is not int or envelope["sha256"] != digest(image) or envelope["size_bytes"] != len(image):
        raise BundleError("image_binding_mismatch")
    validation = strict_json(validation_raw)
    fields = {"jpeg_magic", "decode", "width", "height", "size_bytes", "sha256", "capture_duration_ms", "transfer_duration_ms", "device_metadata"}
    if set(validation) != fields or validation["jpeg_magic"] != "valid" or validation["decode"] != "valid":
        raise BundleError("invalid_source_validation")
    for field in ("width", "height", "size_bytes", "capture_duration_ms", "transfer_duration_ms"):
        value = validation[field]
        lower = -1 if field.endswith("duration_ms") else 1
        upper = 65535 if field in ("width", "height") else MAX_IMAGE_BYTES if field == "size_bytes" else 2147483647
        if type(value) is not int or not lower <= value <= upper:
            raise BundleError("invalid_source_validation")
    metadata = validation["device_metadata"]
    if (not isinstance(metadata, dict) or len(metadata) > 32
            or any(not TOKEN.fullmatch(k) or not isinstance(v, str) or len(v) > 256 for k, v in metadata.items())):
        raise BundleError("invalid_device_metadata")
    if validation["sha256"] != digest(image) or validation["size_bytes"] != len(image):
        raise BundleError("validation_binding_mismatch")
    if version == 2 and any(envelope[k] != validation[k] for k in ("width", "height")):
        raise BundleError("validation_dimension_mismatch")
    return envelope


def _binding(path, raw):
    return {"path": path, "sha256": digest(raw), "size_bytes": len(raw)}


def _manifest(session, snapshots, envelope):
    paths = {"session": "session.json", "image": "capture/" + session["capture"]["image_path"],
             "envelope": "capture/media-envelope.json", "validation": "capture/validation.json"}
    return {"schema_version": 1, "bundle_kind": "offline_inspection_observation",
            "identity": {field: session[field] for field in IDENTITY_FIELDS}, "identity_basis": "operator_assertion",
            "capture_id": envelope["capture_id"], "source_id": envelope["source_id"], "envelope_version": envelope["schema_version"],
            "bindings": {key: _binding(paths[key], raw) for key, raw in snapshots.items()},
            "capture_time": {"captured_at": envelope["captured_at"],
                             "meaning": "host_artifact_write_after_jpeg_receive" if envelope["schema_version"] == 1 and envelope["source_id"] == "xiao-esp32s3-sense-01" else "envelope_declared_time_unverified",
                             "exposure_time": "unknown"}, **STATUS}


def _report(manifest, manifest_raw):
    # At most five {path, sha256} records, usable relative to a measurement
    # JSON located at the bundle root. No human measurement is synthesized.
    refs = [{"path": binding["path"], "sha256": binding["sha256"]} for _, binding in sorted(manifest["bindings"].items())]
    refs.append({"path": "capture-manifest.json", "sha256": digest(manifest_raw)})
    return {"schema_version": 1, "command": "offline-capture-replay", "execution_status": "complete",
            "evidence_class": "observation", "identity": manifest["identity"], "identity_basis": "operator_assertion",
            "manifest_sha256": digest(manifest_raw), "bindings_verified": True,
            "source_validation": "hash_bound_claims_only", "jpeg_decode": "not_performed",
            "capture_time": manifest["capture_time"], "evidence_refs": refs, **STATUS}


def replay_bundle(bundle_dir: Path, *, expected_identity: Mapping[str, Any],
                  expected_manifest_sha256: str | None = None) -> dict[str, Any]:
    root, _ = _checked_path(bundle_dir, directory=True)
    manifest_raw = read_regular(root / "capture-manifest.json", MAX_JSON_BYTES)
    if expected_manifest_sha256 is not None and expected_manifest_sha256 != digest(manifest_raw):
        raise BundleError("manifest_digest_mismatch")
    manifest = strict_json(manifest_raw)
    _structure(manifest, "capture-manifest-v1.schema.json")
    if type(manifest["envelope_version"]) is not int:
        raise BundleError("integer_envelope_version_required")
    snapshots = {}
    for key, binding in manifest["bindings"].items():
        if type(binding["size_bytes"]) is not int:
            raise BundleError("integer_binding_size_required")
        path = relative_path(binding["path"])
        raw = read_regular(root / path, MAX_IMAGE_BYTES if key == "image" else MAX_JSON_BYTES)
        if digest(raw) != binding["sha256"] or len(raw) != binding["size_bytes"]:
            raise BundleError("snapshot_binding_mismatch")
        snapshots[key] = raw
    session = _session(snapshots["session"], expected_identity)
    envelope = _validate_source(session, snapshots["image"], snapshots["envelope"], snapshots["validation"])
    derived = _manifest(session, snapshots, envelope)
    if manifest != derived:
        raise BundleError("manifest_semantics_mismatch")
    # Derive from authoritative snapshots, not user-editable report fields.
    report = _report(derived, manifest_raw)
    stored = read_regular(root / "replay-report.json", MAX_JSON_BYTES)
    if strict_json(stored) != report or stored != canonical(report):
        raise BundleError("replay_report_mismatch")
    refs_raw = read_regular(root / "evidence-refs.json", MAX_JSON_BYTES)
    if strict_json(refs_raw) != {"evidence_refs": report["evidence_refs"]} or refs_raw != canonical({"evidence_refs": report["evidence_refs"]}):
        raise BundleError("evidence_refs_mismatch")
    return report


def _publish(stage: Path, output: Path):
    """Atomic no-replace publication on supported Windows and Linux hosts."""
    if os.name == "nt":
        os.rename(stage, output)  # Windows rename never replaces a destination.
    elif sys.platform.startswith("linux"):
        libc = ctypes.CDLL(None, use_errno=True)
        rename = getattr(libc, "renameat2", None)
        if rename is None:
            raise BundleError("atomic_publication_unavailable")
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        if rename(-100, os.fsencode(stage), -100, os.fsencode(output), 1) != 0:
            code = ctypes.get_errno()
            if code in (errno.ENOSYS, errno.EINVAL):
                raise BundleError("atomic_publication_unavailable")
            raise OSError(code, "atomic publication failed")
    else:
        raise BundleError("atomic_publication_unavailable")


def create_bundle(session_path: Path, capture_dir: Path, output_dir: Path, *,
                  expected_identity: Mapping[str, Any]) -> dict[str, Any]:
    output = Path(os.path.abspath(output_dir))
    parent, _ = _checked_path(output.parent, directory=True)
    if os.path.lexists(output):
        raise BundleError("output_already_exists")
    if any(os.path.lexists(ancestor / ".git") for ancestor in (parent, *parent.parents)):
        raise BundleError("output_must_be_outside_git_checkout")
    source, _ = _checked_path(capture_dir, directory=True)
    # Caller must use a private/quiescent source and trusted output parent.
    # Never publish inside the capture tree or over any source file.
    session_file, _ = _checked_path(session_path)
    if output == source or output.is_relative_to(source) or session_file.is_relative_to(output):
        raise BundleError("output_overlaps_source")
    session_raw = read_regular(session_file, MAX_JSON_BYTES)
    session = _session(session_raw, expected_identity)
    snapshots = {"session": session_raw,
                 "image": read_regular(source / session["capture"]["image_path"], MAX_IMAGE_BYTES),
                 "envelope": read_regular(source / "media-envelope.json", MAX_JSON_BYTES),
                 "validation": read_regular(source / "validation.json", MAX_JSON_BYTES)}
    envelope = _validate_source(session, snapshots["image"], snapshots["envelope"], snapshots["validation"])
    manifest = _manifest(session, snapshots, envelope)
    _structure(manifest, "capture-manifest-v1.schema.json")
    manifest_raw = canonical(manifest)
    report = _report(manifest, manifest_raw)
    stage = None
    try:
        stage = Path(tempfile.mkdtemp(prefix=".inspection-", dir=parent))
        for key, raw in snapshots.items():
            path = stage / manifest["bindings"][key]["path"]
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with path.open("xb") as stream:
                stream.write(raw)
        (stage / "capture-manifest.json").write_bytes(manifest_raw)
        (stage / "replay-report.json").write_bytes(canonical(report))
        (stage / "evidence-refs.json").write_bytes(canonical({"evidence_refs": report["evidence_refs"]}))
        replay_bundle(stage, expected_identity=expected_identity, expected_manifest_sha256=digest(manifest_raw))
        _publish(stage, output)
    except OSError as exc:
        raise BundleError("output_already_exists" if os.path.lexists(output) else "publication_failed") from exc
    finally:
        # Only the uniquely owned stage is removed, never the requested output.
        if stage is not None and stage.exists():
            try:
                shutil.rmtree(stage)
            except OSError as exc:
                raise BundleError("stage_cleanup_failed") from exc
    return report
