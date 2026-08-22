import io
import json
import tempfile
import unittest
from pathlib import Path

from map_tcm.api import MapApplication


class ApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.app = MapApplication(Path(self.temp_dir.name) / "api.db")
        overview = self.app.store.overview()
        self.patient_id = overview["patients"][0]["id"]
        self.encounter_id = overview["encounters"][0]["id"]
        self.scope = {
            "purpose": "synthetic_clinical_care",
            "data_categories": [
                "chief_complaint",
                "observations",
                "assessment",
                "treatment_principle",
                "prescription",
            ],
        }

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def request(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
        *,
        session_id: str | None = None,
        remote_addr: str | None = None,
    ) -> tuple[int, dict]:
        raw = json.dumps(payload or {}).encode("utf-8")
        environ = {
            "REQUEST_METHOD": method,
            "PATH_INFO": path,
            "CONTENT_LENGTH": str(len(raw)) if payload is not None else "0",
            "wsgi.input": io.BytesIO(raw),
        }
        if session_id is not None:
            environ["HTTP_X_MAP_SESSION_ID"] = session_id
        if remote_addr is not None:
            environ["REMOTE_ADDR"] = remote_addr
        status: list[str] = []

        def start_response(value: str, _headers: list[tuple[str, str]]) -> None:
            status.append(value)

        body = b"".join(self.app(environ, start_response))
        return int(status[0].split()[0]), json.loads(body.decode("utf-8"))

    def test_health(self) -> None:
        status, payload = self.request("GET", "/health")
        self.assertEqual(200, status)
        self.assertEqual("ok", payload["status"])
        self.assertEqual("synthetic_only", payload["data_mode"])

    def test_candidate_field_contract_endpoint_keeps_clinical_gate_closed(self) -> None:
        status, payload = self.request("GET", "/api/v1/clinical-fields")
        self.assertEqual(200, status)
        self.assertEqual("TCM-CLIN-001", payload["contract_id"])
        self.assertEqual("candidate_for_doctor_review", payload["status"])
        self.assertFalse(payload["clinical_approval"])
        self.assertEqual(47, payload["field_count"])

    def test_server_session_can_append_registered_doctor_field_and_read_candidate_gap(self) -> None:
        status, field = self.request(
            "POST",
            f"/api/v1/encounters/{self.encounter_id}/fields",
            {
                "field_id": "assessment.pattern",
                "value": ["synthetic api pattern"],
                "source_role": "doctor",
                "review_status": "doctor_reviewed",
                **self.scope,
            },
            session_id=self.app.synthetic_context["doctor_session_id"],
        )
        self.assertEqual(201, status)
        self.assertFalse(field["clinical_approval"])
        status, readiness = self.request(
            "GET", f"/api/v1/encounters/{self.encounter_id}/candidate-readiness"
        )
        self.assertEqual(200, status)
        self.assertTrue(readiness["candidate_gate_only"])
        self.assertFalse(readiness["clinical_approval"])

    def test_security_overview_exposes_no_identity_or_token_material(self) -> None:
        status, payload = self.request("GET", "/api/v1/security")
        self.assertEqual(200, status)
        self.assertEqual("synthetic_security_prototype", payload["status"])
        self.assertFalse(payload["production_identity_ready"])
        serialized = json.dumps(payload, ensure_ascii=False).lower()
        self.assertNotIn("token_hash", serialized)
        self.assertNotIn("recipient_binding", serialized)

    def test_overview_contains_only_synthetic_demo(self) -> None:
        status, payload = self.request("GET", "/api/v1/overview")
        self.assertEqual(200, status)
        self.assertEqual(1, payload["counts"]["patients"])
        self.assertTrue(payload["patients"][0]["is_synthetic"])

    def test_unknown_route_is_safe_json(self) -> None:
        status, payload = self.request("GET", "/missing")
        self.assertEqual(404, status)
        self.assertEqual("not_found", payload["error"])

    def test_synthetic_context_is_local_only(self) -> None:
        status, payload = self.request(
            "GET", "/api/v1/synthetic/context", remote_addr="198.51.100.10"
        )
        self.assertEqual(403, status)
        self.assertEqual("synthetic_context_local_only", payload["error"])

    def test_client_role_claim_cannot_replace_server_session(self) -> None:
        status, payload = self.request(
            "POST",
            f"/api/v1/encounters/{self.encounter_id}/assessments",
            {
                "pattern": "synthetic pattern",
                "mechanism": "synthetic mechanism",
                "rationale": "synthetic rationale",
                "treatment_principle": "synthetic principle",
                "prescription_summary": "none",
                "actor_role": "verified_doctor",
                **self.scope,
            },
        )
        self.assertEqual(403, status)
        self.assertEqual("session_required", payload["error"])

        admin = self.app.security.create_synthetic_subject(
            display_code="SYN-API-ADMIN-001",
            role="clinic_admin",
            actor_id="test",
        )
        admin_session = self.app.security.open_synthetic_session(subject_id=admin["id"])
        status, payload = self.request(
            "POST",
            f"/api/v1/encounters/{self.encounter_id}/assessments",
            {
                "pattern": "synthetic pattern",
                "mechanism": "synthetic mechanism",
                "rationale": "synthetic rationale",
                "treatment_principle": "synthetic principle",
                "prescription_summary": "none",
                "actor_role": "verified_doctor",
                **self.scope,
            },
            session_id=admin_session["id"],
        )
        self.assertEqual(403, status)
        self.assertEqual("capability_forbidden", payload["error"])

    def test_server_session_authorizes_assessment_review_and_sign(self) -> None:
        session_id = self.app.synthetic_context["doctor_session_id"]
        status, assessment = self.request(
            "POST",
            f"/api/v1/encounters/{self.encounter_id}/assessments",
            {
                "pattern": "synthetic pattern",
                "mechanism": "synthetic mechanism",
                "rationale": "synthetic rationale",
                "treatment_principle": "synthetic principle",
                "prescription_summary": "none",
                "actor_id": "SYN-CLIENT-FORGED-ACTOR",
                "actor_role": "clinic_admin",
                **self.scope,
            },
            session_id=session_id,
        )
        self.assertEqual(201, status)
        self.assertEqual("doctor_reviewed", assessment["status"])
        with self.app.store.connect() as connection:
            author = connection.execute(
                "SELECT author_id FROM assessments WHERE id = ?", (assessment["id"],)
            ).fetchone()
        self.assertEqual(self.app.synthetic_context["doctor_subject_id"], author["author_id"])

        status, reviewed = self.request(
            "POST",
            f"/api/v1/encounters/{self.encounter_id}/review",
            self.scope,
            session_id=session_id,
        )
        self.assertEqual(200, status)
        self.assertEqual("doctor_reviewed", reviewed["status"])

        status, signed = self.request(
            "POST",
            f"/api/v1/encounters/{self.encounter_id}/sign",
            self.scope,
            session_id=session_id,
        )
        self.assertEqual(200, status)
        self.assertEqual("signed", signed["status"])

    def test_signed_record_revision_uses_server_session_and_new_version(self) -> None:
        session_id = self.app.synthetic_context["doctor_session_id"]
        status, _ = self.request(
            "POST",
            f"/api/v1/encounters/{self.encounter_id}/assessments",
            {
                "pattern": "synthetic pattern",
                "mechanism": "synthetic mechanism",
                "rationale": "synthetic rationale",
                "treatment_principle": "synthetic principle",
                "prescription_summary": "none",
                **self.scope,
            },
            session_id=session_id,
        )
        self.assertEqual(201, status)
        self.request(
            "POST",
            f"/api/v1/encounters/{self.encounter_id}/review",
            self.scope,
            session_id=session_id,
        )
        status, signed = self.request(
            "POST",
            f"/api/v1/encounters/{self.encounter_id}/sign",
            self.scope,
            session_id=session_id,
        )
        self.assertEqual(200, status)

        status, revised = self.request(
            "POST",
            f"/api/v1/encounters/{self.encounter_id}/revise",
            {
                "reason": "synthetic correction",
                "actor_role": "clinic_admin",
                **self.scope,
            },
            session_id=session_id,
        )
        self.assertEqual(201, status)
        self.assertEqual(2, revised["version_number"])
        self.assertEqual(signed["record_version_id"], revised["supersedes_version_id"])

    def test_cross_patient_write_is_denied(self) -> None:
        other_patient = self.app.service.create_patient(
            display_code="SYN-API-OTHER-001", is_synthetic=True, actor_id="test"
        )
        self.app.service.grant_consent(
            patient_id=other_patient["id"],
            purpose="legacy synthetic consent",
            scope=["clinical_record"],
            actor_id="test",
        )
        other_subject = self.app.security.create_synthetic_subject(
            display_code="SYN-API-OTHER-001",
            role="patient",
            actor_id="test",
        )
        self.app.service.bind_patient_security_subject(
            patient_id=other_patient["id"],
            patient_subject_id=other_subject["id"],
            actor_id="test",
        )
        status, payload = self.request(
            "POST",
            "/api/v1/encounters",
            {
                "patient_id": other_patient["id"],
                "encounter_type": "initial",
                "chief_complaint": "synthetic cross-patient attempt",
                "actor_role": "verified_doctor",
                **self.scope,
            },
            session_id=self.app.synthetic_context["doctor_session_id"],
        )
        self.assertEqual(403, status)
        self.assertEqual("active_relationship_required", payload["error"])

    def test_revoked_and_expired_sessions_are_denied(self) -> None:
        session_id = self.app.synthetic_context["doctor_session_id"]
        self.app.security.revoke_session(
            session_id=session_id,
            actor_id=self.app.synthetic_context["doctor_subject_id"],
        )
        status, payload = self.request(
            "POST",
            f"/api/v1/encounters/{self.encounter_id}/observations",
            {"category": "synthetic", "content": "denied", "source": "doctor", **self.scope},
            session_id=session_id,
        )
        self.assertEqual(403, status)
        self.assertEqual("session_revoked", payload["error"])

        new_session = self.app.security.open_synthetic_session(
            subject_id=self.app.synthetic_context["doctor_subject_id"]
        )
        with self.app.store.connect() as connection:
            connection.execute(
                "UPDATE security_sessions SET expires_at = '2000-01-01T00:00:00+00:00' WHERE id = ?",
                (new_session["id"],),
            )
        status, payload = self.request(
            "POST",
            f"/api/v1/encounters/{self.encounter_id}/observations",
            {"category": "synthetic", "content": "denied", "source": "doctor", **self.scope},
            session_id=new_session["id"],
        )
        self.assertEqual(403, status)
        self.assertEqual("session_expired", payload["error"])

    def test_revoked_consent_denies_new_clinical_write(self) -> None:
        with self.app.store.connect() as connection:
            consent = connection.execute(
                """
                SELECT sc.id
                FROM security_consents sc
                JOIN care_relationships cr ON cr.id = sc.relationship_id
                WHERE cr.patient_subject_id = ? AND sc.status = 'active'
                ORDER BY sc.created_at DESC LIMIT 1
                """,
                (self.app.synthetic_context["patient_subject_id"],),
            ).fetchone()
        self.app.security.revoke_consent(
            patient_session_id=self.app.synthetic_context["patient_session_id"],
            consent_id=consent["id"],
            reason="synthetic API revocation test",
        )
        status, payload = self.request(
            "POST",
            f"/api/v1/encounters/{self.encounter_id}/observations",
            {"category": "synthetic", "content": "denied", "source": "doctor", **self.scope},
            session_id=self.app.synthetic_context["doctor_session_id"],
        )
        self.assertEqual(403, status)
        self.assertEqual("active_consent_required", payload["error"])

    def test_synthetic_knowledge_contribution_api(self) -> None:
        status, contribution = self.request(
            "POST",
            "/api/v1/knowledge/contributions",
            {
                "subject_code": "SYN-API-KNOW-001",
                "is_synthetic": True,
                "contributor_id": "synthetic_patient_api",
                "contributor_role": "patient",
                "content": "Synthetic knowledge contribution through HTTP.",
                "source_ref": "synthetic://api/SYN-API-KNOW-001",
                "access_level": "commons_candidate",
                "withdrawal_rule": "Append withdrawal and stop future use.",
            },
        )
        self.assertEqual(201, status)
        self.assertEqual("private_draft", contribution["state"])

        status, consent = self.request(
            "POST",
            f"/api/v1/knowledge/contributions/{contribution['id']}/consent",
            {
                "consent_reference": "synthetic-consent://api/v1",
                "actor_id": "synthetic_patient_api",
                "actor_role": "patient",
            },
        )
        self.assertEqual(200, status)
        self.assertEqual("consented_contribution", consent["state"])

        status, overview = self.request("GET", "/api/v1/knowledge")
        self.assertEqual(200, status)
        self.assertEqual("synthetic_only", overview["mode"])
        self.assertEqual(1, overview["counts"]["consented_contributions"])


if __name__ == "__main__":
    unittest.main()
