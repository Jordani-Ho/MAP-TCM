import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "schemas" / "tcm_sec_identity_access_contract_v0.1.json"


class IdentityAccessContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))

    def test_contract_is_synthetic_and_not_production_ready(self) -> None:
        self.assertEqual("TCM-SEC-001", self.contract["contract_id"])
        self.assertEqual("security_architecture_candidate", self.contract["status"])
        self.assertTrue(self.contract["synthetic_only"])
        self.assertFalse(self.contract["production_identity_ready"])
        self.assertFalse(self.contract["client_supplied_role_is_authoritative"])

    def test_invitation_is_one_time_bound_and_audited(self) -> None:
        invitation = self.contract["invitation"]
        required = set(invitation["required_fields"])
        self.assertTrue(
            {
                "token_hash",
                "expected_role",
                "intended_recipient_binding",
                "expires_at",
                "revocation_status",
                "audit_reference",
            }.issubset(required)
        )
        self.assertEqual(1, invitation["default_maximum_uses"])
        self.assertEqual("deny", invitation["plaintext_token_persistence"])

    def test_doctor_invitation_never_becomes_signing_authority(self) -> None:
        requirements = set(self.contract["doctor_signing_requirements"])
        self.assertTrue(
            {
                "verified_doctor_role",
                "active_license_or_credential_status",
                "active_care_relationship",
                "patient_consent_covers_purpose_and_fields",
                "recent_step_up_authentication",
                "audit_event",
            }.issubset(requirements)
        )

    def test_patient_acceptance_and_consent_are_separate_from_invitation(self) -> None:
        relationship = self.contract["care_relationship"]
        self.assertTrue(relationship["patient_acceptance_required"])
        self.assertFalse(relationship["doctor_invitation_is_patient_consent"])
        self.assertEqual("deny", relationship["cross_doctor_authorization_default"])

    def test_non_doctor_roles_cannot_inherit_clinical_authority(self) -> None:
        denials = self.contract["capability_denials"]
        self.assertIn("prescription.create_or_modify", denials["care_executor"])
        self.assertIn("clinical_content.read_by_default", denials["clinic_admin"])
        self.assertIn("clinical_content.read_by_default", denials["founder_operator"])
        self.assertIn("clinical_record.sign", denials["delegated_agent"])
        self.assertIn("consent.grant_or_revoke", denials["delegated_agent"])

    def test_authorization_checks_relationship_and_consent_before_audit(self) -> None:
        order = self.contract["authorization_decision_order"]
        self.assertLess(order.index("object_relationship"), order.index("consent_purpose_and_field_scope"))
        self.assertLess(order.index("consent_purpose_and_field_scope"), order.index("audit"))

    def test_session_has_no_browser_token_storage_and_supports_revocation(self) -> None:
        session = self.contract["session_candidate"]
        self.assertEqual("secure_httponly_samesite_cookie", session["transport"])
        self.assertEqual("deny_local_storage", session["browser_token_storage"])
        self.assertTrue(session["csrf_protection_required"])
        self.assertTrue(session["server_side_revocation_required"])

    def test_negative_suite_covers_core_privilege_failures(self) -> None:
        requirements = set(self.contract["negative_test_requirements"])
        self.assertTrue(
            {
                "invitation_reuse_is_denied",
                "identity_pending_doctor_cannot_sign",
                "doctor_cannot_read_unrelated_patient",
                "client_role_claim_cannot_elevate_privilege",
                "delegated_agent_cannot_consent_or_sign",
                "revoked_session_is_denied",
            }.issubset(requirements)
        )


if __name__ == "__main__":
    unittest.main()
