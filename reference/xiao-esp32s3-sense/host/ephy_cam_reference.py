#!/usr/bin/env python3
"""Loopback preview and explicit one-shot capture for the reference device."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import queue
import threading
import time
from typing import Any
from uuid import uuid4

import jsonschema
from PIL import Image
import serial


PROTOCOL = "EPHYCAM/1"
SOURCE_ID = "xiao-esp32s3-sense-01"
PREVIEW_SIZE = (640, 480)
CAPTURE_SIZE = (2048, 1536)
LOOPBACK = "127.0.0.1"


class ProtocolError(RuntimeError):
    """Raised when the device violates the bounded USB framing protocol."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_fields(line: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for token in line.split():
        if "=" in token:
            key, value = token.split("=", 1)
            fields[key] = value
    return fields


def read_nonempty_line(transport: serial.Serial) -> str:
    while True:
        raw = transport.readline()
        if not raw:
            raise ProtocolError("timed out waiting for a protocol line")
        line = raw.decode("ascii", errors="replace").strip()
        if line:
            if line.startswith(f"{PROTOCOL} ERROR"):
                raise ProtocolError(line)
            return line


def wait_for_line(transport: serial.Serial, prefix: str) -> str:
    for _ in range(40):
        line = read_nonempty_line(transport)
        if line.startswith(prefix):
            return line
    raise ProtocolError(f"did not receive expected marker: {prefix}")


