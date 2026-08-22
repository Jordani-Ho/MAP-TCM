from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from .clinical_fields import ClinicalFieldRegistryService
from .errors import DomainError
from .store import SQLiteStore, utc_now


SYNTHETIC_CODE = re.compile(r"^SYN-[A-Z0-9-]{3,}$")
ENCOUNTER_TYPES = {"initial", "follow_up", "check_in", "review"}
OBSERVATION_SOURCES = {"patient", "doctor", "file", "ai", "system"}
ALLOWED_TRANSITIONS = {
    "draft": {"doctor_reviewed"},
    "doctor_reviewed": {"signed"},
    "signed": {"withdrawn"},
    "withdrawn": set(),
}


class ClinicalService:
    def __init__(self, store: SQLiteStore) -> None:
        self.store = store
        self.field_registry = ClinicalFieldRegistryService(store)

    @staticmethod
    def new_id(prefix: str) -> str:
        return f"{prefix}_{uuid4().hex[:16]}"

    def create_patient(
        self,
        *,
        display_code: str,
        is_synthetic: bool,
        actor_id: str,
    ) -> dict[str, Any]:
        if not is_synthetic or not SYNTHETIC_CODE.fullmatch(display_code):
            raise DomainError(
                "synthetic_only",
                "Alpha 工程环境只接受以 SYN- 开头的合成患者编号。",
            )
        patient_id = self.new_id("pt")
        created_at = utc_now()
        try:
            with self.store.connect() as connection:
                connection.execute(
                    "INSERT INTO patients (id, display_code, is_synthetic, created_at) VALUES (?, ?, 1, ?)",
                    (patient_id, display_code, created_at),
                )
                self.store.audit(
                    connection,
                    event_type="patient.created",
                    actor_id=actor_id,
                    object_type="patient",
                    object_id=patient_id,
                    metadata={"display_code": display_code, "is_synthetic": True},
                )
        except sqlite3.IntegrityError as error:
            raise DomainError("duplicate_patient", "合成患者编号已存在。") from error
        return {"id": patient_id, "display_code": display_code, "is_synthetic": True}

    def bind_patient_security_subject(
        self,
        *,
        patient_id: str,
        patient_subject_id: str,
        actor_id: str,
    ) -> dict[str, Any]:
        """Bind a legacy patient record to exactly one server security subject.

        Gate 1 keeps the original ``patients`` table as a compatibility
        projection, but clinical authorization and Vault ownership use this
        immutable key instead of joining on a display code.
        """
        with self.store.connect() as connection:
            patient = connection.execute(
                "SELECT * FROM patients WHERE id = ? AND is_synthetic = 1",
                (patient_id,),
            ).fetchone()
            subject = connection.execute(
                """
                SELECT * FROM security_subjects
                WHERE id = ? AND role = 'patient' AND is_synthetic = 1
                """,
                (patient_subject_id,),
            ).fetchone()
            if patient is None:
                raise DomainError("patient_not_found", "合成患者记录不存在。", status=404)
            if subject is None:
                raise DomainError(
                    "patient_security_subject_required",
                    "患者记录必须绑定有效的服务端患者安全主体。",
                    status=403,
                )
            if subject["account_status"] != "active" or subject["verification_status"] != "verified":
                raise DomainError(
                    "patient_security_subject_inactive",
                    "患者安全主体尚未激活并核验。",
                    status=403,
                )
            if patient["display_code"] != subject["display_code"]:
                raise DomainError(
                    "patient_subject_identity_mismatch",
                    "患者记录编号与安全主体不一致。",
                    status=403,
                )

            existing = connection.execute(
                "SELECT * FROM patient_record_subject_bindings WHERE patient_id = ?",
                (patient_id,),
            ).fetchone()
            if existing is not None and existing["patient_subject_id"] != patient_subject_id:
                raise DomainError(
                    "patient_record_already_bound",
                    "患者记录已经绑定其他安全主体。",
                    status=409,
                )
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO patient_record_subject_bindings (
                        patient_id, patient_subject_id, is_synthetic, bound_by, bound_at
                    ) VALUES (?, ?, 1, ?, ?)
                    """,
                    (patient_id, patient_subject_id, actor_id, utc_now()),
                )

            vault = connection.execute(
                """
                SELECT * FROM vaults
                WHERE owner_subject_id = ? AND vault_type = 'patient_private'
                """,
                (patient_subject_id,),
            ).fetchone()
            if vault is None:
                compatibility_vault = connection.execute(
                    """
                    SELECT * FROM patient_private_vaults
                    WHERE patient_subject_code = ?
                    """,
                    (patient["display_code"],),
                ).fetchone()
                vault_id = (
                    compatibility_vault["id"]
                    if compatibility_vault is not None
                    else self.new_id("pvault")
                )
                if compatibility_vault is None:
                    connection.execute(
                        """
                        INSERT INTO patient_private_vaults (
                            id, patient_subject_code, is_synthetic, status, created_at
                        ) VALUES (?, ?, 1, 'active', ?)
                        """,
                        (vault_id, patient["display_code"], utc_now()),
                    )
                connection.execute(
                    """
                    INSERT INTO vaults (
                        id, vault_type, owner_subject_id, patient_private_vault_id,
                        key_reference, is_synthetic, status, created_at
                    ) VALUES (?, 'patient_private', ?, ?, NULL, 1, 'active', ?)
                    """,
                    (vault_id, patient_subject_id, vault_id, utc_now()),
                )
            else:
                vault_id = vault["id"]

            self.store.audit(
                connection,
                event_type="patient.record_subject_bound",
                actor_id=actor_id,
                object_type="patient",
                object_id=patient_id,
                metadata={
                    "patient_subject_id": patient_subject_id,
                    "vault_id": vault_id,
                    "synthetic": True,
                },
            )
        return {
            "patient_id": patient_id,
            "patient_subject_id": patient_subject_id,
            "vault_id": vault_id,
        }

    def grant_consent(
        self,
        *,
        patient_id: str,
        purpose: str,
        scope: list[str],
        actor_id: str,
        valid_days: int = 30,
    ) -> dict[str, Any]:
        if not purpose.strip() or not scope:
            raise DomainError("invalid_consent", "授权目的和范围不能为空。")
        consent_id = self.new_id("consent")
        effective_at = utc_now()
        expires_at = (datetime.now(UTC) + timedelta(days=valid_days)).isoformat(timespec="seconds")
        with self.store.connect() as connection:
            patient = connection.execute("SELECT id FROM patients WHERE id = ?", (patient_id,)).fetchone()
            if patient is None:
                raise DomainError("patient_not_found", "患者不存在。", status=404)
            connection.execute(
                """
                INSERT INTO consents (
                    id, patient_id, purpose, scope_json, effective_at, expires_at, revoked_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?)
                """,
                (consent_id, patient_id, purpose, self._json(scope), effective_at, expires_at, effective_at),
            )
            self.store.audit(
                connection,
                event_type="consent.granted",
                actor_id=actor_id,
                object_type="consent",
                object_id=consent_id,
                metadata={"patient_id": patient_id, "purpose": purpose, "scope": scope},
            )
        return {"id": consent_id, "patient_id": patient_id, "expires_at": expires_at}

    def create_encounter(
        self,
        *,
        patient_id: str,
        encounter_type: str,
        chief_complaint: str,
        actor_id: str,
        occurred_at: str | None = None,
    ) -> dict[str, Any]:
        if encounter_type not in ENCOUNTER_TYPES:
            raise DomainError("invalid_encounter_type", "不支持的就诊类型。")
        if not chief_complaint.strip():
            raise DomainError("missing_chief_complaint", "主诉不能为空。")
        encounter_id = self.new_id("enc")
        now = utc_now()
        with self.store.connect() as connection:
            if not self._has_active_consent(connection, patient_id):
                raise DomainError("consent_required", "没有有效授权，不能创建就诊记录。", status=403)
            connection.execute(
                """
                INSERT INTO encounters (
                    id, patient_id, encounter_type, status, occurred_at, doctor_id,
                    chief_complaint, version, created_at, updated_at
                ) VALUES (?, ?, ?, 'draft', ?, NULL, ?, 1, ?, ?)
                """,
                (encounter_id, patient_id, encounter_type, occurred_at or now, chief_complaint.strip(), now, now),
            )
            record_version_id = self.new_id("record_version")
            connection.execute(
                """
                INSERT INTO clinical_record_versions (
                    id, encounter_id, version_number, status, supersedes_version_id,
                    created_by, created_at, reviewed_by, reviewed_at, signed_by, signed_at
                ) VALUES (?, ?, 1, 'draft', NULL, ?, ?, NULL, NULL, NULL, NULL)
                """,
                (record_version_id, encounter_id, actor_id, now),
            )
            self._insert_field_value(
                connection,
                record_version_id=record_version_id,
                field_id="chief_complaint",
                value={"text": chief_complaint.strip()},
                source_type="patient",
                source_ref=f"synthetic://encounter/{encounter_id}/chief-complaint",
                source_anchor={"encounter_id": encounter_id, "field": "chief_complaint"},
                author_id=actor_id,
            )
            self.store.audit(
                connection,
                event_type="encounter.created",
                actor_id=actor_id,
                object_type="encounter",
                object_id=encounter_id,
                metadata={"patient_id": patient_id, "encounter_type": encounter_type},
            )
        return {
            "id": encounter_id,
            "patient_id": patient_id,
            "status": "draft",
            "record_version_id": record_version_id,
            "version_number": 1,
        }

    def add_observation(
        self,
        *,
        encounter_id: str,
        category: str,
        content: str,
        source: str,
        actor_id: str,
    ) -> dict[str, Any]:
        if source not in OBSERVATION_SOURCES:
            raise DomainError("invalid_source", "观察来源无效。")
        if not category.strip() or not content.strip():
            raise DomainError("invalid_observation", "观察类别和内容不能为空。")
        observation_id = self.new_id("obs")
        recorded_at = utc_now()
        review_status = "doctor_reviewed" if source == "doctor" else "draft"
        with self.store.connect() as connection:
            encounter = self._get_encounter(connection, encounter_id)
            if encounter["status"] != "draft":
                raise DomainError("encounter_locked", "只有草稿就诊可以新增观察。")
            version = self._current_record_version(connection, encounter)
            connection.execute(
                """
                INSERT INTO observations (
                    id, encounter_id, category, content, source, author_id, recorded_at, review_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    observation_id,
                    encounter_id,
                    category.strip(),
                    content.strip(),
                    source,
                    actor_id,
                    recorded_at,
                    review_status,
                ),
            )
            self._insert_field_value(
                connection,
                record_version_id=version["id"],
                field_id=f"observation.{observation_id}",
                value={
                    "category": category.strip(),
                    "content": content.strip(),
                    "source": source,
                },
                source_type=source,
                source_ref=f"synthetic://observation/{observation_id}",
                source_anchor={"observation_id": observation_id, "encounter_id": encounter_id},
                author_id=actor_id,
            )
            self.store.audit(
                connection,
                event_type="observation.created",
                actor_id=actor_id,
                object_type="observation",
                object_id=observation_id,
                metadata={"encounter_id": encounter_id, "source": source},
            )
        return {
            "id": observation_id,
            "review_status": review_status,
            "source": source,
            "record_version_id": version["id"],
        }

    def add_structured_field(
        self,
        *,
        encounter_id: str,
        field_id: str,
        value: Any = None,
        unknown_state: str | None = None,
        source_role: str,
        actor_id: str,
        actor_role: str,
        review_status: str = "draft",
    ) -> dict[str, Any]:
        """Append one TCM-CLIN-001 candidate value to the draft record.

        The registry is intentionally only a candidate gate. This method does
        not turn the contract into a clinical standard and never accepts AI as
        the formal source of a field value.
        """
        with self.store.connect() as connection:
            encounter = self._get_encounter(connection, encounter_id)
            if encounter["status"] != "draft":
                raise DomainError("encounter_locked", "只有草稿就诊可以追加结构化字段。")
            validated = self.field_registry.validate_value(
                connection,
                encounter_type=encounter["encounter_type"],
                field_id=field_id,
                value=value,
                unknown_state=unknown_state,
                source_role=source_role,
                review_status=review_status,
                actor_role=actor_role,
            )
            version = self._current_record_version(connection, encounter)
            envelope = (
                {"unknown_state": unknown_state}
                if unknown_state is not None
                else {"value": value}
            )
            field_value_id = self._insert_field_value(
                connection,
                record_version_id=version["id"],
                field_id=field_id,
                value=envelope,
                source_type=source_role,
                source_ref=f"synthetic://structured-field/{field_id}/{self.new_id('source')}",
                source_anchor={
                    "encounter_id": encounter_id,
                    "record_version_id": version["id"],
                    "field_id": field_id,
                },
                author_id=actor_id,
            )
            connection.execute(
                """
                INSERT INTO clinical_field_registry_links (
                    field_value_id, registry_version_id, field_id, unknown_state,
                    evidence_tag, source_role, review_status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    field_value_id,
                    validated["registry_version_id"],
                    field_id,
                    unknown_state,
                    validated["evidence_tag"],
                    source_role,
                    review_status,
                    utc_now(),
                ),
            )
            self.store.audit(
                connection,
                event_type="clinical.structured_field.appended",
                actor_id=actor_id,
                object_type="clinical_field_value",
                object_id=field_value_id,
                metadata={
                    "encounter_id": encounter_id,
                    "record_version_id": version["id"],
                    "field_id": field_id,
                    "source_role": source_role,
                    "review_status": review_status,
                    "unknown_state": unknown_state,
                    "contract_status": validated["contract_status"],
                    "clinical_approval": False,
                },
            )
        return {
            "id": field_value_id,
            "field_id": field_id,
            "record_version_id": version["id"],
            "review_status": review_status,
            "unknown_state": unknown_state,
            "contract_status": validated["contract_status"],
            "clinical_approval": False,
        }

    def candidate_field_readiness(self, encounter_id: str) -> dict[str, Any]:
        return self.field_registry.readiness(encounter_id)

    def add_assessment(
        self,
        *,
        encounter_id: str,
        pattern: str,
        mechanism: str,
        rationale: str,
        treatment_principle: str,
        prescription_summary: str,
        actor_id: str,
        actor_role: str,
    ) -> dict[str, Any]:
        self._require_doctor(actor_role)
        values = [pattern, mechanism, rationale, treatment_principle]
        if any(not value.strip() for value in values):
            raise DomainError("incomplete_assessment", "证候、病机、依据和治则不能为空。")
        assessment_id = self.new_id("assessment")
        with self.store.connect() as connection:
            encounter = self._get_encounter(connection, encounter_id)
            if encounter["status"] != "draft":
                raise DomainError("encounter_locked", "只有草稿就诊可以新增医生判断。")
            version = self._current_record_version(connection, encounter)
            connection.execute(
                """
                INSERT INTO assessments (
                    id, encounter_id, pattern, mechanism, rationale, treatment_principle,
                    prescription_summary, author_id, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'doctor_reviewed', ?)
                """,
                (
                    assessment_id,
                    encounter_id,
                    pattern.strip(),
                    mechanism.strip(),
                    rationale.strip(),
                    treatment_principle.strip(),
                    prescription_summary.strip(),
                    actor_id,
                    utc_now(),
                ),
            )
            self._insert_field_value(
                connection,
                record_version_id=version["id"],
                field_id=f"doctor_assessment.{assessment_id}",
                value={
                    "pattern": pattern.strip(),
                    "mechanism": mechanism.strip(),
                    "rationale": rationale.strip(),
                    "treatment_principle": treatment_principle.strip(),
                    "prescription_summary": prescription_summary.strip(),
                },
                source_type="doctor",
                source_ref=f"synthetic://assessment/{assessment_id}",
                source_anchor={"assessment_id": assessment_id, "encounter_id": encounter_id},
                author_id=actor_id,
            )
            self.store.audit(
                connection,
                event_type="assessment.created",
                actor_id=actor_id,
                object_type="assessment",
                object_id=assessment_id,
                metadata={"encounter_id": encounter_id, "doctor_authored": True},
            )
        return {
            "id": assessment_id,
            "status": "doctor_reviewed",
            "record_version_id": version["id"],
        }

    def transition_encounter(
        self,
        *,
        encounter_id: str,
        target_status: str,
        actor_id: str,
        actor_role: str,
        reason: str = "",
    ) -> dict[str, Any]:
        self._require_doctor(actor_role)
        with self.store.connect() as connection:
            encounter = self._get_encounter(connection, encounter_id)
            record_version = self._current_record_version(connection, encounter)
            current_status = encounter["status"]
            if target_status not in ALLOWED_TRANSITIONS[current_status]:
                raise DomainError(
                    "invalid_transition",
                    f"不能把就诊从 {current_status} 变更为 {target_status}。",
                )
            if target_status == "doctor_reviewed":
                observation_count = connection.execute(
                    "SELECT COUNT(*) FROM observations WHERE encounter_id = ?", (encounter_id,)
                ).fetchone()[0]
                if observation_count == 0:
                    raise DomainError("observation_required", "审核前至少需要一条来源明确的观察。")
            if target_status == "signed":
                assessment_count = connection.execute(
                    "SELECT COUNT(*) FROM assessments WHERE encounter_id = ?", (encounter_id,)
                ).fetchone()[0]
                if assessment_count == 0:
                    raise DomainError("assessment_required", "签署前需要医生填写临床判断。")
            snapshot_id = None
            now = utc_now()
            if target_status == "doctor_reviewed":
                connection.execute(
                    """
                    UPDATE clinical_record_versions
                    SET status = 'doctor_reviewed', reviewed_by = ?, reviewed_at = ?
                    WHERE id = ? AND status = 'draft'
                    """,
                    (actor_id, now, record_version["id"]),
                )
            elif target_status == "signed":
                connection.execute(
                    """
                    UPDATE clinical_record_versions
                    SET status = 'signed', signed_by = ?, signed_at = ?
                    WHERE id = ? AND status = 'doctor_reviewed'
                    """,
                    (actor_id, now, record_version["id"]),
                )
                snapshot_id = self._create_responsibility_snapshot(
                    connection,
                    record_version_id=record_version["id"],
                    signed_by=actor_id,
                )
            elif target_status == "withdrawn":
                withdrawal_reason = reason.strip() or "合成记录撤回"
                withdrawal_id = self.new_id("record_withdrawal")
                connection.execute(
                    """
                    INSERT INTO clinical_record_withdrawal_events (
                        id, encounter_id, signed_version_id, reason, actor_id, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        withdrawal_id,
                        encounter_id,
                        record_version["id"],
                        withdrawal_reason,
                        actor_id,
                        now,
                    ),
                )
            connection.execute(
                """
                UPDATE encounters
                SET status = ?, doctor_id = ?, version = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    target_status,
                    actor_id,
                    record_version["version_number"],
                    now,
                    encounter_id,
                ),
            )
            if target_status in {"doctor_reviewed", "signed"}:
                connection.execute(
                    "UPDATE observations SET review_status = ? WHERE encounter_id = ?",
                    (target_status, encounter_id),
                )
                connection.execute(
                    "UPDATE assessments SET status = ? WHERE encounter_id = ?",
                    (target_status, encounter_id),
                )
            self.store.audit(
                connection,
                event_type=f"encounter.{target_status}",
                actor_id=actor_id,
                object_type="encounter",
                object_id=encounter_id,
                metadata={
                    "from": current_status,
                    "to": target_status,
                    "record_version_id": record_version["id"],
                    "version_number": record_version["version_number"],
                    "responsibility_snapshot_id": snapshot_id,
                },
            )
        return {
            "id": encounter_id,
            "status": target_status,
            "record_version_id": record_version["id"],
            "version_number": record_version["version_number"],
            "responsibility_snapshot_id": snapshot_id,
        }

    def start_revision(
        self,
        *,
        encounter_id: str,
        reason: str,
        actor_id: str,
        actor_role: str,
    ) -> dict[str, Any]:
        self._require_doctor(actor_role)
        if not reason.strip():
            raise DomainError("revision_reason_required", "修订必须说明原因。")
        with self.store.connect() as connection:
            encounter = self._get_encounter(connection, encounter_id)
            if encounter["status"] not in {"signed", "withdrawn"}:
                raise DomainError("signed_version_required", "只有已签署记录可以开始新修订。")
            previous = self._current_record_version(connection, encounter)
            if previous["status"] != "signed":
                raise DomainError("signed_version_required", "修订必须基于已签署版本。")
            next_number = int(previous["version_number"]) + 1
            next_id = self.new_id("record_version")
            now = utc_now()
            connection.execute(
                """
                INSERT INTO clinical_record_versions (
                    id, encounter_id, version_number, status, supersedes_version_id,
                    created_by, created_at, reviewed_by, reviewed_at, signed_by, signed_at
                ) VALUES (?, ?, ?, 'draft', ?, ?, ?, NULL, NULL, NULL, NULL)
                """,
                (next_id, encounter_id, next_number, previous["id"], actor_id, now),
            )
            previous_fields = connection.execute(
                """
                SELECT * FROM clinical_field_values
                WHERE record_version_id = ? ORDER BY created_at, id
                """,
                (previous["id"],),
            ).fetchall()
            for field in previous_fields:
                new_field_id = self.new_id("field_value")
                connection.execute(
                    """
                    INSERT INTO clinical_field_values (
                        id, record_version_id, field_id, value_json, value_hash,
                        source_type, source_ref, source_anchor_json, author_id,
                        supersedes_field_value_id, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_field_id,
                        next_id,
                        field["field_id"],
                        field["value_json"],
                        field["value_hash"],
                        field["source_type"],
                        field["source_ref"],
                        field["source_anchor_json"],
                        actor_id,
                        field["id"],
                        now,
                    ),
                )
                prior_source = connection.execute(
                    """
                    SELECT source_object_id, source_anchor_id, link_status
                    FROM clinical_field_source_links WHERE field_value_id = ?
                    """,
                    (field["id"],),
                ).fetchone()
                if prior_source is None:
                    connection.execute(
                        """
                        INSERT INTO clinical_field_source_links (
                            field_value_id, source_object_id, source_anchor_id,
                            link_status, created_at
                        ) VALUES (?, NULL, NULL, 'source_pending', ?)
                        """,
                        (new_field_id, now),
                    )
                else:
                    connection.execute(
                        """
                        INSERT INTO clinical_field_source_links (
                            field_value_id, source_object_id, source_anchor_id,
                            link_status, created_at
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            new_field_id,
                            prior_source["source_object_id"],
                            prior_source["source_anchor_id"],
                            prior_source["link_status"],
                            now,
                        ),
                    )
            connection.execute(
                """
                UPDATE encounters
                SET status = 'draft', version = ?, doctor_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (next_number, actor_id, now, encounter_id),
            )
            self.store.audit(
                connection,
                event_type="encounter.revision_started",
                actor_id=actor_id,
                object_type="encounter",
                object_id=encounter_id,
                metadata={
                    "reason": reason.strip(),
                    "record_version_id": next_id,
                    "version_number": next_number,
                    "supersedes_version_id": previous["id"],
                    "copied_field_count": len(previous_fields),
                },
            )
        return {
            "id": encounter_id,
            "status": "draft",
            "record_version_id": next_id,
            "version_number": next_number,
            "supersedes_version_id": previous["id"],
        }

    def seed_synthetic_demo(self) -> None:
        with self.store.connect() as connection:
            patient = connection.execute(
                "SELECT id FROM patients WHERE display_code = 'SYN-DEMO-001'"
            ).fetchone()
        if patient is not None:
            return
        patient_record = self.create_patient(
            display_code="SYN-DEMO-001", is_synthetic=True, actor_id="system_seed"
        )
        self.grant_consent(
            patient_id=patient_record["id"],
            purpose="Alpha 合成流程演示",
            scope=["clinical_record", "follow_up", "audit"],
            actor_id="system_seed",
            valid_days=365,
        )
        encounter = self.create_encounter(
            patient_id=patient_record["id"],
            encounter_type="initial",
            chief_complaint="合成示例：近三日睡眠不稳（非真实患者资料）",
            actor_id="system_seed",
        )
        self.add_observation(
            encounter_id=encounter["id"],
            category="问诊",
            content="合成示例：入睡延迟，晨起精神一般。",
            source="patient",
            actor_id="synthetic_patient",
        )

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _canonical_json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _content_hash(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def _insert_field_value(
        self,
        connection: sqlite3.Connection,
        *,
        record_version_id: str,
        field_id: str,
        value: Any,
        source_type: str,
        source_ref: str,
        source_anchor: dict[str, Any],
        author_id: str,
        supersedes_field_value_id: str | None = None,
    ) -> str:
        if source_type not in OBSERVATION_SOURCES:
            raise DomainError("invalid_source", "字段来源类型无效。")
        if not source_ref.startswith("synthetic://"):
            raise DomainError("synthetic_source_required", "Gate 1 字段来源必须使用合成引用。")
        value_json = self._canonical_json(value)
        value_hash = self._content_hash(value_json)
        field_value_id = self.new_id("field_value")
        connection.execute(
            """
            INSERT INTO clinical_field_values (
                id, record_version_id, field_id, value_json, value_hash,
                source_type, source_ref, source_anchor_json, author_id,
                supersedes_field_value_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                field_value_id,
                record_version_id,
                field_id,
                value_json,
                value_hash,
                source_type,
                source_ref,
                self._canonical_json(source_anchor),
                author_id,
                supersedes_field_value_id,
                utc_now(),
            ),
        )
        source_context = connection.execute(
            """
            SELECT v.id AS vault_id
            FROM clinical_record_versions rv
            JOIN encounters e ON e.id = rv.encounter_id
            JOIN patient_record_subject_bindings b ON b.patient_id = e.patient_id
            JOIN vaults v
              ON v.owner_subject_id = b.patient_subject_id
             AND v.vault_type = 'patient_private'
             AND v.status = 'active'
            WHERE rv.id = ?
            """,
            (record_version_id,),
        ).fetchone()
        author = connection.execute(
            "SELECT id FROM security_subjects WHERE id = ? AND is_synthetic = 1",
            (author_id,),
        ).fetchone()
        if source_context is None or author is None:
            connection.execute(
                """
                INSERT INTO clinical_field_source_links (
                    field_value_id, source_object_id, source_anchor_id,
                    link_status, created_at
                ) VALUES (?, NULL, NULL, 'source_pending', ?)
                """,
                (field_value_id, utc_now()),
            )
            return field_value_id

        source_object_id = self.new_id("source_object")
        source_anchor_id = self.new_id("source_anchor")
        source_object_type = {
            "file": "document",
            "patient": "structured_form",
            "doctor": "structured_form",
            "ai": "structured_form",
            "system": "structured_form",
        }[source_type]
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
                f"clinical-field:{field_value_id}",
                source_context["vault_id"],
                source_object_type,
                source_ref,
                value_hash,
                author_id,
                utc_now(),
            ),
        )
        connection.execute(
            """
            INSERT INTO source_anchors (
                id, source_object_id, source_object_version, anchor_type,
                anchor_json, extracted_text, confidence, transform_chain_json,
                created_at
            ) VALUES (?, ?, 1, 'object_version', ?, NULL, NULL, '[]', ?)
            """,
            (
                source_anchor_id,
                source_object_id,
                self._canonical_json(source_anchor),
                utc_now(),
            ),
        )
        connection.execute(
            """
            INSERT INTO clinical_field_source_links (
                field_value_id, source_object_id, source_anchor_id,
                link_status, created_at
            ) VALUES (?, ?, ?, 'anchored', ?)
            """,
            (field_value_id, source_object_id, source_anchor_id, utc_now()),
        )
        return field_value_id

    def _current_record_version(
        self, connection: sqlite3.Connection, encounter: sqlite3.Row
    ) -> sqlite3.Row:
        current = connection.execute(
            """
            SELECT * FROM clinical_record_versions
            WHERE encounter_id = ? ORDER BY version_number DESC LIMIT 1
            """,
            (encounter["id"],),
        ).fetchone()
        if current is not None:
            return current

        version_id = self.new_id("record_version")
        migrated_status = (
            encounter["status"]
            if encounter["status"] in {"draft", "doctor_reviewed", "signed"}
            else "signed"
        )
        actor_id = encounter["doctor_id"] or "system_projection_migration"
        now = utc_now()
        connection.execute(
            """
            INSERT INTO clinical_record_versions (
                id, encounter_id, version_number, status, supersedes_version_id,
                created_by, created_at, reviewed_by, reviewed_at, signed_by, signed_at
            ) VALUES (?, ?, 1, ?, NULL, ?, ?, ?, ?, ?, ?)
            """,
            (
                version_id,
                encounter["id"],
                migrated_status,
                actor_id,
                encounter["created_at"],
                actor_id if migrated_status in {"doctor_reviewed", "signed"} else None,
                now if migrated_status in {"doctor_reviewed", "signed"} else None,
                actor_id if migrated_status == "signed" else None,
                now if migrated_status == "signed" else None,
            ),
        )
        self._insert_field_value(
            connection,
            record_version_id=version_id,
            field_id="chief_complaint",
            value={"text": encounter["chief_complaint"]},
            source_type="patient",
            source_ref=f"synthetic://encounter/{encounter['id']}/chief-complaint",
            source_anchor={"encounter_id": encounter["id"], "field": "chief_complaint"},
            author_id=actor_id,
        )
        observations = connection.execute(
            "SELECT * FROM observations WHERE encounter_id = ? ORDER BY recorded_at, id",
            (encounter["id"],),
        ).fetchall()
        for observation in observations:
            self._insert_field_value(
                connection,
                record_version_id=version_id,
                field_id=f"observation.{observation['id']}",
                value={
                    "category": observation["category"],
                    "content": observation["content"],
                    "source": observation["source"],
                },
                source_type=observation["source"],
                source_ref=f"synthetic://observation/{observation['id']}",
                source_anchor={
                    "observation_id": observation["id"],
                    "encounter_id": encounter["id"],
                },
                author_id=observation["author_id"],
            )
        assessments = connection.execute(
            "SELECT * FROM assessments WHERE encounter_id = ? ORDER BY created_at, id",
            (encounter["id"],),
        ).fetchall()
        for assessment in assessments:
            self._insert_field_value(
                connection,
                record_version_id=version_id,
                field_id=f"doctor_assessment.{assessment['id']}",
                value={
                    "pattern": assessment["pattern"],
                    "mechanism": assessment["mechanism"],
                    "rationale": assessment["rationale"],
                    "treatment_principle": assessment["treatment_principle"],
                    "prescription_summary": assessment["prescription_summary"],
                },
                source_type="doctor",
                source_ref=f"synthetic://assessment/{assessment['id']}",
                source_anchor={
                    "assessment_id": assessment["id"],
                    "encounter_id": encounter["id"],
                },
                author_id=assessment["author_id"],
            )
        if migrated_status == "signed":
            self._create_responsibility_snapshot(
                connection, record_version_id=version_id, signed_by=actor_id
            )
        self.store.audit(
            connection,
            event_type="clinical_record.version_projection_migrated",
            actor_id="system_projection_migration",
            object_type="clinical_record_version",
            object_id=version_id,
            metadata={
                "encounter_id": encounter["id"],
                "version_number": 1,
                "status": migrated_status,
            },
        )
        return connection.execute(
            "SELECT * FROM clinical_record_versions WHERE id = ?", (version_id,)
        ).fetchone()

    def _create_responsibility_snapshot(
        self,
        connection: sqlite3.Connection,
        *,
        record_version_id: str,
        signed_by: str,
    ) -> str:
        existing = connection.execute(
            """
            SELECT id FROM clinical_record_responsibility_snapshots
            WHERE record_version_id = ?
            """,
            (record_version_id,),
        ).fetchone()
        if existing is not None:
            return existing["id"]
        fields = connection.execute(
            """
            SELECT f.id, f.field_id, f.value_hash, f.source_type, f.source_ref,
                   f.source_anchor_json, f.supersedes_field_value_id,
                   l.source_object_id, l.source_anchor_id, l.link_status
            FROM clinical_field_values f
            LEFT JOIN clinical_field_source_links l ON l.field_value_id = f.id
            WHERE f.record_version_id = ? ORDER BY f.field_id, f.created_at, f.id
            """,
            (record_version_id,),
        ).fetchall()
        if not fields:
            raise DomainError("record_fields_required", "签署版本必须至少包含一个来源字段。")
        manifest = [
            {
                "field_value_id": field["id"],
                "field_id": field["field_id"],
                "value_hash": field["value_hash"],
                "source_type": field["source_type"],
                "source_ref": field["source_ref"],
                "source_anchor": json.loads(field["source_anchor_json"]),
                "source_object_id": field["source_object_id"],
                "source_anchor_id": field["source_anchor_id"],
                "source_link_status": field["link_status"] or "source_pending",
                "supersedes_field_value_id": field["supersedes_field_value_id"],
            }
            for field in fields
        ]
        manifest_json = self._canonical_json(manifest)
        snapshot_id = self.new_id("record_snapshot")
        connection.execute(
            """
            INSERT INTO clinical_record_responsibility_snapshots (
                id, record_version_id, field_manifest_json, manifest_hash,
                signed_by, raw_source_content_persisted, created_at
            ) VALUES (?, ?, ?, ?, ?, 0, ?)
            """,
            (
                snapshot_id,
                record_version_id,
                manifest_json,
                self._content_hash(manifest_json),
                signed_by,
                utc_now(),
            ),
        )
        return snapshot_id

    @staticmethod
    def _has_active_consent(connection: sqlite3.Connection, patient_id: str) -> bool:
        now = utc_now()
        row = connection.execute(
            """
            SELECT 1 FROM consents
            WHERE patient_id = ? AND revoked_at IS NULL
              AND effective_at <= ? AND (expires_at IS NULL OR expires_at > ?)
            LIMIT 1
            """,
            (patient_id, now, now),
        ).fetchone()
        return row is not None

    @staticmethod
    def _get_encounter(connection: sqlite3.Connection, encounter_id: str) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM encounters WHERE id = ?", (encounter_id,)).fetchone()
        if row is None:
            raise DomainError("encounter_not_found", "就诊记录不存在。", status=404)
        return row

    @staticmethod
    def _require_doctor(actor_role: str) -> None:
        if actor_role != "verified_doctor":
            raise DomainError("doctor_required", "该操作只能由医生完成。", status=403)
