import tempfile
import unittest
from pathlib import Path

from map_tcm.errors import DomainError
from map_tcm.service import ClinicalService
from map_tcm.store import SQLiteStore


class ClinicalServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self.temp_dir.name) / "test.db")
        self.store.initialize()
        self.service = ClinicalService(self.store)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def create_consented_patient(self) -> dict:
        patient = self.service.create_patient(
            display_code="SYN-TEST-001", is_synthetic=True, actor_id="test"
        )
        self.service.grant_consent(
            patient_id=patient["id"],
            purpose="test",
            scope=["clinical_record"],
            actor_id="test",
        )
        return patient

    def test_rejects_non_synthetic_patient(self) -> None:
        with self.assertRaisesRegex(DomainError, "SYN-"):
            self.service.create_patient(
                display_code="REAL-001", is_synthetic=False, actor_id="test"
            )

    def test_requires_active_consent_for_encounter(self) -> None:
        patient = self.service.create_patient(
            display_code="SYN-TEST-002", is_synthetic=True, actor_id="test"
        )
        with self.assertRaisesRegex(DomainError, "有效授权"):
            self.service.create_encounter(
                patient_id=patient["id"],
                encounter_type="initial",
                chief_complaint="synthetic",
                actor_id="test",
            )

    def test_ai_observation_stays_draft(self) -> None:
        patient = self.create_consented_patient()
        encounter = self.service.create_encounter(
            patient_id=patient["id"],
            encounter_type="initial",
            chief_complaint="synthetic",
            actor_id="test",
        )
        observation = self.service.add_observation(
            encounter_id=encounter["id"],
            category="summary",
            content="AI draft only",
            source="ai",
            actor_id="ai_service",
        )
        self.assertEqual("draft", observation["review_status"])

    def test_doctor_review_and_sign_flow(self) -> None:
        patient = self.create_consented_patient()
        encounter = self.service.create_encounter(
            patient_id=patient["id"],
            encounter_type="follow_up",
            chief_complaint="synthetic",
            actor_id="test",
        )
        self.service.add_observation(
            encounter_id=encounter["id"],
            category="follow_up",
            content="synthetic observation",
            source="patient",
            actor_id="synthetic_patient",
        )
        self.service.add_assessment(
            encounter_id=encounter["id"],
            pattern="synthetic pattern",
            mechanism="synthetic mechanism",
            rationale="synthetic rationale",
            treatment_principle="synthetic principle",
            prescription_summary="no prescription",
            actor_id="doctor_001",
            actor_role="verified_doctor",
        )
        reviewed = self.service.transition_encounter(
            encounter_id=encounter["id"],
            target_status="doctor_reviewed",
            actor_id="doctor_001",
            actor_role="verified_doctor",
        )
        signed = self.service.transition_encounter(
            encounter_id=encounter["id"],
            target_status="signed",
            actor_id="doctor_001",
            actor_role="verified_doctor",
        )
        self.assertEqual("doctor_reviewed", reviewed["status"])
        self.assertEqual("signed", signed["status"])
        self.assertGreaterEqual(len(self.store.overview()["audit"]), 6)

    def test_non_doctor_cannot_review(self) -> None:
        patient = self.create_consented_patient()
        encounter = self.service.create_encounter(
            patient_id=patient["id"],
            encounter_type="review",
            chief_complaint="synthetic",
            actor_id="test",
        )
        with self.assertRaisesRegex(DomainError, "医生"):
            self.service.transition_encounter(
                encounter_id=encounter["id"],
                target_status="doctor_reviewed",
                actor_id="operator",
                actor_role="operator",
            )

    def test_review_requires_observation(self) -> None:
        patient = self.create_consented_patient()
        encounter = self.service.create_encounter(
            patient_id=patient["id"],
            encounter_type="initial",
            chief_complaint="synthetic",
            actor_id="test",
        )
        with self.assertRaisesRegex(DomainError, "观察"):
            self.service.transition_encounter(
                encounter_id=encounter["id"],
                target_status="doctor_reviewed",
                actor_id="doctor_001",
                actor_role="verified_doctor",
            )

    def test_sign_requires_doctor_assessment(self) -> None:
        patient = self.create_consented_patient()
        encounter = self.service.create_encounter(
            patient_id=patient["id"],
            encounter_type="initial",
            chief_complaint="synthetic",
            actor_id="test",
        )
        self.service.add_observation(
            encounter_id=encounter["id"],
            category="initial",
            content="synthetic observation",
            source="patient",
            actor_id="synthetic_patient",
        )
        self.service.transition_encounter(
            encounter_id=encounter["id"],
            target_status="doctor_reviewed",
            actor_id="doctor_001",
            actor_role="verified_doctor",
        )
        with self.assertRaisesRegex(DomainError, "医生"):
            self.service.transition_encounter(
                encounter_id=encounter["id"],
                target_status="signed",
                actor_id="doctor_001",
                actor_role="verified_doctor",
            )


if __name__ == "__main__":
    unittest.main()