def read_exact(transport: serial.Serial, length: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < length:
        chunk = transport.read(length - len(chunks))
        if not chunk:
            raise ProtocolError(
                f"binary payload ended at {len(chunks)} of {length} bytes"
            )
        chunks.extend(chunk)
    return bytes(chunks)


def validate_jpeg(jpeg: bytes, expected_size: tuple[int, int]) -> tuple[int, int]:
    if not jpeg.startswith(b"\xff\xd8") or not jpeg.endswith(b"\xff\xd9"):
        raise ProtocolError("JPEG start/end magic is invalid")
    with Image.open(io.BytesIO(jpeg)) as image:
        image.verify()
    with Image.open(io.BytesIO(jpeg)) as image:
        image.load()
        actual = image.size
        if actual != expected_size:
            raise ProtocolError(
                f"JPEG dimensions are {actual}, expected {expected_size}"
            )
        if image.format != "JPEG":
            raise ProtocolError(f"decoded media format is {image.format}")
    return actual


def receive_frame(
    transport: serial.Serial,
    command: bytes,
    begin_marker: str,
    frame_marker: str,
    complete_marker: str,
    expected_size: tuple[int, int],
    payload_marker: str | None = None,
) -> tuple[bytes, dict[str, str], dict[str, str]]:
    transport.reset_input_buffer()
    transport.write(command + b"\n")
    transport.flush()
    wait_for_line(transport, begin_marker)
    metadata_line = wait_for_line(transport, frame_marker)
    metadata = parse_fields(metadata_line)
    payload = metadata
    if payload_marker is not None:
        payload_line = wait_for_line(transport, payload_marker)
        payload = parse_fields(payload_line)
        if payload.get("bytes") != metadata.get("bytes"):
            raise ProtocolError("payload byte count differs from frame metadata")
    try:
        length = int(payload["bytes"])
    except (KeyError, ValueError) as exc:
        raise ProtocolError(f"invalid frame length: {metadata_line}") from exc
    if length <= 0 or length > 10_000_000:
        raise ProtocolError(f"refusing invalid frame length: {length}")
    jpeg = read_exact(transport, length)
    complete = parse_fields(wait_for_line(transport, complete_marker))
    if complete.get("bytes") != str(length):
        raise ProtocolError("completion byte count differs from frame header")
    validate_jpeg(jpeg, expected_size)
    return jpeg, metadata, complete


def configure_device(transport: serial.Serial) -> dict[str, str]:
    transport.reset_input_buffer()
    transport.write(b"STATUS\n")
    transport.flush()
    line = wait_for_line(transport, f"{PROTOCOL} STATUS")
    status = parse_fields(line)
    expected = {
        "ready": "1",
        "camera": "OV3660",
        "psram_found": "1",
        "preview_frame": "VGA",
        "capture_frame": "QXGA",
    }
    mismatches = {
        key: (status.get(key), value)
        for key, value in expected.items()
        if status.get(key) != value
    }
    if mismatches:
        raise ProtocolError(f"device status mismatch: {mismatches}; line={line}")
    return status


def schema_root_default() -> Path:
    return Path(__file__).resolve().parents[3]


def require_external_staging(staging_root: Path, schema_root: Path) -> Path:
    resolved_staging = staging_root.resolve()
    resolved_repository = schema_root.resolve()
    try:
        resolved_staging.relative_to(resolved_repository)
    except ValueError:
        return resolved_staging
    raise ProtocolError("staging root must be outside the Git repository")


def write_capture_artifacts(
    staging_root: Path,
    schema_root: Path,
    jpeg: bytes,
    frame: dict[str, str],
    complete: dict[str, str],
) -> dict[str, Any]:
    capture_id = str(uuid4())
    captured_at = utc_now()
    digest = hashlib.sha256(jpeg).hexdigest()
    capture_dir = require_external_staging(staging_root, schema_root) / capture_id
    capture_dir.mkdir(parents=True, exist_ok=False)
    image_path = capture_dir / "capture.jpg"
    image_path.write_bytes(jpeg)

    source = {
        "schema_version": 1,
        "source_id": SOURCE_ID,
        "media_kinds": ["image"],
        "capabilities": ["explicit-one-shot", "usb-cdc", "jpeg"],
    }
    envelope = {
        "schema_version": 1,
        "capture_id": capture_id,
        "source_id": SOURCE_ID,
        "media_type": "image/jpeg",
        "captured_at": captured_at,
        "sha256": digest,
        "size_bytes": len(jpeg),
        "staging_reference": (
            f"staging://{SOURCE_ID}/{capture_id}/capture.jpg"
        ),
    }
    source_schema = json.loads(
        (schema_root / "schemas" / "capture-source.schema.json").read_text(
            encoding="utf-8"
        )
    )
    envelope_schema = json.loads(
        (schema_root / "schemas" / "media-envelope.schema.json").read_text(
            encoding="utf-8"
        )
    )
    jsonschema.validate(source, source_schema)
    jsonschema.validate(envelope, envelope_schema)

    validation = {
        "jpeg_magic": "valid",
        "decode": "valid",
        "width": CAPTURE_SIZE[0],
        "height": CAPTURE_SIZE[1],
        "size_bytes": len(jpeg),
        "sha256": digest,
        "capture_duration_ms": int(frame.get("capture_ms", "-1")),
        "transfer_duration_ms": int(complete.get("transfer_ms", "-1")),
        "device_metadata": frame,
    }
    documents = {
        "capture-source.json": source,
        "media-envelope.json": envelope,
        "validation.json": validation,
    }
    for name, document in documents.items():
        (capture_dir / name).write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return {
        "capture_path": str(image_path),
        "capture_dir": str(capture_dir),
        "media_envelope": envelope,
        "validation": validation,
    }


@dataclass
class CaptureTicket:
    completed: threading.Event = field(default_factory=threading.Event)
    result: dict[str, Any] | None = None
    error: str | None = None


@dataclass
class PreviewState:
    deadline: float
    latest_jpeg: bytes | None = None
    preview_frames: int = 0
    captures: int = 0
    last_error: str | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)
    frame_ready: threading.Condition = field(init=False)
    requests: queue.Queue[CaptureTicket] = field(default_factory=queue.Queue)
    stop: threading.Event = field(default_factory=threading.Event)

    def __post_init__(self) -> None:
        self.frame_ready = threading.Condition(self.lock)

    def status(self) -> dict[str, Any]:
        with self.lock:
            return {
                "preview_resolution": "640x480",
                "capture_resolution": "2048x1536",
                "preview_frames": self.preview_frames,
                "captures": self.captures,
                "remaining_seconds": max(0, round(self.deadline - time.monotonic())),
                "last_error": self.last_error,
            }


