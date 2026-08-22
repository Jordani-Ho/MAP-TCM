from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from map_tcm.api import MapApplication
from map_tcm.errors import DomainError


class PatientCareEventWriteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.app = MapApplication(Path(self.temp_dir.name) / "patient-care-write.db")
        overview = self.app.store.overview()
        encounter_id = overview["encounters"][0]["id"]
        doctor_id = self.app.synthetic_context["doctor_subject_id"]
        self.patient_id = self.app.synthetic_context["patient_subject_id"]
        self.patient_session_id = self.app.synthetic_context["patient_session_id"]
        self.app.service.add_assessment(
            encounter_id=encounter_id,
            pattern="synthetic setup pattern",
            mechanism="synthetic setup mechanism",
            rationale="synthetic setup rationale",
            treatment_principle="synthetic setup principle",
            prescription_summary="no prescription",
            actor_id=doctor_id,
            actor_role="verified_doctor",
        )
        self.app.service.transition_encounter(
            encounter_id=encounter_id,
            target_status="doctor_reviewed",
            actor_id=doctor_id,
            actor_role="verified_doctor",
        )
        self.app.service.transition_encounter(
            encounter_id=encounter_id,
            target_status="signed",
            actor_id=doctor_id,
            actor_role="verified_doctor",
        )
        self.plan = self.app.care_loop.create_intervention_plan(
            encounter_id=encounter_id,
            action_summary="synthetic self-write plan",
            instructions="record synthetic execution and feedback",
            follow_up_due_at=None,
            actor_id=doctor_id,
            actor_role="verified_doctor",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def request(
        self,
        path: str,
        payload: dict,
        *,
        session_id: str,
        idempotency_key: str,
    ) -> tuple[int, dict]:
        raw = json.dumps(payload).encode("utf-8")
        environ = {
            "REQUEST_METHOD": "POST",
            "PATH_INFO": path,
            "CONTENT_LENGTH": str(len(raw)),
            "CONTENT_TYPE": "application/json",
            "HTTP_X_MAP_SESSION_ID": session_id,
            "HTTP_IDEMPOTENCY_KEY": idempotency_key,
            "wsgi.input": io.BytesIO(raw),
        }
        statuses: list[str] = []

        def start_response(status: str, _headers: list[tuple[str, str]]) -> None:
            statuses.append(status)

        body = b"".join(self.app(environ, start_response))
        return int(statuses[0].split()[0]), json.loads(body.decode("utf-8"))

    def test_patient_execution_write_is_authorized_atomic_and_idempotent(self) -> None:
        path = f"/api/v1/care-loop/plans/{self.plan['id']}/execution"
        payload = {"event_type": "completed", "detail": {"synthetic": True}}
        status, first = self.request(
            path,
            payload,
            session_id=self.patient_session_id,
            idempotency_key="exec:SYN:0001",
        )
        replay_status, replay = self.request(
            path,
            payload,
            session_id=self.patient_session_id,
            idempotency_key="exec:SYN:0001",
        )
        self.assertEqual(201, status)
        self.assertEqual(200, replay_status)
        self.assertEqual(first["id"], replay["id"])
        self.assertFalse(first["idempotency_replayed"])
        self.assertTrue(replay["idempotency_replayed"])
        with self.app.store.connect() as connection:
            event_count = connection.execute(
                "SELECT COUNT(*) FROM intervention_execution_events WHERE plan_version_id = ?",
                (self.plan["id"],),
            ).fetchone()[0]
            linked = connection.execute(
                """
                SELECT e.capability, e.subject_id, o.raw_clinical_content_persisted
                FROM clinical_authorization_evidence e
                JOIN clinical_write_evidence_links l ON l.authorization_evidence_id = e.id
                JOIN clinical_outbox_events o ON o.write_link_id = l.id
                WHERE l.clinical_object_id = ?
                """,
                (first["id"],),
            ).fetchone()
        self.assertEqual(1, event_count)
        self.assertEqual("care_event.write", linked["capability"])
        self.assertEqual(self.patient_id, linked["subject_id"])
        self.assertEqual(0, linked["raw_clinical_content_persisted"])

    def test_patient_feedback_write_is_idempotent_and_outbox_has_no_narrative(self) -> None:
        narrative = "synthetic private feedback narrative"
        status, feedback = self.request(
            f"/api/v1/care-loop/plans/{self.plan['id']}/feedback",
            {
                "response_code": "improved",
                "adverse_events": [],
                "narrative": narrative,
            },
            session_id=self.patient_session_id,
            idempotency_key="feedback:SYN:0001",
        )
        self.assertEqual(201, status)
        with self.app.store.connect() as connection:
            outbox = connection.execute(
                """
                SELECT o.payload_json
                FROM clinical_write_evidence_links l
                JOIN clinical_outbox_events o ON o.write_link_id = l.id
                WHERE l.clinical_object_id = ?
                """,
                (feedback["id"],),
            ).fetchone()
        self.assertNotIn(narrative, outbox["payload_json"])

    def test_doctor_and_other_patient_cannot_submit_patient_self_events(self) -> None:
        with self.assertRaises(DomainError) as doctor_error:
            self.app.authorized_care_loop.record_execution(
                session_id=self.app.synthetic_context["doctor_session_id"],
                idempotency_key="exec:SYN:doctor",
                plan_version_id=self.plan["id"],
                event_type="started",
                detail={"synthetic": True},
            )
        self.assertEqual("capability_forbidden", doctor_error.exception.code)

        other = self.app.security.create_synthetic_subject(
            display_code="SYN-OTHER-PATIENT-001",
            role="patient",
            actor_id="test_setup",
        )
        other_session = self.app.security.open_synthetic_session(subject_id=other["id"])
        with self.assertRaises(DomainError) as other_error:
            self.app.authorized_care_loop.record_execution(
                session_id=other_session["id"],
                idempotency_key="exec:SYN:other1",
                plan_version_id=self.plan["id"],
                event_type="started",
                detail={"synthetic": True},
            )
        self.assertEqual("other_patient_record_forbidden", other_error.exception.code)

    def test_same_idempotency_key_with_changed_payload_is_rejected(self) -> None:
        self.app.authorized_care_loop.record_execution(
            session_id=self.patient_session_id,
            idempotency_key="exec:SYN:conflict",
            plan_version_id=self.plan["id"],
            event_type="started",
            detail={"sequence": 1},
        )
        with self.assertRaises(DomainError) as conflict:
            self.app.authorized_care_loop.record_execution(
                session_id=self.patient_session_id,
                idempotency_key="exec:SYN:conflict",
                plan_version_id=self.plan["id"],
                event_type="completed",
                detail={"sequence": 2},
            )
        self.assertEqual("idempotency_conflict", conflict.exception.code)

    def test_idempotency_persistence_failure_rolls_back_entire_patient_write(self) -> None:
        with self.app.store.connect() as connection:
            connection.execute(
                """
                CREATE TRIGGER force_care_idempotency_failure
                BEFORE INSERT ON care_event_idempotency_records
                BEGIN
                    SELECT RAISE(ABORT, 'synthetic_idempotency_failure');
                END
                """
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.app.authorized_care_loop.record_execution(
                session_id=self.patient_session_id,
                idempotency_key="exec:SYN:rollback",
                plan_version_id=self.plan["id"],
                event_type="started",
                detail={"synthetic": True},
            )
        with self.app.store.connect() as connection:
            event_count = connection.execute(
                "SELECT COUNT(*) FROM intervention_execution_events"
            ).fetchone()[0]
            evidence_count = connection.execute(
                "SELECT COUNT(*) FROM clinical_authorization_evidence"
            ).fetchone()[0]
            outbox_count = connection.execute(
                "SELECT COUNT(*) FROM clinical_outbox_events"
            ).fetchone()[0]
        self.assertEqual((0, 0, 0), (event_count, evidence_count, outbox_count))


if __name__ == "__main__":
    unittest.main()
