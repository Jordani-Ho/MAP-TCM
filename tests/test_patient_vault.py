from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from map_tcm.agent_knowledge import AgentKnowledgeService
from map_tcm.agent_runtime import build_agent
from map_tcm.errors import DomainError
from map_tcm.identity import IdentityAccessService
from map_tcm.patient_vault import PatientVaultService
from map_tcm.store import SQLiteStore


class PatientVaultTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self.tempdir.name) / "patient-vault.sqlite3")
        self.store.initialize()
        self.vaults = PatientVaultService(self.store)
        self.knowledge = AgentKnowledgeService(self.store)
        self.security = IdentityAccessService(self.store)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def create_vault(self, patient_code: str = "SYN-PAT-001") -> dict:
        with self.store.connect() as connection:
            subject = connection.execute(
                "SELECT id FROM security_subjects WHERE display_code = ?",
                (patient_code,),
            ).fetchone()
        if subject is None:
            self.security.create_synthetic_subject(
                display_code=patient_code,
                role="patient",
                actor_id="test_setup",
            )
        return self.vaults.create_vault(
            patient_subject_code=patient_code,
            actor_id=patient_code,
        )

    def append_identity(self, vault_id: str, patient_code: str = "SYN-PAT-001") -> dict:
        return self.vaults.append_entry(
            vault_id=vault_id,
            category="identity_profile",
            title="合成身份基础",
            payload={"name": "合成甲", "sex": "synthetic", "age_band": "40-49"},
            source_type="patient",
            source_ref="synthetic://patient/profile-v1",
            occurred_at="2026-08-13T08:00:00+00:00",
            created_by=patient_code,
        )

    def test_vault_rejects_real_patient_identifier(self) -> None:
        with self.assertRaisesRegex(DomainError, "SYN-"):
            self.vaults.create_vault(patient_subject_code="PATIENT-001", actor_id="operator")

    def test_machine_contract_includes_identity_records_media_and_gate(self) -> None:
        contract_path = (
            Path(__file__).parents[1]
            / "schemas"
            / "agent"
            / "patient-private-vault-v0.1.json"
        )
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        categories = set(contract["layers"]["source_foundation"]["categories"])
        self.assertTrue(contract["synthetic_only"])
        self.assertFalse(contract["production_ready"])
        self.assertTrue({"identity_profile", "medical_record", "image", "video"} <= categories)

    def test_identity_and_medical_record_are_patient_owned_source_entries(self) -> None:
        vault = self.create_vault()
        identity = self.append_identity(vault["id"])
        record = self.vaults.append_entry(
            vault_id=vault["id"],
            category="medical_record",
            title="合成门诊病历",
            payload={"document_type": "visit_note", "summary": "仅用于工程测试"},
            source_type="file",
            source_ref="synthetic://document/visit-001",
            occurred_at="2026-08-12T08:00:00+00:00",
            created_by="SYN-PAT-001",
        )
        with self.store.connect() as connection:
            rows = connection.execute(
                "SELECT category, status FROM patient_vault_entries WHERE vault_id = ?",
                (vault["id"],),
            ).fetchall()
        self.assertEqual(identity["status"], "active")
        self.assertEqual(record["status"], "active")
        self.assertEqual({row["category"] for row in rows}, {"identity_profile", "medical_record"})

    def test_original_entry_is_corrected_by_append_not_overwrite(self) -> None:
        vault = self.create_vault()
        first = self.append_identity(vault["id"])
        second = self.vaults.append_entry(
            vault_id=vault["id"],
            category="identity_profile",
            title="合成身份基础更正",
            payload={"name": "合成甲", "sex": "synthetic", "age_band": "50-59"},
            source_type="patient",
            source_ref="synthetic://patient/profile-v2",
            occurred_at="2026-08-13T09:00:00+00:00",
            created_by="SYN-PAT-001",
            supersedes_id=first["id"],
        )
        with self.store.connect() as connection:
            old = connection.execute(
                "SELECT status, payload_json FROM patient_vault_entries WHERE id = ?",
                (first["id"],),
            ).fetchone()
        self.assertEqual(old["status"], "superseded")
        self.assertEqual(json.loads(old["payload_json"])["age_band"], "40-49")
        self.assertEqual(second["version"], 2)

    def test_timeline_requires_matching_source_and_confirmation(self) -> None:
        vault = self.create_vault()
        entry = self.append_identity(vault["id"])
        with self.assertRaisesRegex(DomainError, "来源不一致"):
            self.vaults.append_timeline_event(
                vault_id=vault["id"],
                primary_entry_id=entry["id"],
                event_kind="identity_baseline",
                summary="合成身份基线",
                provenance_role="patient",
                confirmation_status="doctor_confirmed",
                occurred_at="2026-08-13T08:00:00+00:00",
                created_by="SYN-PAT-001",
            )

    def test_ai_derived_knowledge_has_sources_and_no_clinical_authority(self) -> None:
        vault = self.create_vault()
        entry = self.append_identity(vault["id"])
        derived = self.vaults.add_derived_knowledge(
            vault_id=vault["id"],
            knowledge_kind="summary",
            content="合成身份资料摘要，等待患者核对。",
            source_entry_ids=[entry["id"]],
            author_role="ai",
            status="ai_draft",
            created_by="anode_synthetic",
        )
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT clinical_authority, status FROM patient_derived_knowledge WHERE id = ?",
                (derived["id"],),
            ).fetchone()
        self.assertEqual(dict(row), {"clinical_authority": 0, "status": "ai_draft"})

    def test_patient_agent_can_search_only_its_own_vault(self) -> None:
        first_vault = self.create_vault("SYN-PAT-001")
        second_vault = self.create_vault("SYN-PAT-002")
        self.append_identity(first_vault["id"], "SYN-PAT-001")
        self.vaults.append_entry(
            vault_id=second_vault["id"],
            category="medical_record",
            title="另一患者的合成病历",
            payload={"summary": "隔离资料"},
            source_type="file",
            source_ref="synthetic://document/other-patient",
            occurred_at="2026-08-13T08:00:00+00:00",
            created_by="SYN-PAT-002",
        )
        node = self.knowledge.create_node(
            node_type="patient_tutor",
            owner_subject_code="SYN-PAT-001",
            teacher_subject_code=None,
            actor_id="SYN-PAT-001",
        )
        own = self.vaults.search_for_patient_agent(agent_node_id=node["id"], query="合成甲")
        other = self.vaults.search_for_patient_agent(agent_node_id=node["id"], query="隔离资料")
        self.assertEqual(len(own), 1)
        self.assertEqual(other, [])

    def test_patient_agent_exposes_separate_teaching_and_private_memory_tools(self) -> None:
        try:
            import agents  # noqa: F401
        except ImportError:
            self.skipTest("openai-agents optional dependency is not installed")
        node = self.knowledge.create_node(
            node_type="patient_tutor",
            owner_subject_code="SYN-PAT-001",
            teacher_subject_code=None,
            actor_id="SYN-PAT-001",
        )
        agent = build_agent(
            role_type="patient_tutor",
            agent_node_id=node["id"],
            knowledge=self.knowledge,
            patient_vault=self.vaults,
            model="gpt-5.4-mini",
        )
        self.assertEqual(len(agent.tools), 2)

    def test_every_patient_vault_write_is_audited(self) -> None:
        vault = self.create_vault()
        entry = self.append_identity(vault["id"])
        self.vaults.append_timeline_event(
            vault_id=vault["id"],
            primary_entry_id=entry["id"],
            event_kind="identity_baseline",
            summary="合成身份基线",
            provenance_role="patient",
            confirmation_status="patient_reported",
            occurred_at="2026-08-13T08:00:00+00:00",
            created_by="SYN-PAT-001",
        )
        with self.store.connect() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM audit_events WHERE event_type LIKE 'patient.%'"
            ).fetchone()[0]
        self.assertEqual(count, 3)


class PhysicianPatientAccessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self.tempdir.name) / "physician-access.sqlite3")
        self.store.initialize()
        self.vaults = PatientVaultService(self.store)
        self.knowledge = AgentKnowledgeService(self.store)
        self.security = IdentityAccessService(self.store)
        self.doctor = self.security.create_synthetic_subject(
            display_code="SYN-DOC-ACCESS-001",
            role="verified_doctor",
            actor_id="test_setup",
        )
        self.patient = self.security.create_synthetic_subject(
            display_code="SYN-PAT-ACCESS-001",
            role="patient",
            actor_id="test_setup",
        )
        self.doctor_session = self.security.open_synthetic_session(subject_id=self.doctor["id"])
        self.patient_session = self.security.open_synthetic_session(subject_id=self.patient["id"])
        self.security.step_up_session(session_id=self.doctor_session["id"])
        self.security.step_up_session(session_id=self.patient_session["id"])
        self.agent = self.knowledge.create_node(
            node_type="physician_apprentice",
            owner_subject_code="SYN-DOC-ACCESS-001",
            teacher_subject_code="SYN-DOC-ACCESS-001",
            actor_id="SYN-DOC-ACCESS-001",
        )
        self.vault = self.vaults.create_vault(
            patient_subject_code="SYN-PAT-ACCESS-001",
            actor_id="SYN-PAT-ACCESS-001",
        )
        self.identity_entry = self.vaults.append_entry(
            vault_id=self.vault["id"],
            category="identity_profile",
            title="合成患者身份",
            payload={"name": "合成会诊患者", "birth_context": "合成时地"},
            source_type="patient",
            source_ref="synthetic://patient/access-profile",
            occurred_at="2026-08-13T08:00:00+00:00",
            created_by="SYN-PAT-ACCESS-001",
        )
        self.record_entry = self.vaults.append_entry(
            vault_id=self.vault["id"],
            category="medical_record",
            title="合成既往病历",
            payload={"summary": "合成整体辨证资料"},
            source_type="file",
            source_ref="synthetic://document/access-record",
            occurred_at="2026-08-12T08:00:00+00:00",
            created_by="SYN-PAT-ACCESS-001",
        )
        self.relationship = None

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    @property
    def scope(self) -> list[str]:
        return ["identity_profile", "medical_record", "image", "derived_knowledge"]

    def activate_relationship(self) -> dict:
        requested = self.security.create_care_relationship(
            doctor_session_id=self.doctor_session["id"],
            patient_subject_id=self.patient["id"],
            purpose="synthetic_clinical_care",
            data_categories=self.scope,
        )
        self.relationship = self.security.accept_care_relationship(
            patient_session_id=self.patient_session["id"],
            relationship_id=requested["id"],
            purpose="synthetic_clinical_care",
            data_categories=self.scope,
        )
        return self.relationship

    def open_access(self) -> dict:
        if self.relationship is None:
            self.activate_relationship()
        return self.vaults.open_physician_access_session(
            physician_agent_node_id=self.agent["id"],
            doctor_security_session_id=self.doctor_session["id"],
            patient_subject_id=self.patient["id"],
            purpose="synthetic_clinical_care",
            data_categories=self.scope,
        )

    def test_unrelated_doctor_cannot_open_patient_vault(self) -> None:
        with self.assertRaises(DomainError) as caught:
            self.vaults.open_physician_access_session(
                physician_agent_node_id=self.agent["id"],
                doctor_security_session_id=self.doctor_session["id"],
                patient_subject_id=self.patient["id"],
                purpose="synthetic_clinical_care",
                data_categories=self.scope,
            )
        self.assertEqual(caught.exception.code, "active_relationship_required")

    def test_ephemeral_access_contract_requires_no_patient_copy(self) -> None:
        contract_path = (
            Path(__file__).parents[1]
            / "schemas"
            / "agent"
            / "physician-ephemeral-access-v0.1.json"
        )
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        self.assertTrue(contract["synthetic_only"])
        self.assertFalse(contract["production_ready"])
        self.assertFalse(contract["retention"]["temporary_patient_copy"])
        self.assertTrue(contract["retention"]["responsibility_snapshot"]["source_hashes"])

    def test_only_agent_bound_to_logged_in_doctor_can_open_access(self) -> None:
        self.activate_relationship()
        wrong_agent = self.knowledge.create_node(
            node_type="physician_apprentice",
            owner_subject_code="SYN-DOC-OTHER-001",
            teacher_subject_code="SYN-DOC-OTHER-001",
            actor_id="SYN-DOC-OTHER-001",
        )
        with self.assertRaisesRegex(DomainError, "绑定当前登录医生"):
            self.vaults.open_physician_access_session(
                physician_agent_node_id=wrong_agent["id"],
                doctor_security_session_id=self.doctor_session["id"],
                patient_subject_id=self.patient["id"],
                purpose="synthetic_clinical_care",
                data_categories=self.scope,
            )

    def test_physician_agent_reads_current_patient_without_persistent_copy(self) -> None:
        access = self.open_access()
        hits = self.vaults.search_for_physician_session(
            access_session_id=access["id"], query="合成"
        )
        with self.store.connect() as connection:
            row = connection.execute(
                """
                SELECT temporary_copy_persisted
                FROM physician_patient_access_sessions WHERE id = ?
                """,
                (access["id"],),
            ).fetchone()
            audit = connection.execute(
                """
                SELECT metadata_json FROM audit_events
                WHERE event_type = 'physician.patient_vault.read'
                ORDER BY id DESC LIMIT 1
                """
            ).fetchone()
        self.assertEqual({hit.id for hit in hits}, {self.identity_entry["id"], self.record_entry["id"]})
        self.assertEqual(row["temporary_copy_persisted"], 0)
        metadata = json.loads(audit["metadata_json"])
        self.assertFalse(metadata["query_or_content_persisted"])
        self.assertNotIn("合成", audit["metadata_json"])

    def test_access_scope_does_not_expand_to_unapproved_category(self) -> None:
        self.activate_relationship()
        access = self.vaults.open_physician_access_session(
            physician_agent_node_id=self.agent["id"],
            doctor_security_session_id=self.doctor_session["id"],
            patient_subject_id=self.patient["id"],
            purpose="synthetic_clinical_care",
            data_categories=["medical_record"],
        )
        identity_hits = self.vaults.search_for_physician_session(
            access_session_id=access["id"], query="合成患者身份"
        )
        record_hits = self.vaults.search_for_physician_session(
            access_session_id=access["id"], query="合成整体辨证资料"
        )
        self.assertEqual(identity_hits, [])
        self.assertEqual([hit.id for hit in record_hits], [self.record_entry["id"]])

    def test_revoked_access_basis_stops_existing_consultation(self) -> None:
        access = self.open_access()
        assert self.relationship is not None
        self.security.revoke_consent(
            patient_session_id=self.patient_session["id"],
            consent_id=self.relationship["consent_id"],
            reason="synthetic revocation",
        )
        with self.assertRaisesRegex(DomainError, "授权依据已失效"):
            self.vaults.search_for_physician_session(
                access_session_id=access["id"], query="合成"
            )

    def test_close_discards_access_and_prevents_further_reads(self) -> None:
        access = self.open_access()
        closed = self.vaults.close_physician_access_session(
            access_session_id=access["id"],
            doctor_security_session_id=self.doctor_session["id"],
        )
        self.assertFalse(closed["temporary_copy_persisted"])
        with self.assertRaisesRegex(DomainError, "已经关闭"):
            self.vaults.search_for_physician_session(
                access_session_id=access["id"], query="合成"
            )

    def test_responsibility_snapshot_keeps_hashes_not_raw_patient_content(self) -> None:
        access = self.open_access()
        snapshot = self.vaults.create_responsibility_snapshot(
            access_session_id=access["id"],
            source_entry_ids=[self.identity_entry["id"], self.record_entry["id"]],
            created_by=self.doctor["id"],
            ai_draft_id="adraft_syn_001",
            final_record_ref="synthetic://clinical-record/signed-001",
        )
        with self.store.connect() as connection:
            row = connection.execute(
                """
                SELECT source_manifest_json, raw_patient_content_persisted
                FROM physician_access_responsibility_snapshots WHERE id = ?
                """,
                (snapshot["id"],),
            ).fetchone()
        manifest = json.loads(row["source_manifest_json"])
        self.assertEqual(row["raw_patient_content_persisted"], 0)
        self.assertEqual({item["entry_id"] for item in manifest}, {self.identity_entry["id"], self.record_entry["id"]})
        self.assertNotIn("合成会诊患者", row["source_manifest_json"])
        self.assertTrue(all("content_hash" in item for item in manifest))

    def test_physician_agent_gets_session_tool_only_when_access_is_mounted(self) -> None:
        try:
            import agents  # noqa: F401
        except ImportError:
            self.skipTest("openai-agents optional dependency is not installed")
        access = self.open_access()
        without_patient = build_agent(
            role_type="physician_apprentice",
            agent_node_id=self.agent["id"],
            knowledge=self.knowledge,
            model="gpt-5.4-mini",
        )
        with_patient = build_agent(
            role_type="physician_apprentice",
            agent_node_id=self.agent["id"],
            knowledge=self.knowledge,
            patient_vault=self.vaults,
            physician_access_session_id=access["id"],
            model="gpt-5.4-mini",
        )
        self.assertEqual(len(without_patient.tools), 1)
        self.assertEqual(len(with_patient.tools), 2)


if __name__ == "__main__":
    unittest.main()