def serial_worker(
    state: PreviewState,
    serial_device: str,
    staging_root: Path,
    schema_root: Path,
    fps: float,
    timeout_seconds: float,
) -> None:
    try:
        with serial.Serial(serial_device, 115200, timeout=timeout_seconds) as transport:
            configure_device(transport)
            while not state.stop.is_set() and time.monotonic() < state.deadline:
                try:
                    ticket = state.requests.get_nowait()
                except queue.Empty:
                    ticket = None
                if ticket is not None:
                    try:
                        jpeg, frame, complete = receive_frame(
                            transport,
                            b"CAPTURE",
                            f"{PROTOCOL} CAPTURE_BEGIN",
                            f"{PROTOCOL} CAPTURED",
                            f"{PROTOCOL} COMPLETE",
                            CAPTURE_SIZE,
                            payload_marker=f"{PROTOCOL} FRAME",
                        )
                        ticket.result = write_capture_artifacts(
                            staging_root, schema_root, jpeg, frame, complete
                        )
                        with state.lock:
                            state.captures += 1
                    except Exception as exc:  # surfaced to the requesting client
                        ticket.error = str(exc)
                    finally:
                        ticket.completed.set()
                    continue

                started = time.monotonic()
                jpeg, _, _ = receive_frame(
                    transport,
                    b"PREVIEW",
                    f"{PROTOCOL} PREVIEW_BEGIN",
                    f"{PROTOCOL} PREVIEW_FRAME",
                    f"{PROTOCOL} PREVIEW_COMPLETE",
                    PREVIEW_SIZE,
                )
                with state.frame_ready:
                    state.latest_jpeg = jpeg
                    state.preview_frames += 1
                    state.frame_ready.notify_all()
                delay = (1.0 / fps) - (time.monotonic() - started)
                if delay > 0:
                    state.stop.wait(delay)
    except Exception as exc:
        with state.frame_ready:
            state.last_error = str(exc)
            state.frame_ready.notify_all()
    finally:
        state.stop.set()


PAGE = b"""<!doctype html>
<html lang="en"><meta charset="utf-8"><title>ephy-cam reference preview</title>
<style>body{font-family:sans-serif;max-width:900px;margin:2rem auto;background:#16181d;color:#eee}
img{width:100%;height:auto;background:#000}button{font-size:1rem;padding:.6rem 1rem}</style>
<h1>XIAO ESP32S3 Sense</h1><p>VGA memory-only preview. QXGA is captured only on request.</p>
<img src="/stream.mjpg"><p><button id="capture">Capture QXGA</button></p><pre id="result"></pre>
<script>document.querySelector('#capture').onclick=async()=>{let r=await fetch('/capture',{method:'POST'});
document.querySelector('#result').textContent=JSON.stringify(await r.json(),null,2)};</script></html>"""


