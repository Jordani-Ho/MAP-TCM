import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from map_tcm.errors import DomainError
from map_tcm.identity import IdentityAccessService
from map_tcm.store import SQLiteStore


class IdentityAccessServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self.temp_dir.name) / "security.db")
        self.store.initialize()
        self.security = IdentityAccessService(self.store)
        self.admin = self.subject("SYN-ADMIN-001", "clinic_admin")
        self.admin_session = self.session(self.admin["id"], step_up=True)
        self.doctor = self.subject("SYN-DOC-SEC-001", "verified_doctor")
        self.doctor_session = self.session(self.doctor["id"], step_up=True)
        self.patient = self.subject("SYN-PAT-SEC-001", "patient")
        self.patient_session = self.session(self.patient["id"], step_up=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def subject(self, code: str, role: str) -> dict:
        return self.security.create_synthetic_subject(
            display_code=code,
            role=role,
            actor_id="test_setup",
        )

    def session(self, subject_id: str, *, step_up: bool = False) -> dict:
        session = self.security.open_synthetic_session(subject_id=subject_id)
        if step_up:
            self.security.step_up_session(session_id=session["id"])
        return session

    def activate_relationship(self) -> dict:
        relationship = self.security.create_care_relationship(
            doctor_session_id=self.doctor_session["id"],
            patient_subject_id=self.patient["id"],
            purpose="synthetic_clinical_care",
            data_categories=["symptoms", "observations"],
        )
        return self.security.accept_care_relationship(
            patient_session_id=self.patient_session["id"],
            relationship_id=relationship["id"],
            purpose="synthetic_clinical_care",
            data_categories=["symptoms", "observations"],
        )

    def test_only_token_hash_is_persisted_and_token_is_returned_once(self) -> None:
        invitation = self.security.issue_invitation(
            session_id=self.doctor_session["id"],
            expected_role="patient",
            intended_recipient_binding="SYN-RECIPIENT-001",
            purpose="synthetic onboarding",
        )
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT token_hash FROM security_invitations WHERE id = ?", (invitation["id"],)
            ).fetchone()
            database_text = self.store.database_path.read_bytes()
        self.assertEqual(hashlib.sha256(invitation["token"].encode()).hexdigest(), row["token_hash"])
        self.assertNotIn(invitation["token"].encode(), database_text)
        self.assertTrue(invitation["token_returned_once"])

    def test_invitation_reuse_is_denied_and_audited(self) -> None:
        invitation = self.security.issue_invitation(
            session_id=self.doctor_session["id"],
            expected_role="patient",
            intended_recipient_binding="SYN-RECIPIENT-002",
            purpose="synthetic onboarding",
        )
        self.security.accept_invitation(
            token=invitation["token"],
            recipient_binding="SYN-RECIPIENT-002",
            display_code="SYN-PAT-INVITED-001",
        )
        with self.assertRaisesRegex(DomainError, "邀请") as caught:
            self.security.accept_invitation(
                token=invitation["token"],
                recipient_binding="SYN-RECIPIENT-002",
                display_code="SYN-PAT-INVITED-002",
            )
        self.assertEqual("invitation_reuse_denied", caught.exception.code)
        with self.store.connect() as connection:
            denial = connection.execute(
                "SELECT metadata_json FROM audit_events WHERE event_type = 'security.invitation.denied' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        self.assertEqual("invitation_reuse_denied", json.loads(denial["metadata_json"])["reason"])

    def test_wrong_recipient_binding_is_denied(self) -> None:
        invitation = self.security.issue_invitation(
            session_id=self.doctor_session["id"],
            expected_role="patient",
            intended_recipient_binding="SYN-EXPECTED-001",
            purpose="synthetic onboarding",
        )
        with self.assertRaises(DomainError) as caught:
            self.security.accept_invitation(
                token=invitation["token"],
                recipient_binding="SYN-WRONG-001",
                display_code="SYN-PAT-WRONG-001",
            )
        self.assertEqual("invitation_recipient_mismatch", caught.exception.code)

    def test_expired_invitation_is_denied(self) -> None:
        invitation = self.security.issue_invitation(
            session_id=self.doctor_session["id"],
            expected_role="patient",
            intended_recipient_binding="SYN-EXPIRED-001",
            purpose="synthetic onboarding",
        )
        with self.store.connect() as connection:
            connection.execute(
                "UPDATE security_invitations SET expires_at = '2000-01-01T00:00:00+00:00' WHERE id = ?",
                (invitation["id"],),
            )
        with self.assertRaises(DomainError) as caught:
            self.security.accept_invitation(
                token=invitation["token"],
                recipient_binding="SYN-EXPIRED-001",
                display_code="SYN-PAT-EXPIRED-001",
            )
        self.assertEqual("invitation_expired", caught.exception.code)

    def test_identity_pending_doctor_cannot_sign_even_with_client_role_claim(self) -> None:
        invitation = self.security.issue_invitation(
            session_id=self.doctor_session["id"],
            expected_role="verified_doctor",
            intended_recipient_binding="SYN-DOC-CANDIDATE-001",
            purpose="synthetic doctor application",
        )
        pending = self.security.accept_invitation(
            token=invitation["token"],
            recipient_binding="SYN-DOC-CANDIDATE-001",
            display_code="SYN-DOC-PENDING-001",
        )
        pending_session = self.session(pending["subject_id"], step_up=True)
        with self.assertRaises(DomainError) as caught:
            self.security.authorize(
                session_id=pending_session["id"],
                capability="clinical_record.sign",
                patient_subject_id=self.patient["id"],
                purpose="synthetic_clinical_care",
                data_categories=["symptoms"],
                object_state="doctor_reviewed",
                client_role_claim="verified_doctor",
            )
        self.assertEqual("inactive_or_unverified_subject", caught.exception.code)

    def test_admin_verification_is_separate_from_invitation_acceptance(self) -> None:
        invitation = self.security.issue_invitation(
            session_id=self.doctor_session["id"],
            expected_role="verified_doctor",
            intended_recipient_binding="SYN-DOC-CANDIDATE-002",
            purpose="synthetic doctor application",
        )
        pending = self.security.accept_invitation(
            token=invitation["token"],
            recipient_binding="SYN-DOC-CANDIDATE-002",
            display_code="SYN-DOC-PENDING-002",
        )
        verified = self.security.verify_subject(
            admin_session_id=self.admin_session["id"],
            subject_id=pending["subject_id"],
            credential_status="active",
        )
        self.assertEqual("verified", verified["verification_status"])
        self.assertEqual("active", verified["credential_status"])

    def test_doctor_cannot_read_unrelated_patient(self) -> None:
        with self.assertRaises(DomainError) as caught:
            self.security.authorize(
                session_id=self.doctor_session["id"],
                capability="clinical_record.read",
                patient_subject_id=self.patient["id"],
                purpose="synthetic_clinical_care",
                data_categories=["symptoms"],
            )
        self.assertEqual("active_relationship_required", caught.exception.code)

    def test_patient_acceptance_activates_scoped_access(self) -> None:
        self.activate_relationship()
        decision = self.security.authorize(
            session_id=self.doctor_session["id"],
            capability="clinical_record.read",
            patient_subject_id=self.patient["id"],
            purpose="synthetic_clinical_care",
            data_categories=["symptoms"],
            client_role_claim="clinic_admin",
        )
        self.assertTrue(decision["allowed"])
        self.assertEqual("verified_doctor", decision["server_role"])

    def test_consent_does_not_allow_field_scope_expansion(self) -> None:
        self.activate_relationship()
        with self.assertRaises(DomainError) as caught:
            self.security.authorize(
                session_id=self.doctor_session["id"],
                capability="clinical_record.read",
                patient_subject_id=self.patient["id"],
                purpose="synthetic_clinical_care",
                data_categories=["prescription"],
            )
        self.assertEqual("consent_field_scope_denied", caught.exception.code)

    def test_revoked_consent_immediately_denies_new_access(self) -> None:
        relationship = self.activate_relationship()
        self.security.revoke_consent(
            patient_session_id=self.patient_session["id"],
            consent_id=relationship["consent_id"],
            reason="synthetic participant withdrew",
        )
        with self.assertRaises(DomainError) as caught:
            self.security.authorize(
                session_id=self.doctor_session["id"],
                capability="clinical_record.read",
                patient_subject_id=self.patient["id"],
                purpose="synthetic_clinical_care",
                data_categories=["symptoms"],
            )
        self.assertEqual("active_consent_required", caught.exception.code)

    def test_admin_and_delegated_agent_cannot_claim_clinical_signing(self) -> None:
        agent = self.subject("SYN-AGENT-001", "delegated_agent")
        agent_session = self.session(agent["id"], step_up=True)
        for session_id in (self.admin_session["id"], agent_session["id"]):
            with self.assertRaises(DomainError) as caught:
                self.security.authorize(
                    session_id=session_id,
                    capability="clinical_record.sign",
                    patient_subject_id=self.patient["id"],
                    purpose="synthetic_clinical_care",
                    data_categories=["symptoms"],
                    object_state="doctor_reviewed",
                    client_role_claim="verified_doctor",
                )
            self.assertEqual("capability_forbidden", caught.exception.code)

    def test_signing_requires_relationship_consent_state_and_recent_step_up(self) -> None:
        self.activate_relationship()
        unstepped_session = self.session(self.doctor["id"], step_up=False)
        with self.assertRaises(DomainError) as caught:
            self.security.authorize(
                session_id=unstepped_session["id"],
                capability="clinical_record.sign",
                patient_subject_id=self.patient["id"],
                purpose="synthetic_clinical_care",
                data_categories=["symptoms"],
                object_state="doctor_reviewed",
            )
        self.assertEqual("recent_step_up_required", caught.exception.code)
        allowed = self.security.authorize(
            session_id=self.doctor_session["id"],
            capability="clinical_record.sign",
            patient_subject_id=self.patient["id"],
            purpose="synthetic_clinical_care",
            data_categories=["symptoms"],
            object_state="doctor_reviewed",
        )
        self.assertTrue(allowed["allowed"])

    def test_revoked_session_is_denied_and_denial_is_audited(self) -> None:
        self.security.revoke_session(
            session_id=self.doctor_session["id"], actor_id=self.doctor["id"]
        )
        with self.assertRaises(DomainError) as caught:
            self.security.authorize(
                session_id=self.doctor_session["id"],
                capability="clinical_record.read",
                patient_subject_id=self.patient["id"],
                purpose="synthetic_clinical_care",
                data_categories=["symptoms"],
            )
        self.assertEqual("session_revoked", caught.exception.code)
        with self.store.connect() as connection:
            event = connection.execute(
                "SELECT metadata_json FROM audit_events WHERE event_type = 'security.authorization.decision' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        metadata = json.loads(event["metadata_json"])
        self.assertFalse(metadata["allowed"])
        self.assertEqual("session_revoked", metadata["reason"])


if __name__ == "__main__":
    unittest.main()
