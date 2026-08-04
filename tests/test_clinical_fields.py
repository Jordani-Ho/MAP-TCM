import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "schemas" / "tcm_clinical_fields_v0.1.json"


class ClinicalFieldContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.raw = CONTRACT_PATH.read_text(encoding="utf-8")
        cls.contract = json.loads(cls.raw)
        cls.fields = cls.contract["fields"]
        cls.by_id = {field["field_id"]: field for field in cls.fields}

    def test_contract_is_an_unapproved_synthetic_review_candidate(self) -> None:
        self.assertEqual("TCM-CLIN-001", self.contract["contract_id"])
        self.assertEqual("0.1.0", self.contract["schema_version"])
        self.assertEqual("candidate_for_doctor_review", self.contract["status"])
        self.assertTrue(self.contract["synthetic_only"])
        self.assertFalse(self.contract["clinical_approval"])
        self.assertEqual("SYN-", self.contract["subject_code_prefix"])

    def test_field_ids_are_unique_and_machine_validatable(self) -> None:
        self.assertGreaterEqual(len(self.fields), 40)
        self.assertEqual(len(self.fields), len(self.by_id))
        required_keys = {
            "field_id", "encounters", "section", "label_zh", "value_type",
            "requiredness", "allow_unknown", "evidence_tag", "source_roles",
            "review_owner", "sensitivity", "condition", "order",
        }
        for field in self.fields:
            self.assertEqual(required_keys, set(field), field["field_id"])
            self.assertTrue(set(field["encounters"]).issubset({"initial", "follow_up"}))
            self.assertIn(field["requiredness"], self.contract["requiredness_levels"])
            self.assertIn(field["evidence_tag"], self.contract["evidence_tags"])

    def test_initial_and_follow_up_have_explicit_safety_core(self) -> None:
        for encounter_type in ("initial", "follow_up"):
            applicable = {
                field["field_id"]: field
                for field in self.fields
                if encounter_type in field["encounters"]
            }
            for required_id in (
                "encounter.subject_code",
                "encounter.consent_reference",
                "history.current_medications",
                "history.allergies_reactions",
                "safety.red_flag_screen",
            ):
                self.assertEqual("required", applicable[required_id]["requiredness"])

    def test_follow_up_captures_change_exposure_outcome_and_adverse_events(self) -> None:
        required_follow_up = {
            "follow_up.interval_changes",
            "follow_up.intervention_exposure_adherence",
            "follow_up.response_outcome",
            "follow_up.adverse_events",
            "follow_up.new_diagnoses_medications",
            "follow_up.doctor_comparison",
        }
        self.assertTrue(required_follow_up.issubset(self.by_id))
        for field_id in required_follow_up:
            field = self.by_id[field_id]
            self.assertIn("follow_up", field["encounters"])
            self.assertEqual("required", field["requiredness"])

    def test_sensitive_conditional_topics_are_never_universally_required(self) -> None:
        for field_id in (
            "listening.odor",
            "inquiry.emotion_stress",
            "inquiry.menstrual_reproductive",
            "inquiry.pregnancy_lactation",
            "palpation.other_findings",
        ):
            field = self.by_id[field_id]
            self.assertIn(field["requiredness"], {"conditional", "optional"})
            self.assertTrue(field["allow_unknown"])
            self.assertIsNotNone(field["condition"])

    def test_doctor_judgment_cannot_be_authored_or_reviewed_by_ai(self) -> None:
        doctor_fields = [
            field for field in self.fields if field["evidence_tag"] == "doctor_judgment"
        ]
        self.assertGreaterEqual(len(doctor_fields), 8)
        for field in doctor_fields:
            self.assertEqual(["doctor"], field["source_roles"], field["field_id"])
            self.assertEqual("doctor", field["review_owner"], field["field_id"])

    def test_unknown_states_and_provenance_are_first_class(self) -> None:
        self.assertEqual(
            {"unknown", "not_collected", "not_applicable", "declined"},
            set(self.contract["unknown_states"]),
        )
        envelope = self.contract["value_envelope"]
        self.assertTrue(
            {"value", "source_role", "author_id", "recorded_at", "review_status"}
            .issubset(envelope["required"])
        )
        self.assertIn("signed", envelope["review_status_values"])
        self.assertTrue(all(field["allow_unknown"] for field in self.fields if field["section"] != "encounter"))

    def test_contract_contains_no_direct_identifier_fields(self) -> None:
        forbidden = {"patient_name", "phone", "id_card", "exact_address", "wechat_id"}
        field_ids = {field["field_id"].split(".")[-1] for field in self.fields}
        self.assertTrue(forbidden.isdisjoint(field_ids))
        self.assertEqual("SYN-", self.contract["subject_code_prefix"])


if __name__ == "__main__":
    unittest.main()
