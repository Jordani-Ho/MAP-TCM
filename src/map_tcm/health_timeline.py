from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from .errors import DomainError
from .patient_vault import CONFIRMATION_BY_PROVENANCE, PatientVaultService
from .store import SQLiteStore, utc_now


OBSERVATION_KINDS = {
    "diet",
    "sleep",
    "elimination",
    "emotion_stress",
    "activity",
    "clothing_thermal",
    "body_sensation",
    "climate",
    "season",
    "solar_term",
    "location_context",
    "housing_environment",
}
OBSERVATION_STATES = {
    "observed",
    "unknown",
    "not_collected",
    "not_applicable",
    "declined",
}
SOURCE_TO_PROVENANCE = {
    "patient": "patient",
    "doctor": "doctor",
    "file": "file",
    "device": "device",
    "system": "system",
}
LIFESTYLE_KINDS = {
    "diet",
    "sleep",
    "elimination",
    "emotion_stress",
    "activity",
    "clothing_thermal",
}
ENVIRONMENT_KINDS = {
    "climate",
    "season",
    "solar_term",
    "location_context",
    "housing_environment",
}
LOCATION_GRANULARITIES = {"none", "climate_region", "city_level"}
FORBIDDEN_LOCATION_KEYS = {
    "exact_address",
    "street_address",
    "latitude",
    "longitude",
    "gps",
    "coordinates",
    "postal_code",
}
FORBIDDEN_CLINICAL_KEYS = {
    "diagnosis",
    "diagnosis_code",
    "syndrome_diagnosis",
    "prescription",
    "treatment_recommendation",
    "causal_claim",
}


