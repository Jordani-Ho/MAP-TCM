from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from map_tcm.agent_knowledge import AgentKnowledgeService
from map_tcm.agent_runtime import (
    PATIENT_TUTOR_INSTRUCTIONS,
    PHYSICIAN_APPRENTICE_INSTRUCTIONS,
    build_agent,
    validate_agent_input,
)
from map_tcm.errors import DomainError
from map_tcm.store import SQLiteStore


class AgentKnowledgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "agent-test.sqlite3"
        self.store = SQLiteStore(self.db_path)
        self.store.initialize()
        self.service = AgentKnowledgeService(self.store)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def create_physician_node(self) -> str:
        result = self.service.create_node(
            node_type="physician_apprentice",
            owner_subject_code="SYN-DOC-001",
            teacher_subject_code="SYN-DOC-001",
            actor_id="SYN-DOC-001",
        )
        return result["id"]

    def test_nodes_are_synthetic_and_physician_apprentice_requires_teacher(self) -> None:
        with self.assertRaisesRegex(DomainError, "SYN-"):
            self.service.create_node(
                node_type="patient_tutor",
                owner_subject_code="REAL-PATIENT",
                teacher_subject_code=None,
                actor_id="operator",
            )
        with self.assertRaisesRegex(DomainError, "老师"):
            self.service.create_node(
                node_type="physician_apprentice",
                owner_subject_code="SYN-DOC-001",
                teacher_subject_code=None,
                actor_id="SYN-DOC-001",
            )

    def test_only_bound_teacher_can_confirm_physician_framework(self) -> None:
        node_id = self.create_physician_node()
        with self.assertRaisesRegex(DomainError, "绑定医生"):
            self.service.add_knowledge_item(
                agent_node_id=node_id,
                knowledge_domain="physician_framework",
                title="动态人体图",
                content="先看病势与正邪关系，再决定是否干预。",
                source_ref="synthetic://teacher/session-1",
                source_layer="physician_view",
                access_level="private",
                rights_status="owned",
                status="teacher_confirmed",
                created_by="SYN-DOC-999",
            )

    def test_patient_search_cannot_see_private_physician_knowledge(self) -> None:
        node_id = self.create_physician_node()
        private = self.service.add_knowledge_item(
            agent_node_id=node_id,
            knowledge_domain="physician_experience",
            title="老师私有判断",
            content="睡眠变化要结合脉象和病势观察。",
            source_ref="synthetic://teacher/private-1",
            source_layer="physician_view",
            access_level="private",
            rights_status="owned",
            status="candidate",
            created_by="SYN-DOC-001",
        )
        teaching = self.service.add_knowledge_item(
            agent_node_id=node_id,
            knowledge_domain="patient_learning",
            title="患者可学的睡眠观察",
            content="连续记录睡眠变化，用于复诊时向老师说明趋势。",
            source_ref="synthetic://teacher/lesson-1",
            source_layer="physician_view",
            access_level="teaching_approved",
            rights_status="authorized",
            status="teacher_confirmed",
            created_by="SYN-DOC-001",
        )

        physician_hits = self.service.search(
            agent_node_id=node_id, query="睡眠", audience="physician"
        )
        patient_hits = self.service.search(
            agent_node_id=node_id, query="睡眠", audience="patient"
        )

        self.assertEqual({hit.id for hit in physician_hits}, {private["id"], teaching["id"]})
        self.assertEqual([hit.id for hit in patient_hits], [teaching["id"]])

    def test_framework_evolves_by_superseding_not_deleting(self) -> None:
        node_id = self.create_physician_node()
        first = self.service.add_knowledge_item(
            agent_node_id=node_id,
            knowledge_domain="physician_framework",
            title="辨证框架 1",
            content="先看寒热虚实。",
            source_ref="synthetic://teacher/framework-1",
            source_layer="physician_view",
            access_level="private",
            rights_status="owned",
            status="teacher_confirmed",
            created_by="SYN-DOC-001",
        )
        second = self.service.add_knowledge_item(
            agent_node_id=node_id,
            knowledge_domain="physician_framework",
            title="辨证框架 2",
            content="先形成整体图像，再看寒热虚实与病势。",
            source_ref="synthetic://teacher/framework-2",
            source_layer="physician_view",
            access_level="private",
            rights_status="owned",
            status="teacher_confirmed",
            created_by="SYN-DOC-001",
            supersedes_id=first["id"],
        )
        with self.store.connect() as connection:
            old = connection.execute(
                "SELECT status FROM agent_knowledge_items WHERE id = ?", (first["id"],)
            ).fetchone()
            count = connection.execute(
                "SELECT COUNT(*) FROM agent_knowledge_items WHERE agent_node_id = ?",
                (node_id,),
            ).fetchone()[0]
        self.assertEqual(old["status"], "superseded")
        self.assertEqual(second["version"], 2)
        self.assertEqual(count, 2)

    def test_unknown_rights_cannot_enter_original_layer(self) -> None:
        node_id = self.create_physician_node()
        with self.assertRaisesRegex(DomainError, "权利不明"):
            self.service.add_knowledge_item(
                agent_node_id=node_id,
                knowledge_domain="classical_source",
                title="来源不明原文",
                content="未经核验的文本。",
                source_ref="synthetic://source/unknown",
                source_layer="original",
                access_level="private",
                rights_status="unknown",
                status="candidate",
                created_by="SYN-DOC-001",
            )

    def test_ai_output_is_recorded_as_non_clinical_draft_and_audited(self) -> None:
        node_id = self.create_physician_node()
        draft = self.service.record_ai_draft(
            agent_node_id=node_id,
            role_type="physician_apprentice",
            input_summary="合成观察：睡眠变化。",
            output={"overall_image": "等待老师审阅"},
            cited_item_ids=[],
        )
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT status, clinical_authority FROM agent_run_drafts WHERE id = ?",
                (draft["id"],),
            ).fetchone()
            audit_count = connection.execute(
                "SELECT COUNT(*) FROM audit_events WHERE object_id = ?", (draft["id"],)
            ).fetchone()[0]
        self.assertEqual(dict(row), {"status": "ai_draft", "clinical_authority": 0})
        self.assertEqual(audit_count, 1)

    def test_machine_contract_and_role_prompts_keep_boundaries(self) -> None:
        contract_path = (
            Path(__file__).parents[1] / "schemas" / "agent" / "individual-agent-knowledge-v0.1.json"
        )
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        self.assertTrue(contract["synthetic_only"])
        self.assertFalse(contract["production_ready"])
        self.assertIn("不独立诊断", PHYSICIAN_APPRENTICE_INSTRUCTIONS)
        self.assertIn("不是增加聊天时长或依赖", PATIENT_TUTOR_INSTRUCTIONS)
        self.assertIn("不开方", PATIENT_TUTOR_INSTRUCTIONS)

    def test_runtime_blocks_real_or_direct_clinical_requests_before_model(self) -> None:
        with self.assertRaisesRegex(DomainError, "合成工程输入"):
            validate_agent_input(prompt="普通输入", is_synthetic=False)
        with self.assertRaisesRegex(DomainError, "真人医生"):
            validate_agent_input(prompt="这是合成案例，请直接开方", is_synthetic=True)

    def test_sdk_builds_one_focused_agent_without_calling_api(self) -> None:
        try:
            import agents  # noqa: F401
        except ImportError:
            self.skipTest("openai-agents optional dependency is not installed")
        node_id = self.create_physician_node()
        agent = build_agent(
            role_type="physician_apprentice",
            agent_node_id=node_id,
            knowledge=self.service,
            model="gpt-5.4-mini",
        )
        self.assertEqual(agent.name, "医师智能体徒弟")
        self.assertEqual(agent.model, "gpt-5.4-mini")
        self.assertEqual(len(agent.tools), 1)


if __name__ == "__main__":
    unittest.main()
