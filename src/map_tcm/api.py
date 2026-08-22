from __future__ import annotations

import json
import mimetypes
import re
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import unquote

from .care_loop import CareLoopService
from .care_session import SessionAuthorizedCareLoopService
from .clinical_session import SessionAuthorizedClinicalService
from .errors import DomainError
from .identity import IdentityAccessService
from .knowledge import KnowledgeService
from .service import ClinicalService
from .store import SQLiteStore


StartResponse = Callable[[str, list[tuple[str, str]]], None]


class MapApplication:
    def __init__(self, database_path: str | Path) -> None:
        self.store = SQLiteStore(database_path)
        self.store.initialize()
        self.service = ClinicalService(self.store)
        contract_path = Path(__file__).resolve().parents[2] / "schemas" / "tcm_clinical_fields_v0.1.json"
        self.clinical_field_contract = self.service.field_registry.register_contract(contract_path)
        self.care_loop = CareLoopService(self.store, self.service)
        self.security = IdentityAccessService(self.store)
        self.authorized_clinical = SessionAuthorizedClinicalService(
            self.store, self.service, self.security
        )
        self.authorized_care_loop = SessionAuthorizedCareLoopService(
            self.store,
            self.service,
            self.care_loop,
            self.security,
            self.authorized_clinical,
        )
        self.knowledge = KnowledgeService(self.store)
        self.service.seed_synthetic_demo()
        self.synthetic_context = self._seed_synthetic_clinical_context()
        self.static_root = Path(__file__).with_name("static")

    def __call__(self, environ: dict[str, Any], start_response: StartResponse) -> Iterable[bytes]:
        method = environ.get("REQUEST_METHOD", "GET").upper()
        path = unquote(environ.get("PATH_INFO", "/"))
        try:
            if method == "GET" and path == "/health":
                return self._json(
                    start_response,
                    200,
                    {
                        "status": "ok",
                        "service": "map-tcm-alpha",
                        "data_mode": "synthetic_only",
                    },
                )
            if method == "GET" and path == "/api/v1/overview":
                return self._json(start_response, 200, self.store.overview())
            if method == "GET" and path == "/api/v1/knowledge":
                return self._json(start_response, 200, self.knowledge.overview())
            if method == "GET" and path == "/api/v1/security":
                return self._json(start_response, 200, self.security.overview())
            if method == "GET" and path == "/api/v1/clinical-fields":
                return self._json(start_response, 200, self.clinical_field_contract)
            if method == "GET" and path == "/api/v1/synthetic/context":
                remote_addr = environ.get("REMOTE_ADDR")
                if remote_addr not in {None, "", "127.0.0.1", "::1"}:
                    raise DomainError(
                        "synthetic_context_local_only",
                        "合成演示会话只允许从本机工作台获取。",
                        status=403,
                    )
                return self._json(
                    start_response,
                    200,
                    {
                        "data_mode": "synthetic_only",
                        "doctor_session_id": self.synthetic_context["doctor_session_id"],
                        "patient_session_id": self.synthetic_context["patient_session_id"],
                    },
                )
            if method == "POST" and path == "/api/v1/knowledge/contributions":
                payload = self._payload(environ)
                contributor_id = self._required(payload, "contributor_id")
                result = self.knowledge.create_contribution(
                    subject_code=self._required(payload, "subject_code"),
                    is_synthetic=payload.get("is_synthetic") is True,
                    contributor_id=contributor_id,
                    contributor_role=self._required(payload, "contributor_role"),
                    content=self._required(payload, "content"),
                    source_ref=self._required(payload, "source_ref"),
                    access_level=self._required(payload, "access_level"),
                    withdrawal_rule=self._required(payload, "withdrawal_rule"),
                    actor_id=payload.get("actor_id", contributor_id),
                )
                return self._json(start_response, 201, result)

            contribution_action_match = re.fullmatch(
                r"/api/v1/knowledge/contributions/([^/]+)/(consent|withdraw)", path
            )
            if method == "POST" and contribution_action_match:
                payload = self._payload(environ)
                contribution_id = contribution_action_match.group(1)
                if contribution_action_match.group(2) == "consent":
                    result = self.knowledge.consent_contribution(
                        contribution_id=contribution_id,
                        consent_reference=self._required(payload, "consent_reference"),
                        actor_id=self._required(payload, "actor_id"),
                        actor_role=self._required(payload, "actor_role"),
                    )
                else:
                    result = self.knowledge.withdraw_contribution(
                        contribution_id=contribution_id,
                        reason=self._required(payload, "reason"),
                        actor_id=self._required(payload, "actor_id"),
                        actor_role=self._required(payload, "actor_role"),
                    )
                return self._json(start_response, 200, result)

            if method == "POST" and path == "/api/v1/knowledge/claims":
                payload = self._payload(environ)
                parent_claim_id = payload.get("parent_claim_id")
                if parent_claim_id is not None and not isinstance(parent_claim_id, str):
                    raise DomainError("invalid_parent_claim", "父主张编号格式无效。")
                result = self.knowledge.create_claim(
                    contribution_id=self._required(payload, "contribution_id"),
                    claim_kind=self._required(payload, "claim_kind"),
                    statement=self._required(payload, "statement"),
                    source_ref=self._required(payload, "source_ref"),
                    author_id=self._required(payload, "author_id"),
                    author_role=self._required(payload, "author_role"),
                    parent_claim_id=parent_claim_id,
                )
                return self._json(start_response, 201, result)

            claim_action_match = re.fullmatch(
                r"/api/v1/knowledge/claims/([^/]+)/(submit|reviews|snapshots)", path
            )
            if method == "POST" and claim_action_match:
                payload = self._payload(environ)
                claim_id = claim_action_match.group(1)
                action = claim_action_match.group(2)
                if action == "submit":
                    result = self.knowledge.submit_claim_for_review(
                        claim_id=claim_id,
                        actor_id=self._required(payload, "actor_id"),
                        actor_role=self._required(payload, "actor_role"),
                    )
                    return self._json(start_response, 200, result)
                if action == "reviews":
                    result = self.knowledge.add_review(
                        claim_id=claim_id,
                        verdict=self._required(payload, "verdict"),
                        rationale=self._required(payload, "rationale"),
                        reviewer_id=self._required(payload, "reviewer_id"),
                        reviewer_role=self._required(payload, "reviewer_role"),
                    )
                    return self._json(start_response, 201, result)
                result = self.knowledge.create_consensus_snapshot(
                    claim_id=claim_id,
                    target_state=self._required(payload, "target_state"),
                    summary=self._required(payload, "summary"),
                    actor_id=self._required(payload, "actor_id"),
                    actor_role=self._required(payload, "actor_role"),
                )
                return self._json(start_response, 201, result)
            if method == "POST" and path == "/api/v1/encounters":
                payload = self._payload(environ)
                result = self.authorized_clinical.create_encounter(
                    session_id=self._session_id(environ),
                    patient_id=self._required(payload, "patient_id"),
                    encounter_type=payload.get("encounter_type", "follow_up"),
                    chief_complaint=self._required(payload, "chief_complaint"),
                    purpose=self._required(payload, "purpose"),
                    data_categories=self._string_list(payload, "data_categories"),
                    client_role_claim=payload.get("actor_role"),
                )
                return self._json(start_response, 201, result)

            observation_match = re.fullmatch(r"/api/v1/encounters/([^/]+)/observations", path)
            if method == "POST" and observation_match:
                payload = self._payload(environ)
                result = self.authorized_clinical.add_observation(
                    session_id=self._session_id(environ),
                    encounter_id=observation_match.group(1),
                    category=self._required(payload, "category"),
                    content=self._required(payload, "content"),
                    source=payload.get("source", "patient"),
                    purpose=self._required(payload, "purpose"),
                    data_categories=self._string_list(payload, "data_categories"),
                    client_role_claim=payload.get("actor_role"),
                )
                return self._json(start_response, 201, result)

            field_match = re.fullmatch(r"/api/v1/encounters/([^/]+)/fields", path)
            if method == "POST" and field_match:
                payload = self._payload(environ)
                result = self.authorized_clinical.add_structured_field(
                    session_id=self._session_id(environ),
                    encounter_id=field_match.group(1),
                    field_id=self._required(payload, "field_id"),
                    value=payload.get("value"),
                    unknown_state=payload.get("unknown_state"),
                    source_role=self._required(payload, "source_role"),
                    review_status=payload.get("review_status", "draft"),
                    purpose=self._required(payload, "purpose"),
                    data_categories=self._string_list(payload, "data_categories"),
                    client_role_claim=payload.get("actor_role"),
                )
                return self._json(start_response, 201, result)

            readiness_match = re.fullmatch(
                r"/api/v1/encounters/([^/]+)/candidate-readiness", path
            )
            if method == "GET" and readiness_match:
                return self._json(
                    start_response,
                    200,
                    self.service.candidate_field_readiness(readiness_match.group(1)),
                )

            assessment_match = re.fullmatch(r"/api/v1/encounters/([^/]+)/assessments", path)
            if method == "POST" and assessment_match:
                payload = self._payload(environ)
                result = self.authorized_clinical.add_assessment(
                    session_id=self._session_id(environ),
                    encounter_id=assessment_match.group(1),
                    pattern=self._required(payload, "pattern"),
                    mechanism=self._required(payload, "mechanism"),
                    rationale=self._required(payload, "rationale"),
                    treatment_principle=self._required(payload, "treatment_principle"),
                    prescription_summary=payload.get("prescription_summary", ""),
                    purpose=self._required(payload, "purpose"),
                    data_categories=self._string_list(payload, "data_categories"),
                    client_role_claim=payload.get("actor_role"),
                )
                return self._json(start_response, 201, result)

            transition_match = re.fullmatch(
                r"/api/v1/encounters/([^/]+)/(review|sign|withdraw|revise)", path
            )
            if method == "POST" and transition_match:
                payload = self._payload(environ)
                action = transition_match.group(2)
                if action == "revise":
                    result = self.authorized_clinical.start_revision(
                        session_id=self._session_id(environ),
                        encounter_id=transition_match.group(1),
                        reason=self._required(payload, "reason"),
                        purpose=self._required(payload, "purpose"),
                        data_categories=self._string_list(payload, "data_categories"),
                        client_role_claim=payload.get("actor_role"),
                    )
                    return self._json(start_response, 201, result)
                targets = {"review": "doctor_reviewed", "sign": "signed", "withdraw": "withdrawn"}
                result = self.authorized_clinical.transition_encounter(
                    session_id=self._session_id(environ),
                    encounter_id=transition_match.group(1),
                    target_status=targets[action],
                    purpose=self._required(payload, "purpose"),
                    data_categories=self._string_list(payload, "data_categories"),
                    client_role_claim=payload.get("actor_role"),
                    reason=self._required(payload, "reason") if action == "withdraw" else "",
                )
                return self._json(start_response, 200, result)

            execution_match = re.fullmatch(
                r"/api/v1/care-loop/plans/([^/]+)/execution", path
            )
            if method == "POST" and execution_match:
                payload = self._payload(environ)
                result = self.authorized_care_loop.record_execution(
                    session_id=self._session_id(environ),
                    idempotency_key=self._idempotency_key(environ),
                    plan_version_id=execution_match.group(1),
                    event_type=self._required(payload, "event_type"),
                    detail=self._object(payload, "detail"),
                    occurred_at=payload.get("occurred_at"),
                )
                return self._json(
                    start_response,
                    200 if result["idempotency_replayed"] else 201,
                    result,
                )

            feedback_match = re.fullmatch(
                r"/api/v1/care-loop/plans/([^/]+)/feedback", path
            )
            if method == "POST" and feedback_match:
                payload = self._payload(environ)
                result = self.authorized_care_loop.record_feedback(
                    session_id=self._session_id(environ),
                    idempotency_key=self._idempotency_key(environ),
                    plan_version_id=feedback_match.group(1),
                    response_code=self._required(payload, "response_code"),
                    adverse_events=self._optional_string_list(payload, "adverse_events"),
                    narrative=self._required(payload, "narrative"),
                )
                return self._json(
                    start_response,
                    200 if result["idempotency_replayed"] else 201,
                    result,
                )

            if method == "GET" and path in {"/", "/index.html", "/app.js", "/styles.css"}:
                filename = "index.html" if path in {"/", "/index.html"} else path.removeprefix("/")
                return self._static(start_response, filename)
            return self._json(start_response, 404, {"error": "not_found", "message": "资源不存在。"})
        except DomainError as error:
            return self._json(
                start_response,
                error.status,
                {"error": error.code, "message": error.message},
            )
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            return self._json(
                start_response,
                400,
                {"error": "invalid_request", "message": "请求格式无效。"},
            )

    @staticmethod
    def _required(payload: dict[str, Any], key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            raise DomainError("missing_field", f"缺少必填字段：{key}")
        return value.strip()

    @staticmethod
    def _string_list(payload: dict[str, Any], key: str) -> list[str]:
        value = payload.get(key)
        if (
            not isinstance(value, list)
            or not value
            or any(not isinstance(item, str) or not item.strip() for item in value)
        ):
            raise DomainError("missing_field", f"缺少有效列表字段：{key}")
        return [item.strip() for item in value]

    @staticmethod
    def _optional_string_list(payload: dict[str, Any], key: str) -> list[str]:
        value = payload.get(key, [])
        if not isinstance(value, list) or any(
            not isinstance(item, str) or not item.strip() for item in value
        ):
            raise DomainError("invalid_field", f"字段必须是文本列表：{key}")
        return [item.strip() for item in value]

    @staticmethod
    def _object(payload: dict[str, Any], key: str) -> dict[str, Any]:
        value = payload.get(key)
        if not isinstance(value, dict):
            raise DomainError("invalid_field", f"字段必须是结构化对象：{key}")
        return value

    @staticmethod
    def _session_id(environ: dict[str, Any]) -> str:
        value = environ.get("HTTP_X_MAP_SESSION_ID")
        if not isinstance(value, str) or not value.strip():
            raise DomainError(
                "session_required",
                "临床写入必须提供服务端会话。",
                status=403,
            )
        return value.strip()

    @staticmethod
    def _idempotency_key(environ: dict[str, Any]) -> str:
        value = environ.get("HTTP_IDEMPOTENCY_KEY")
        if not isinstance(value, str) or not value.strip():
            raise DomainError(
                "idempotency_key_required",
                "患者执行或反馈写入必须提供防重复键。",
            )
        return value.strip()

    def _seed_synthetic_clinical_context(self) -> dict[str, str]:
        purpose = "synthetic_clinical_care"
        data_categories = [
            "chief_complaint",
            "observations",
            "assessment",
            "treatment_principle",
            "prescription",
        ]

        def ensure_subject(display_code: str, role: str) -> dict[str, Any]:
            with self.store.connect() as connection:
                row = connection.execute(
                    "SELECT * FROM security_subjects WHERE display_code = ?",
                    (display_code,),
                ).fetchone()
            if row is not None:
                return dict(row)
            return self.security.create_synthetic_subject(
                display_code=display_code,
                role=role,
                actor_id="system_seed",
            )

        doctor = ensure_subject("SYN-DEMO-DOCTOR-001", "verified_doctor")
        patient = ensure_subject("SYN-DEMO-001", "patient")
        with self.store.connect() as connection:
            patient_record = connection.execute(
                "SELECT id FROM patients WHERE display_code = ? AND is_synthetic = 1",
                (patient["display_code"],),
            ).fetchone()
        if patient_record is None:
            raise DomainError(
                "synthetic_patient_record_required",
                "合成患者记录初始化失败。",
                status=500,
            )
        binding = self.service.bind_patient_security_subject(
            patient_id=patient_record["id"],
            patient_subject_id=patient["id"],
            actor_id="system_seed",
        )
        doctor_session = self.security.open_synthetic_session(subject_id=doctor["id"])
        patient_session = self.security.open_synthetic_session(subject_id=patient["id"])
        self.security.step_up_session(session_id=doctor_session["id"])
        self.security.step_up_session(session_id=patient_session["id"])

        with self.store.connect() as connection:
            active = connection.execute(
                """
                SELECT cr.id
                FROM care_relationships cr
                JOIN security_consents sc ON sc.relationship_id = cr.id
                WHERE cr.patient_subject_id = ?
                  AND cr.practitioner_subject_id = ?
                  AND cr.status = 'active'
                  AND sc.status = 'active'
                  AND sc.purpose = ?
                ORDER BY cr.created_at DESC LIMIT 1
                """,
                (patient["id"], doctor["id"], purpose),
            ).fetchone()
        if active is None:
            relationship = self.security.create_care_relationship(
                doctor_session_id=doctor_session["id"],
                patient_subject_id=patient["id"],
                purpose=purpose,
                data_categories=data_categories,
                valid_days=365,
            )
            self.security.accept_care_relationship(
                patient_session_id=patient_session["id"],
                relationship_id=relationship["id"],
                purpose=purpose,
                data_categories=data_categories,
            )
        return {
            "doctor_subject_id": doctor["id"],
            "patient_subject_id": patient["id"],
            "patient_vault_id": binding["vault_id"],
            "doctor_session_id": doctor_session["id"],
            "patient_session_id": patient_session["id"],
            "purpose": purpose,
        }

    @staticmethod
    def _payload(environ: dict[str, Any]) -> dict[str, Any]:
        length = int(environ.get("CONTENT_LENGTH") or 0)
        if length <= 0 or length > 65_536:
            raise ValueError("invalid content length")
        body = environ["wsgi.input"].read(length)
        payload = json.loads(body.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("JSON object required")
        return payload

    @staticmethod
    def _headers(content_type: str, length: int) -> list[tuple[str, str]]:
        return [
            ("Content-Type", content_type),
            ("Content-Length", str(length)),
            ("Cache-Control", "no-store"),
            ("X-Content-Type-Options", "nosniff"),
            ("X-Frame-Options", "DENY"),
            ("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'"),
        ]

    def _json(
        self,
        start_response: StartResponse,
        status_code: int,
        payload: dict[str, Any],
    ) -> list[bytes]:
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        reasons = {
            200: "OK",
            201: "Created",
            400: "Bad Request",
            403: "Forbidden",
            404: "Not Found",
            409: "Conflict",
        }
        start_response(f"{status_code} {reasons.get(status_code, 'Error')}", self._headers("application/json; charset=utf-8", len(body)))
        return [body]

    def _static(self, start_response: StartResponse, filename: str) -> list[bytes]:
        path = self.static_root / filename
        if not path.is_file() or path.parent != self.static_root:
            return self._json(start_response, 404, {"error": "not_found", "message": "资源不存在。"})
        body = path.read_bytes()
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        start_response("200 OK", self._headers(f"{mime}; charset=utf-8", len(body)))
        return [body]
