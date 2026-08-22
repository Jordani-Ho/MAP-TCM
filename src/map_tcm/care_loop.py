from __future__ import annotations

import hashlib
import json
from typing import Any

from .errors import DomainError
from .service import ClinicalService
from .store import SQLiteStore, utc_now


EXECUTION_EVENT_TYPES = {"started", "completed", "missed", "stopped", "deviation"}
RESPONSE_CODES = {"improved", "unchanged", "worsened", "uncertain"}


class CareLoopService:
    """Deterministic synthetic care loop that remains usable with AI disabled."""

    def __init__(self, store: SQLiteStore, clinical: ClinicalService) -> None:
        self.store = store
        self.clinical = clinical

    def create_intervention_plan(
        self,
        *,
        encounter_id: str,
        action_summary: str,
        instructions: str,
        follow_up_due_at: str | None,
        actor_id: str,
        actor_role: str,
    ) -> dict[str, Any]:
        self.clinical._require_doctor(actor_role)
        if not action_summary.strip() or not instructions.strip():
            raise DomainError("intervention_plan_incomplete", "干预计划和执行说明不能为空。")
        with self.store.connect() as connection:
            encounter = self.clinical._get_encounter(connection, encounter_id)
            if encounter["status"] != "signed":
                raise DomainError(
                    "signed_record_required",
                    "只有医生签署后的记录可以形成干预计划。",
                    status=409,
                )
            record_version = self.clinical._current_record_version(connection, encounter)
            if record_version["status"] != "signed":
                raise DomainError("signed_record_required", "干预计划必须引用已签署记录。")
            previous = connection.execute(
                """
                SELECT MAX(version_number) FROM intervention_plan_versions
                WHERE encounter_id = ?
                """,
                (encounter_id,),
            ).fetchone()[0]
            version_number = (previous or 0) + 1
            plan_id = self.clinical.new_id("plan_version")
            payload = {
                "encounter_id": encounter_id,
                "signed_record_version_id": record_version["id"],
                "version_number": version_number,
                "action_summary": action_summary.strip(),
                "instructions": instructions.strip(),
                "follow_up_due_at": follow_up_due_at,
                "signed_by": actor_id,
            }
            connection.execute(
                """
                INSERT INTO intervention_plan_versions (
                    id, encounter_id, signed_record_version_id, version_number,
                    action_summary, instructions, follow_up_due_at, signed_by,
                    plan_hash, is_synthetic, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    plan_id,
                    encounter_id,
                    record_version["id"],
                    version_number,
                    action_summary.strip(),
                    instructions.strip(),
                    follow_up_due_at,
                    actor_id,
                    self._hash(payload),
                    utc_now(),
                ),
            )
            self.store.audit(
                connection,
                event_type="care_loop.intervention_plan.signed",
                actor_id=actor_id,
                object_type="intervention_plan_version",
                object_id=plan_id,
                metadata={
                    "encounter_id": encounter_id,
                    "signed_record_version_id": record_version["id"],
                    "version_number": version_number,
                    "synthetic": True,
                },
            )
        return {
            "id": plan_id,
            "encounter_id": encounter_id,
            "signed_record_version_id": record_version["id"],
            "version_number": version_number,
            "status": "signed",
        }

    def record_execution(
        self,
        *,
        plan_version_id: str,
        patient_subject_id: str,
        event_type: str,
        detail: dict[str, Any],
        occurred_at: str | None = None,
    ) -> dict[str, Any]:
        if event_type not in EXECUTION_EVENT_TYPES:
            raise DomainError("invalid_execution_event", "执行事件类型无效。")
        event_id = self.clinical.new_id("execution")
        with self.store.connect() as connection:
            self._require_plan_patient(connection, plan_version_id, patient_subject_id)
            event_payload = {
                "plan_version_id": plan_version_id,
                "patient_subject_id": patient_subject_id,
                "event_type": event_type,
                "detail": detail,
                "occurred_at": occurred_at or utc_now(),
            }
            connection.execute(
                """
                INSERT INTO intervention_execution_events (
                    id, plan_version_id, patient_subject_id, event_type, detail_json,
                    occurred_at, event_hash, is_synthetic, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    event_id,
                    plan_version_id,
                    patient_subject_id,
                    event_type,
                    self._json(detail),
                    event_payload["occurred_at"],
                    self._hash(event_payload),
                    utc_now(),
                ),
            )
            self.store.audit(
                connection,
                event_type="care_loop.execution.recorded",
                actor_id=patient_subject_id,
                object_type="intervention_execution_event",
                object_id=event_id,
                metadata={"plan_version_id": plan_version_id, "event_type": event_type},
            )
        return {"id": event_id, "plan_version_id": plan_version_id, "event_type": event_type}

    def record_feedback(
        self,
        *,
        plan_version_id: str,
        patient_subject_id: str,
        response_code: str,
        adverse_events: list[str],
        narrative: str,
    ) -> dict[str, Any]:
        if response_code not in RESPONSE_CODES:
            raise DomainError("invalid_response_code", "患者反馈结果代码无效。")
        feedback_id = self.clinical.new_id("feedback")
        with self.store.connect() as connection:
            self._require_plan_patient(connection, plan_version_id, patient_subject_id)
            payload = {
                "plan_version_id": plan_version_id,
                "patient_subject_id": patient_subject_id,
                "response_code": response_code,
                "adverse_events": adverse_events,
                "narrative": narrative.strip(),
            }
            connection.execute(
                """
                INSERT INTO patient_feedback_events (
                    id, plan_version_id, patient_subject_id, response_code,
                    adverse_events_json, narrative, feedback_hash, is_synthetic, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    feedback_id,
                    plan_version_id,
                    patient_subject_id,
                    response_code,
                    self._json(adverse_events),
                    narrative.strip(),
                    self._hash(payload),
                    utc_now(),
                ),
            )
            self.store.audit(
                connection,
                event_type="care_loop.patient_feedback.recorded",
                actor_id=patient_subject_id,
                object_type="patient_feedback_event",
                object_id=feedback_id,
                metadata={"plan_version_id": plan_version_id, "response_code": response_code},
            )
        return {"id": feedback_id, "plan_version_id": plan_version_id, "response_code": response_code}

    def create_follow_up_encounter(
        self,
        *,
        plan_version_id: str,
        patient_subject_id: str,
        chief_complaint: str,
    ) -> dict[str, Any]:
        with self.store.connect() as connection:
            context = self._require_plan_patient(connection, plan_version_id, patient_subject_id)
            encounter = self.clinical.create_encounter(
                patient_id=context["patient_id"],
                encounter_type="follow_up",
                chief_complaint=chief_complaint,
                actor_id=patient_subject_id,
            )
            connection.execute(
                """
                INSERT INTO follow_up_record_links (
                    follow_up_encounter_id, plan_version_id,
                    previous_signed_record_version_id, created_by, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    encounter["id"],
                    plan_version_id,
                    context["signed_record_version_id"],
                    patient_subject_id,
                    utc_now(),
                ),
            )
            self.store.audit(
                connection,
                event_type="care_loop.follow_up.created",
                actor_id=patient_subject_id,
                object_type="encounter",
                object_id=encounter["id"],
                metadata={
                    "plan_version_id": plan_version_id,
                    "previous_signed_record_version_id": context["signed_record_version_id"],
                    "ai_used": False,
                },
            )
        return encounter

    def project_patient_follow_up_fields(
        self,
        *,
        follow_up_encounter_id: str,
        patient_subject_id: str,
    ) -> dict[str, Any]:
        """Project append-only patient facts from execution and feedback events."""
        with self.store.connect() as connection:
            link = connection.execute(
                """
                SELECT * FROM follow_up_record_links WHERE follow_up_encounter_id = ?
                """,
                (follow_up_encounter_id,),
            ).fetchone()
            if link is None:
                raise DomainError("follow_up_link_required", "复诊没有关联上次签署记录。")
            self._require_plan_patient(connection, link["plan_version_id"], patient_subject_id)
            executions = connection.execute(
                """
                SELECT event_type, detail_json, occurred_at
                FROM intervention_execution_events
                WHERE plan_version_id = ? AND patient_subject_id = ?
                ORDER BY occurred_at, created_at
                """,
                (link["plan_version_id"], patient_subject_id),
            ).fetchall()
            feedback = connection.execute(
                """
                SELECT response_code, adverse_events_json, narrative, created_at
                FROM patient_feedback_events
                WHERE plan_version_id = ? AND patient_subject_id = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (link["plan_version_id"], patient_subject_id),
            ).fetchone()
            if feedback is None:
                raise DomainError("patient_feedback_required", "生成复诊比较前需要患者反馈。")
            counts = {event_type: 0 for event_type in sorted(EXECUTION_EVENT_TYPES)}
            for event in executions:
                counts[event["event_type"]] += 1
            adverse_events = json.loads(feedback["adverse_events_json"])
            field_specs = [
                (
                    "follow_up.interval_changes",
                    {
                        "patient_response": feedback["response_code"],
                        "narrative": feedback["narrative"],
                    },
                    None,
                ),
                (
                    "follow_up.intervention_exposure_adherence",
                    {"execution_event_counts": counts, "event_total": len(executions)},
                    None,
                ),
                (
                    "follow_up.response_outcome",
                    {"response_code": feedback["response_code"], "narrative": feedback["narrative"]},
                    None,
                ),
                ("follow_up.adverse_events", adverse_events, None),
                ("follow_up.new_diagnoses_medications", None, "not_collected"),
            ]
            created = []
            for field_id, value, unknown_state in field_specs:
                created.append(
                    self.clinical.add_structured_field(
                        encounter_id=follow_up_encounter_id,
                        field_id=field_id,
                        value=value,
                        unknown_state=unknown_state,
                        source_role="patient",
                        actor_id=patient_subject_id,
                        actor_role="patient",
                        review_status="draft",
                    )
                )
            self.store.audit(
                connection,
                event_type="care_loop.follow_up.patient_facts_projected",
                actor_id=patient_subject_id,
                object_type="encounter",
                object_id=follow_up_encounter_id,
                metadata={
                    "field_count": len(created),
                    "previous_signed_record_version_id": link["previous_signed_record_version_id"],
                    "ai_used": False,
                },
            )
        return {
            "follow_up_encounter_id": follow_up_encounter_id,
            "previous_signed_record_version_id": link["previous_signed_record_version_id"],
            "created_field_ids": [item["field_id"] for item in created],
            "ai_used": False,
        }

    def add_doctor_comparison(
        self,
        *,
        follow_up_encounter_id: str,
        comparison: str,
        actor_id: str,
        actor_role: str,
    ) -> dict[str, Any]:
        if not comparison.strip():
            raise DomainError("doctor_comparison_required", "医生前后比较不能为空。")
        with self.store.connect() as connection:
            link = connection.execute(
                "SELECT * FROM follow_up_record_links WHERE follow_up_encounter_id = ?",
                (follow_up_encounter_id,),
            ).fetchone()
            if link is None:
                raise DomainError("follow_up_link_required", "医生比较必须引用上次签署记录。")
        return self.clinical.add_structured_field(
            encounter_id=follow_up_encounter_id,
            field_id="follow_up.doctor_comparison",
            value={
                "comparison": comparison.strip(),
                "previous_signed_record_version_id": link["previous_signed_record_version_id"],
            },
            source_role="doctor",
            actor_id=actor_id,
            actor_role=actor_role,
            review_status="doctor_reviewed",
        )

    def _require_plan_patient(
        self,
        connection: Any,
        plan_version_id: str,
        patient_subject_id: str,
    ) -> Any:
        row = connection.execute(
            """
            SELECT p.id AS patient_id, b.patient_subject_id,
                   ipv.signed_record_version_id
            FROM intervention_plan_versions ipv
            JOIN encounters e ON e.id = ipv.encounter_id
            JOIN patients p ON p.id = e.patient_id AND p.is_synthetic = 1
            JOIN patient_record_subject_bindings b ON b.patient_id = p.id
            WHERE ipv.id = ?
            """,
            (plan_version_id,),
        ).fetchone()
        if row is None:
            raise DomainError("intervention_plan_not_found", "干预计划不存在。", status=404)
        if row["patient_subject_id"] != patient_subject_id:
            raise DomainError("plan_patient_mismatch", "不能写入其他患者的执行或反馈事件。", status=403)
        return row

    def plan_context(self, connection: Any, plan_version_id: str) -> dict[str, Any]:
        row = connection.execute(
            """
            SELECT ipv.id AS plan_version_id, ipv.signed_record_version_id,
                   p.id AS patient_id, b.patient_subject_id
            FROM intervention_plan_versions ipv
            JOIN encounters e ON e.id = ipv.encounter_id
            JOIN patients p ON p.id = e.patient_id AND p.is_synthetic = 1
            JOIN patient_record_subject_bindings b ON b.patient_id = p.id
            WHERE ipv.id = ?
            """,
            (plan_version_id,),
        ).fetchone()
        if row is None:
            raise DomainError("intervention_plan_not_found", "干预计划不存在。", status=404)
        return dict(row)

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @classmethod
    def _hash(cls, value: Any) -> str:
        return hashlib.sha256(cls._json(value).encode("utf-8")).hexdigest()
