from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from map_tcm.api import MapApplication
from map_tcm.errors import DomainError
from map_tcm.identity import IdentityAccessService
from map_tcm.patient_vault import PatientVaultService
from map_tcm.service import ClinicalService
from map_tcm.store import SQLiteStore


class VaultProvenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self.temp_dir.name) / "vault-provenance.db")
        self.store.initialize()
        self.clinical = ClinicalService(self.store)
        self.security = IdentityAccessService(self.store)
        self.patient_vault = PatientVaultService(self.store)
        self.patient = self.clinical.create_patient(
            display_code="SYN-PROVENANCE-001",
            is_synthetic=True,
            actor_id="test_setup",
        )
        self.subject = self.security.create_synthetic_subject(
            display_code="SYN-PROVENANCE-001",
            role="patient",
            actor_id="test_setup",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_patient_record_binding_is_explicit_and_immutable(self) -> None:
        binding = self.clinical.bind_patient_security_subject(
            patient_id=self.patient["id"],
            patient_subject_id=self.subject["id"],
            actor_id="test_setup",
        )
        self.assertEqual(self.subject["id"], binding["patient_subject_id"])
        with self.store.connect() as connection:
            vault = connection.execute(
                "SELECT * FROM vaults WHERE id = ?", (binding["vault_id"],)
            ).fetchone()
        self.assertEqual("patient_private", vault["vault_type"])
        self.assertEqual(self.subject["id"], vault["owner_subject_id"])
        self.assertIsNone(vault["key_reference"])

        with self.assertRaises(sqlite3.IntegrityError), self.store.connect() as connection:
            connection.execute(
                "UPDATE patient_record_subject_bindings SET bound_by = 'forged' WHERE patient_id = ?",
                (self.patient["id"],),
            )

    def test_matching_display_code_does_not_replace_explicit_binding(self) -> None:
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM patient_record_subject_bindings"
            ).fetchone()
        self.assertEqual(0, row[0])

    def test_machine_contract_keeps_vault_and_source_production_gates_closed(self) -> None:
        contract_path = (
            Path(__file__).parents[1]
            / "schemas"
            / "gate1"
            / "vault-source-provenance-v0.1.json"
        )
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        self.assertEqual("MAP-DEV-009", contract["contract_id"])
        self.assertTrue(contract["synthetic_only"])
        self.assertFalse(contract["production_ready"])
        self.assertTrue(contract["entities"]["PatientRecordSubjectBinding"]["immutable"])
        self.assertFalse(contract["entities"]["SourceObject"]["raw_content_in_source_object_row"])
        self.assertEqual(
            "MAP-DEV-010",
            contract["implemented_followups"][
                "single authorization-write-outbox transaction"
            ],
        )

    def test_source_object_and_anchor_are_immutable_and_store_no_raw_fingerprint(self) -> None:
        binding = self.clinical.bind_patient_security_subject(
            patient_id=self.patient["id"],
            patient_subject_id=self.subject["id"],
            actor_id="test_setup",
        )
        raw_fingerprint = {"synthetic_text": "仅用于来源哈希，不应写入 source_objects"}
        source = self.patient_vault.register_source_object(
            vault_id=binding["vault_id"],
            source_type="document",
            storage_reference="synthetic://document/report-001",
            content_fingerprint=raw_fingerprint,
            created_by_subject_id=self.subject["id"],
        )
        anchor = self.patient_vault.add_source_anchor(
            source_object_id=source["id"],
            anchor_type="page_bbox",
            anchor={"page": 2, "bbox": [0.1, 0.2, 0.8, 0.4]},
            confidence=0.99,
            transform_chain=[{"operation": "synthetic_manual_anchor", "version": "0.1"}],
        )
        with self.store.connect() as connection:
            source_row = connection.execute(
                "SELECT * FROM source_objects WHERE id = ?", (source["id"],)
            ).fetchone()
            anchor_row = connection.execute(
                "SELECT * FROM source_anchors WHERE id = ?", (anchor["id"],)
            ).fetchone()
        self.assertEqual(64, len(source_row["content_hash"]))
        self.assertNotIn("仅用于来源哈希", json.dumps(dict(source_row), ensure_ascii=False))
        self.assertEqual(2, json.loads(anchor_row["anchor_json"])["page"])

        with self.assertRaises(sqlite3.IntegrityError), self.store.connect() as connection:
            connection.execute(
                "UPDATE source_anchors SET anchor_json = '{}' WHERE id = ?", (anchor["id"],)
            )

    def test_patient_vault_creation_reuses_bound_subject_vault(self) -> None:
        binding = self.clinical.bind_patient_security_subject(
            patient_id=self.patient["id"],
            patient_subject_id=self.subject["id"],
            actor_id="test_setup",
        )
        vault = self.patient_vault.create_vault(
            patient_subject_code="SYN-PROVENANCE-001",
            actor_id=self.subject["id"],
        )
        self.assertEqual(binding["vault_id"], vault["id"])

    def test_wrong_patient_subject_cannot_be_bound(self) -> None:
        wrong_subject = self.security.create_synthetic_subject(
            display_code="SYN-PROVENANCE-OTHER-001",
            role="patient",
            actor_id="test_setup",
        )
        with self.assertRaisesRegex(DomainError, "不一致"):
            self.clinical.bind_patient_security_subject(
                patient_id=self.patient["id"],
                patient_subject_id=wrong_subject["id"],
                actor_id="test_setup",
            )


class ClinicalFieldProvenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.app = MapApplication(Path(self.temp_dir.name) / "clinical-source.db")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_session_authorized_new_encounter_anchors_fields_to_patient_vault(self) -> None:
        result = self.app.authorized_clinical.create_encounter(
            session_id=self.app.synthetic_context["doctor_session_id"],
            patient_id=self.app.store.overview()["patients"][0]["id"],
            encounter_type="follow_up",
            chief_complaint="合成来源锚点验证",
            purpose=self.app.synthetic_context["purpose"],
            data_categories=["chief_complaint"],
        )
        with self.app.store.connect() as connection:
            link = connection.execute(
                """
                SELECT l.*, o.owner_vault_id, a.source_object_version
                FROM clinical_field_values f
                JOIN clinical_field_source_links l ON l.field_value_id = f.id
                JOIN source_objects o ON o.id = l.source_object_id
                JOIN source_anchors a ON a.id = l.source_anchor_id
                WHERE f.record_version_id = ? AND f.field_id = 'chief_complaint'
                """,
                (result["record_version_id"],),
            ).fetchone()
        self.assertEqual("anchored", link["link_status"])
        self.assertEqual(self.app.synthetic_context["patient_vault_id"], link["owner_vault_id"])
        self.assertEqual(1, link["source_object_version"])


if __name__ == "__main__":
    unittest.main()
