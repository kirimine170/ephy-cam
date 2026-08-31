"""Fail-closed authorization gate for a resolved CapturePermit."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any, Mapping

from .contracts import (
    ContractViolation,
    parse_timestamp,
    require_aware_utc,
    validate_capture_permit,
    validate_capture_request,
)


class CaptureDenied(RuntimeError):
    """A capture request did not satisfy every authorization condition."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class CaptureAuthorizationLedger:
    """In-memory replay, limit, and cooldown state for one gateway process.

    Authentication and token decoding are deliberately outside this class. The
    caller passes the resolved permit after its provider has authenticated the
    opaque ``permit_token`` from the request.
    """

    def __init__(self) -> None:
        self._consumed_request_ids: set[str] = set()
        self._captures_by_permit: dict[str, list[datetime]] = defaultdict(list)

    def authorize_and_record(
        self,
        request: Mapping[str, Any],
        permit: Mapping[str, Any],
        *,
        now: datetime,
        indicator_ready: bool,
        revoked_permit_ids: frozenset[str] = frozenset(),
    ) -> None:
        try:
            validate_capture_request(request)
            validate_capture_permit(permit)
            current_time = require_aware_utc(now)
        except ContractViolation as exc:
            raise CaptureDenied(exc.code) from exc

        request_id = str(request.get("request_id", ""))
        permit_id = str(permit.get("permit_id", ""))
        if request_id in self._consumed_request_ids:
            raise CaptureDenied("request_replay")
        if permit_id in revoked_permit_ids:
            raise CaptureDenied("permit_revoked")
        if request.get("source_id") != permit.get("source_id"):
            raise CaptureDenied("source_mismatch")
        if request.get("purpose") not in permit.get("allowed_purposes", []):
            raise CaptureDenied("purpose_mismatch")

        requested_at = parse_timestamp(request.get("requested_at"), "requested_at")
        deadline = parse_timestamp(request.get("deadline"), "deadline")
        issued_at = parse_timestamp(permit.get("issued_at"), "issued_at")
        permit_expiry = parse_timestamp(permit.get("expires_at"), "expires_at")
        if current_time < requested_at:
            raise CaptureDenied("request_not_yet_valid")
        if current_time >= deadline:
            raise CaptureDenied("request_deadline_expired")
        if current_time < issued_at:
            raise CaptureDenied("permit_not_yet_valid")
        if current_time >= permit_expiry:
            raise CaptureDenied("permit_expired")
        if permit.get("indicator_required") is not True or not indicator_ready:
            raise CaptureDenied("indicator_not_ready")

        captures = self._captures_by_permit[permit_id]
        max_captures = int(permit.get("max_captures", 0))
        if len(captures) >= max_captures:
            raise CaptureDenied("capture_limit_reached")
        minimum_cooldown = int(permit.get("minimum_cooldown_seconds", 0))
        cooldown_elapsed = (
            not captures
            or (current_time - captures[-1]).total_seconds() >= minimum_cooldown
        )
        if not cooldown_elapsed:
            raise CaptureDenied("cooldown_active")

        self._consumed_request_ids.add(request_id)
        captures.append(current_time)

    def capture_count(self, permit_id: str) -> int:
        return len(self._captures_by_permit.get(permit_id, ()))
