import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from map_tcm.service import ClinicalService
from map_tcm.store import SQLiteStore


class ClinicalRecordVersionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self.temp_dir.name) / "clinical-versions.db")
        self.store.initialize()
        self.service = ClinicalService(self.store)
        self.patient = self.service.create_patient(
            display_code="SYN-VERSION-001", is_synthetic=True, actor_id="test"
        )
        self.service.grant_consent(
            patient_id=self.patient["id"],
            purpose="synthetic version tests",
            scope=["clinical_record"],
            actor_id="test",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def draft(self) -> dict:
        return self.service.create_encounter(
            patient_id=self.patient["id"],
            encounter_type="initial",
            chief_complaint="synthetic versioned complaint",
            actor_id="SYN-DOC-VERSION-001",
        )

    def sign(self) -> dict:
        encounter = self.draft()
        self.service.add_observation(
            encounter_id=encounter["id"],
            category="synthetic observation",
            content="synthetic sourced content",
            source="patient",
            actor_id="SYN-PAT-VERSION-001",
        )
        self.service.add_assessment(
            encounter_id=encounter["id"],
            pattern="synthetic pattern",
            mechanism="synthetic mechanism",
            rationale="synthetic rationale",
            treatment_principle="synthetic principle",
            prescription_summary="no prescription",
            actor_id="SYN-DOC-VERSION-001",
            actor_role="verified_doctor",
        )
        self.service.transition_encounter(
            encounter_id=encounter["id"],
            target_status="doctor_reviewed",
            actor_id="SYN-DOC-VERSION-001",
            actor_role="verified_doctor",
        )
        return self.service.transition_encounter(
            encounter_id=encounter["id"],
            target_status="signed",
            actor_id="SYN-DOC-VERSION-001",
            actor_role="verified_doctor",
        )

    def test_create_encounter_starts_version_one_with_sourced_field(self) -> None:
        encounter = self.draft()
        self.assertEqual(1, encounter["version_number"])
        with self.store.connect() as connection:
            version = connection.execute(
                "SELECT * FROM clinical_record_versions WHERE id = ?",
                (encounter["record_version_id"],),
            ).fetchone()
            field = connection.execute(
                "SELECT * FROM clinical_field_values WHERE record_version_id = ?",
                (encounter["record_version_id"],),
            ).fetchone()
        self.assertEqual("draft", version["status"])
        self.assertEqual("chief_complaint", field["field_id"])
        self.assertTrue(field["source_ref"].startswith("synthetic://"))
        self.assertEqual(64, len(field["value_hash"]))

    def test_machine_contract_keeps_append_only_and_production_gates(self) -> None:
        contract_path = (
            Path(__file__).parents[1]
            / "schemas"
            / "gate1"
            / "clinical-record-version-v0.1.json"
        )
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        self.assertEqual("MAP-DEV-008", contract["contract_id"])
        self.assertTrue(contract["synthetic_only"])
        self.assertFalse(contract["production_ready"])
        self.assertTrue(contract["entities"]["ClinicalRecordVersion"]["signed_immutable"])
        self.assertFalse(
            contract["entities"]["ResponsibilitySnapshot"]["raw_source_content_persisted"]
        )
        self.assertEqual(
            "MAP-DEV-010",
            contract["implemented_followups"][
                "single authorization-write-outbox transaction"
            ],
        )

    def test_signing_creates_minimal_hash_manifest_without_raw_content(self) -> None:
        signed = self.sign()
        self.assertIsNotNone(signed["responsibility_snapshot_id"])
        with self.store.connect() as connection:
            snapshot = connection.execute(
                "SELECT * FROM clinical_record_responsibility_snapshots WHERE id = ?",
                (signed["responsibility_snapshot_id"],),
            ).fetchone()
        manifest = json.loads(snapshot["field_manifest_json"])
        self.assertGreaterEqual(len(manifest), 3)
        self.assertEqual(0, snapshot["raw_source_content_persisted"])
        self.assertNotIn("synthetic sourced content", snapshot["field_manifest_json"])
        self.assertTrue(all("value_hash" in field for field in manifest))

    def test_signed_version_fields_and_snapshot_are_database_immutable(self) -> None:
        signed = self.sign()
        with self.assertRaises(sqlite3.IntegrityError), self.store.connect() as connection:
            connection.execute(
                "UPDATE clinical_record_versions SET status = 'draft' WHERE id = ?",
                (signed["record_version_id"],),
            )
        with self.store.connect() as connection:
            field = connection.execute(
                "SELECT id FROM clinical_field_values WHERE record_version_id = ? LIMIT 1",
                (signed["record_version_id"],),
            ).fetchone()
        with self.assertRaises(sqlite3.IntegrityError), self.store.connect() as connection:
            connection.execute(
                "UPDATE clinical_field_values SET value_json = '{}' WHERE id = ?",
                (field["id"],),
            )
        with self.assertRaises(sqlite3.IntegrityError), self.store.connect() as connection:
            connection.execute(
                "DELETE FROM clinical_record_responsibility_snapshots WHERE id = ?",
                (signed["responsibility_snapshot_id"],),
            )

    def test_revision_supersedes_signed_version_and_preserves_prior_fields(self) -> None:
        signed = self.sign()
        revised = self.service.start_revision(
            encounter_id=signed["id"],
            reason="synthetic correction after follow-up",
            actor_id="SYN-DOC-VERSION-001",
            actor_role="verified_doctor",
        )
        self.assertEqual(2, revised["version_number"])
        self.assertEqual(signed["record_version_id"], revised["supersedes_version_id"])
        with self.store.connect() as connection:
            versions = connection.execute(
                """
                SELECT id, version_number, status, supersedes_version_id
                FROM clinical_record_versions WHERE encounter_id = ?
                ORDER BY version_number
                """,
                (signed["id"],),
            ).fetchall()
            copied = connection.execute(
                """
                SELECT supersedes_field_value_id FROM clinical_field_values
                WHERE record_version_id = ?
                """,
                (revised["record_version_id"],),
            ).fetchall()
        self.assertEqual(["signed", "draft"], [row["status"] for row in versions])
        self.assertTrue(copied)
        self.assertTrue(all(row["supersedes_field_value_id"] for row in copied))

    def test_withdrawal_appends_event_without_mutating_signed_version(self) -> None:
        signed = self.sign()
        withdrawn = self.service.transition_encounter(
            encounter_id=signed["id"],
            target_status="withdrawn",
            actor_id="SYN-DOC-VERSION-001",
            actor_role="verified_doctor",
            reason="synthetic withdrawal reason",
        )
        self.assertEqual("withdrawn", withdrawn["status"])
        with self.store.connect() as connection:
            version = connection.execute(
                "SELECT status FROM clinical_record_versions WHERE id = ?",
                (signed["record_version_id"],),
            ).fetchone()
            event = connection.execute(
                "SELECT reason FROM clinical_record_withdrawal_events WHERE encounter_id = ?",
                (signed["id"],),
            ).fetchone()
        self.assertEqual("signed", version["status"])
        self.assertEqual("synthetic withdrawal reason", event["reason"])


if __name__ == "__main__":
    unittest.main()
