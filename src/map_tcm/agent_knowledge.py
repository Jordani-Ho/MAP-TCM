from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from .errors import DomainError
from .store import SQLiteStore, utc_now


SYNTHETIC_CODE = re.compile(r"^SYN-[A-Z0-9-]{3,}$")
NODE_TYPES = {"physician_apprentice", "patient_tutor"}
KNOWLEDGE_DOMAINS = {
    "classical_source",
    "physician_framework",
    "physician_experience",
    "patient_learning",
    "patient_observation",
    "commons_reference",
}
SOURCE_LAYERS = {"original", "commentary", "physician_view", "ai_summary"}
ACCESS_LEVELS = {"private", "care_relationship", "teaching_approved", "commons"}
RIGHTS_STATES = {"owned", "authorized", "public_domain", "citation_only", "unknown"}
ITEM_STATES = {"candidate", "teacher_confirmed", "superseded", "withdrawn"}


@dataclass(frozen=True)
class KnowledgeHit:
    id: str
    title: str
    content: str
    source_ref: str
    source_layer: str
    knowledge_domain: str
    status: str


class AgentKnowledgeService:
    """Append-only synthetic knowledge nodes for one physician or patient agent."""

    def __init__(self, store: SQLiteStore) -> None:
        self.store = store

    @staticmethod
    def new_id(prefix: str) -> str:
        return f"{prefix}_{uuid4().hex[:16]}"

    @staticmethod
    def content_hash(content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    @staticmethod
    def _require_synthetic(code: str, field: str) -> None:
        if not SYNTHETIC_CODE.fullmatch(code):
            raise DomainError("synthetic_agent_only", f"{field} 必须使用 SYN- 合成编号。")

    def create_node(
        self,
        *,
        node_type: str,
        owner_subject_code: str,
        teacher_subject_code: str | None,
        actor_id: str,
    ) -> dict[str, Any]:
        if node_type not in NODE_TYPES:
            raise DomainError("invalid_agent_node_type", "智能体节点类型无效。")
        self._require_synthetic(owner_subject_code, "节点所有者")
        if node_type == "physician_apprentice":
            if not teacher_subject_code:
                raise DomainError("teacher_required", "医师徒弟节点必须绑定一位真人老师。")
            self._require_synthetic(teacher_subject_code, "老师")
        elif teacher_subject_code is not None:
            raise DomainError("unexpected_teacher", "患者辅导节点不能冒充某位医师徒弟。")

        node_id = self.new_id("anode")
        with self.store.connect() as connection:
            connection.execute(
                """
                INSERT INTO agent_nodes (
                    id, node_type, owner_subject_code, teacher_subject_code,
                    is_synthetic, status, created_at
                ) VALUES (?, ?, ?, ?, 1, 'draft', ?)
                """,
                (node_id, node_type, owner_subject_code, teacher_subject_code, utc_now()),
            )
            self.store.audit(
                connection,
                event_type="agent.node.created",
                actor_id=actor_id,
                object_type="agent_node",
                object_id=node_id,
                metadata={"node_type": node_type, "synthetic": True},
            )
        return {"id": node_id, "node_type": node_type, "status": "draft"}

    def add_knowledge_item(
        self,
        *,
        agent_node_id: str,
        knowledge_domain: str,
        title: str,
        content: str,
        source_ref: str,
        source_layer: str,
        access_level: str,
        rights_status: str,
        status: str,
        created_by: str,
        supersedes_id: str | None = None,
    ) -> dict[str, Any]:
        if knowledge_domain not in KNOWLEDGE_DOMAINS:
            raise DomainError("invalid_knowledge_domain", "智能体知识域无效。")
        if source_layer not in SOURCE_LAYERS:
            raise DomainError("invalid_source_layer", "知识来源层级无效。")
        if access_level not in ACCESS_LEVELS:
            raise DomainError("invalid_access_level", "知识访问级别无效。")
        if rights_status not in RIGHTS_STATES:
            raise DomainError("invalid_rights_status", "知识权利状态无效。")
        if status not in ITEM_STATES - {"superseded", "withdrawn"}:
            raise DomainError("invalid_initial_knowledge_state", "新知识只能是候选或老师确认状态。")
        if not all(value.strip() for value in (title, content, source_ref, created_by)):
            raise DomainError("invalid_knowledge_item", "标题、内容、来源和创建者不能为空。")
        if source_layer == "original" and rights_status == "unknown":
            raise DomainError("rights_review_required", "权利不明的原文不能进入原文层。")
        item_id = self.new_id("aknow")
        now = utc_now()
        with self.store.connect() as connection:
            node = connection.execute(
                "SELECT * FROM agent_nodes WHERE id = ?", (agent_node_id,)
            ).fetchone()
            if node is None:
                raise DomainError("agent_node_not_found", "智能体节点不存在。", status=404)
            if status == "teacher_confirmed" and knowledge_domain == "physician_framework":
                if created_by != node["teacher_subject_code"]:
                    raise DomainError("teacher_confirmation_required", "医师框架只能由绑定医生确认。")

            version = 1
            if supersedes_id:
                previous = connection.execute(
                    "SELECT * FROM agent_knowledge_items WHERE id = ? AND agent_node_id = ?",
                    (supersedes_id, agent_node_id),
                ).fetchone()
                if previous is None:
                    raise DomainError("superseded_item_not_found", "被替代的知识版本不存在。")
                if previous["status"] in {"superseded", "withdrawn"}:
                    raise DomainError("inactive_knowledge_version", "不能替代已经失效的知识版本。")
                version = int(previous["version"]) + 1
                connection.execute(
                    "UPDATE agent_knowledge_items SET status = 'superseded' WHERE id = ?",
                    (supersedes_id,),
                )

            connection.execute(
                """
                INSERT INTO agent_knowledge_items (
                    id, agent_node_id, knowledge_domain, title, content, source_ref,
                    source_layer, access_level, rights_status, content_hash, version,
                    supersedes_id, status, created_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item_id, agent_node_id, knowledge_domain, title.strip(), content.strip(),
                    source_ref.strip(), source_layer, access_level, rights_status,
                    self.content_hash(content.strip()), version, supersedes_id,
                    status, created_by.strip(), now,
                ),
            )
            self.store.audit(
                connection,
                event_type="agent.knowledge.appended",
                actor_id=created_by,
                object_type="agent_knowledge_item",
                object_id=item_id,
                metadata={
                    "agent_node_id": agent_node_id,
                    "knowledge_domain": knowledge_domain,
                    "version": version,
                    "status": status,
                    "supersedes_id": supersedes_id,
                },
            )
        return {"id": item_id, "version": version, "status": status}

    def search(
        self,
        *,
        agent_node_id: str,
        query: str,
        audience: str,
        limit: int = 6,
    ) -> list[KnowledgeHit]:
        if audience not in {"physician", "patient"}:
            raise DomainError("invalid_audience", "知识检索受众无效。")
        if not query.strip():
            raise DomainError("query_required", "检索词不能为空。")
        allowed_access = (
            ("private", "care_relationship", "teaching_approved", "commons")
            if audience == "physician"
            else ("teaching_approved", "commons")
        )
        placeholders = ",".join("?" for _ in allowed_access)
        terms = [part.lower() for part in re.split(r"\s+", query.strip()) if part]
        with self.store.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT id, title, content, source_ref, source_layer,
                       knowledge_domain, status
                FROM agent_knowledge_items
                WHERE agent_node_id = ?
                  AND status IN ('candidate', 'teacher_confirmed')
                  AND access_level IN ({placeholders})
                ORDER BY CASE status WHEN 'teacher_confirmed' THEN 0 ELSE 1 END,
                         created_at DESC
                """,
                (agent_node_id, *allowed_access),
            ).fetchall()
        ranked: list[tuple[int, KnowledgeHit]] = []
        for row in rows:
            haystack = f"{row['title']} {row['content']}".lower()
            score = sum(haystack.count(term) for term in terms)
            if score:
                ranked.append((score, KnowledgeHit(**dict(row))))
        ranked.sort(key=lambda item: (-item[0], item[1].title))
        return [item for _, item in ranked[: max(1, min(limit, 12))]]

    def record_ai_draft(
        self,
        *,
        agent_node_id: str,
        role_type: str,
        input_summary: str,
        output: dict[str, Any],
        cited_item_ids: list[str],
    ) -> dict[str, Any]:
        if role_type not in NODE_TYPES:
            raise DomainError("invalid_agent_role", "智能体角色无效。")
        draft_id = self.new_id("adraft")
        with self.store.connect() as connection:
            node = connection.execute(
                "SELECT node_type FROM agent_nodes WHERE id = ?", (agent_node_id,)
            ).fetchone()
            if node is None:
                raise DomainError("agent_node_not_found", "智能体节点不存在。", status=404)
            if node["node_type"] != role_type:
                raise DomainError("agent_role_mismatch", "运行角色与知识节点不一致。")
            connection.execute(
                """
                INSERT INTO agent_run_drafts (
                    id, agent_node_id, role_type, input_summary, output_json,
                    cited_item_ids_json, status, clinical_authority, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'ai_draft', 0, ?)
                """,
                (
                    draft_id, agent_node_id, role_type, input_summary.strip(),
                    json.dumps(output, ensure_ascii=False, sort_keys=True),
                    json.dumps(cited_item_ids, ensure_ascii=False), utc_now(),
                ),
            )
            self.store.audit(
                connection,
                event_type="agent.run.draft_recorded",
                actor_id=agent_node_id,
                object_type="agent_run_draft",
                object_id=draft_id,
                metadata={"role_type": role_type, "clinical_authority": False},
            )
        return {"id": draft_id, "status": "ai_draft", "clinical_authority": False}
