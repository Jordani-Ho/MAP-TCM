from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from .errors import DomainError
from .store import SQLiteStore, utc_now


ROLES = {
    "patient",
    "verified_doctor",
    "care_executor",
    "clinic_admin",
    "founder_operator",
    "knowledge_steward",
    "delegated_agent",
}

CAPABILITIES = {
    "patient": {
        "clinical_record.read",
        "care_event.write",
        "consent.change",
        "care_relationship.change",
    },
    "verified_doctor": {
        "clinical_record.read",
        "clinical_record.write",
        "clinical_record.sign",
        "care_relationship.change",
        "invitation.issue",
    },
    "care_executor": {"care_task.read", "care_task.record"},
    "clinic_admin": {"invitation.issue", "role_or_verification.change"},
    "founder_operator": {"governance.configure", "deidentified_quality.read"},
    "knowledge_steward": {"knowledge_source.manage", "knowledge_claim.moderate"},
    "delegated_agent": {"knowledge.retrieve"},
}

PATIENT_SELF_ONLY_CAPABILITIES = {"clinical_record.read", "care_event.write"}

HIGH_RISK_CAPABILITIES = {
    "clinical_record.sign",
    "consent.change",
    "care_relationship.change",
    "invitation.issue",
    "bulk_export.request",
    "role_or_verification.change",
}


