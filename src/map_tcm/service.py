from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

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
            self.store.audit(
                connection,
                event_type="encounter.created",
                actor_id=actor_id,
                object_type="encounter",
                object_id=encounter_id,
                metadata={"patient_id": patient_id, "encounter_type": encounter_type},
            )
        return {"id": encounter_id, "patient_id": patient_id, "status": "draft"}

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
            self.store.audit(
                connection,
                event_type="observation.created",
                actor_id=actor_id,
                object_type="observation",
                object_id=observation_id,
                metadata={"encounter_id": encounter_id, "source": source},
            )
        return {"id": observation_id, "review_status": review_status, "source": source}

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
            self.store.audit(
                connection,
                event_type="assessment.created",
                actor_id=actor_id,
                object_type="assessment",
                object_id=assessment_id,
                metadata={"encounter_id": encounter_id, "doctor_authored": True},
            )
        return {"id": assessment_id, "status": "doctor_reviewed"}

    def transition_encounter(
        self,
        *,
        encounter_id: str,
        target_status: str,
        actor_id: str,
        actor_role: str,
    ) -> dict[str, Any]:
        self._require_doctor(actor_role)
        with self.store.connect() as connection:
            encounter = self._get_encounter(connection, encounter_id)
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
            connection.execute(
                """
                UPDATE encounters
                SET status = ?, doctor_id = ?, version = version + 1, updated_at = ?
                WHERE id = ?
                """,
                (target_status, actor_id, utc_now(), encounter_id),
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
                metadata={"from": current_status, "to": target_status},
            )
        return {"id": encounter_id, "status": target_status}

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
        import json

        return json.dumps(value, ensure_ascii=False, sort_keys=True)

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
        if actor_role != "doctor":
            raise DomainError("doctor_required", "该操作只能由医生完成。", status=403)
