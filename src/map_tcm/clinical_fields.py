from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from .errors import DomainError
from .store import SQLiteStore, utc_now


UNKNOWN_STATES = {"unknown", "not_collected", "not_applicable", "declined"}
FORMAL_SOURCE_ROLES = {"patient", "doctor", "file", "system"}
REVIEW_STATUSES = {"draft", "doctor_reviewed", "signed", "withdrawn"}


class ClinicalFieldRegistryService:
    """Versioned server registry for the unapproved TCM-CLIN-001 candidate.

    Registration deliberately accepts only a synthetic, clinically unapproved
    candidate. A later doctor-approved contract must be a new version, never a
    mutation of this baseline.
    """

    def __init__(self, store: SQLiteStore) -> None:
        self.store = store

    def register_contract(self, contract_path: str | Path) -> dict[str, Any]:
        path = Path(contract_path)
        try:
            contract = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise DomainError("invalid_clinical_field_contract", "临床字段合同无法读取。") from error
        self._validate_contract(contract)
        canonical = self._canonical_json(contract)
        contract_hash = self._sha256(canonical)
        registry_id = self._registry_id(contract["contract_id"], contract["schema_version"])
        with self.store.connect() as connection:
            existing = connection.execute(
                """
                SELECT * FROM clinical_field_registry_versions
                WHERE contract_id = ? AND schema_version = ?
                """,
                (contract["contract_id"], contract["schema_version"]),
            ).fetchone()
            if existing is not None:
                if existing["contract_hash"] != contract_hash:
                    raise DomainError(
                        "clinical_field_contract_hash_conflict",
                        "同一版本的临床字段合同内容已发生变化，必须发布新版本。",
                        status=409,
                    )
                return self._summary(connection, existing["id"])
            connection.execute(
                """
                INSERT INTO clinical_field_registry_versions (
                    id, contract_id, schema_version, status, language,
                    synthetic_only, clinical_approval, contract_hash, loaded_at
                ) VALUES (?, ?, ?, 'candidate_for_doctor_review', ?, 1, 0, ?, ?)
                """,
                (
                    registry_id,
                    contract["contract_id"],
                    contract["schema_version"],
                    contract.get("language", "zh-CN"),
                    contract_hash,
                    utc_now(),
                ),
            )
            for field in contract["fields"]:
                connection.execute(
                    """
                    INSERT INTO clinical_field_definitions (
                        registry_version_id, field_id, encounters_json, section,
                        label_zh, value_type, requiredness, allow_unknown,
                        evidence_tag, source_roles_json, review_owner, sensitivity,
                        condition_text, display_order
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        registry_id,
                        field["field_id"],
                        self._canonical_json(field["encounters"]),
                        field["section"],
                        field["label_zh"],
                        field["value_type"],
                        field["requiredness"],
                        int(field["allow_unknown"]),
                        field["evidence_tag"],
                        self._canonical_json(field["source_roles"]),
                        field["review_owner"],
                        field["sensitivity"],
                        field.get("condition"),
                        field["order"],
                    ),
                )
            self.store.audit(
                connection,
                event_type="clinical_field_registry.candidate_loaded",
                actor_id="system_registry",
                object_type="clinical_field_registry_version",
                object_id=registry_id,
                metadata={
                    "contract_id": contract["contract_id"],
                    "schema_version": contract["schema_version"],
                    "field_count": len(contract["fields"]),
                    "clinical_approval": False,
                },
            )
            return self._summary(connection, registry_id)

    def active_candidate(self, connection: sqlite3.Connection) -> sqlite3.Row:
        row = connection.execute(
            """
            SELECT * FROM clinical_field_registry_versions
            WHERE status = 'candidate_for_doctor_review'
              AND synthetic_only = 1 AND clinical_approval = 0
            ORDER BY loaded_at DESC LIMIT 1
            """
        ).fetchone()
        if row is None:
            raise DomainError(
                "clinical_field_registry_not_loaded",
                "中医临床候选字段注册表尚未加载。",
                status=503,
            )
        return row

    def validate_value(
        self,
        connection: sqlite3.Connection,
        *,
        encounter_type: str,
        field_id: str,
        value: Any,
        unknown_state: str | None,
        source_role: str,
        review_status: str,
        actor_role: str,
    ) -> dict[str, Any]:
        registry = self.active_candidate(connection)
        definition = connection.execute(
            """
            SELECT * FROM clinical_field_definitions
            WHERE registry_version_id = ? AND field_id = ?
            """,
            (registry["id"], field_id),
        ).fetchone()
        if definition is None:
            raise DomainError("unregistered_clinical_field", "字段不属于当前候选合同。")
        if encounter_type not in json.loads(definition["encounters_json"]):
            raise DomainError("field_not_applicable", "字段不适用于当前就诊类型。")
        if source_role not in FORMAL_SOURCE_ROLES:
            raise DomainError(
                "formal_ai_source_forbidden",
                "AI 内容必须保存在独立草稿区，不能直接写入正式临床字段。",
            )
        allowed_sources = set(json.loads(definition["source_roles_json"]))
        if source_role not in allowed_sources:
            raise DomainError("field_source_role_forbidden", "该来源角色不能填写此字段。")
        if review_status not in REVIEW_STATUSES:
            raise DomainError("invalid_field_review_status", "字段审核状态无效。")
        if unknown_state is not None:
            if unknown_state not in UNKNOWN_STATES:
                raise DomainError("invalid_unknown_state", "未知状态值无效。")
            if not definition["allow_unknown"]:
                raise DomainError("field_unknown_forbidden", "该字段不允许使用未知状态。")
            if value is not None:
                raise DomainError("unknown_value_conflict", "未知状态与实际字段值不能同时出现。")
        elif value is None:
            raise DomainError("field_value_required", "字段值或显式未知状态至少需要一个。")
        else:
            self._validate_value_type(definition["value_type"], value)
        if definition["evidence_tag"] == "doctor_judgment":
            if source_role != "doctor" or actor_role != "verified_doctor":
                raise DomainError(
                    "doctor_judgment_source_required",
                    "医生判断字段只能由已核验医生填写。",
                    status=403,
                )
            if review_status not in {"doctor_reviewed", "signed"}:
                raise DomainError(
                    "doctor_judgment_review_required",
                    "医生判断字段必须明确标记为医生已审核。",
                )
        return {
            "registry_version_id": registry["id"],
            "field_id": field_id,
            "evidence_tag": definition["evidence_tag"],
            "source_role": source_role,
            "review_status": review_status,
            "unknown_state": unknown_state,
            "contract_status": registry["status"],
            "clinical_approval": False,
        }

    def readiness(self, encounter_id: str) -> dict[str, Any]:
        with self.store.connect() as connection:
            encounter = connection.execute(
                "SELECT * FROM encounters WHERE id = ?", (encounter_id,)
            ).fetchone()
            if encounter is None:
                raise DomainError("encounter_not_found", "就诊记录不存在。", status=404)
            registry = self.active_candidate(connection)
            required = connection.execute(
                """
                SELECT field_id FROM clinical_field_definitions
                WHERE registry_version_id = ? AND requiredness = 'required'
                ORDER BY display_order
                """,
                (registry["id"],),
            ).fetchall()
            applicable = [
                row["field_id"]
                for row in required
                if encounter["encounter_type"]
                in json.loads(
                    connection.execute(
                        """
                        SELECT encounters_json FROM clinical_field_definitions
                        WHERE registry_version_id = ? AND field_id = ?
                        """,
                        (registry["id"], row["field_id"]),
                    ).fetchone()["encounters_json"]
                )
            ]
            present_rows = connection.execute(
                """
                SELECT DISTINCT l.field_id
                FROM clinical_record_versions rv
                JOIN clinical_field_values fv ON fv.record_version_id = rv.id
                JOIN clinical_field_registry_links l ON l.field_value_id = fv.id
                WHERE rv.encounter_id = ? AND rv.version_number = ?
                """,
                (encounter_id, encounter["version"]),
            ).fetchall()
            present = {row["field_id"] for row in present_rows}
            missing = [field_id for field_id in applicable if field_id not in present]
        return {
            "encounter_id": encounter_id,
            "contract_id": registry["contract_id"],
            "schema_version": registry["schema_version"],
            "contract_status": registry["status"],
            "clinical_approval": False,
            "candidate_gate_only": True,
            "required_count": len(applicable),
            "present_required_count": len(applicable) - len(missing),
            "missing_required_fields": missing,
            "candidate_complete": not missing,
        }

    def _summary(self, connection: sqlite3.Connection, registry_id: str) -> dict[str, Any]:
        row = connection.execute(
            "SELECT * FROM clinical_field_registry_versions WHERE id = ?", (registry_id,)
        ).fetchone()
        count = connection.execute(
            "SELECT COUNT(*) FROM clinical_field_definitions WHERE registry_version_id = ?",
            (registry_id,),
        ).fetchone()[0]
        return {
            "id": row["id"],
            "contract_id": row["contract_id"],
            "schema_version": row["schema_version"],
            "status": row["status"],
            "synthetic_only": True,
            "clinical_approval": False,
            "contract_hash": row["contract_hash"],
            "field_count": count,
        }

    @staticmethod
    def _validate_contract(contract: dict[str, Any]) -> None:
        required = {"contract_id", "schema_version", "status", "synthetic_only", "clinical_approval", "fields"}
        if not required.issubset(contract):
            raise DomainError("invalid_clinical_field_contract", "临床字段合同缺少必要元数据。")
        if contract["status"] != "candidate_for_doctor_review":
            raise DomainError("candidate_contract_required", "当前阶段只允许加载待医生评审合同。")
        if contract["synthetic_only"] is not True or contract["clinical_approval"] is not False:
            raise DomainError(
                "unapproved_synthetic_contract_required",
                "当前阶段只允许加载未获临床批准的合成数据合同。",
            )
        field_ids = [field.get("field_id") for field in contract["fields"]]
        if not field_ids or None in field_ids or len(field_ids) != len(set(field_ids)):
            raise DomainError("invalid_clinical_field_contract", "字段标识缺失或重复。")

    @staticmethod
    def _validate_value_type(value_type: str, value: Any) -> None:
        if value_type == "list" and not isinstance(value, list):
            raise DomainError("field_value_type_mismatch", "字段值应为列表。")
        if value_type == "structured" and not isinstance(value, dict):
            raise DomainError("field_value_type_mismatch", "字段值应为结构化对象。")
        if value_type == "structured_text" and not isinstance(value, (str, dict)):
            raise DomainError("field_value_type_mismatch", "字段值应为文本或结构化对象。")
        if value_type in {"string", "code", "text", "datetime", "reference"} and not isinstance(value, str):
            raise DomainError("field_value_type_mismatch", "字段值应为文本。")
        try:
            json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError) as error:
            raise DomainError("field_value_not_json", "字段值必须可序列化。") from error

    @staticmethod
    def _registry_id(contract_id: str, schema_version: str) -> str:
        safe = "_".join(part for part in f"{contract_id}_{schema_version}".replace("-", "_").split(".") if part)
        return f"field_registry_{safe.lower()}"

    @staticmethod
    def _canonical_json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _sha256(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()
