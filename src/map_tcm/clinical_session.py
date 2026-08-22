from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any, Callable

from .errors import DomainError
from .identity import IdentityAccessService
from .service import ClinicalService
from .store import SQLiteStore, utc_now


class SessionAuthorizedClinicalService:
    """Server-session boundary for all HTTP clinical writes.

    The lower-level ClinicalService remains the synthetic domain primitive used
    by seed and migration code. HTTP callers must enter through this class so a
    client-supplied actor id or role can never become the authorization source.
    """

    def __init__(
        self,
        store: SQLiteStore,
        clinical: ClinicalService,
        security: IdentityAccessService,
    ) -> None:
        self.store = store
        self.clinical = clinical
        self.security = security

    def create_encounter(
        self,
        *,
        session_id: str,
        patient_id: str,
        encounter_type: str,
        chief_complaint: str,
        purpose: str,
        data_categories: list[str],
        client_role_claim: str | None = None,
    ) -> dict[str, Any]:
        return self._run_atomic_write(
            session_id=session_id,
            capability="clinical_record.write",
            context_resolver=lambda: (self._patient_subject_id(patient_id), None),
            purpose=purpose,
            data_categories=data_categories,
            authorization_object_type="patient",
            authorization_object_id=patient_id,
            clinical_object_type="encounter",
            action_type="create",
            event_type="clinical.encounter.created",
            client_role_claim=client_role_claim,
            writer=lambda decision: self.clinical.create_encounter(
                patient_id=patient_id,
                encounter_type=encounter_type,
                chief_complaint=chief_complaint,
                actor_id=decision["subject_id"],
            ),
        )

    def add_observation(
        self,
        *,
        session_id: str,
        encounter_id: str,
        category: str,
        content: str,
        source: str,
        purpose: str,
        data_categories: list[str],
        client_role_claim: str | None = None,
    ) -> dict[str, Any]:
        return self._run_atomic_write(
            session_id=session_id,
            capability="clinical_record.write",
            context_resolver=lambda: self._encounter_context(encounter_id),
            purpose=purpose,
            data_categories=data_categories,
            authorization_object_type="encounter",
            authorization_object_id=encounter_id,
            clinical_object_type="observation",
            action_type="append",
            event_type="clinical.observation.created",
            client_role_claim=client_role_claim,
            writer=lambda decision: self.clinical.add_observation(
                encounter_id=encounter_id,
                category=category,
                content=content,
                source=source,
                actor_id=decision["subject_id"],
            ),
        )

    def add_assessment(
        self,
        *,
        session_id: str,
        encounter_id: str,
        pattern: str,
        mechanism: str,
        rationale: str,
        treatment_principle: str,
        prescription_summary: str,
        purpose: str,
        data_categories: list[str],
        client_role_claim: str | None = None,
    ) -> dict[str, Any]:
        return self._run_atomic_write(
            session_id=session_id,
            capability="clinical_record.write",
            context_resolver=lambda: self._encounter_context(encounter_id),
            purpose=purpose,
            data_categories=data_categories,
            authorization_object_type="encounter",
            authorization_object_id=encounter_id,
            clinical_object_type="assessment",
            action_type="append",
            event_type="clinical.assessment.created",
            client_role_claim=client_role_claim,
            writer=lambda decision: self.clinical.add_assessment(
                encounter_id=encounter_id,
                pattern=pattern,
                mechanism=mechanism,
                rationale=rationale,
                treatment_principle=treatment_principle,
                prescription_summary=prescription_summary,
                actor_id=decision["subject_id"],
                actor_role=decision["server_role"],
            ),
        )

    def add_structured_field(
        self,
        *,
        session_id: str,
        encounter_id: str,
        field_id: str,
        value: Any,
        unknown_state: str | None,
        source_role: str,
        review_status: str,
        purpose: str,
        data_categories: list[str],
        client_role_claim: str | None = None,
    ) -> dict[str, Any]:
        if source_role == "system":
            raise DomainError(
                "client_system_source_forbidden",
                "客户端不能把自己声明为系统来源。",
                status=403,
            )
        return self._run_atomic_write(
            session_id=session_id,
            capability="clinical_record.write",
            context_resolver=lambda: self._encounter_context(encounter_id),
            purpose=purpose,
            data_categories=data_categories,
            authorization_object_type="encounter",
            authorization_object_id=encounter_id,
            clinical_object_type="clinical_field_value",
            action_type="append:structured_field",
            event_type="clinical.structured_field.appended",
            client_role_claim=client_role_claim,
            writer=lambda decision: self.clinical.add_structured_field(
                encounter_id=encounter_id,
                field_id=field_id,
                value=value,
                unknown_state=unknown_state,
                source_role=source_role,
                actor_id=decision["subject_id"],
                actor_role=decision["server_role"],
                review_status=review_status,
            ),
        )

    def transition_encounter(
        self,
        *,
        session_id: str,
        encounter_id: str,
        target_status: str,
        purpose: str,
        data_categories: list[str],
        client_role_claim: str | None = None,
        reason: str = "",
    ) -> dict[str, Any]:
        capability = (
            "clinical_record.sign" if target_status == "signed" else "clinical_record.write"
        )
        return self._run_atomic_write(
            session_id=session_id,
            capability=capability,
            context_resolver=lambda: self._encounter_context(encounter_id),
            purpose=purpose,
            data_categories=data_categories,
            authorization_object_type="encounter",
            authorization_object_id=encounter_id,
            clinical_object_type="encounter",
            action_type=f"transition:{target_status}",
            event_type=f"clinical.encounter.{target_status}",
            client_role_claim=client_role_claim,
            writer=lambda decision: self.clinical.transition_encounter(
                encounter_id=encounter_id,
                target_status=target_status,
                actor_id=decision["subject_id"],
                actor_role=decision["server_role"],
                reason=reason,
            ),
        )

    def start_revision(
        self,
        *,
        session_id: str,
        encounter_id: str,
        reason: str,
        purpose: str,
        data_categories: list[str],
        client_role_claim: str | None = None,
    ) -> dict[str, Any]:
        return self._run_atomic_write(
            session_id=session_id,
            capability="clinical_record.write",
            context_resolver=lambda: self._encounter_context(encounter_id),
            purpose=purpose,
            data_categories=data_categories,
            authorization_object_type="encounter",
            authorization_object_id=encounter_id,
            clinical_object_type="encounter",
            action_type="revision:start",
            event_type="clinical.encounter.revision_started",
            client_role_claim=client_role_claim,
            writer=lambda decision: self.clinical.start_revision(
                encounter_id=encounter_id,
                reason=reason,
                actor_id=decision["subject_id"],
                actor_role=decision["server_role"],
            ),
        )

    def _run_atomic_write(
        self,
        *,
        session_id: str,
        capability: str,
        context_resolver: Callable[[], tuple[str, str | None]],
        purpose: str,
        data_categories: list[str],
        authorization_object_type: str,
        authorization_object_id: str,
        clinical_object_type: str,
        action_type: str,
        event_type: str,
        writer: Callable[[dict[str, Any]], dict[str, Any]],
        client_role_claim: str | None,
    ) -> dict[str, Any]:
        self._validate_authorization_input(
            session_id=session_id,
            purpose=purpose,
            data_categories=data_categories,
        )
        denial_reason: str | None = None
        result: dict[str, Any] | None = None
        with self.store.connect() as connection:
            patient_subject_id, object_state = context_resolver()
            decision = self.security.authorize_in_transaction(
                connection,
                session_id=session_id,
                capability=capability,
                patient_subject_id=patient_subject_id,
                purpose=purpose,
                data_categories=sorted(set(data_categories)),
                object_state=object_state,
                object_id=authorization_object_id,
                client_role_claim=client_role_claim,
            )
            if not decision["allowed"]:
                denial_reason = decision["reason"]
            else:
                evidence_id = self._capture_authorization_evidence(
                    connection,
                    decision=decision,
                    session_id=session_id,
                    patient_subject_id=patient_subject_id,
                    purpose=purpose,
                    data_categories=data_categories,
                    object_type=authorization_object_type,
                    object_id=authorization_object_id,
                    object_state=object_state,
                )
                result = writer(decision)
                self._link_write_and_outbox(
                    connection,
                    authorization_evidence_id=evidence_id,
                    clinical_object_type=clinical_object_type,
                    clinical_object_id=result["id"],
                    action_type=action_type,
                    event_type=event_type,
                    result=result,
                )
        if denial_reason is not None:
            raise DomainError(
                denial_reason,
                "服务端授权决策拒绝该操作。",
                status=403,
            )
        if result is None:
            raise DomainError("clinical_write_failed", "临床写入未完成。", status=500)
        return result

    def _capture_authorization_evidence(
        self,
        connection: sqlite3.Connection,
        *,
        decision: dict[str, Any],
        session_id: str,
        patient_subject_id: str,
        purpose: str,
        data_categories: list[str],
        object_type: str,
        object_id: str,
        object_state: str | None,
    ) -> str:
        evidence_id = self.clinical.new_id("auth_evidence")
        created_at = utc_now()
        evidence = {
            "session_id": session_id,
            "subject_id": decision["subject_id"],
            "server_role": decision["server_role"],
            "capability": decision["capability"],
            "patient_subject_id": patient_subject_id,
            "purpose": purpose,
            "data_categories": sorted(set(data_categories)),
            "object_type": object_type,
            "object_id": object_id,
            "object_state": object_state,
            "relationship_id": decision["relationship_id"],
            "consent_id": decision["consent_id"],
            "allowed": True,
            "created_at": created_at,
        }
        evidence_json = self._canonical_json(evidence)
        connection.execute(
            """
            INSERT INTO clinical_authorization_evidence (
                id, session_id, subject_id, server_role, capability,
                patient_subject_id, purpose, data_categories_json,
                object_type, object_id, object_state, relationship_id,
                consent_id, evidence_hash, allowed, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
            """,
            (
                evidence_id,
                session_id,
                decision["subject_id"],
                decision["server_role"],
                decision["capability"],
                patient_subject_id,
                purpose,
                self._canonical_json(sorted(set(data_categories))),
                object_type,
                object_id,
                object_state,
                decision["relationship_id"],
                decision["consent_id"],
                self._sha256(evidence_json),
                created_at,
            ),
        )
        return evidence_id

    def _link_write_and_outbox(
        self,
        connection: sqlite3.Connection,
        *,
        authorization_evidence_id: str,
        clinical_object_type: str,
        clinical_object_id: str,
        action_type: str,
        event_type: str,
        result: dict[str, Any],
    ) -> None:
        created_at = utc_now()
        link_id = self.clinical.new_id("write_evidence")
        outbox_id = self.clinical.new_id("outbox")
        connection.execute(
            """
            INSERT INTO clinical_write_evidence_links (
                id, authorization_evidence_id, clinical_object_type,
                clinical_object_id, action_type, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                link_id,
                authorization_evidence_id,
                clinical_object_type,
                clinical_object_id,
                action_type,
                created_at,
            ),
        )
        payload = {
            "authorization_evidence_id": authorization_evidence_id,
            "clinical_object_type": clinical_object_type,
            "clinical_object_id": clinical_object_id,
            "action_type": action_type,
            "status": result.get("status"),
            "record_version_id": result.get("record_version_id"),
            "version_number": result.get("version_number"),
            "responsibility_snapshot_id": result.get("responsibility_snapshot_id"),
        }
        payload_json = self._canonical_json(payload)
        connection.execute(
            """
            INSERT INTO clinical_outbox_events (
                id, authorization_evidence_id, write_link_id, aggregate_type,
                aggregate_id, event_type, payload_json, payload_hash,
                raw_clinical_content_persisted, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 'pending', ?)
            """,
            (
                outbox_id,
                authorization_evidence_id,
                link_id,
                clinical_object_type,
                clinical_object_id,
                event_type,
                payload_json,
                self._sha256(payload_json),
                created_at,
            ),
        )

    @staticmethod
    def _canonical_json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _sha256(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def _validate_authorization_input(
        self,
        *,
        session_id: str,
        purpose: str,
        data_categories: list[str],
    ) -> None:
        if not session_id.strip():
            raise DomainError("session_required", "临床写入必须使用服务端会话。", status=403)
        if not purpose.strip() or not data_categories:
            raise DomainError(
                "clinical_authorization_scope_required",
                "临床写入必须声明目的和资料范围。",
                status=403,
            )

    def _patient_subject_id(self, patient_id: str) -> str:
        with self.store.connect() as connection:
            row = connection.execute(
                """
                SELECT b.patient_subject_id
                FROM patients p
                JOIN patient_record_subject_bindings b ON b.patient_id = p.id
                JOIN security_subjects s ON s.id = b.patient_subject_id
                WHERE p.id = ? AND p.is_synthetic = 1
                  AND s.role = 'patient' AND s.account_status = 'active'
                  AND s.verification_status = 'verified'
                """,
                (patient_id,),
            ).fetchone()
        if row is None:
            raise DomainError(
                "patient_security_subject_required",
                "合成患者尚未绑定服务端安全主体。",
                status=403,
            )
        return row["patient_subject_id"]

    def _encounter_context(self, encounter_id: str) -> tuple[str, str]:
        with self.store.connect() as connection:
            row = connection.execute(
                """
                SELECT b.patient_subject_id, e.status
                FROM encounters e
                JOIN patients p ON p.id = e.patient_id
                JOIN patient_record_subject_bindings b ON b.patient_id = p.id
                JOIN security_subjects s ON s.id = b.patient_subject_id
                WHERE e.id = ? AND p.is_synthetic = 1
                  AND s.role = 'patient' AND s.account_status = 'active'
                  AND s.verification_status = 'verified'
                """,
                (encounter_id,),
            ).fetchone()
        if row is None:
            raise DomainError(
                "encounter_security_context_required",
                "就诊记录缺少服务端患者安全上下文。",
                status=403,
            )
        return row["patient_subject_id"], row["status"]
