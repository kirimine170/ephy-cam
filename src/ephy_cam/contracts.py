"""Cross-field validation that JSON Schema cannot express by itself."""

from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
import json
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError


SCHEMA_ROOT = Path(__file__).resolve().parents[2] / "schemas"


class ContractViolation(ValueError):
    """A structurally valid contract has invalid cross-field semantics."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@lru_cache(maxsize=None)
def contract_validator(schema_name: str) -> Draft202012Validator:
    schema = json.loads(
        (SCHEMA_ROOT / schema_name).read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def validate_structure(
    document: Mapping[str, Any],
    *,
    schema_name: str,
    violation_code: str,
) -> None:
    try:
        contract_validator(schema_name).validate(document)
    except ValidationError as exc:
        raise ContractViolation(violation_code) from exc


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
    validate_structure(
        request,
        schema_name="capture-request-v1.schema.json",
        violation_code="invalid_capture_request_schema",
    )
    requested_at = parse_timestamp(request.get("requested_at"), "requested_at")
    deadline = parse_timestamp(request.get("deadline"), "deadline")
    if deadline <= requested_at:
        raise ContractViolation("deadline_not_after_requested_at")
    if request.get("retention_intent") == "accepted":
        raise ContractViolation("capture_cannot_accept_media")


def validate_capture_permit(permit: Mapping[str, Any]) -> None:
    validate_structure(
        permit,
        schema_name="capture-permit-v1.schema.json",
        violation_code="invalid_capture_permit_schema",
    )
    issued_at = parse_timestamp(permit.get("issued_at"), "issued_at")
    expires_at = parse_timestamp(permit.get("expires_at"), "expires_at")
    if expires_at <= issued_at:
        raise ContractViolation("permit_expiry_not_after_issue")
    if permit.get("indicator_required") is not True:
        raise ContractViolation("indicator_not_required")


def validate_media_envelope_v2(envelope: Mapping[str, Any]) -> None:
    validate_structure(
        envelope,
        schema_name="media-envelope-v2.schema.json",
        violation_code="invalid_media_envelope_v2_schema",
    )
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
    validate_structure(
        acceptance,
        schema_name="photo-acceptance-v1.schema.json",
        violation_code="invalid_photo_acceptance_schema",
    )
    parse_timestamp(acceptance.get("decided_at"), "decided_at")
    if acceptance.get("decided_by") == "policy" and not acceptance.get(
        "policy_revision"
    ):
        raise ContractViolation("policy_revision_required")
