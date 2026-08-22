from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from map_tcm.errors import DomainError
from map_tcm.health_timeline import HealthTimelineService
from map_tcm.identity import IdentityAccessService
from map_tcm.patient_vault import PatientVaultService
from map_tcm.store import SQLiteStore


class HealthTimelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self.tempdir.name) / "health-timeline.sqlite3")
        self.store.initialize()
        self.security = IdentityAccessService(self.store)
        self.patient_vault = PatientVaultService(self.store)
        self.timeline = HealthTimelineService(self.store, self.patient_vault)
        self.security.create_synthetic_subject(
            display_code="SYN-PAT-TIME-001",
            role="patient",
            actor_id="test_setup",
        )
        self.vault = self.patient_vault.create_vault(
            patient_subject_code="SYN-PAT-TIME-001",
            actor_id="SYN-PAT-TIME-001",
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def append_diet(self, *, start: str = "2026-08-13T08:00:00+08:00") -> dict:
        return self.timeline.append_observation(
            vault_id=self.vault["id"],
            observation_kind="diet",
            observation_state="observed",
            value={"meal_pattern": "合成示例：规律", "thirst_change": "无合成变化"},
            summary="合成饮食观察",
            source_type="patient",
            source_ref="synthetic://patient/diet-001",
            confirmation_status="patient_reported",
            observed_start_at=start,
            observed_end_at=None,
            timezone_name="Asia/Shanghai",
            location_context={"granularity": "climate_region", "label": "合成湿润区"},
            created_by="SYN-PAT-TIME-001",
        )

    def test_machine_contract_keeps_timeline_observational_and_synthetic(self) -> None:
        contract_path = (
            Path(__file__).parents[1]
            / "schemas"
            / "gate1"
            / "health-timeline-context-v0.1.json"
        )
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        self.assertEqual(contract["contract_id"], "MAP-DEV-013")
        self.assertTrue(contract["synthetic_only"])
        self.assertFalse(contract["production_ready"])
        self.assertFalse(contract["clinical_approval"])
        self.assertFalse(contract["semantic_boundaries"]["timeline_order_implies_causation"])
        self.assertFalse(contract["location_context"]["precise_location_copied_into_timeline"])

    def test_observation_links_source_timeline_context_and_audit(self) -> None:
        result = self.append_diet()
        with self.store.connect() as connection:
            context = connection.execute(
                "SELECT * FROM health_context_observations WHERE id = ?",
                (result["id"],),
            ).fetchone()
            entry = connection.execute(
                "SELECT * FROM patient_vault_entries WHERE id = ?",
                (result["source_entry_id"],),
            ).fetchone()
            event = connection.execute(
                "SELECT * FROM patient_health_timeline_events WHERE id = ?",
                (result["timeline_event_id"],),
            ).fetchone()
            audit = connection.execute(
                "SELECT metadata_json FROM audit_events WHERE event_type = ?",
                ("patient.health_context.observation_appended",),
            ).fetchone()
        self.assertEqual(entry["category"], "lifestyle")
        self.assertEqual(event["event_kind"], "lifestyle")
        self.assertEqual(context["observation_kind"], "diet")
        self.assertEqual(context["observed_start_at"], "2026-08-13T00:00:00+00:00")
        self.assertEqual(context["causal_inference"], 0)
        self.assertEqual(context["clinical_authority"], 0)
        self.assertNotIn("合成饮食观察", audit["metadata_json"])

    def test_unknown_states_do_not_accept_invented_values(self) -> None:
        unknown = self.timeline.append_observation(
            vault_id=self.vault["id"],
            observation_kind="elimination",
            observation_state="declined",
            value=None,
            summary="合成示例：患者选择不回答",
            source_type="patient",
            source_ref="synthetic://patient/elimination-declined",
            confirmation_status="patient_reported",
            observed_start_at="2026-08-13T08:10:00+08:00",
            observed_end_at=None,
            timezone_name="Asia/Shanghai",
            location_context=None,
            created_by="SYN-PAT-TIME-001",
        )
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT value_json FROM health_context_observations WHERE id = ?",
                (unknown["id"],),
            ).fetchone()
        self.assertEqual(row["value_json"], "{}")
        with self.assertRaisesRegex(DomainError, "不能附带推测内容"):
            self.timeline.append_observation(
                vault_id=self.vault["id"],
                observation_kind="sleep",
                observation_state="unknown",
                value={"hours": 7},
                summary="合成未知",
                source_type="patient",
                source_ref="synthetic://patient/sleep-unknown",
                confirmation_status="patient_reported",
                observed_start_at="2026-08-13T08:20:00+08:00",
                observed_end_at=None,
                timezone_name="Asia/Shanghai",
                location_context=None,
                created_by="SYN-PAT-TIME-001",
            )

    def test_precise_location_and_clinical_conclusions_are_rejected(self) -> None:
        common = {
            "vault_id": self.vault["id"],
            "observation_kind": "climate",
            "observation_state": "observed",
            "summary": "合成气候观察",
            "source_type": "patient",
            "source_ref": "synthetic://patient/climate-001",
            "confirmation_status": "patient_reported",
            "observed_start_at": "2026-08-13T09:00:00+08:00",
            "observed_end_at": None,
            "timezone_name": "Asia/Shanghai",
            "created_by": "SYN-PAT-TIME-001",
        }
        with self.assertRaisesRegex(DomainError, "精确定位"):
            self.timeline.append_observation(
                **common,
                value={"weather": "合成潮湿"},
                location_context={"granularity": "city_level", "label": "合成城", "latitude": 0},
            )
        with self.assertRaisesRegex(DomainError, "诊断"):
            self.timeline.append_observation(
                **common,
                value={"weather": "合成潮湿", "diagnosis": "禁止推断"},
                location_context={"granularity": "city_level", "label": "合成城"},
            )

    def test_system_solar_term_requires_versioned_source(self) -> None:
        common = {
            "vault_id": self.vault["id"],
            "observation_kind": "solar_term",
            "observation_state": "observed",
            "summary": "合成节气上下文",
            "source_type": "system",
            "source_ref": "synthetic://calendar/solar-term-001",
            "confirmation_status": "system_recorded",
            "observed_start_at": "2026-08-13T00:00:00+00:00",
            "observed_end_at": None,
            "timezone_name": "Asia/Shanghai",
            "location_context": {"granularity": "city_level", "label": "合成城"},
            "created_by": "synthetic_calendar_service",
        }
        with self.assertRaisesRegex(DomainError, "计算方法和历法版本"):
            self.timeline.append_observation(**common, value={"label": "合成节气"})
        valid = self.timeline.append_observation(
            **common,
            value={
                "label": "合成节气",
                "calculation_method": "synthetic_fixture",
                "calendar_version": "0.1-test",
            },
        )
        self.assertEqual(valid["observation_kind"], "solar_term")

    def test_window_is_chronological_sourced_and_non_causal(self) -> None:
        self.append_diet(start="2026-08-13T09:00:00+08:00")
        self.timeline.append_observation(
            vault_id=self.vault["id"],
            observation_kind="sleep",
            observation_state="observed",
            value={"restfulness": "合成一般"},
            summary="合成睡眠观察",
            source_type="patient",
            source_ref="synthetic://patient/sleep-001",
            confirmation_status="patient_reported",
            observed_start_at="2026-08-13T08:00:00+08:00",
            observed_end_at=None,
            timezone_name="Asia/Shanghai",
            location_context=None,
            created_by="SYN-PAT-TIME-001",
        )
        window = self.timeline.list_window(
            vault_id=self.vault["id"],
            start_at="2026-08-12T00:00:00+00:00",
            end_at="2026-08-14T00:00:00+00:00",
        )
        self.assertEqual([item["observation_kind"] for item in window], ["sleep", "diet"])
        self.assertTrue(all(item["source_ref"].startswith("synthetic://") for item in window))
        self.assertTrue(all(len(item["content_hash"]) == 64 for item in window))
        self.assertTrue(all(item["causal_inference"] is False for item in window))
        self.assertTrue(all(item["clinical_authority"] is False for item in window))

    def test_context_and_timeline_rows_are_database_immutable(self) -> None:
        result = self.append_diet()
        with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
            with self.store.connect() as connection:
                connection.execute(
                    "UPDATE health_context_observations SET observation_kind = 'sleep' WHERE id = ?",
                    (result["id"],),
                )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "append_only"):
            with self.store.connect() as connection:
                connection.execute(
                    "DELETE FROM patient_health_timeline_events WHERE id = ?",
                    (result["timeline_event_id"],),
                )

    def test_final_insert_failure_rolls_back_source_timeline_and_audits(self) -> None:
        with self.store.connect() as connection:
            connection.execute(
                """
                CREATE TRIGGER synthetic_fail_health_context_insert
                BEFORE INSERT ON health_context_observations
                BEGIN
                    SELECT RAISE(ABORT, 'synthetic_health_context_failure');
                END;
                """
            )
            before = {
                table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in (
                    "patient_vault_entries",
                    "patient_health_timeline_events",
                    "health_context_observations",
                    "audit_events",
                )
            }
        with self.assertRaisesRegex(sqlite3.IntegrityError, "synthetic_health_context_failure"):
            self.append_diet()
        with self.store.connect() as connection:
            after = {
                table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in before
            }
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
