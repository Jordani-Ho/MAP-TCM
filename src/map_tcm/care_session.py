from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Callable

from .care_loop import CareLoopService
from .clinical_session import SessionAuthorizedClinicalService
from .errors import DomainError
from .identity import IdentityAccessService
from .service import ClinicalService
from .store import SQLiteStore, utc_now


IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")


class SessionAuthorizedCareLoopService:
    """Patient self-write boundary for append-only execution and feedback events."""

    def __init__(
        self,
        store: SQLiteStore,
        clinical: ClinicalService,
        care_loop: CareLoopService,
        security: IdentityAccessService,
        clinical_session: SessionAuthorizedClinicalService,
    ) -> None:
        self.store = store
        self.clinical = clinical
        self.care_loop = care_loop
        self.security = security
        self.clinical_session = clinical_session

    def record_execution(
        self,
        *,
        session_id: str,
        idempotency_key: str,
        plan_version_id: str,
        event_type: str,
        detail: dict[str, Any],
        occurred_at: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "plan_version_id": plan_version_id,
            "event_type": event_type,
            "detail": detail,
            "occurred_at": occurred_at,
        }
        return self._run_patient_write(
            session_id=session_id,
            idempotency_key=idempotency_key,
            plan_version_id=plan_version_id,
            operation_type="execution",
            request_payload=payload,
            data_categories=["intervention_execution"],
            result_object_type="intervention_execution_event",
            event_type="care_loop.execution.recorded",
            writer=lambda patient_subject_id: self.care_loop.record_execution(
                plan_version_id=plan_version_id,
                patient_subject_id=patient_subject_id,
                event_type=event_type,
                detail=detail,
                occurred_at=occurred_at,
            ),
        )

    def record_feedback(
        self,
        *,
        session_id: str,
        idempotency_key: str,
        plan_version_id: str,
        response_code: str,
        adverse_events: list[str],
        narrative: str,
    ) -> dict[str, Any]:
        payload = {
            "plan_version_id": plan_version_id,
            "response_code": response_code,
            "adverse_events": adverse_events,
            "narrative": narrative,
        }
        return self._run_patient_write(
            session_id=session_id,
            idempotency_key=idempotency_key,
            plan_version_id=plan_version_id,
            operation_type="feedback",
            request_payload=payload,
            data_categories=["patient_feedback"],
            result_object_type="patient_feedback_event",
            event_type="care_loop.patient_feedback.recorded",
            writer=lambda patient_subject_id: self.care_loop.record_feedback(
                plan_version_id=plan_version_id,
                patient_subject_id=patient_subject_id,
                response_code=response_code,
                adverse_events=adverse_events,
                narrative=narrative,
            ),
        )

    def _run_patient_write(
        self,
        *,
        session_id: str,
        idempotency_key: str,
        plan_version_id: str,
        operation_type: str,
        request_payload: dict[str, Any],
        data_categories: list[str],
        result_object_type: str,
        event_type: str,
        writer: Callable[[str], dict[str, Any]],
    ) -> dict[str, Any]:
        if not session_id.strip():
            raise DomainError("session_required", "患者写入必须提供服务端会话。", status=403)
        if not IDEMPOTENCY_KEY.fullmatch(idempotency_key):
            raise DomainError(
                "idempotency_key_required",
                "执行或反馈写入必须提供 8–128 位防重复键。",
            )
        request_hash = self._hash(request_payload)
        denial_reason: str | None = None
        conflict = False
        result: dict[str, Any] | None = None
        with self.store.connect() as connection:
            context = self.care_loop.plan_context(connection, plan_version_id)
            decision = self.security.authorize_in_transaction(
                connection,
                session_id=session_id,
                capability="care_event.write",
                patient_subject_id=context["patient_subject_id"],
                purpose="self_health_tracking",
                data_categories=data_categories,
                object_state="signed_plan",
                object_id=plan_version_id,
            )
            if not decision["allowed"]:
                denial_reason = decision["reason"]
            else:
                existing = connection.execute(
                    """
                    SELECT * FROM care_event_idempotency_records
                    WHERE patient_subject_id = ? AND operation_type = ?
                      AND idempotency_key = ?
                    """,
                    (decision["subject_id"], operation_type, idempotency_key),
                ).fetchone()
                if existing is not None:
                    if existing["request_hash"] != request_hash:
                        self.store.audit(
                            connection,
                            event_type="care_loop.idempotency.conflict",
                            actor_id=decision["subject_id"],
                            object_type=result_object_type,
                            object_id=existing["result_object_id"],
                            metadata={
                                "operation_type": operation_type,
                                "idempotency_key_hash": self._sha256(idempotency_key),
                            },
                        )
                        conflict = True
                    else:
                        result = json.loads(existing["response_json"])
                        result["idempotency_replayed"] = True
                else:
                    evidence_id = self.clinical_session._capture_authorization_evidence(
                        connection,
                        decision=decision,
                        session_id=session_id,
                        patient_subject_id=context["patient_subject_id"],
                        purpose="self_health_tracking",
                        data_categories=data_categories,
                        object_type="intervention_plan_version",
                        object_id=plan_version_id,
                        object_state="signed_plan",
                    )
                    result = writer(decision["subject_id"])
                    self.clinical_session._link_write_and_outbox(
                        connection,
                        authorization_evidence_id=evidence_id,
                        clinical_object_type=result_object_type,
                        clinical_object_id=result["id"],
                        action_type=f"patient_append:{operation_type}",
                        event_type=event_type,
                        result=result,
                    )
                    stored_response = {
                        "id": result["id"],
                        "plan_version_id": plan_version_id,
                        **(
                            {"event_type": result["event_type"]}
                            if operation_type == "execution"
                            else {"response_code": result["response_code"]}
                        ),
                        "idempotency_replayed": False,
                    }
                    connection.execute(
                        """
                        INSERT INTO care_event_idempotency_records (
                            id, patient_subject_id, operation_type, idempotency_key,
                            request_hash, result_object_type, result_object_id,
                            response_json, authorization_evidence_id, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            self.clinical.new_id("care_idempotency"),
                            decision["subject_id"],
                            operation_type,
                            idempotency_key,
                            request_hash,
                            result_object_type,
                            result["id"],
                            self._json(stored_response),
                            evidence_id,
                            utc_now(),
                        ),
                    )
                    result = stored_response
        if denial_reason is not None:
            raise DomainError(denial_reason, "患者事件写入被服务端授权拒绝。", status=403)
        if conflict:
            raise DomainError(
                "idempotency_conflict",
                "同一个防重复键不能用于不同内容。",
                status=409,
            )
        if result is None:
            raise DomainError("care_event_write_failed", "患者事件写入失败。", status=500)
        return result

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @classmethod
    def _hash(cls, value: Any) -> str:
        return cls._sha256(cls._json(value))

    @staticmethod
    def _sha256(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()