class HealthTimelineService:
    """Append-only synthetic observations for 天—地—人 longitudinal context.

    The service records sourced observations only. It does not diagnose,
    calculate causation, prescribe, or silently infer calendar/environment data.
    """

    def __init__(self, store: SQLiteStore, patient_vault: PatientVaultService) -> None:
        self.store = store
        self.patient_vault = patient_vault

    def append_observation(
        self,
        *,
        vault_id: str,
        observation_kind: str,
        observation_state: str,
        value: dict[str, Any] | None,
        summary: str,
        source_type: str,
        source_ref: str,
        confirmation_status: str,
        observed_start_at: str,
        observed_end_at: str | None,
        timezone_name: str | None,
        location_context: dict[str, Any] | None,
        created_by: str,
    ) -> dict[str, Any]:
        if observation_kind not in OBSERVATION_KINDS:
            raise DomainError("invalid_health_observation_kind", "健康观察类型无效。")
        if observation_state not in OBSERVATION_STATES:
            raise DomainError("invalid_health_observation_state", "健康观察状态无效。")
        if source_type not in SOURCE_TO_PROVENANCE:
            raise DomainError(
                "invalid_health_observation_source",
                "结构化健康时间线只接受患者、医生、文件、设备或系统来源。",
            )
        provenance_role = SOURCE_TO_PROVENANCE[source_type]
        if CONFIRMATION_BY_PROVENANCE.get(confirmation_status) != provenance_role:
            raise DomainError(
                "health_observation_provenance_mismatch",
                "健康观察的确认状态与来源不一致。",
            )
        if not summary.strip():
            raise DomainError("health_observation_summary_required", "健康观察摘要不能为空。")

        normalized_value = value or {}
        if observation_state == "observed" and not normalized_value:
            raise DomainError("health_observation_value_required", "已观察状态必须提供结构化内容。")
        if observation_state != "observed" and normalized_value:
            raise DomainError(
                "health_observation_unknown_has_value",
                "未知、未采集、不适用或拒答状态不能附带推测内容。",
            )
        self._reject_forbidden_keys(normalized_value, FORBIDDEN_CLINICAL_KEYS)

        start_at = self._aware_timestamp(observed_start_at, "观察开始时间")
        end_at = self._aware_timestamp(observed_end_at or observed_start_at, "观察结束时间")
        if start_at > end_at:
            raise DomainError("health_observation_time_order", "观察结束时间不能早于开始时间。")
        normalized_timezone = timezone_name.strip() if timezone_name and timezone_name.strip() else None
        if normalized_timezone is not None and len(normalized_timezone) > 80:
            raise DomainError("invalid_health_timezone", "时区名称过长。")

        normalized_location = location_context or {}
        self._reject_forbidden_keys(normalized_location, FORBIDDEN_LOCATION_KEYS)
        granularity = str(normalized_location.get("granularity", "none"))
        if granularity not in LOCATION_GRANULARITIES:
            raise DomainError(
                "invalid_health_location_granularity",
                "时间线地点只能使用气候区域或城市级抽象。",
            )
        if granularity == "none" and normalized_location:
            raise DomainError(
                "health_location_context_mismatch",
                "未记录地点时，地点上下文必须为空。",
            )
        if granularity != "none" and not str(normalized_location.get("label", "")).strip():
            raise DomainError(
                "health_location_label_required",
                "气候区域或城市级地点抽象必须提供合成标签。",
            )

        if observation_kind == "solar_term" and observation_state == "observed":
            if not str(normalized_value.get("label", "")).strip():
                raise DomainError("solar_term_label_required", "节气上下文必须提供节气标签。")
            if source_type == "system" and (
                not str(normalized_value.get("calculation_method", "")).strip()
                or not str(normalized_value.get("calendar_version", "")).strip()
            ):
                raise DomainError(
                    "solar_term_source_version_required",
                    "系统节气必须记录计算方法和历法版本，不能无来源推断。",
                )

        category, event_kind = self._storage_classification(observation_kind)
        payload = {
            "observation_kind": observation_kind,
            "observation_state": observation_state,
            "value": normalized_value,
            "time_context": {
                "start_at": start_at.isoformat(),
                "end_at": end_at.isoformat(),
                "timezone_name": normalized_timezone,
            },
            "location_context": normalized_location,
            "causal_inference": False,
            "clinical_authority": False,
        }

        observation_id = self.patient_vault.new_id("hcontext")
        with self.store.connect() as connection:
            entry = self.patient_vault.append_entry(
                vault_id=vault_id,
                category=category,
                title=f"合成健康观察：{observation_kind}",
                payload=payload,
                source_type=source_type,
                source_ref=source_ref,
                occurred_at=start_at.isoformat(),
                created_by=created_by,
            )
            timeline = self.patient_vault.append_timeline_event(
                vault_id=vault_id,
                primary_entry_id=entry["id"],
                event_kind=event_kind,
                summary=summary,
                provenance_role=provenance_role,
                confirmation_status=confirmation_status,
                occurred_at=start_at.isoformat(),
                created_by=created_by,
            )
            connection.execute(
                """
                INSERT INTO health_context_observations (
                    id, vault_id, timeline_event_id, source_entry_id,
                    observation_kind, observation_state, value_json,
                    observed_start_at, observed_end_at, timezone_name,
                    location_granularity, location_context_json,
                    provenance_role, confirmation_status, causal_inference,
                    clinical_authority, created_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?)
                """,
                (
                    observation_id,
                    vault_id,
                    timeline["id"],
                    entry["id"],
                    observation_kind,
                    observation_state,
                    self.patient_vault.canonical_json(normalized_value),
                    start_at.isoformat(),
                    end_at.isoformat(),
                    normalized_timezone,
                    granularity,
                    self.patient_vault.canonical_json(normalized_location),
                    provenance_role,
                    confirmation_status,
                    created_by,
                    utc_now(),
                ),
            )
            self.store.audit(
                connection,
                event_type="patient.health_context.observation_appended",
                actor_id=created_by,
                object_type="health_context_observation",
                object_id=observation_id,
                metadata={
                    "vault_id": vault_id,
                    "observation_kind": observation_kind,
                    "observation_state": observation_state,
                    "source_entry_id": entry["id"],
                    "timeline_event_id": timeline["id"],
                    "causal_inference": False,
                    "clinical_authority": False,
                },
            )
        return {
            "id": observation_id,
            "source_entry_id": entry["id"],
            "timeline_event_id": timeline["id"],
            "observation_kind": observation_kind,
            "observation_state": observation_state,
            "causal_inference": False,
            "clinical_authority": False,
        }

    def list_window(
        self,
        *,
        vault_id: str,
        start_at: str,
        end_at: str,
        observation_kinds: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        start = self._aware_timestamp(start_at, "窗口开始时间")
        end = self._aware_timestamp(end_at, "窗口结束时间")
        if start > end:
            raise DomainError("health_window_time_order", "窗口结束时间不能早于开始时间。")
        kinds = list(dict.fromkeys(observation_kinds or []))
        if any(kind not in OBSERVATION_KINDS for kind in kinds):
            raise DomainError("invalid_health_observation_kind", "健康观察类型无效。")

        where_kind = ""
        parameters: list[Any] = [vault_id, start.isoformat(), end.isoformat()]
        if kinds:
            placeholders = ",".join("?" for _ in kinds)
            where_kind = f" AND h.observation_kind IN ({placeholders})"
            parameters.extend(kinds)
        with self.store.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT h.*, t.summary, e.source_ref, e.content_hash
                FROM health_context_observations h
                JOIN patient_health_timeline_events t ON t.id = h.timeline_event_id
                JOIN patient_vault_entries e ON e.id = h.source_entry_id
                WHERE h.vault_id = ?
                  AND h.observed_start_at >= ? AND h.observed_start_at <= ?
                  {where_kind}
                ORDER BY h.observed_start_at, h.id
                """,
                parameters,
            ).fetchall()
        return [
            {
                "id": row["id"],
                "observation_kind": row["observation_kind"],
                "observation_state": row["observation_state"],
                "value": json.loads(row["value_json"]),
                "summary": row["summary"],
                "start_at": row["observed_start_at"],
                "end_at": row["observed_end_at"],
                "timezone_name": row["timezone_name"],
                "location_context": json.loads(row["location_context_json"]),
                "provenance_role": row["provenance_role"],
                "confirmation_status": row["confirmation_status"],
                "source_entry_id": row["source_entry_id"],
                "source_ref": row["source_ref"],
                "content_hash": row["content_hash"],
                "causal_inference": False,
                "clinical_authority": False,
            }
            for row in rows
        ]

    @staticmethod
    def _storage_classification(observation_kind: str) -> tuple[str, str]:
        if observation_kind in LIFESTYLE_KINDS:
            return "lifestyle", "lifestyle"
        if observation_kind in ENVIRONMENT_KINDS:
            return "environment", "environment"
        return "patient_observation", "observation"

    @staticmethod
    def _aware_timestamp(value: str, label: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except (AttributeError, ValueError) as exc:
            raise DomainError("invalid_health_timestamp", f"{label}必须是带时区的 ISO 时间。") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise DomainError("invalid_health_timestamp", f"{label}必须包含时区。")
        return parsed.astimezone(UTC)

    @classmethod
    def _reject_forbidden_keys(cls, value: Any, forbidden: set[str]) -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                if str(key).strip().lower() in forbidden:
                    raise DomainError(
                        "health_observation_boundary_violation",
                        "健康时间线不能写入精确定位、诊断、处方或因果结论。",
                    )
                cls._reject_forbidden_keys(nested, forbidden)
        elif isinstance(value, list):
            for item in value:
                cls._reject_forbidden_keys(item, forbidden)