class PreviewHandler(BaseHTTPRequestHandler):
    server: "PreviewServer"

    def log_message(self, format: str, *args: object) -> None:
        return

    def send_json(self, status: HTTPStatus, document: dict[str, Any]) -> None:
        body = json.dumps(document, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/":
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(PAGE)))
            self.end_headers()
            self.wfile.write(PAGE)
        elif self.path == "/status":
            self.send_json(HTTPStatus.OK, self.server.state.status())
        elif self.path == "/stream.mjpg":
            self.send_response(HTTPStatus.OK)
            self.send_header(
                "Content-Type", "multipart/x-mixed-replace; boundary=frame"
            )
            self.end_headers()
            last_frame = 0
            try:
                while not self.server.state.stop.is_set():
                    with self.server.state.frame_ready:
                        self.server.state.frame_ready.wait_for(
                            lambda: self.server.state.preview_frames > last_frame
                            or self.server.state.stop.is_set(),
                            timeout=2,
                        )
                        jpeg = self.server.state.latest_jpeg
                        last_frame = self.server.state.preview_frames
                    if jpeg is None:
                        continue
                    self.wfile.write(
                        b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                        + str(len(jpeg)).encode("ascii")
                        + b"\r\n\r\n"
                        + jpeg
                        + b"\r\n"
                    )
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if self.path != "/capture":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        ticket = CaptureTicket()
        self.server.state.requests.put(ticket)
        if not ticket.completed.wait(self.server.capture_timeout):
            self.send_json(HTTPStatus.GATEWAY_TIMEOUT, {"error": "capture timed out"})
        elif ticket.error is not None:
            self.send_json(HTTPStatus.BAD_GATEWAY, {"error": ticket.error})
        else:
            self.send_json(HTTPStatus.OK, ticket.result or {})


class PreviewServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        state: PreviewState,
        capture_timeout: float,
    ) -> None:
        super().__init__(address, PreviewHandler)
        self.state = state
        self.capture_timeout = capture_timeout


def capture_command(args: argparse.Namespace) -> int:
    with serial.Serial(
        args.serial_device, 115200, timeout=args.serial_timeout_seconds
    ) as transport:
        configure_device(transport)
        jpeg, frame, complete = receive_frame(
            transport,
            b"CAPTURE",
            f"{PROTOCOL} CAPTURE_BEGIN",
            f"{PROTOCOL} CAPTURED",
            f"{PROTOCOL} COMPLETE",
            CAPTURE_SIZE,
            payload_marker=f"{PROTOCOL} FRAME",
        )
    result = write_capture_artifacts(
        args.staging_root, args.schema_root, jpeg, frame, complete
    )
    print(json.dumps(result, indent=2))
    return 0


def serve_command(args: argparse.Namespace) -> int:
    deadline = time.monotonic() + args.duration_seconds
    state = PreviewState(deadline=deadline)
    worker = threading.Thread(
        target=serial_worker,
        args=(
            state,
            args.serial_device,
            args.staging_root,
            args.schema_root,
            args.fps,
            args.serial_timeout_seconds,
        ),
        daemon=True,
    )
    worker.start()
    server = PreviewServer(
        (LOOPBACK, args.port), state, args.serial_timeout_seconds + 40
    )
    server.timeout = 0.5
    print(f"Preview: http://{LOOPBACK}:{args.port}/")
    try:
        while time.monotonic() < deadline and not state.stop.is_set():
            server.handle_request()
    except KeyboardInterrupt:
        pass
    finally:
        state.stop.set()
        server.server_close()
        worker.join(timeout=args.serial_timeout_seconds + 2)
    if state.last_error is not None:
        raise ProtocolError(state.last_error)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial-timeout-seconds", type=float, default=8.0)
    parser.add_argument(
        "--schema-root", type=Path, default=schema_root_default()
    )
    subparsers = parser.add_subparsers(required=True)
    capture = subparsers.add_parser("capture", help="capture one QXGA JPEG")
    capture.add_argument("serial_device")
    capture.add_argument("staging_root", type=Path)
    capture.set_defaults(handler=capture_command)
    serve = subparsers.add_parser("serve", help="run a bounded loopback preview")
    serve.add_argument("serial_device")
    serve.add_argument("staging_root", type=Path)
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--fps", type=float, default=5.0)
    serve.add_argument("--duration-seconds", type=int, default=1800)
    serve.set_defaults(handler=serve_command)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if getattr(args, "fps", 1.0) <= 0:
        raise SystemExit("--fps must be positive")
    if getattr(args, "duration_seconds", 1) <= 0:
        raise SystemExit("--duration-seconds must be positive")
    args.staging_root = require_external_staging(args.staging_root, args.schema_root)
    args.staging_root.mkdir(parents=True, exist_ok=True)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
