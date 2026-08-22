from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from .errors import DomainError
from .identity import IdentityAccessService
from .store import SQLiteStore, utc_now


SYNTHETIC_CODE = re.compile(r"^SYN-[A-Z0-9-]{3,}$")
ENTRY_CATEGORIES = {
    "identity_profile",
    "birth_context",
    "family_history",
    "medical_record",
    "test_report",
    "prescription",
    "medication_history",
    "allergy_history",
    "procedure_history",
    "image",
    "video",
    "audio",
    "patient_observation",
    "lifestyle",
    "environment",
    "doctor_guidance",
    "outcome",
}
SOURCE_TYPES = {"patient", "doctor", "file", "device", "system", "external"}
EVENT_KINDS = {
    "identity_baseline",
    "clinical_encounter",
    "observation",
    "investigation",
    "intervention",
    "outcome",
    "lifestyle",
    "environment",
}
CONFIRMATION_BY_PROVENANCE = {
    "patient_reported": "patient",
    "doctor_confirmed": "doctor",
    "external_document": "file",
    "device_recorded": "device",
    "system_recorded": "system",
}
DERIVED_KINDS = {"summary", "trend", "reminder", "question", "learning_note"}
DERIVED_STATUS_BY_AUTHOR = {
    "ai": "ai_draft",
    "patient": "patient_confirmed",
    "doctor": "doctor_confirmed",
}
SOURCE_OBJECT_TYPES = {
    "text",
    "audio",
    "video",
    "image",
    "document",
    "structured_form",
    "external_reference",
}
SOURCE_ANCHOR_TYPES = {"text_range", "time_range", "page_bbox", "object_version"}


@dataclass(frozen=True)
class PatientMemoryHit:
    id: str
    memory_type: str
    title: str
    content: str
    occurred_at: str
    source_ref: str
    status: str


