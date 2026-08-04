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

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def request(self, method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
        raw = json.dumps(payload or {}).encode("utf-8")
        environ = {
            "REQUEST_METHOD": method,
            "PATH_INFO": path,
            "CONTENT_LENGTH": str(len(raw)) if payload is not None else "0",
            "wsgi.input": io.BytesIO(raw),
        }
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

    def test_overview_contains_only_synthetic_demo(self) -> None:
        status, payload = self.request("GET", "/api/v1/overview")
        self.assertEqual(200, status)
        self.assertEqual(1, payload["counts"]["patients"])
        self.assertTrue(payload["patients"][0]["is_synthetic"])

    def test_unknown_route_is_safe_json(self) -> None:
        status, payload = self.request("GET", "/missing")
        self.assertEqual(404, status)
        self.assertEqual("not_found", payload["error"])

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
