import tempfile
import unittest
from pathlib import Path

from map_tcm.errors import DomainError
from map_tcm.knowledge import KnowledgeService
from map_tcm.store import SQLiteStore


class KnowledgeServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self.temp_dir.name) / "knowledge.db")
        self.store.initialize()
        self.service = KnowledgeService(self.store)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def create_contribution(self, *, consent: bool = True) -> dict:
        contribution = self.service.create_contribution(
            subject_code="SYN-KNOW-001",
            is_synthetic=True,
            contributor_id="synthetic_patient_001",
            contributor_role="patient",
            content="合成观察：连续三日记录睡眠与晨起精神变化。",
            source_ref="synthetic://case/SYN-KNOW-001/day-3",
            access_level="commons_candidate",
            withdrawal_rule="撤回后停止新使用并追加撤回事件。",
            actor_id="synthetic_patient_001",
        )
        if consent:
            self.service.consent_contribution(
                contribution_id=contribution["id"],
                consent_reference="synthetic-consent://SYN-KNOW-001/v1",
                actor_id="synthetic_patient_001",
                actor_role="patient",
            )
        return contribution

    def create_claim(self) -> tuple[dict, dict]:
        contribution = self.create_contribution()
        claim = self.service.create_claim(
            contribution_id=contribution["id"],
            claim_kind="claim",
            statement="合成候选：规律记录可帮助发现睡眠变化趋势。",
            source_ref="synthetic://case/SYN-KNOW-001/summary",
            author_id="researcher_001",
            author_role="researcher",
        )
        return contribution, claim

    def test_rejects_non_synthetic_contribution(self) -> None:
        with self.assertRaisesRegex(DomainError, "SYN-"):
            self.service.create_contribution(
                subject_code="REAL-001",
                is_synthetic=False,
                contributor_id="patient_001",
                contributor_role="patient",
                content="not allowed",
                source_ref="local://not-allowed",
                access_level="commons_candidate",
                withdrawal_rule="withdraw",
                actor_id="patient_001",
            )

    def test_ai_cannot_authorize_a_contribution(self) -> None:
        contribution = self.service.create_contribution(
            subject_code="SYN-AI-001",
            is_synthetic=True,
            contributor_id="ai_agent_001",
            contributor_role="ai",
            content="AI candidate draft",
            source_ref="synthetic://ai/draft",
            access_level="commons_candidate",
            withdrawal_rule="human review required",
            actor_id="ai_agent_001",
        )
        with self.assertRaisesRegex(DomainError, "AI"):
            self.service.consent_contribution(
                contribution_id=contribution["id"],
                consent_reference="synthetic-consent://invalid-ai",
                actor_id="ai_agent_001",
                actor_role="ai",
            )

    def test_rejects_non_synthetic_source_reference(self) -> None:
        with self.assertRaisesRegex(DomainError, "synthetic://"):
            self.service.create_contribution(
                subject_code="SYN-SOURCE-001",
                is_synthetic=True,
                contributor_id="synthetic_patient_002",
                contributor_role="patient",
                content="Synthetic content with an invalid source reference.",
                source_ref="file://real-or-external-source",
                access_level="commons_candidate",
                withdrawal_rule="withdraw",
                actor_id="synthetic_patient_002",
            )

    def test_claim_requires_explicit_contribution_consent(self) -> None:
        contribution = self.create_contribution(consent=False)
        with self.assertRaisesRegex(DomainError, "授权"):
            self.service.create_claim(
                contribution_id=contribution["id"],
                claim_kind="claim",
                statement="unapproved candidate",
                source_ref="synthetic://unapproved",
                author_id="researcher_001",
                author_role="researcher",
            )

    def test_ai_review_is_advisory_and_two_doctors_validate(self) -> None:
        _contribution, claim = self.create_claim()
        self.service.submit_claim_for_review(
            claim_id=claim["id"],
            actor_id="knowledge_steward_001",
            actor_role="knowledge_steward",
        )
        ai_review = self.service.add_review(
            claim_id=claim["id"],
            verdict="support",
            rationale="AI synthetic pattern comparison; advisory only.",
            reviewer_id="ai_reviewer_001",
            reviewer_role="ai",
        )
        self.assertTrue(ai_review["advisory"])
        with self.assertRaisesRegex(DomainError, "医生"):
            self.service.create_consensus_snapshot(
                claim_id=claim["id"],
                target_state="provisional_knowledge",
                summary="AI support alone is insufficient.",
                actor_id="knowledge_steward_001",
                actor_role="knowledge_steward",
            )

        self.service.add_review(
            claim_id=claim["id"],
            verdict="support",
            rationale="Synthetic clinical review one.",
            reviewer_id="doctor_001",
            reviewer_role="doctor",
        )
        provisional = self.service.create_consensus_snapshot(
            claim_id=claim["id"],
            target_state="provisional_knowledge",
            summary="One-doctor synthetic provisional finding; not clinical guidance.",
            actor_id="knowledge_steward_001",
            actor_role="knowledge_steward",
        )
        self.assertEqual("provisional_knowledge", provisional["state"])
        with self.assertRaisesRegex(DomainError, "两名"):
            self.service.create_consensus_snapshot(
                claim_id=claim["id"],
                target_state="validated_snapshot",
                summary="Insufficient independent review.",
                actor_id="doctor_001",
                actor_role="doctor",
            )

        self.service.add_review(
            claim_id=claim["id"],
            verdict="support",
            rationale="Independent synthetic clinical review two.",
            reviewer_id="doctor_002",
            reviewer_role="doctor",
        )
        validated = self.service.create_consensus_snapshot(
            claim_id=claim["id"],
            target_state="validated_snapshot",
            summary="Two-doctor synthetic validation; scope remains synthetic.",
            actor_id="doctor_002",
            actor_role="doctor",
        )
        self.assertEqual("validated_snapshot", validated["state"])
        self.assertEqual(2, validated["doctor_support_count"])
        self.assertEqual(1, self.service.overview()["counts"]["validated_snapshot"])

    def test_counterclaim_can_move_reviewed_claim_to_disputed(self) -> None:
        contribution, claim = self.create_claim()
        self.service.submit_claim_for_review(
            claim_id=claim["id"],
            actor_id="doctor_001",
            actor_role="doctor",
        )
        counterclaim = self.service.create_claim(
            contribution_id=contribution["id"],
            claim_kind="counterclaim",
            parent_claim_id=claim["id"],
            statement="合成反例：短期记录不足以排除偶然波动。",
            source_ref="synthetic://case/SYN-KNOW-001/counterexample",
            author_id="researcher_002",
            author_role="researcher",
        )
        disputed = self.service.create_consensus_snapshot(
            claim_id=claim["id"],
            target_state="disputed",
            summary="存在有效合成反主张，保留双方观点。",
            actor_id="knowledge_steward_001",
            actor_role="knowledge_steward",
        )
        self.assertEqual("disputed", disputed["state"])
        self.assertEqual(1, disputed["active_counterclaim_count"])
        self.assertEqual("deidentified_candidate", counterclaim["state"])

    def test_withdrawal_retracts_claim_without_deleting_history(self) -> None:
        contribution, claim = self.create_claim()
        result = self.service.withdraw_contribution(
            contribution_id=contribution["id"],
            reason="Synthetic contributor withdrew future use.",
            actor_id="synthetic_patient_001",
            actor_role="patient",
        )
        self.assertEqual(1, result["retracted_claims"])
        overview = self.service.overview()
        self.assertEqual("withdrawn", overview["contributions"][0]["state"])
        self.assertEqual("retracted", overview["claims"][0]["state"])
        with self.store.connect() as connection:
            state_events = connection.execute(
                "SELECT COUNT(*) FROM knowledge_claim_state_events WHERE claim_id = ?",
                (claim["id"],),
            ).fetchone()[0]
            claim_rows = connection.execute(
                "SELECT COUNT(*) FROM knowledge_claims WHERE id = ?",
                (claim["id"],),
            ).fetchone()[0]
        self.assertEqual(2, state_events)
        self.assertEqual(1, claim_rows)
        self.assertGreaterEqual(len(self.store.overview()["audit"]), 5)


if __name__ == "__main__":
    unittest.main()
