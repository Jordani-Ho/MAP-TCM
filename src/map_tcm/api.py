from __future__ import annotations

import json
import mimetypes
import re
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import unquote

from .errors import DomainError
from .service import ClinicalService
from .store import SQLiteStore


StartResponse = Callable[[str, list[tuple[str, str]]], None]


class MapApplication:
    def __init__(self, database_path: str | Path) -> None:
        self.store = SQLiteStore(database_path)
        self.store.initialize()
        self.service = ClinicalService(self.store)
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
        reasons = {200: "OK", 201: "Created", 400: "Bad Request", 403: "Forbidden", 404: "Not Found"}
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
