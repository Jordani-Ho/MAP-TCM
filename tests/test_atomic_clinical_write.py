from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from map_tcm.api import MapApplication
from map_tcm.errors import DomainError


class AtomicClinicalWriteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.app = MapApplication(Path(self.temp_dir.name) / "atomic-clinical.db")
        overview = self.app.store.overview()
        self.patient_id = overview["patients"][0]["id"]
        self.encounter_id = overview["encounters"][0]["id"]
        self.session_id = self.app.synthetic_context["doctor_session_id"]
        self.purpose = self.app.synthetic_context["purpose"]
        self.categories = [
            "chief_complaint",
            "observations",
            "assessment",
            "treatment_principle",
            "prescription",
        ]

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def add_assessment(self) -> dict:
        return self.app.authorized_clinical.add_assessment(
            session_id=self.session_id,
            encounter_id=self.encounter_id,
            pattern="synthetic atomic pattern",
            mechanism="synthetic atomic mechanism",
            rationale="synthetic atomic rationale",
            treatment_principle="synthetic atomic principle",
            prescription_summary="none",
            purpose=self.purpose,
            data_categories=self.categories,
        )

    def test_allowed_write_evidence_domain_write_and_outbox_are_linked(self) -> None:
        assessment = self.add_assessment()
        with self.app.store.connect() as connection:
            row = connection.execute(
                """
                SELECT e.*, l.clinical_object_type, l.clinical_object_id,
                       l.action_type, o.event_type, o.payload_json,
                       o.payload_hash, o.raw_clinical_content_persisted,
                       o.status AS outbox_status
                FROM clinical_authorization_evidence e
                JOIN clinical_write_evidence_links l
                  ON l.authorization_evidence_id = e.id
                JOIN clinical_outbox_events o
                  ON o.authorization_evidence_id = e.id
                WHERE l.clinical_object_id = ?
                """,
                (assessment["id"],),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(self.session_id, row["session_id"])
        self.assertEqual(self.app.synthetic_context["doctor_subject_id"], row["subject_id"])
        self.assertIsNotNone(row["relationship_id"])
        self.assertIsNotNone(row["consent_id"])
        self.assertEqual("assessment", row["clinical_object_type"])
        self.assertEqual("pending", row["outbox_status"])
        self.assertEqual(64, len(row["evidence_hash"]))
        self.assertEqual(64, len(row["payload_hash"]))
        self.assertEqual(0, row["raw_clinical_content_persisted"])
        self.assertNotIn("synthetic atomic pattern", row["payload_json"])

    def test_outbox_failure_rolls_back_write_evidence_and_allowed_audit(self) -> None:
        with self.app.store.connect() as connection:
            before_observations = connection.execute(
                "SELECT COUNT(*) FROM observations"
            ).fetchone()[0]
            before_audit = connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
            connection.execute(
                """
                CREATE TRIGGER force_atomic_outbox_failure
                BEFORE INSERT ON clinical_outbox_events
                BEGIN
                    SELECT RAISE(ABORT, 'synthetic_outbox_failure');
                END
                """
            )

        with self.assertRaises(sqlite3.IntegrityError):
            self.app.authorized_clinical.add_observation(
                session_id=self.session_id,
                encounter_id=self.encounter_id,
                category="synthetic rollback",
                content="must not persist",
                source="doctor",
                purpose=self.purpose,
                data_categories=self.categories,
            )

        with self.app.store.connect() as connection:
            after_observations = connection.execute(
                "SELECT COUNT(*) FROM observations"
            ).fetchone()[0]
            after_audit = connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
            evidence_count = connection.execute(
                "SELECT COUNT(*) FROM clinical_authorization_evidence"
            ).fetchone()[0]
            link_count = connection.execute(
                "SELECT COUNT(*) FROM clinical_write_evidence_links"
            ).fetchone()[0]
            outbox_count = connection.execute(
                "SELECT COUNT(*) FROM clinical_outbox_events"
            ).fetchone()[0]
        self.assertEqual(before_observations, after_observations)
        self.assertEqual(before_audit, after_audit)
        self.assertEqual((0, 0, 0), (evidence_count, link_count, outbox_count))

    def test_denied_decision_commits_audit_but_no_allowed_evidence(self) -> None:
        with self.app.store.connect() as connection:
            consent = connection.execute(
                """
                SELECT sc.id FROM security_consents sc
                JOIN care_relationships cr ON cr.id = sc.relationship_id
                WHERE cr.patient_subject_id = ? AND sc.status = 'active'
                ORDER BY sc.created_at DESC LIMIT 1
                """,
                (self.app.synthetic_context["patient_subject_id"],),
            ).fetchone()
        self.app.security.revoke_consent(
            patient_session_id=self.app.synthetic_context["patient_session_id"],
            consent_id=consent["id"],
            reason="synthetic atomic denial test",
        )
        with self.assertRaisesRegex(DomainError, "拒绝") as denied:
            self.app.authorized_clinical.add_observation(
                session_id=self.session_id,
                encounter_id=self.encounter_id,
                category="denied",
                content="must not persist",
                source="doctor",
                purpose=self.purpose,
                data_categories=self.categories,
            )
        self.assertEqual("active_consent_required", denied.exception.code)
        with self.app.store.connect() as connection:
            audit = connection.execute(
                """
                SELECT metadata_json FROM audit_events
                WHERE event_type = 'security.authorization.decision'
                ORDER BY id DESC LIMIT 1
                """
            ).fetchone()
            evidence_count = connection.execute(
                "SELECT COUNT(*) FROM clinical_authorization_evidence"
            ).fetchone()[0]
            outbox_count = connection.execute(
                "SELECT COUNT(*) FROM clinical_outbox_events"
            ).fetchone()[0]
        metadata = json.loads(audit["metadata_json"])
        self.assertFalse(metadata["allowed"])
        self.assertEqual("active_consent_required", metadata["reason"])
        self.assertEqual((0, 0), (evidence_count, outbox_count))

    def test_review_sign_and_revision_each_create_minimal_outbox_event(self) -> None:
        self.add_assessment()
        self.app.authorized_clinical.transition_encounter(
            session_id=self.session_id,
            encounter_id=self.encounter_id,
            target_status="doctor_reviewed",
            purpose=self.purpose,
            data_categories=self.categories,
        )
        self.app.authorized_clinical.transition_encounter(
            session_id=self.session_id,
            encounter_id=self.encounter_id,
            target_status="signed",
            purpose=self.purpose,
            data_categories=self.categories,
        )
        self.app.authorized_clinical.start_revision(
            session_id=self.session_id,
            encounter_id=self.encounter_id,
            reason="synthetic post-sign correction",
            purpose=self.purpose,
            data_categories=self.categories,
        )
        with self.app.store.connect() as connection:
            rows = connection.execute(
                """
                SELECT event_type, payload_json, raw_clinical_content_persisted
                FROM clinical_outbox_events ORDER BY created_at, id
                """
            ).fetchall()
        self.assertEqual(4, len(rows))
        self.assertEqual(
            {
                "clinical.assessment.created",
                "clinical.encounter.doctor_reviewed",
                "clinical.encounter.signed",
                "clinical.encounter.revision_started",
            },
            {row["event_type"] for row in rows},
        )
        self.assertTrue(all(row["raw_clinical_content_persisted"] == 0 for row in rows))
        self.assertNotIn("synthetic post-sign correction", "".join(row["payload_json"] for row in rows))

    def test_evidence_and_outbox_rows_are_database_immutable(self) -> None:
        assessment = self.add_assessment()
        with self.app.store.connect() as connection:
            row = connection.execute(
                """
                SELECT e.id AS evidence_id, o.id AS outbox_id
                FROM clinical_authorization_evidence e
                JOIN clinical_write_evidence_links l
                  ON l.authorization_evidence_id = e.id
                JOIN clinical_outbox_events o
                  ON o.authorization_evidence_id = e.id
                WHERE l.clinical_object_id = ?
                """,
                (assessment["id"],),
            ).fetchone()
        with self.assertRaises(sqlite3.IntegrityError), self.app.store.connect() as connection:
            connection.execute(
                "UPDATE clinical_authorization_evidence SET purpose = 'forged' WHERE id = ?",
                (row["evidence_id"],),
            )
        with self.assertRaises(sqlite3.IntegrityError), self.app.store.connect() as connection:
            connection.execute(
                "DELETE FROM clinical_outbox_events WHERE id = ?", (row["outbox_id"],)
            )

    def test_machine_contract_keeps_dispatcher_and_production_gates_closed(self) -> None:
        contract_path = (
            Path(__file__).parents[1]
            / "schemas"
            / "gate1"
            / "atomic-clinical-write-outbox-v0.1.json"
        )
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        self.assertEqual("MAP-DEV-010", contract["contract_id"])
        self.assertTrue(contract["synthetic_only"])
        self.assertFalse(contract["production_ready"])
        self.assertTrue(
            contract["transaction_boundary"]["any_write_failure_rolls_back_all"]
        )
        self.assertFalse(
            contract["entities"]["ClinicalOutboxEvent"][
                "payload_contains_raw_clinical_content"
            ]
        )
        self.assertIn(
            "outbox dispatcher lease retry and append-only delivery attempts",
            contract["open_production_gates"],
        )


if __name__ == "__main__":
    unittest.main()
