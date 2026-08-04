from __future__ import annotations

import json
import mimetypes
import re
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import unquote

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
        self.security = IdentityAccessService(self.store)
        self.knowledge = KnowledgeService(self.store)
        self.service.seed_synthetic_demo()
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
                result = self.service.create_encounter(
                    patient_id=self._required(payload, "patient_id"),
                    encounter_type=payload.get("encounter_type", "follow_up"),
                    chief_complaint=self._required(payload, "chief_complaint"),
                    actor_id=payload.get("actor_id", "demo_operator"),
                )
                return self._json(start_response, 201, result)

            observation_match = re.fullmatch(r"/api/v1/encounters/([^/]+)/observations", path)
            if method == "POST" and observation_match:
                payload = self._payload(environ)
                result = self.service.add_observation(
                    encounter_id=observation_match.group(1),
                    category=self._required(payload, "category"),
                    content=self._required(payload, "content"),
                    source=payload.get("source", "patient"),
                    actor_id=payload.get("actor_id", "demo_operator"),
                )
                return self._json(start_response, 201, result)

            assessment_match = re.fullmatch(r"/api/v1/encounters/([^/]+)/assessments", path)
            if method == "POST" and assessment_match:
                payload = self._payload(environ)
                result = self.service.add_assessment(
                    encounter_id=assessment_match.group(1),
                    pattern=self._required(payload, "pattern"),
                    mechanism=self._required(payload, "mechanism"),
                    rationale=self._required(payload, "rationale"),
                    treatment_principle=self._required(payload, "treatment_principle"),
                    prescription_summary=payload.get("prescription_summary", ""),
                    actor_id=payload.get("actor_id", "demo_doctor"),
                    actor_role=payload.get("actor_role", "doctor"),
                )
                return self._json(start_response, 201, result)

            transition_match = re.fullmatch(
                r"/api/v1/encounters/([^/]+)/(review|sign|withdraw)", path
            )
            if method == "POST" and transition_match:
                payload = self._payload(environ)
                targets = {"review": "doctor_reviewed", "sign": "signed", "withdraw": "withdrawn"}
                result = self.service.transition_encounter(
                    encounter_id=transition_match.group(1),
                    target_status=targets[transition_match.group(2)],
                    actor_id=payload.get("actor_id", "demo_doctor"),
                    actor_role=payload.get("actor_role", "doctor"),
                )
                return self._json(start_response, 200, result)

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
