from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from map_tcm.api import MapApplication
from map_tcm.errors import DomainError


class TcmCandidateRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.app = MapApplication(Path(self.temp_dir.name) / "candidate-registry.db")
        overview = self.app.store.overview()
        self.encounter_id = overview["encounters"][0]["id"]

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_registry_is_versioned_unapproved_and_immutable(self) -> None:
        contract_path = Path(__file__).parents[1] / "schemas" / "tcm_clinical_fields_v0.1.json"
        source = json.loads(contract_path.read_text(encoding="utf-8"))
        self.assertEqual(len(source["fields"]), self.app.clinical_field_contract["field_count"])
        self.assertEqual("candidate_for_doctor_review", self.app.clinical_field_contract["status"])
        self.assertFalse(self.app.clinical_field_contract["clinical_approval"])
        with self.assertRaises(sqlite3.IntegrityError), self.app.store.connect() as connection:
            connection.execute(
                "UPDATE clinical_field_registry_versions SET clinical_approval = 1"
            )

    def test_formal_ai_value_and_patient_doctor_judgment_are_rejected(self) -> None:
        with self.assertRaises(DomainError) as ai_error:
            self.app.service.add_structured_field(
                encounter_id=self.encounter_id,
                field_id="history.patient_goal",
                value="synthetic goal",
                source_role="ai",
                actor_id="synthetic_ai",
                actor_role="delegated_agent",
            )
        self.assertEqual("formal_ai_source_forbidden", ai_error.exception.code)

        with self.assertRaises(DomainError) as patient_error:
            self.app.service.add_structured_field(
                encounter_id=self.encounter_id,
                field_id="assessment.pattern",
                value=["synthetic pattern"],
                source_role="patient",
                actor_id=self.app.synthetic_context["patient_subject_id"],
                actor_role="patient",
            )
        self.assertEqual("field_source_role_forbidden", patient_error.exception.code)

    def test_session_structured_field_write_has_evidence_and_minimal_outbox(self) -> None:
        result = self.app.authorized_clinical.add_structured_field(
            session_id=self.app.synthetic_context["doctor_session_id"],
            encounter_id=self.encounter_id,
            field_id="assessment.pattern",
            value=["synthetic candidate pattern"],
            unknown_state=None,
            source_role="doctor",
            review_status="doctor_reviewed",
            purpose=self.app.synthetic_context["purpose"],
            data_categories=["assessment"],
        )
        with self.app.store.connect() as connection:
            row = connection.execute(
                """
                SELECT l.clinical_object_type, o.payload_json, r.clinical_approval,
                       fl.evidence_tag, fl.review_status
                FROM clinical_write_evidence_links l
                JOIN clinical_outbox_events o ON o.write_link_id = l.id
                JOIN clinical_field_registry_links fl
                  ON fl.field_value_id = l.clinical_object_id
                JOIN clinical_field_registry_versions r
                  ON r.id = fl.registry_version_id
                WHERE l.clinical_object_id = ?
                """,
                (result["id"],),
            ).fetchone()
        self.assertEqual("clinical_field_value", row["clinical_object_type"])
        self.assertEqual("doctor_judgment", row["evidence_tag"])
        self.assertEqual("doctor_reviewed", row["review_status"])
        self.assertEqual(0, row["clinical_approval"])
        self.assertNotIn("synthetic candidate pattern", row["payload_json"])


class NoAiSyntheticCareLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.app = MapApplication(Path(self.temp_dir.name) / "no-ai-care-loop.db")
        overview = self.app.store.overview()
        self.initial_encounter_id = overview["encounters"][0]["id"]
        self.patient_subject_id = self.app.synthetic_context["patient_subject_id"]
        self.doctor_subject_id = self.app.synthetic_context["doctor_subject_id"]

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_initial_signed_execution_feedback_follow_up_signed_without_ai(self) -> None:
        self.app.service.add_assessment(
            encounter_id=self.initial_encounter_id,
            pattern="synthetic initial pattern",
            mechanism="synthetic initial mechanism",
            rationale="synthetic initial rationale",
            treatment_principle="synthetic initial principle",
            prescription_summary="synthetic non-drug observation plan",
            actor_id=self.doctor_subject_id,
            actor_role="verified_doctor",
        )
        self.app.service.transition_encounter(
            encounter_id=self.initial_encounter_id,
            target_status="doctor_reviewed",
            actor_id=self.doctor_subject_id,
            actor_role="verified_doctor",
        )
        initial_signed = self.app.service.transition_encounter(
            encounter_id=self.initial_encounter_id,
            target_status="signed",
            actor_id=self.doctor_subject_id,
            actor_role="verified_doctor",
        )
        plan = self.app.care_loop.create_intervention_plan(
            encounter_id=self.initial_encounter_id,
            action_summary="synthetic lifestyle observation",
            instructions="record one synthetic observation daily",
            follow_up_due_at="2030-01-08T09:00:00+00:00",
            actor_id=self.doctor_subject_id,
            actor_role="verified_doctor",
        )
        self.app.care_loop.record_execution(
            plan_version_id=plan["id"],
            patient_subject_id=self.patient_subject_id,
            event_type="started",
            detail={"synthetic": True},
        )
        self.app.care_loop.record_execution(
            plan_version_id=plan["id"],
            patient_subject_id=self.patient_subject_id,
            event_type="completed",
            detail={"synthetic": True},
        )
        self.app.care_loop.record_feedback(
            plan_version_id=plan["id"],
            patient_subject_id=self.patient_subject_id,
            response_code="improved",
            adverse_events=[],
            narrative="synthetic feedback only",
        )
        follow_up = self.app.care_loop.create_follow_up_encounter(
            plan_version_id=plan["id"],
            patient_subject_id=self.patient_subject_id,
            chief_complaint="synthetic follow-up comparison",
        )
        projection = self.app.care_loop.project_patient_follow_up_fields(
            follow_up_encounter_id=follow_up["id"],
            patient_subject_id=self.patient_subject_id,
        )
        self.assertFalse(projection["ai_used"])
        self.assertEqual(initial_signed["record_version_id"], projection["previous_signed_record_version_id"])

        self.app.care_loop.add_doctor_comparison(
            follow_up_encounter_id=follow_up["id"],
            comparison="synthetic comparison grounded in the previous signed record",
            actor_id=self.doctor_subject_id,
            actor_role="verified_doctor",
        )
        self.app.service.add_observation(
            encounter_id=follow_up["id"],
            category="follow_up",
            content="synthetic doctor observation",
            source="doctor",
            actor_id=self.doctor_subject_id,
        )
        self.app.service.add_assessment(
            encounter_id=follow_up["id"],
            pattern="synthetic follow-up pattern",
            mechanism="synthetic follow-up mechanism",
            rationale="synthetic follow-up rationale",
            treatment_principle="synthetic follow-up principle",
            prescription_summary="no prescription",
            actor_id=self.doctor_subject_id,
            actor_role="verified_doctor",
        )
        self.app.service.transition_encounter(
            encounter_id=follow_up["id"],
            target_status="doctor_reviewed",
            actor_id=self.doctor_subject_id,
            actor_role="verified_doctor",
        )
        signed_follow_up = self.app.service.transition_encounter(
            encounter_id=follow_up["id"],
            target_status="signed",
            actor_id=self.doctor_subject_id,
            actor_role="verified_doctor",
        )
        self.assertEqual("signed", signed_follow_up["status"])

        with self.app.store.connect() as connection:
            ai_formal_count = connection.execute(
                """
                SELECT COUNT(*)
                FROM clinical_field_registry_links l
                JOIN clinical_field_values fv ON fv.id = l.field_value_id
                WHERE fv.record_version_id = ? AND l.source_role = 'ai'
                """,
                (signed_follow_up["record_version_id"],),
            ).fetchone()[0]
            link = connection.execute(
                "SELECT * FROM follow_up_record_links WHERE follow_up_encounter_id = ?",
                (follow_up["id"],),
            ).fetchone()
            execution_count = connection.execute(
                "SELECT COUNT(*) FROM intervention_execution_events WHERE plan_version_id = ?",
                (plan["id"],),
            ).fetchone()[0]
        self.assertEqual(0, ai_formal_count)
        self.assertEqual(initial_signed["record_version_id"], link["previous_signed_record_version_id"])
        self.assertEqual(2, execution_count)


if __name__ == "__main__":
    unittest.main()
