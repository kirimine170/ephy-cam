"""Cross-field validation that JSON Schema cannot express by itself."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping


class ContractViolation(ValueError):
    """A structurally valid contract has invalid cross-field semantics."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def parse_timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise ContractViolation(f"invalid_{field}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractViolation(f"invalid_{field}") from exc
    if parsed.tzinfo is None:
        raise ContractViolation(f"invalid_{field}")
    return parsed.astimezone(timezone.utc)


def require_aware_utc(value: datetime, field: str = "now") -> datetime:
    if value.tzinfo is None:
        raise ContractViolation(f"invalid_{field}")
    return value.astimezone(timezone.utc)


def validate_capture_request(request: Mapping[str, Any]) -> None:
    requested_at = parse_timestamp(request.get("requested_at"), "requested_at")
    deadline = parse_timestamp(request.get("deadline"), "deadline")
    if deadline <= requested_at:
        raise ContractViolation("deadline_not_after_requested_at")
    if request.get("retention_intent") == "accepted":
        raise ContractViolation("capture_cannot_accept_media")


def validate_capture_permit(permit: Mapping[str, Any]) -> None:
    issued_at = parse_timestamp(permit.get("issued_at"), "issued_at")
    expires_at = parse_timestamp(permit.get("expires_at"), "expires_at")
    if expires_at <= issued_at:
        raise ContractViolation("permit_expiry_not_after_issue")
    if permit.get("indicator_required") is not True:
        raise ContractViolation("indicator_not_required")


def validate_media_envelope_v2(envelope: Mapping[str, Any]) -> None:
    captured_at = parse_timestamp(envelope.get("captured_at"), "captured_at")
    expires_at = parse_timestamp(envelope.get("expires_at"), "expires_at")
    if expires_at <= captured_at:
        raise ContractViolation("media_expiry_not_after_capture")
    forbidden_fields = {
        "coordinates",
        "latitude",
        "longitude",
        "ssid",
        "bssid",
        "caption",
        "identity",
        "emotion",
        "personality",
        "intent",
        "media_bytes",
    }
    if forbidden_fields.intersection(envelope):
        raise ContractViolation("forbidden_media_metadata")


def validate_photo_acceptance(acceptance: Mapping[str, Any]) -> None:
    parse_timestamp(acceptance.get("decided_at"), "decided_at")
    if acceptance.get("decided_by") == "policy" and not acceptance.get(
        "policy_revision"
    ):
        raise ContractViolation("policy_revision_required")