class PatientVaultService:
    """Synthetic patient-owned facts, timeline and derived knowledge.

    Gate 0 stores only synthetic JSON. Production media and identifiers require
    encrypted object storage, KMS, server sessions and approved retention rules.
    """

    def __init__(self, store: SQLiteStore) -> None:
        self.store = store

    @staticmethod
    def new_id(prefix: str) -> str:
        return f"{prefix}_{uuid4().hex[:16]}"

    @staticmethod
    def canonical_json(value: Any) -> str:
        try:
            return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise DomainError("invalid_vault_payload", "患者私域内容必须是可序列化结构。") from exc

    @staticmethod
    def content_hash(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _require_synthetic(code: str, field: str) -> None:
        if not SYNTHETIC_CODE.fullmatch(code):
            raise DomainError("synthetic_patient_vault_only", f"{field} 必须使用 SYN- 合成编号。")

    def create_vault(self, *, patient_subject_code: str, actor_id: str) -> dict[str, Any]:
        self._require_synthetic(patient_subject_code, "患者")
        vault_id = self.new_id("pvault")
        with self.store.connect() as connection:
            subject = connection.execute(
                """
                SELECT * FROM security_subjects
                WHERE display_code = ? AND role = 'patient'
                  AND account_status = 'active' AND verification_status = 'verified'
                  AND is_synthetic = 1
                """,
                (patient_subject_code,),
            ).fetchone()
            if subject is None:
                raise DomainError(
                    "patient_security_subject_required",
                    "患者私域必须属于已激活并核验的服务端患者安全主体。",
                    status=403,
                )
            existing = connection.execute(
                """
                SELECT p.id, p.status
                FROM patient_private_vaults p
                JOIN vaults v ON v.patient_private_vault_id = p.id
                WHERE v.owner_subject_id = ? AND v.vault_type = 'patient_private'
                """,
                (subject["id"],),
            ).fetchone()
            if existing is not None:
                return {
                    "id": existing["id"],
                    "patient_subject_code": patient_subject_code,
                    "owner_subject_id": subject["id"],
                    "status": existing["status"],
                }
            connection.execute(
                """
                INSERT INTO patient_private_vaults (
                    id, patient_subject_code, is_synthetic, status, created_at
                ) VALUES (?, ?, 1, 'active', ?)
                """,
                (vault_id, patient_subject_code, utc_now()),
            )
            connection.execute(
                """
                INSERT INTO vaults (
                    id, vault_type, owner_subject_id, patient_private_vault_id,
                    key_reference, is_synthetic, status, created_at
                ) VALUES (?, 'patient_private', ?, ?, NULL, 1, 'active', ?)
                """,
                (vault_id, subject["id"], vault_id, utc_now()),
            )
            self.store.audit(
                connection,
                event_type="patient.vault.created",
                actor_id=actor_id,
                object_type="patient_private_vault",
                object_id=vault_id,
                metadata={"patient_subject_code": patient_subject_code, "synthetic": True},
            )
        return {
            "id": vault_id,
            "patient_subject_code": patient_subject_code,
            "owner_subject_id": subject["id"],
            "status": "active",
        }

    def append_entry(
        self,
        *,
        vault_id: str,
        category: str,
        title: str,
        payload: dict[str, Any],
        source_type: str,
        source_ref: str,
        occurred_at: str,
        created_by: str,
        supersedes_id: str | None = None,
    ) -> dict[str, Any]:
        if category not in ENTRY_CATEGORIES:
            raise DomainError("invalid_patient_vault_category", "患者私域资料类别无效。")
        if source_type not in SOURCE_TYPES:
            raise DomainError("invalid_patient_vault_source", "患者私域资料来源无效。")
        if not title.strip() or not occurred_at.strip() or not created_by.strip():
            raise DomainError("invalid_patient_vault_entry", "标题、发生时间和创建者不能为空。")
        if not source_ref.startswith("synthetic://"):
            raise DomainError("synthetic_source_required", "Gate 0 来源必须使用 synthetic:// 合成引用。")
        if not payload:
            raise DomainError("patient_vault_payload_required", "患者私域内容不能为空。")

        payload_json = self.canonical_json(payload)
        entry_id = self.new_id("pentry")
        now = utc_now()
        with self.store.connect() as connection:
            vault = connection.execute(
                "SELECT status FROM patient_private_vaults WHERE id = ?", (vault_id,)
            ).fetchone()
            if vault is None:
                raise DomainError("patient_vault_not_found", "患者私域不存在。", status=404)
            if vault["status"] != "active":
                raise DomainError("patient_vault_inactive", "患者私域当前不可写入。")

            version = 1
            if supersedes_id:
                previous = connection.execute(
                    "SELECT * FROM patient_vault_entries WHERE id = ? AND vault_id = ?",
                    (supersedes_id, vault_id),
                ).fetchone()
                if previous is None:
                    raise DomainError("patient_entry_not_found", "被更正的原始资料不存在。")
                if previous["status"] != "active":
                    raise DomainError("patient_entry_inactive", "不能更正已经失效的资料版本。")
                if previous["category"] != category:
                    raise DomainError("patient_entry_category_mismatch", "更正版本必须保持资料类别。")
                version = int(previous["version"]) + 1
                connection.execute(
                    "UPDATE patient_vault_entries SET status = 'superseded' WHERE id = ?",
                    (supersedes_id,),
                )

            connection.execute(
                """
                INSERT INTO patient_vault_entries (
                    id, vault_id, category, title, payload_json, source_type,
                    source_ref, occurred_at, content_hash, version,
                    supersedes_id, status, created_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
                """,
                (
                    entry_id,
                    vault_id,
                    category,
                    title.strip(),
                    payload_json,
                    source_type,
                    source_ref,
                    occurred_at.strip(),
                    self.content_hash(payload_json),
                    version,
                    supersedes_id,
                    created_by.strip(),
                    now,
                ),
            )
            self.store.audit(
                connection,
                event_type="patient.vault.entry_appended",
                actor_id=created_by,
                object_type="patient_vault_entry",
                object_id=entry_id,
                metadata={
                    "vault_id": vault_id,
                    "category": category,
                    "version": version,
                    "supersedes_id": supersedes_id,
                },
            )
        return {"id": entry_id, "version": version, "status": "active"}

    def register_source_object(
        self,
        *,
        vault_id: str,
        source_type: str,
        storage_reference: str,
        content_fingerprint: Any,
        created_by_subject_id: str,
        object_key: str | None = None,
    ) -> dict[str, Any]:
        """Register an immutable synthetic source without storing its raw bytes."""
        if source_type not in SOURCE_OBJECT_TYPES:
            raise DomainError("invalid_source_object_type", "原始资料对象类型无效。")
        if not storage_reference.startswith("synthetic://"):
            raise DomainError(
                "synthetic_source_required",
                "Gate 1 原始资料存储引用必须使用 synthetic://。",
            )
        fingerprint_json = self.canonical_json(content_fingerprint)
        source_object_id = self.new_id("source_object")
        logical_key = object_key.strip() if object_key and object_key.strip() else source_object_id
        with self.store.connect() as connection:
            vault = connection.execute(
                "SELECT * FROM vaults WHERE id = ? AND status = 'active'",
                (vault_id,),
            ).fetchone()
            creator = connection.execute(
                "SELECT id FROM security_subjects WHERE id = ? AND is_synthetic = 1",
                (created_by_subject_id,),
            ).fetchone()
            if vault is None:
                raise DomainError("patient_vault_not_found", "患者私域不存在。", status=404)
            if creator is None:
                raise DomainError(
                    "source_creator_subject_required",
                    "原始资料必须记录服务端创建主体。",
                    status=403,
                )
            connection.execute(
                """
                INSERT INTO source_objects (
                    id, object_key, owner_vault_id, source_type, object_version,
                    storage_reference, content_hash, created_by_subject_id,
                    consent_grant_id, supersedes_source_object_id, status,
                    is_synthetic, created_at
                ) VALUES (?, ?, ?, ?, 1, ?, ?, ?, NULL, NULL, 'active', 1, ?)
                """,
                (
                    source_object_id,
                    logical_key,
                    vault_id,
                    source_type,
                    storage_reference,
                    self.content_hash(fingerprint_json),
                    created_by_subject_id,
                    utc_now(),
                ),
            )
            self.store.audit(
                connection,
                event_type="patient.source_object.registered",
                actor_id=created_by_subject_id,
                object_type="source_object",
                object_id=source_object_id,
                metadata={
                    "vault_id": vault_id,
                    "source_type": source_type,
                    "object_version": 1,
                    "raw_content_persisted": False,
                },
            )
        return {
            "id": source_object_id,
            "object_key": logical_key,
            "object_version": 1,
            "content_hash": self.content_hash(fingerprint_json),
            "raw_content_persisted": False,
        }

    def add_source_anchor(
        self,
        *,
        source_object_id: str,
        anchor_type: str,
        anchor: dict[str, Any],
        extracted_text: str | None = None,
        confidence: float | None = None,
        transform_chain: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if anchor_type not in SOURCE_ANCHOR_TYPES:
            raise DomainError("invalid_source_anchor_type", "来源锚点类型无效。")
        if not anchor:
            raise DomainError("source_anchor_required", "来源锚点不能为空。")
        if confidence is not None and not 0 <= confidence <= 1:
            raise DomainError("invalid_source_confidence", "来源置信度必须在 0 到 1 之间。")
        source_anchor_id = self.new_id("source_anchor")
        with self.store.connect() as connection:
            source = connection.execute(
                "SELECT * FROM source_objects WHERE id = ?",
                (source_object_id,),
            ).fetchone()
            if source is None:
                raise DomainError("source_object_not_found", "原始资料对象不存在。", status=404)
            connection.execute(
                """
                INSERT INTO source_anchors (
                    id, source_object_id, source_object_version, anchor_type,
                    anchor_json, extracted_text, confidence, transform_chain_json,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_anchor_id,
                    source_object_id,
                    source["object_version"],
                    anchor_type,
                    self.canonical_json(anchor),
                    extracted_text,
                    confidence,
                    self.canonical_json(transform_chain or []),
                    utc_now(),
                ),
            )
        return {
            "id": source_anchor_id,
            "source_object_id": source_object_id,
            "source_object_version": source["object_version"],
            "anchor_type": anchor_type,
        }

    def append_timeline_event(
        self,
        *,
        vault_id: str,
        primary_entry_id: str,
        event_kind: str,
        summary: str,
        provenance_role: str,
        confirmation_status: str,
        occurred_at: str,
        created_by: str,
    ) -> dict[str, Any]:
        if event_kind not in EVENT_KINDS:
            raise DomainError("invalid_timeline_event_kind", "健康时间线事件类型无效。")
        expected_role = CONFIRMATION_BY_PROVENANCE.get(confirmation_status)
        if expected_role is None or expected_role != provenance_role:
            raise DomainError("timeline_provenance_mismatch", "时间线确认状态与资料来源不一致。")
        if not summary.strip():
            raise DomainError("timeline_summary_required", "时间线摘要不能为空。")
        event_id = self.new_id("ptevent")
        with self.store.connect() as connection:
            entry = connection.execute(
                "SELECT vault_id FROM patient_vault_entries WHERE id = ?",
                (primary_entry_id,),
            ).fetchone()
            if entry is None or entry["vault_id"] != vault_id:
                raise DomainError("timeline_entry_mismatch", "时间线只能引用同一患者私域的资料。")
            connection.execute(
                """
                INSERT INTO patient_health_timeline_events (
                    id, vault_id, primary_entry_id, event_kind, summary,
                    provenance_role, confirmation_status, occurred_at,
                    created_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    vault_id,
                    primary_entry_id,
                    event_kind,
                    summary.strip(),
                    provenance_role,
                    confirmation_status,
                    occurred_at.strip(),
                    created_by.strip(),
                    utc_now(),
                ),
            )
            self.store.audit(
                connection,
                event_type="patient.timeline.event_appended",
                actor_id=created_by,
                object_type="patient_health_timeline_event",
                object_id=event_id,
                metadata={"vault_id": vault_id, "event_kind": event_kind},
            )
        return {"id": event_id, "confirmation_status": confirmation_status}

    def add_derived_knowledge(
        self,
        *,
        vault_id: str,
        knowledge_kind: str,
        content: str,
        source_entry_ids: list[str],
        author_role: str,
        status: str,
        created_by: str,
        supersedes_id: str | None = None,
    ) -> dict[str, Any]:
        if knowledge_kind not in DERIVED_KINDS:
            raise DomainError("invalid_patient_derived_kind", "患者派生知识类型无效。")
        if DERIVED_STATUS_BY_AUTHOR.get(author_role) != status:
            raise DomainError("invalid_patient_derived_status", "作者角色与派生知识状态不一致。")
        if not content.strip() or not source_entry_ids:
            raise DomainError("patient_derived_source_required", "派生知识必须有内容和原始资料来源。")
        knowledge_id = self.new_id("pderived")
        with self.store.connect() as connection:
            placeholders = ",".join("?" for _ in source_entry_ids)
            rows = connection.execute(
                f"SELECT id FROM patient_vault_entries WHERE vault_id = ? AND id IN ({placeholders})",
                (vault_id, *source_entry_ids),
            ).fetchall()
            if len(rows) != len(set(source_entry_ids)):
                raise DomainError("patient_derived_source_mismatch", "派生知识只能引用同一私域内存在的原始资料。")

            version = 1
            if supersedes_id:
                previous = connection.execute(
                    "SELECT * FROM patient_derived_knowledge WHERE id = ? AND vault_id = ?",
                    (supersedes_id, vault_id),
                ).fetchone()
                if previous is None or previous["status"] in {"superseded", "withdrawn"}:
                    raise DomainError("patient_derived_version_not_found", "被替代的派生知识版本不可用。")
                version = int(previous["version"]) + 1
                connection.execute(
                    "UPDATE patient_derived_knowledge SET status = 'superseded' WHERE id = ?",
                    (supersedes_id,),
                )

            connection.execute(
                """
                INSERT INTO patient_derived_knowledge (
                    id, vault_id, knowledge_kind, content, source_entry_ids_json,
                    author_role, status, clinical_authority, version,
                    supersedes_id, created_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)
                """,
                (
                    knowledge_id,
                    vault_id,
                    knowledge_kind,
                    content.strip(),
                    json.dumps(sorted(set(source_entry_ids)), ensure_ascii=False),
                    author_role,
                    status,
                    version,
                    supersedes_id,
                    created_by.strip(),
                    utc_now(),
                ),
            )
            self.store.audit(
                connection,
                event_type="patient.derived_knowledge.appended",
                actor_id=created_by,
                object_type="patient_derived_knowledge",
                object_id=knowledge_id,
                metadata={
                    "vault_id": vault_id,
                    "knowledge_kind": knowledge_kind,
                    "status": status,
                    "clinical_authority": False,
                },
            )
        return {
            "id": knowledge_id,
            "status": status,
            "version": version,
            "clinical_authority": False,
        }

    def search_for_patient_agent(
        self, *, agent_node_id: str, query: str, limit: int = 8
    ) -> list[PatientMemoryHit]:
        if not query.strip():
            raise DomainError("query_required", "患者记忆检索词不能为空。")
        terms = [part.lower() for part in re.split(r"\s+", query.strip()) if part]
        with self.store.connect() as connection:
            node = connection.execute(
                "SELECT node_type, owner_subject_code FROM agent_nodes WHERE id = ?",
                (agent_node_id,),
            ).fetchone()
            if node is None or node["node_type"] != "patient_tutor":
                raise DomainError("patient_agent_required", "只有患者自己的辅导智能体可以检索患者私域。")
            vault = connection.execute(
                "SELECT id FROM patient_private_vaults WHERE patient_subject_code = ? AND status = 'active'",
                (node["owner_subject_code"],),
            ).fetchone()
            if vault is None:
                return []
            entries = connection.execute(
                """
                SELECT id, category, title, payload_json, occurred_at, source_ref, status
                FROM patient_vault_entries
                WHERE vault_id = ? AND status = 'active'
                ORDER BY occurred_at DESC, created_at DESC
                """,
                (vault["id"],),
            ).fetchall()
            derived = connection.execute(
                """
                SELECT id, knowledge_kind, content, created_at, status
                FROM patient_derived_knowledge
                WHERE vault_id = ? AND status IN ('ai_draft', 'patient_confirmed', 'doctor_confirmed')
                ORDER BY created_at DESC
                """,
                (vault["id"],),
            ).fetchall()

        ranked: list[tuple[int, PatientMemoryHit]] = []
        for row in entries:
            content = row["payload_json"]
            haystack = f"{row['title']} {content}".lower()
            score = sum(haystack.count(term) for term in terms)
            if score:
                ranked.append(
                    (
                        score,
                        PatientMemoryHit(
                            id=row["id"],
                            memory_type=row["category"],
                            title=row["title"],
                            content=content,
                            occurred_at=row["occurred_at"],
                            source_ref=row["source_ref"],
                            status=row["status"],
                        ),
                    )
                )
        for row in derived:
            haystack = row["content"].lower()
            score = sum(haystack.count(term) for term in terms)
            if score:
                ranked.append(
                    (
                        score,
                        PatientMemoryHit(
                            id=row["id"],
                            memory_type=f"derived:{row['knowledge_kind']}",
                            title="派生知识",
                            content=row["content"],
                            occurred_at=row["created_at"],
                            source_ref="derived://patient-vault",
                            status=row["status"],
                        ),
                    )
                )
        ranked.sort(key=lambda item: item[1].occurred_at, reverse=True)
        ranked.sort(key=lambda item: item[0], reverse=True)
        return [hit for _, hit in ranked[: max(1, min(limit, 12))]]

    def open_physician_access_session(
        self,
        *,
        physician_agent_node_id: str,
        doctor_security_session_id: str,
        patient_subject_id: str,
        purpose: str,
        data_categories: list[str],
        valid_minutes: int = 60,
    ) -> dict[str, Any]:
        """Mount one patient's vault for one verified doctor's bound apprentice."""
        categories = sorted(set(data_categories))
        if not purpose.strip() or not categories:
            raise DomainError("physician_access_scope_required", "临时会诊必须声明目的和资料范围。")
        if valid_minutes <= 0 or valid_minutes > 240:
            raise DomainError("invalid_physician_access_duration", "临时会诊有效期必须为 1–240 分钟。")

        IdentityAccessService(self.store).authorize(
            session_id=doctor_security_session_id,
            capability="clinical_record.read",
            patient_subject_id=patient_subject_id,
            purpose=purpose,
            data_categories=categories,
            object_id="patient_private_vault",
        )

        access_id = self.new_id("paccess")
        now_text = utc_now()
        with self.store.connect() as connection:
            doctor_session = connection.execute(
                """
                SELECT s.expires_at, s.subject_id, p.display_code
                FROM security_sessions s
                JOIN security_subjects p ON p.id = s.subject_id
                WHERE s.id = ?
                """,
                (doctor_security_session_id,),
            ).fetchone()
            patient = connection.execute(
                "SELECT display_code FROM security_subjects WHERE id = ? AND role = 'patient'",
                (patient_subject_id,),
            ).fetchone()
            node = connection.execute(
                "SELECT node_type, teacher_subject_code FROM agent_nodes WHERE id = ?",
                (physician_agent_node_id,),
            ).fetchone()
            if doctor_session is None or patient is None:
                raise DomainError("physician_access_subject_missing", "临时会诊主体不存在。")
            if (
                node is None
                or node["node_type"] != "physician_apprentice"
                or node["teacher_subject_code"] != doctor_session["display_code"]
            ):
                raise DomainError("physician_agent_binding_mismatch", "医师智能体必须绑定当前登录医生。")
            vault = connection.execute(
                """
                SELECT id FROM patient_private_vaults
                WHERE patient_subject_code = ? AND status = 'active'
                """,
                (patient["display_code"],),
            ).fetchone()
            if vault is None:
                raise DomainError("patient_vault_not_found", "当前患者没有可用私域。", status=404)
            relationship = connection.execute(
                """
                SELECT * FROM care_relationships
                WHERE patient_subject_id = ? AND practitioner_subject_id = ?
                  AND status = 'active' AND effective_at <= ?
                  AND (expires_at IS NULL OR expires_at > ?)
                ORDER BY created_at DESC LIMIT 1
                """,
                (patient_subject_id, doctor_session["subject_id"], now_text, now_text),
            ).fetchone()
            if relationship is None:
                raise DomainError("active_relationship_required", "当前医生与患者没有有效照护关系。")
            consent = connection.execute(
                """
                SELECT * FROM security_consents
                WHERE relationship_id = ? AND patient_subject_id = ?
                  AND status = 'active' AND purpose = ? AND effective_at <= ?
                  AND (expires_at IS NULL OR expires_at > ?)
                ORDER BY created_at DESC LIMIT 1
                """,
                (relationship["id"], patient_subject_id, purpose, now_text, now_text),
            ).fetchone()
            if consent is None:
                raise DomainError("active_consent_required", "当前会诊缺少有效的系统授权依据。")

            requested_expiry = datetime.fromisoformat(now_text) + timedelta(minutes=valid_minutes)
            security_expiry = datetime.fromisoformat(doctor_session["expires_at"])
            expires_at = min(requested_expiry, security_expiry).isoformat(timespec="seconds")
            connection.execute(
                """
                INSERT INTO physician_patient_access_sessions (
                    id, physician_agent_node_id, doctor_security_session_id,
                    doctor_subject_id, patient_subject_id, vault_id,
                    care_relationship_id, consent_id, purpose, data_categories_json,
                    status, temporary_copy_persisted, started_at, expires_at, closed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', 0, ?, ?, NULL)
                """,
                (
                    access_id,
                    physician_agent_node_id,
                    doctor_security_session_id,
                    doctor_session["subject_id"],
                    patient_subject_id,
                    vault["id"],
                    relationship["id"],
                    consent["id"],
                    purpose.strip(),
                    json.dumps(categories, ensure_ascii=False),
                    now_text,
                    expires_at,
                ),
            )
            self.store.audit(
                connection,
                event_type="physician.patient_vault.access_opened",
                actor_id=doctor_session["subject_id"],
                object_type="physician_patient_access_session",
                object_id=access_id,
                metadata={
                    "physician_agent_node_id": physician_agent_node_id,
                    "patient_subject_id": patient_subject_id,
                    "purpose": purpose.strip(),
                    "data_categories": categories,
                    "temporary_copy_persisted": False,
                },
            )
        return {
            "id": access_id,
            "status": "active",
            "expires_at": expires_at,
            "temporary_copy_persisted": False,
        }

    def search_for_physician_session(
        self, *, access_session_id: str, query: str, limit: int = 12
    ) -> list[PatientMemoryHit]:
        """Read through an active mount without persisting the query or returned content."""
        if not query.strip():
            raise DomainError("query_required", "会诊资料检索词不能为空。")
        now = utc_now()
        terms = [part.lower() for part in re.split(r"\s+", query.strip()) if part]
        with self.store.connect() as connection:
            access = connection.execute(
                """
                SELECT a.*, ss.status AS security_status, ss.expires_at AS security_expires_at,
                       cr.status AS relationship_status, cr.expires_at AS relationship_expires_at,
                       sc.status AS consent_status, sc.expires_at AS consent_expires_at
                FROM physician_patient_access_sessions a
                JOIN security_sessions ss ON ss.id = a.doctor_security_session_id
                JOIN care_relationships cr ON cr.id = a.care_relationship_id
                JOIN security_consents sc ON sc.id = a.consent_id
                WHERE a.id = ?
                """,
                (access_session_id,),
            ).fetchone()
            if access is None:
                raise DomainError("physician_access_not_found", "临时会诊空间不存在。", status=404)
            if access["status"] != "active":
                raise DomainError("physician_access_inactive", "临时会诊空间已经关闭或失效。")
            if access["expires_at"] <= now:
                connection.execute(
                    "UPDATE physician_patient_access_sessions SET status = 'expired' WHERE id = ?",
                    (access_session_id,),
                )
                raise DomainError("physician_access_expired", "临时会诊空间已经过期。")
            if access["security_status"] != "active" or access["security_expires_at"] <= now:
                raise DomainError("doctor_session_inactive", "医生登录会话已失效。")
            if access["relationship_status"] != "active" or (
                access["relationship_expires_at"] is not None
                and access["relationship_expires_at"] <= now
            ):
                raise DomainError("care_relationship_inactive", "照护关系已失效。")
            if access["consent_status"] != "active" or (
                access["consent_expires_at"] is not None and access["consent_expires_at"] <= now
            ):
                raise DomainError("access_basis_inactive", "临时会诊的授权依据已失效。")

            categories = set(json.loads(access["data_categories_json"]))
            source_categories = sorted(categories & ENTRY_CATEGORIES)
            entries = []
            if source_categories:
                placeholders = ",".join("?" for _ in source_categories)
                entries = connection.execute(
                    f"""
                    SELECT id, category, title, payload_json, occurred_at, source_ref, status
                    FROM patient_vault_entries
                    WHERE vault_id = ? AND status = 'active'
                      AND category IN ({placeholders})
                    ORDER BY occurred_at DESC, created_at DESC
                    """,
                    (access["vault_id"], *source_categories),
                ).fetchall()
            derived = []
            if "derived_knowledge" in categories:
                derived = connection.execute(
                    """
                    SELECT id, knowledge_kind, content, created_at, status
                    FROM patient_derived_knowledge
                    WHERE vault_id = ?
                      AND status IN ('ai_draft', 'patient_confirmed', 'doctor_confirmed')
                    ORDER BY created_at DESC
                    """,
                    (access["vault_id"],),
                ).fetchall()

            ranked: list[tuple[int, PatientMemoryHit]] = []
            for row in entries:
                content = row["payload_json"]
                score = sum(f"{row['title']} {content}".lower().count(term) for term in terms)
                if score:
                    ranked.append(
                        (
                            score,
                            PatientMemoryHit(
                                id=row["id"],
                                memory_type=row["category"],
                                title=row["title"],
                                content=content,
                                occurred_at=row["occurred_at"],
                                source_ref=row["source_ref"],
                                status=row["status"],
                            ),
                        )
                    )
            for row in derived:
                score = sum(row["content"].lower().count(term) for term in terms)
                if score:
                    ranked.append(
                        (
                            score,
                            PatientMemoryHit(
                                id=row["id"],
                                memory_type=f"derived:{row['knowledge_kind']}",
                                title="患者派生知识",
                                content=row["content"],
                                occurred_at=row["created_at"],
                                source_ref="derived://patient-vault",
                                status=row["status"],
                            ),
                        )
                    )
            self.store.audit(
                connection,
                event_type="physician.patient_vault.read",
                actor_id=access["doctor_subject_id"],
                object_type="physician_patient_access_session",
                object_id=access_session_id,
                metadata={
                    "patient_subject_id": access["patient_subject_id"],
                    "result_count": len(ranked),
                    "data_categories": sorted(categories),
                    "query_or_content_persisted": False,
                },
            )
        ranked.sort(key=lambda item: item[1].occurred_at, reverse=True)
        ranked.sort(key=lambda item: item[0], reverse=True)
        return [hit for _, hit in ranked[: max(1, min(limit, 20))]]

    def create_responsibility_snapshot(
        self,
        *,
        access_session_id: str,
        source_entry_ids: list[str],
        created_by: str,
        ai_draft_id: str | None = None,
        final_record_ref: str | None = None,
    ) -> dict[str, Any]:
        if not source_entry_ids:
            raise DomainError("responsibility_sources_required", "责任快照必须记录所依据的资料版本。")
        snapshot_id = self.new_id("presp")
        with self.store.connect() as connection:
            access = connection.execute(
                "SELECT * FROM physician_patient_access_sessions WHERE id = ?",
                (access_session_id,),
            ).fetchone()
            if access is None or access["status"] != "active":
                raise DomainError("active_physician_access_required", "只能在有效会诊空间中形成责任快照。")
            placeholders = ",".join("?" for _ in source_entry_ids)
            rows = connection.execute(
                f"""
                SELECT id, category, content_hash, version
                FROM patient_vault_entries
                WHERE vault_id = ? AND id IN ({placeholders})
                """,
                (access["vault_id"], *source_entry_ids),
            ).fetchall()
            if len(rows) != len(set(source_entry_ids)):
                raise DomainError("responsibility_source_mismatch", "责任快照只能引用当前患者私域资料。")
            allowed = set(json.loads(access["data_categories_json"]))
            if any(row["category"] not in allowed for row in rows):
                raise DomainError("responsibility_source_out_of_scope", "责任快照包含会诊范围外资料。")
            manifest = sorted(
                (
                    {
                        "entry_id": row["id"],
                        "category": row["category"],
                        "content_hash": row["content_hash"],
                        "version": row["version"],
                    }
                    for row in rows
                ),
                key=lambda item: item["entry_id"],
            )
            connection.execute(
                """
                INSERT INTO physician_access_responsibility_snapshots (
                    id, access_session_id, source_manifest_json, ai_draft_id,
                    final_record_ref, raw_patient_content_persisted,
                    created_by, created_at
                ) VALUES (?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    snapshot_id,
                    access_session_id,
                    json.dumps(manifest, ensure_ascii=False, sort_keys=True),
                    ai_draft_id,
                    final_record_ref,
                    created_by.strip(),
                    utc_now(),
                ),
            )
            self.store.audit(
                connection,
                event_type="physician.patient_vault.responsibility_snapshot_created",
                actor_id=created_by,
                object_type="physician_access_responsibility_snapshot",
                object_id=snapshot_id,
                metadata={
                    "access_session_id": access_session_id,
                    "source_count": len(manifest),
                    "raw_patient_content_persisted": False,
                },
            )
        return {
            "id": snapshot_id,
            "source_count": len(manifest),
            "raw_patient_content_persisted": False,
        }

    def close_physician_access_session(
        self, *, access_session_id: str, doctor_security_session_id: str
    ) -> dict[str, Any]:
        with self.store.connect() as connection:
            access = connection.execute(
                "SELECT * FROM physician_patient_access_sessions WHERE id = ?",
                (access_session_id,),
            ).fetchone()
            if access is None:
                raise DomainError("physician_access_not_found", "临时会诊空间不存在。", status=404)
            if access["doctor_security_session_id"] != doctor_security_session_id:
                raise DomainError("physician_access_owner_mismatch", "不能关闭其他医生的会诊空间。")
            if access["status"] == "active":
                closed_at = utc_now()
                connection.execute(
                    """
                    UPDATE physician_patient_access_sessions
                    SET status = 'closed', closed_at = ? WHERE id = ?
                    """,
                    (closed_at, access_session_id),
                )
                self.store.audit(
                    connection,
                    event_type="physician.patient_vault.access_closed",
                    actor_id=access["doctor_subject_id"],
                    object_type="physician_patient_access_session",
                    object_id=access_session_id,
                    metadata={
                        "temporary_copy_persisted": False,
                        "raw_patient_content_persisted": False,
                    },
                )
        return {
            "id": access_session_id,
            "status": "closed",
            "temporary_copy_persisted": False,
        }