class IdentityAccessService:
    """Synthetic-only identity and object-authorization state machine.

    This service intentionally does not authenticate real people. It provides a
    testable server-side contract that production identity providers must later
    replace without weakening the authorization order.
    """

    def __init__(self, store: SQLiteStore, *, step_up_minutes: int = 10) -> None:
        self.store = store
        self.step_up_minutes = step_up_minutes

    @staticmethod
    def new_id(prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex[:16]}"

    def create_synthetic_subject(
        self,
        *,
        display_code: str,
        role: str,
        actor_id: str,
        account_status: str = "active",
        verification_status: str = "verified",
        credential_status: str | None = None,
    ) -> dict[str, Any]:
        if not display_code.startswith("SYN-"):
            raise DomainError("synthetic_subject_required", "安全原型只允许 SYN- 合成主体。")
        if role not in ROLES:
            raise DomainError("invalid_role", "主体角色不在安全合同允许范围内。")
        if credential_status is None:
            credential_status = "active" if role == "verified_doctor" else "not_applicable"
        subject_id = self.new_id("subject")
        now = utc_now()
        with self.store.connect() as connection:
            connection.execute(
                """
                INSERT INTO security_subjects (
                    id, display_code, role, account_status, verification_status,
                    credential_status, is_synthetic, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    subject_id,
                    display_code,
                    role,
                    account_status,
                    verification_status,
                    credential_status,
                    now,
                    now,
                ),
            )
            self.store.audit(
                connection,
                event_type="security.subject.created",
                actor_id=actor_id,
                object_type="security_subject",
                object_id=subject_id,
                metadata={
                    "role": role,
                    "account_status": account_status,
                    "verification_status": verification_status,
                    "synthetic_only": True,
                },
            )
        return {
            "id": subject_id,
            "display_code": display_code,
            "role": role,
            "account_status": account_status,
        }

    def open_synthetic_session(
        self, *, subject_id: str, valid_minutes: int = 60
    ) -> dict[str, Any]:
        if valid_minutes <= 0 or valid_minutes > 720:
            raise DomainError("invalid_session_duration", "合成会话有效期必须为 1–720 分钟。")
        session_id = self.new_id("session")
        now = self._now()
        with self.store.connect() as connection:
            subject = self._get_subject(connection, subject_id)
            if subject["account_status"] in {"suspended", "rejected"}:
                raise DomainError("inactive_account", "主体账户不可建立会话。", status=403)
            connection.execute(
                """
                INSERT INTO security_sessions (
                    id, subject_id, status, authenticated_at, step_up_at,
                    expires_at, revoked_at, created_at
                ) VALUES (?, ?, 'active', ?, NULL, ?, NULL, ?)
                """,
                (session_id, subject_id, self._iso(now), self._iso(now + timedelta(minutes=valid_minutes)), self._iso(now)),
            )
            self.store.audit(
                connection,
                event_type="security.session.opened",
                actor_id=subject_id,
                object_type="security_session",
                object_id=session_id,
                metadata={"authentication": "synthetic_test_only"},
            )
        return {"id": session_id, "status": "active", "subject_id": subject_id}

    def step_up_session(self, *, session_id: str) -> dict[str, Any]:
        with self.store.connect() as connection:
            session, subject = self._require_live_session(connection, session_id)
            now = utc_now()
            connection.execute(
                "UPDATE security_sessions SET step_up_at = ? WHERE id = ?",
                (now, session_id),
            )
            self.store.audit(
                connection,
                event_type="security.session.step_up",
                actor_id=subject["id"],
                object_type="security_session",
                object_id=session_id,
                metadata={"synthetic_only": True},
            )
        return {"id": session_id, "step_up_at": now}

    def revoke_session(self, *, session_id: str, actor_id: str) -> dict[str, Any]:
        with self.store.connect() as connection:
            session = connection.execute(
                "SELECT * FROM security_sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if session is None:
                raise DomainError("session_not_found", "会话不存在。", status=404)
            if session["status"] != "revoked":
                now = utc_now()
                connection.execute(
                    "UPDATE security_sessions SET status = 'revoked', revoked_at = ? WHERE id = ?",
                    (now, session_id),
                )
                self.store.audit(
                    connection,
                    event_type="security.session.revoked",
                    actor_id=actor_id,
                    object_type="security_session",
                    object_id=session_id,
                    metadata={"subject_id": session["subject_id"]},
                )
        return {"id": session_id, "status": "revoked"}

    def issue_invitation(
        self,
        *,
        session_id: str,
        expected_role: str,
        intended_recipient_binding: str,
        purpose: str,
        valid_minutes: int = 30,
    ) -> dict[str, Any]:
        if expected_role not in ROLES:
            raise DomainError("invalid_role", "邀请角色无效。")
        if not intended_recipient_binding.startswith("SYN-"):
            raise DomainError("synthetic_binding_required", "邀请仅允许绑定合成接收对象。")
        if not purpose.strip() or valid_minutes <= 0 or valid_minutes > 1_440:
            raise DomainError("invalid_invitation", "邀请目的或有效期无效。")
        plaintext_token = secrets.token_urlsafe(32)
        token_hash = self._hash_token(plaintext_token)
        invitation_id = self.new_id("invite")
        now = self._now()
        with self.store.connect() as connection:
            _, subject = self._require_live_session(connection, session_id)
            self._require_active_verified(subject)
            if subject["role"] not in {"verified_doctor", "clinic_admin"}:
                raise DomainError("invitation_forbidden", "当前服务端角色不能签发邀请。", status=403)
            if subject["role"] == "verified_doctor" and subject["credential_status"] != "active":
                raise DomainError("credential_inactive", "医生资质状态无效。", status=403)
            if subject["role"] == "verified_doctor" and expected_role not in {
                "patient",
                "verified_doctor",
                "care_executor",
            }:
                raise DomainError("invitation_scope_forbidden", "医生不能邀请该高权限角色。", status=403)
            self._require_recent_step_up(connection, session_id)
            connection.execute(
                """
                INSERT INTO security_invitations (
                    id, token_hash, expected_role, inviter_subject_id,
                    accepted_subject_id, intended_recipient_binding, purpose, issued_at, expires_at,
                    maximum_uses, use_count, status
                ) VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, 1, 0, 'issued')
                """,
                (
                    invitation_id,
                    token_hash,
                    expected_role,
                    subject["id"],
                    intended_recipient_binding,
                    purpose.strip(),
                    self._iso(now),
                    self._iso(now + timedelta(minutes=valid_minutes)),
                ),
            )
            self._append_invitation_event(
                connection,
                invitation_id=invitation_id,
                from_state=None,
                to_state="issued",
                actor_id=subject["id"],
                metadata={"maximum_uses": 1, "plaintext_token_persisted": False},
            )
            self.store.audit(
                connection,
                event_type="security.invitation.issued",
                actor_id=subject["id"],
                object_type="security_invitation",
                object_id=invitation_id,
                metadata={
                    "expected_role": expected_role,
                    "session_id": session_id,
                    "recipient_binding": intended_recipient_binding,
                },
            )
        return {
            "id": invitation_id,
            "status": "issued",
            "token": plaintext_token,
            "token_returned_once": True,
            "expires_at": self._iso(now + timedelta(minutes=valid_minutes)),
        }

    def accept_invitation(
        self,
        *,
        token: str,
        recipient_binding: str,
        display_code: str,
    ) -> dict[str, Any]:
        if not token or not display_code.startswith("SYN-"):
            raise DomainError("invalid_invitation_acceptance", "邀请令牌或合成主体编号无效。")
        token_hash = self._hash_token(token)
        error: DomainError | None = None
        result: dict[str, Any] | None = None
        with self.store.connect() as connection:
            invitation = connection.execute(
                "SELECT * FROM security_invitations WHERE token_hash = ?", (token_hash,)
            ).fetchone()
            if invitation is None:
                self._audit_invitation_denial(
                    connection, "unknown", recipient_binding, "invitation_not_found"
                )
                error = DomainError("invitation_not_found", "邀请不存在或令牌无效。", status=403)
            else:
                reason = self._invitation_denial_reason(invitation, recipient_binding)
                if reason is not None:
                    if reason == "invitation_expired" and invitation["status"] == "issued":
                        connection.execute(
                            "UPDATE security_invitations SET status = 'expired' WHERE id = ?",
                            (invitation["id"],),
                        )
                        self._append_invitation_event(
                            connection,
                            invitation_id=invitation["id"],
                            from_state="issued",
                            to_state="expired",
                            actor_id=recipient_binding,
                            metadata={"reason": reason},
                        )
                    self._audit_invitation_denial(
                        connection, invitation["id"], recipient_binding, reason
                    )
                    error = DomainError(reason, "邀请已失效、已使用或接收对象不匹配。", status=403)
                else:
                    subject_id = self.new_id("subject")
                    now = utc_now()
                    credential_status = (
                        "pending" if invitation["expected_role"] == "verified_doctor" else "not_applicable"
                    )
                    connection.execute(
                        """
                        INSERT INTO security_subjects (
                            id, display_code, role, account_status, verification_status,
                            credential_status, is_synthetic, created_at, updated_at
                        ) VALUES (?, ?, ?, 'identity_pending', 'pending', ?, 1, ?, ?)
                        """,
                        (
                            subject_id,
                            display_code,
                            invitation["expected_role"],
                            credential_status,
                            now,
                            now,
                        ),
                    )
                    connection.execute(
                        """
                        UPDATE security_invitations
                        SET status = 'identity_pending', use_count = use_count + 1,
                            accepted_subject_id = ?
                        WHERE id = ?
                        """,
                        (subject_id, invitation["id"]),
                    )
                    self._append_invitation_event(
                        connection,
                        invitation_id=invitation["id"],
                        from_state="issued",
                        to_state="accepted",
                        actor_id=subject_id,
                        metadata={"subject_id": subject_id},
                    )
                    self._append_invitation_event(
                        connection,
                        invitation_id=invitation["id"],
                        from_state="accepted",
                        to_state="identity_pending",
                        actor_id=subject_id,
                        metadata={"verification_required": True},
                    )
                    self.store.audit(
                        connection,
                        event_type="security.invitation.accepted",
                        actor_id=subject_id,
                        object_type="security_invitation",
                        object_id=invitation["id"],
                        metadata={"resulting_account_status": "identity_pending"},
                    )
                    result = {
                        "invitation_id": invitation["id"],
                        "subject_id": subject_id,
                        "role": invitation["expected_role"],
                        "account_status": "identity_pending",
                        "verification_status": "pending",
                    }
        if error is not None:
            raise error
        assert result is not None
        return result

    def verify_subject(
        self,
        *,
        admin_session_id: str,
        subject_id: str,
        credential_status: str | None = None,
    ) -> dict[str, Any]:
        with self.store.connect() as connection:
            _, admin = self._require_live_session(connection, admin_session_id)
            self._require_active_verified(admin)
            if admin["role"] != "clinic_admin":
                raise DomainError("verification_forbidden", "只有安全管理员可完成合成核验。", status=403)
            self._require_recent_step_up(connection, admin_session_id)
            subject = self._get_subject(connection, subject_id)
            next_credential = credential_status or subject["credential_status"]
            if subject["role"] == "verified_doctor" and next_credential != "active":
                raise DomainError("active_credential_required", "医生激活前必须具有 active 合成资质状态。")
            if subject["role"] != "verified_doctor":
                next_credential = "not_applicable"
            now = utc_now()
            connection.execute(
                """
                UPDATE security_subjects
                SET account_status = 'active', verification_status = 'verified',
                    credential_status = ?, updated_at = ?
                WHERE id = ?
                """,
                (next_credential, now, subject_id),
            )
            invitation = connection.execute(
                """
                SELECT id FROM security_invitations
                WHERE accepted_subject_id = ? AND status = 'identity_pending'
                ORDER BY issued_at DESC LIMIT 1
                """,
                (subject_id,),
            ).fetchone()
            if invitation is not None:
                connection.execute(
                    "UPDATE security_invitations SET status = 'active' WHERE id = ?",
                    (invitation["id"],),
                )
                self._append_invitation_event(
                    connection,
                    invitation_id=invitation["id"],
                    from_state="identity_pending",
                    to_state="active",
                    actor_id=admin["id"],
                    metadata={"subject_id": subject_id},
                )
            self.store.audit(
                connection,
                event_type="security.subject.verified",
                actor_id=admin["id"],
                object_type="security_subject",
                object_id=subject_id,
                metadata={
                    "credential_status": next_credential,
                    "session_id": admin_session_id,
                    "synthetic_only": True,
                },
            )
        return {
            "id": subject_id,
            "account_status": "active",
            "verification_status": "verified",
            "credential_status": next_credential,
        }

    def create_care_relationship(
        self,
        *,
        doctor_session_id: str,
        patient_subject_id: str,
        purpose: str,
        data_categories: list[str],
        valid_days: int = 30,
    ) -> dict[str, Any]:
        self._require_scope(purpose, data_categories)
        if valid_days <= 0 or valid_days > 365:
            raise DomainError("invalid_relationship_duration", "照护关系有效期必须为 1–365 天。")
        relationship_id = self.new_id("relationship")
        now = self._now()
        with self.store.connect() as connection:
            _, doctor = self._require_live_session(connection, doctor_session_id)
            self._require_active_verified(doctor)
            if doctor["role"] != "verified_doctor" or doctor["credential_status"] != "active":
                raise DomainError("verified_doctor_required", "只有有效资质医生可申请照护关系。", status=403)
            self._require_recent_step_up(connection, doctor_session_id)
            patient = self._get_subject(connection, patient_subject_id)
            self._require_active_verified(patient)
            if patient["role"] != "patient":
                raise DomainError("patient_required", "照护关系对象必须是患者主体。")
            connection.execute(
                """
                INSERT INTO care_relationships (
                    id, patient_subject_id, practitioner_subject_id, status,
                    purpose, data_categories_json, effective_at, expires_at,
                    created_by, created_at, updated_at
                ) VALUES (?, ?, ?, 'pending', ?, ?, NULL, ?, ?, ?, ?)
                """,
                (
                    relationship_id,
                    patient_subject_id,
                    doctor["id"],
                    purpose.strip(),
                    self._json(sorted(set(data_categories))),
                    self._iso(now + timedelta(days=valid_days)),
                    doctor["id"],
                    self._iso(now),
                    self._iso(now),
                ),
            )
            self.store.audit(
                connection,
                event_type="security.care_relationship.requested",
                actor_id=doctor["id"],
                object_type="care_relationship",
                object_id=relationship_id,
                metadata={"patient_subject_id": patient_subject_id, "purpose": purpose.strip()},
            )
        return {"id": relationship_id, "status": "pending"}

    def accept_care_relationship(
        self,
        *,
        patient_session_id: str,
        relationship_id: str,
        purpose: str,
        data_categories: list[str],
    ) -> dict[str, Any]:
        self._require_scope(purpose, data_categories)
        consent_id = self.new_id("security_consent")
        with self.store.connect() as connection:
            _, patient = self._require_live_session(connection, patient_session_id)
            self._require_active_verified(patient)
            if patient["role"] != "patient":
                raise DomainError("patient_acceptance_required", "照护关系必须由患者本人接受。", status=403)
            self._require_recent_step_up(connection, patient_session_id)
            relationship = self._get_relationship(connection, relationship_id)
            if relationship["patient_subject_id"] != patient["id"]:
                raise DomainError("relationship_patient_mismatch", "不能接受其他患者的关系申请。", status=403)
            if relationship["status"] != "pending":
                raise DomainError("relationship_not_pending", "关系申请不处于待接受状态。", status=409)
            proposed = set(json.loads(relationship["data_categories_json"]))
            accepted = set(data_categories)
            if purpose != relationship["purpose"] or not accepted.issubset(proposed):
                raise DomainError("consent_scope_expansion", "患者接受范围不能超出关系申请。", status=403)
            now = utc_now()
            connection.execute(
                """
                UPDATE care_relationships
                SET status = 'active', effective_at = ?, updated_at = ? WHERE id = ?
                """,
                (now, now, relationship_id),
            )
            connection.execute(
                """
                INSERT INTO security_consents (
                    id, relationship_id, patient_subject_id, purpose,
                    data_categories_json, status, effective_at, expires_at,
                    revoked_at, created_at
                ) VALUES (?, ?, ?, ?, ?, 'active', ?, ?, NULL, ?)
                """,
                (
                    consent_id,
                    relationship_id,
                    patient["id"],
                    purpose,
                    self._json(sorted(accepted)),
                    now,
                    relationship["expires_at"],
                    now,
                ),
            )
            self.store.audit(
                connection,
                event_type="security.care_relationship.accepted",
                actor_id=patient["id"],
                object_type="care_relationship",
                object_id=relationship_id,
                metadata={"consent_id": consent_id, "purpose": purpose},
            )
        return {"id": relationship_id, "status": "active", "consent_id": consent_id}

    def revoke_consent(
        self, *, patient_session_id: str, consent_id: str, reason: str
    ) -> dict[str, Any]:
        if not reason.strip():
            raise DomainError("reason_required", "撤回同意必须记录原因。")
        with self.store.connect() as connection:
            _, patient = self._require_live_session(connection, patient_session_id)
            self._require_active_verified(patient)
            if patient["role"] != "patient":
                raise DomainError("patient_consent_required", "只有患者本人可以撤回同意。", status=403)
            self._require_recent_step_up(connection, patient_session_id)
            consent = connection.execute(
                "SELECT * FROM security_consents WHERE id = ?", (consent_id,)
            ).fetchone()
            if consent is None:
                raise DomainError("consent_not_found", "同意记录不存在。", status=404)
            if consent["patient_subject_id"] != patient["id"]:
                raise DomainError("consent_patient_mismatch", "不能撤回其他患者的同意。", status=403)
            if consent["status"] != "active":
                raise DomainError("consent_not_active", "同意已经失效。", status=409)
            now = utc_now()
            connection.execute(
                "UPDATE security_consents SET status = 'revoked', revoked_at = ? WHERE id = ?",
                (now, consent_id),
            )
            self.store.audit(
                connection,
                event_type="security.consent.revoked",
                actor_id=patient["id"],
                object_type="security_consent",
                object_id=consent_id,
                metadata={"reason": reason.strip(), "relationship_id": consent["relationship_id"]},
            )
        return {"id": consent_id, "status": "revoked"}

    def authorize(
        self,
        *,
        session_id: str,
        capability: str,
        patient_subject_id: str | None = None,
        purpose: str | None = None,
        data_categories: list[str] | None = None,
        object_state: str | None = None,
        object_id: str = "synthetic_object",
        client_role_claim: str | None = None,
    ) -> dict[str, Any]:
        with self.store.connect() as connection:
            decision = self.authorize_in_transaction(
                connection,
                session_id=session_id,
                capability=capability,
                patient_subject_id=patient_subject_id,
                purpose=purpose,
                data_categories=data_categories,
                object_state=object_state,
                object_id=object_id,
                client_role_claim=client_role_claim,
            )
        if not decision["allowed"]:
            raise DomainError(
                decision["reason"],
                "服务端授权决策拒绝该操作。",
                status=403,
            )
        return decision

    def authorize_in_transaction(
        self,
        connection: sqlite3.Connection,
        *,
        session_id: str,
        capability: str,
        patient_subject_id: str | None = None,
        purpose: str | None = None,
        data_categories: list[str] | None = None,
        object_state: str | None = None,
        object_id: str = "synthetic_object",
        client_role_claim: str | None = None,
    ) -> dict[str, Any]:
        """Evaluate and audit authorization on the caller's transaction.

        The method returns denied decisions instead of raising so callers can
        commit the denial audit without performing a clinical write.
        """
        fields = set(data_categories or [])
        allowed = False
        reason = "authorization_denied"
        subject_id = "anonymous"
        server_role: str | None = None
        relationship_id: str | None = None
        consent_id: str | None = None
        session = connection.execute(
            """
            SELECT s.*, p.role, p.account_status, p.verification_status,
                   p.credential_status, p.is_synthetic
            FROM security_sessions s
            JOIN security_subjects p ON p.id = s.subject_id
            WHERE s.id = ?
            """,
            (session_id,),
        ).fetchone()
        if session is None:
            reason = "session_not_found"
        else:
            subject_id = session["subject_id"]
            server_role = session["role"]
            reason = self._session_denial_reason(session) or ""
            if not reason and (
                session["account_status"] != "active"
                or session["verification_status"] != "verified"
            ):
                reason = "inactive_or_unverified_subject"
            if not reason and capability not in CAPABILITIES.get(server_role, set()):
                reason = "capability_forbidden"
            if (
                not reason
                and server_role == "verified_doctor"
                and session["credential_status"] != "active"
            ):
                reason = "credential_inactive"
            if not reason and server_role == "patient":
                if capability in PATIENT_SELF_ONLY_CAPABILITIES and patient_subject_id != subject_id:
                    reason = "other_patient_record_forbidden"
            if not reason and server_role in {"verified_doctor", "care_executor"}:
                reason, relationship_id, consent_id = self._relationship_or_consent_decision(
                    connection,
                    subject_id=subject_id,
                    patient_subject_id=patient_subject_id,
                    purpose=purpose,
                    fields=fields,
                )
            if not reason and capability == "clinical_record.sign" and object_state != "doctor_reviewed":
                reason = "object_state_forbids_signing"
            if not reason and capability in HIGH_RISK_CAPABILITIES:
                reason = self._step_up_denial_reason(session)
            allowed = not reason
        self.store.audit(
            connection,
            event_type="security.authorization.decision",
            actor_id=subject_id,
            object_type="authorization_target",
            object_id=object_id,
            metadata={
                "allowed": allowed,
                "reason": "allowed" if allowed else reason,
                "server_role": server_role,
                "client_role_claim": client_role_claim,
                "client_role_authoritative": False,
                "capability": capability,
                "patient_subject_id": patient_subject_id,
                "purpose": purpose,
                "data_categories": sorted(fields),
                "session_id": session_id,
                "relationship_id": relationship_id,
                "consent_id": consent_id,
            },
        )
        return {
            "allowed": allowed,
            "reason": "allowed" if allowed else reason,
            "subject_id": subject_id,
            "server_role": server_role,
            "capability": capability,
            "relationship_id": relationship_id,
            "consent_id": consent_id,
        }

    def overview(self) -> dict[str, Any]:
        with self.store.connect() as connection:
            counts = {
                "subjects": connection.execute("SELECT COUNT(*) FROM security_subjects").fetchone()[0],
                "active_sessions": connection.execute(
                    "SELECT COUNT(*) FROM security_sessions WHERE status = 'active' AND expires_at > ?",
                    (utc_now(),),
                ).fetchone()[0],
                "issued_invitations": connection.execute(
                    "SELECT COUNT(*) FROM security_invitations WHERE status = 'issued'"
                ).fetchone()[0],
                "active_relationships": connection.execute(
                    "SELECT COUNT(*) FROM care_relationships WHERE status = 'active'"
                ).fetchone()[0],
                "active_security_consents": connection.execute(
                    "SELECT COUNT(*) FROM security_consents WHERE status = 'active'"
                ).fetchone()[0],
            }
        return {
            "status": "synthetic_security_prototype",
            "production_identity_ready": False,
            "counts": counts,
        }

    def _relationship_or_consent_decision(
        self,
        connection: sqlite3.Connection,
        *,
        subject_id: str,
        patient_subject_id: str | None,
        purpose: str | None,
        fields: set[str],
    ) -> tuple[str, str | None, str | None]:
        if patient_subject_id is None or purpose is None or not fields:
            return "relationship_and_consent_context_required", None, None
        now = utc_now()
        relationship = connection.execute(
            """
            SELECT * FROM care_relationships
            WHERE patient_subject_id = ? AND practitioner_subject_id = ?
              AND status = 'active' AND effective_at <= ?
              AND (expires_at IS NULL OR expires_at > ?)
            ORDER BY created_at DESC LIMIT 1
            """,
            (patient_subject_id, subject_id, now, now),
        ).fetchone()
        if relationship is None:
            return "active_relationship_required", None, None
        consent = connection.execute(
            """
            SELECT * FROM security_consents
            WHERE relationship_id = ? AND patient_subject_id = ?
              AND status = 'active' AND purpose = ? AND effective_at <= ?
              AND (expires_at IS NULL OR expires_at > ?)
            ORDER BY created_at DESC LIMIT 1
            """,
            (relationship["id"], patient_subject_id, purpose, now, now),
        ).fetchone()
        if consent is None:
            return "active_consent_required", relationship["id"], None
        allowed_fields = set(json.loads(consent["data_categories_json"]))
        if not fields.issubset(allowed_fields):
            return "consent_field_scope_denied", relationship["id"], consent["id"]
        return "", relationship["id"], consent["id"]

    def _invitation_denial_reason(
        self, invitation: sqlite3.Row, recipient_binding: str
    ) -> str | None:
        if invitation["status"] != "issued" or invitation["use_count"] >= invitation["maximum_uses"]:
            return "invitation_reuse_denied"
        if invitation["expires_at"] <= utc_now():
            return "invitation_expired"
        if not secrets.compare_digest(invitation["intended_recipient_binding"], recipient_binding):
            return "invitation_recipient_mismatch"
        return None

    def _audit_invitation_denial(
        self,
        connection: sqlite3.Connection,
        invitation_id: str,
        actor_id: str,
        reason: str,
    ) -> None:
        self.store.audit(
            connection,
            event_type="security.invitation.denied",
            actor_id=actor_id,
            object_type="security_invitation",
            object_id=invitation_id,
            metadata={"reason": reason},
        )

    def _append_invitation_event(
        self,
        connection: sqlite3.Connection,
        *,
        invitation_id: str,
        from_state: str | None,
        to_state: str,
        actor_id: str,
        metadata: dict[str, Any],
    ) -> None:
        connection.execute(
            """
            INSERT INTO security_invitation_events (
                invitation_id, from_state, to_state, actor_id, occurred_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                invitation_id,
                from_state,
                to_state,
                actor_id,
                utc_now(),
                self._json(metadata),
            ),
        )

    def _require_live_session(
        self, connection: sqlite3.Connection, session_id: str
    ) -> tuple[sqlite3.Row, sqlite3.Row]:
        session = connection.execute(
            "SELECT * FROM security_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if session is None:
            raise DomainError("session_not_found", "会话不存在。", status=403)
        reason = self._session_denial_reason(session)
        if reason:
            raise DomainError(reason, "会话已失效。", status=403)
        return session, self._get_subject(connection, session["subject_id"])

    def _session_denial_reason(self, session: sqlite3.Row) -> str:
        if session["status"] == "revoked":
            return "session_revoked"
        if session["status"] == "expired" or session["expires_at"] <= utc_now():
            return "session_expired"
        if session["status"] != "active":
            return "session_inactive"
        return ""

    def _require_recent_step_up(
        self, connection: sqlite3.Connection, session_id: str
    ) -> None:
        session = connection.execute(
            "SELECT * FROM security_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if session is None or self._step_up_denial_reason(session):
            raise DomainError("recent_step_up_required", "高风险操作需要近期再次验证。", status=403)

    def _step_up_denial_reason(self, session: sqlite3.Row) -> str:
        if not session["step_up_at"]:
            return "recent_step_up_required"
        step_up_at = datetime.fromisoformat(session["step_up_at"])
        if step_up_at.tzinfo is None:
            step_up_at = step_up_at.replace(tzinfo=UTC)
        if self._now() - step_up_at > timedelta(minutes=self.step_up_minutes):
            return "recent_step_up_required"
        return ""

    @staticmethod
    def _require_active_verified(subject: sqlite3.Row) -> None:
        if subject["account_status"] != "active" or subject["verification_status"] != "verified":
            raise DomainError("active_verified_subject_required", "主体尚未完成激活与核验。", status=403)

    @staticmethod
    def _get_subject(connection: sqlite3.Connection, subject_id: str) -> sqlite3.Row:
        subject = connection.execute(
            "SELECT * FROM security_subjects WHERE id = ?", (subject_id,)
        ).fetchone()
        if subject is None:
            raise DomainError("subject_not_found", "安全主体不存在。", status=404)
        return subject

    @staticmethod
    def _get_relationship(connection: sqlite3.Connection, relationship_id: str) -> sqlite3.Row:
        relationship = connection.execute(
            "SELECT * FROM care_relationships WHERE id = ?", (relationship_id,)
        ).fetchone()
        if relationship is None:
            raise DomainError("relationship_not_found", "照护关系不存在。", status=404)
        return relationship

    @staticmethod
    def _require_scope(purpose: str, data_categories: list[str]) -> None:
        if not purpose.strip() or not data_categories or any(not item.strip() for item in data_categories):
            raise DomainError("invalid_scope", "目的和字段范围不能为空。")

    @staticmethod
    def _hash_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC).replace(microsecond=0)

    @staticmethod
    def _iso(value: datetime) -> str:
        return value.astimezone(UTC).isoformat(timespec="seconds")
