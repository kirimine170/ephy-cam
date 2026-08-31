from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import unittest

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ephy_cam.capture_gate import CaptureAuthorizationLedger, CaptureDenied
from ephy_cam.contracts import (
    ContractViolation,
    validate_capture_request,
    validate_media_envelope_v2,
)
from ephy_cam.lifecycle import LifecycleState, MediaLifecycle, TransitionDenied


FIXTURES = ROOT / "tests" / "fixtures" / "synthetic"
SCHEMAS = ROOT / "schemas"


def load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_fixture(schema_name: str, fixture_name: str) -> None:
    schema = load_json(SCHEMAS / schema_name)
    fixture = load_json(FIXTURES / fixture_name)
    Draft202012Validator(
        schema, format_checker=FormatChecker()
    ).validate(fixture)


class CameraContractSchemaTests(unittest.TestCase):
    def test_all_synthetic_contracts_validate(self) -> None:
        pairs = (
            ("capture-request-v1.schema.json", "capture-request-v1.json"),
            ("capture-permit-v1.schema.json", "capture-permit-v1.json"),
            ("media-envelope-v2.schema.json", "media-envelope-v2.json"),
            ("photo-acceptance-v1.schema.json", "photo-acceptance-v1.json"),
        )
        for schema_name, fixture_name in pairs:
            with self.subTest(schema=schema_name):
                Draft202012Validator.check_schema(
                    load_json(SCHEMAS / schema_name)
                )
                validate_fixture(schema_name, fixture_name)

    def test_media_envelope_v1_fixture_remains_valid(self) -> None:
        validate_fixture("media-envelope.schema.json", "media-envelope.json")

    def test_v1_envelope_is_not_silently_accepted_as_v2(self) -> None:
        schema = load_json(SCHEMAS / "media-envelope-v2.schema.json")
        fixture = load_json(FIXTURES / "media-envelope.json")
        with self.assertRaises(ValidationError):
            Draft202012Validator(schema).validate(fixture)

    def test_v2_envelope_rejects_location_caption_and_binary_fields(self) -> None:
        schema = load_json(SCHEMAS / "media-envelope-v2.schema.json")
        fixture = load_json(FIXTURES / "media-envelope-v2.json")
        for field, value in (
            ("coordinates", [0, 0]),
            ("ssid", "synthetic-network"),
            ("caption", "synthetic caption"),
            ("emotion", "synthetic"),
            ("media_bytes", "not-real-media"),
        ):
            invalid = deepcopy(fixture)
            invalid[field] = value
            with self.subTest(field=field), self.assertRaises(ValidationError):
                Draft202012Validator(schema).validate(invalid)

    def test_capture_request_cannot_request_accepted_retention(self) -> None:
        schema = load_json(SCHEMAS / "capture-request-v1.schema.json")
        request = load_json(FIXTURES / "capture-request-v1.json")
        request["retention_intent"] = "accepted"
        with self.assertRaises(ValidationError):
            Draft202012Validator(schema).validate(request)
        with self.assertRaisesRegex(
            ContractViolation, "invalid_capture_request_schema"
        ):
            validate_capture_request(request)

    def test_automatic_permit_requires_indicator(self) -> None:
        schema = load_json(SCHEMAS / "capture-permit-v1.schema.json")
        permit = load_json(FIXTURES / "capture-permit-v1.json")
        permit["indicator_required"] = False
        with self.assertRaises(ValidationError):
            Draft202012Validator(schema).validate(permit)

    def test_policy_acceptance_requires_revision(self) -> None:
        schema = load_json(SCHEMAS / "photo-acceptance-v1.schema.json")
        acceptance = load_json(FIXTURES / "photo-acceptance-v1.json")
        acceptance["decided_by"] = "policy"
        with self.assertRaises(ValidationError):
            Draft202012Validator(schema).validate(acceptance)

    def test_media_expiry_must_follow_capture(self) -> None:
        envelope = load_json(FIXTURES / "media-envelope-v2.json")
        envelope["expires_at"] = envelope["captured_at"]
        with self.assertRaisesRegex(ContractViolation, "media_expiry"):
            validate_media_envelope_v2(envelope)


class CaptureAuthorizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.request = load_json(FIXTURES / "capture-request-v1.json")
        self.permit = load_json(FIXTURES / "capture-permit-v1.json")
        self.now = datetime(2026, 1, 1, 0, 0, 20, tzinfo=timezone.utc)

    def assert_denied(
        self,
        code: str,
        *,
        request: dict[str, object] | None = None,
        permit: dict[str, object] | None = None,
        now: datetime | None = None,
        indicator_ready: bool = True,
        revoked: frozenset[str] = frozenset(),
        ledger: CaptureAuthorizationLedger | None = None,
    ) -> None:
        ledger = ledger or CaptureAuthorizationLedger()
        with self.assertRaises(CaptureDenied) as raised:
            ledger.authorize_and_record(
                request or self.request,
                permit or self.permit,
                now=now or self.now,
                indicator_ready=indicator_ready,
                revoked_permit_ids=revoked,
            )
        self.assertEqual(raised.exception.code, code)

    def test_valid_permit_authorizes_exactly_one_recorded_capture(self) -> None:
        ledger = CaptureAuthorizationLedger()
        ledger.authorize_and_record(
            self.request,
            self.permit,
            now=self.now,
            indicator_ready=True,
        )
        self.assertEqual(ledger.capture_count(str(self.permit["permit_id"])), 1)

    def test_missing_request_identity_is_rejected(self) -> None:
        request = deepcopy(self.request)
        del request["request_id"]
        self.assert_denied("invalid_capture_request_schema", request=request)

    def test_missing_permit_identity_is_rejected(self) -> None:
        permit = deepcopy(self.permit)
        del permit["permit_id"]
        self.assert_denied("invalid_capture_permit_schema", permit=permit)

    def test_non_array_allowed_purposes_is_rejected(self) -> None:
        permit = deepcopy(self.permit)
        permit["allowed_purposes"] = "ambient-observation"
        self.assert_denied("invalid_capture_permit_schema", permit=permit)

    def test_deadline_is_fail_closed(self) -> None:
        self.assert_denied(
            "request_deadline_expired",
            now=datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc),
        )

    def test_expired_permit_is_rejected(self) -> None:
        request = deepcopy(self.request)
        request["deadline"] = "2026-01-01T00:10:00Z"
        self.assert_denied(
            "permit_expired",
            request=request,
            now=datetime(2026, 1, 1, 0, 6, tzinfo=timezone.utc),
        )

    def test_purpose_mismatch_is_rejected(self) -> None:
        request = deepcopy(self.request)
        request["purpose"] = "karte-record"
        self.assert_denied("purpose_mismatch", request=request)

    def test_capture_limit_is_rejected(self) -> None:
        ledger = CaptureAuthorizationLedger()
        permit = deepcopy(self.permit)
        permit["max_captures"] = 1
        ledger.authorize_and_record(
            self.request, permit, now=self.now, indicator_ready=True
        )
        second = deepcopy(self.request)
        second["request_id"] = "00000000-0000-4000-8000-000000000103"
        second["deadline"] = "2026-01-01T00:04:00Z"
        self.assert_denied(
            "capture_limit_reached",
            request=second,
            permit=permit,
            now=self.now + timedelta(seconds=31),
            ledger=ledger,
        )

    def test_indicator_failure_is_rejected(self) -> None:
        self.assert_denied("indicator_not_ready", indicator_ready=False)

    def test_request_replay_is_rejected(self) -> None:
        ledger = CaptureAuthorizationLedger()
        ledger.authorize_and_record(
            self.request,
            self.permit,
            now=self.now,
            indicator_ready=True,
        )
        self.assert_denied(
            "request_replay",
            now=self.now + timedelta(seconds=31),
            ledger=ledger,
        )

    def test_cooldown_is_rejected(self) -> None:
        ledger = CaptureAuthorizationLedger()
        ledger.authorize_and_record(
            self.request,
            self.permit,
            now=self.now,
            indicator_ready=True,
        )
        second = deepcopy(self.request)
        second["request_id"] = "00000000-0000-4000-8000-000000000104"
        self.assert_denied(
            "cooldown_active",
            request=second,
            now=self.now + timedelta(seconds=5),
            ledger=ledger,
        )

    def test_revocation_is_rejected(self) -> None:
        permit_id = str(self.permit["permit_id"])
        self.assert_denied("permit_revoked", revoked=frozenset({permit_id}))


class MediaLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.captured_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.capture_id = "00000000-0000-4000-8000-000000000301"
        self.trace_id = "00000000-0000-4000-8000-000000000302"

    def lifecycle(self) -> MediaLifecycle:
        return MediaLifecycle(
            self.capture_id,
            self.trace_id,
            captured_at=self.captured_at,
        )

    def test_lifecycle_state_vocabulary_is_stable(self) -> None:
        self.assertEqual(
            {state.value for state in LifecycleState},
            {
                "ephemeral",
                "candidate",
                "accepted",
                "diary-eligible",
                "committed",
                "purged",
            },
        )

    def test_capture_starts_ephemeral_and_not_accepted(self) -> None:
        lifecycle = self.lifecycle()
        self.assertEqual(lifecycle.state, LifecycleState.EPHEMERAL)
        acceptance = load_json(FIXTURES / "photo-acceptance-v1.json")
        with self.assertRaisesRegex(TransitionDenied, "acceptance_requires_candidate"):
            lifecycle.apply_acceptance(acceptance)

    def test_full_valid_transition_path(self) -> None:
        lifecycle = self.lifecycle()
        lifecycle.mark_candidate(occurred_at=self.captured_at + timedelta(seconds=1))
        lifecycle.apply_acceptance(
            load_json(FIXTURES / "photo-acceptance-v1.json")
        )
        lifecycle.transition(
            LifecycleState.DIARY_ELIGIBLE,
            occurred_at=self.captured_at + timedelta(minutes=3),
            reason="selected-for-diary",
        )
        lifecycle.transition(
            LifecycleState.COMMITTED,
            occurred_at=self.captured_at + timedelta(minutes=4),
            reason="karte-transaction-committed",
        )
        lifecycle.transition(
            LifecycleState.PURGED,
            occurred_at=self.captured_at + timedelta(minutes=5),
            reason="retention-expired",
        )
        self.assertEqual(lifecycle.state, LifecycleState.PURGED)
        self.assertEqual(len(lifecycle.history), 6)

    def test_rejected_candidate_is_purged(self) -> None:
        lifecycle = self.lifecycle()
        lifecycle.mark_candidate(occurred_at=self.captured_at + timedelta(seconds=1))
        acceptance = load_json(FIXTURES / "photo-acceptance-v1.json")
        acceptance["decision"] = "rejected"
        lifecycle.apply_acceptance(acceptance)
        self.assertEqual(lifecycle.state, LifecycleState.PURGED)

    def test_acceptance_must_match_trace(self) -> None:
        lifecycle = self.lifecycle()
        lifecycle.mark_candidate(
            occurred_at=self.captured_at + timedelta(seconds=1)
        )
        acceptance = load_json(FIXTURES / "photo-acceptance-v1.json")
        acceptance["trace_id"] = "00000000-0000-4000-8000-000000000399"
        with self.assertRaisesRegex(TransitionDenied, "acceptance_trace_mismatch"):
            lifecycle.apply_acceptance(acceptance)

    def test_acceptance_requires_a_valid_actor(self) -> None:
        for actor in (None, "untrusted-actor"):
            with self.subTest(actor=actor):
                lifecycle = self.lifecycle()
                lifecycle.mark_candidate(
                    occurred_at=self.captured_at + timedelta(seconds=1)
                )
                acceptance = load_json(FIXTURES / "photo-acceptance-v1.json")
                if actor is None:
                    del acceptance["decided_by"]
                else:
                    acceptance["decided_by"] = actor
                with self.assertRaisesRegex(
                    ContractViolation, "invalid_photo_acceptance_schema"
                ):
                    lifecycle.apply_acceptance(acceptance)
                self.assertEqual(lifecycle.state, LifecycleState.CANDIDATE)

    def test_purged_is_terminal(self) -> None:
        lifecycle = self.lifecycle()
        lifecycle.transition(
            LifecycleState.PURGED,
            occurred_at=self.captured_at + timedelta(seconds=1),
            reason="ephemeral-expired",
        )
        with self.assertRaisesRegex(TransitionDenied, "invalid_transition"):
            lifecycle.mark_candidate(
                occurred_at=self.captured_at + timedelta(seconds=2)
            )


if __name__ == "__main__":
    unittest.main()
