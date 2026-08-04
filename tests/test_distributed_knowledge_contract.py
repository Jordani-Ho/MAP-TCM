import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "schemas" / "map_distributed_knowledge_architecture_v0.1.json"


class DistributedKnowledgeContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))

    def test_contract_is_synthetic_and_not_clinical_authority(self) -> None:
        self.assertEqual("MAP-KNOW-003", self.contract["contract_id"])
        self.assertEqual("architecture_candidate", self.contract["status"])
        self.assertTrue(self.contract["synthetic_only"])
        self.assertFalse(self.contract["clinical_authority"])

    def test_three_subject_roles_are_not_conflated(self) -> None:
        self.assertEqual(
            {
                "knowledge_system": "epistemic_and_collaboration_subject",
                "patient": "data_consent_and_rights_subject",
                "licensed_physician": "clinical_decision_and_accountability_subject",
            },
            self.contract["semantic_subjects"],
        )

    def test_two_domains_have_distinct_append_only_rules(self) -> None:
        domains = {item["domain_id"]: item for item in self.contract["knowledge_domains"]}
        self.assertEqual({"immutable_foundation", "evolving_learning"}, set(domains))
        self.assertEqual("append_only_no_overwrite", domains["immutable_foundation"]["mutation_policy"])
        self.assertIn("patient_baseline_fact_event", domains["immutable_foundation"]["objects"])
        self.assertIn("classic_source_artifact", domains["immutable_foundation"]["objects"])
        self.assertEqual(
            "append_events_and_versioned_snapshots",
            domains["evolving_learning"]["mutation_policy"],
        )
        self.assertIn("personal_health_trend", domains["evolving_learning"]["objects"])
        self.assertIn("practitioner_performance_aggregate", domains["evolving_learning"]["objects"])
        self.assertTrue(all(not item["public_by_default"] for item in domains.values()))

    def test_personalized_nodes_and_commons_are_all_explicit(self) -> None:
        nodes = {item["node_type"]: item for item in self.contract["node_types"]}
        self.assertTrue(
            {
                "personal_health_agent_node",
                "doctor_agent_node",
                "care_institution_node",
                "commons_node",
            }.issubset(nodes)
        )
        self.assertTrue(nodes["personal_health_agent_node"]["personalized"])
        self.assertTrue(nodes["doctor_agent_node"]["personalized"])
        self.assertFalse(nodes["commons_node"]["personalized"])

    def test_federation_denies_raw_record_exchange_by_default(self) -> None:
        self.assertEqual("deny", self.contract["raw_identifiable_record_exchange_default"])
        required = set(self.contract["federation_envelope_required"])
        self.assertTrue(
            {
                "origin_node_id",
                "content_hash",
                "consent_or_license_reference",
                "access_level",
                "withdrawal_rule",
                "audit_proof",
            }.issubset(required)
        )

    def test_doctor_metrics_cannot_be_a_naive_success_rate(self) -> None:
        requirements = set(self.contract["doctor_performance_metric_requirements"])
        self.assertTrue(
            {
                "outcome_definition",
                "sample_size",
                "case_mix",
                "follow_up_window",
                "loss_to_follow_up_and_missingness",
                "adverse_events_failures_and_referrals",
                "statistical_method_and_uncertainty",
            }.issubset(requirements)
        )
        prohibited = set(self.contract["doctor_performance_prohibited_uses"])
        self.assertIn("public_leaderboard_by_default", prohibited)
        self.assertIn("treatment_guarantee", prohibited)
        self.assertIn("automatic_triage", prohibited)

    def test_personal_trends_do_not_become_automatic_diagnoses(self) -> None:
        self.assertIn(
            "no_automatic_causality_or_clinical_conclusion",
            self.contract["personal_trend_requirements"],
        )
        boundaries = set(self.contract["decision_boundaries"])
        self.assertIn("ai_must_not_diagnose_independently", boundaries)
        self.assertIn("ai_must_not_prescribe_independently", boundaries)
        self.assertIn("community_consensus_must_not_auto_apply_to_an_individual", boundaries)


if __name__ == "__main__":
    unittest.main()
