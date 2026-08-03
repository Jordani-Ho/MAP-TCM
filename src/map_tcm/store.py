from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS patients (
    id TEXT PRIMARY KEY,
    display_code TEXT NOT NULL UNIQUE,
    is_synthetic INTEGER NOT NULL CHECK (is_synthetic = 1),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS consents (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients(id),
    purpose TEXT NOT NULL,
    scope_json TEXT NOT NULL,
    effective_at TEXT NOT NULL,
    expires_at TEXT,
    revoked_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS encounters (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients(id),
    encounter_type TEXT NOT NULL CHECK (
        encounter_type IN ('initial', 'follow_up', 'check_in', 'review')
    ),
    status TEXT NOT NULL CHECK (
        status IN ('draft', 'doctor_reviewed', 'signed', 'withdrawn')
    ),
    occurred_at TEXT NOT NULL,
    doctor_id TEXT,
    chief_complaint TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS observations (
    id TEXT PRIMARY KEY,
    encounter_id TEXT NOT NULL REFERENCES encounters(id),
    category TEXT NOT NULL,
    content TEXT NOT NULL,
    source TEXT NOT NULL CHECK (
        source IN ('patient', 'doctor', 'file', 'ai', 'system')
    ),
    author_id TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    review_status TEXT NOT NULL CHECK (
        review_status IN ('draft', 'doctor_reviewed', 'signed', 'withdrawn')
    )
);

CREATE TABLE IF NOT EXISTS assessments (
    id TEXT PRIMARY KEY,
    encounter_id TEXT NOT NULL REFERENCES encounters(id),
    pattern TEXT NOT NULL,
    mechanism TEXT NOT NULL,
    rationale TEXT NOT NULL,
    treatment_principle TEXT NOT NULL,
    prescription_summary TEXT NOT NULL,
    author_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('draft', 'doctor_reviewed', 'signed')),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    object_type TEXT NOT NULL,
    object_id TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_consents_patient ON consents(patient_id);
CREATE INDEX IF NOT EXISTS idx_encounters_patient ON encounters(patient_id);
CREATE INDEX IF NOT EXISTS idx_observations_encounter ON observations(encounter_id);
CREATE INDEX IF NOT EXISTS idx_audit_object ON audit_events(object_type, object_id);
"""


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class SQLiteStore:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    @staticmethod
    def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
        return [dict(row) for row in rows]

    @staticmethod
    def audit(
        connection: sqlite3.Connection,
        *,
        event_type: str,
        actor_id: str,
        object_type: str,
        object_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO audit_events (
                event_type, actor_id, object_type, object_id, occurred_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                event_type,
                actor_id,
                object_type,
                object_id,
                utc_now(),
                json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True),
            ),
        )

    def overview(self) -> dict[str, Any]:
        with self.connect() as connection:
            counts = {
                "patients": connection.execute("SELECT COUNT(*) FROM patients").fetchone()[0],
                "active_consents": connection.execute(
                    """
                    SELECT COUNT(*) FROM consents
                    WHERE revoked_at IS NULL
                      AND effective_at <= ?
                      AND (expires_at IS NULL OR expires_at > ?)
                    """,
                    (utc_now(), utc_now()),
                ).fetchone()[0],
                "encounters": connection.execute("SELECT COUNT(*) FROM encounters").fetchone()[0],
                "drafts": connection.execute(
                    "SELECT COUNT(*) FROM encounters WHERE status = 'draft'"
                ).fetchone()[0],
                "signed": connection.execute(
                    "SELECT COUNT(*) FROM encounters WHERE status = 'signed'"
                ).fetchone()[0],
            }
            patients = self.rows_to_dicts(
                connection.execute(
                    "SELECT id, display_code, is_synthetic, created_at FROM patients ORDER BY created_at"
                ).fetchall()
            )
            encounters = self.rows_to_dicts(
                connection.execute(
                    """
                    SELECT e.id, e.patient_id, p.display_code, e.encounter_type, e.status,
                           e.occurred_at, e.chief_complaint, e.doctor_id, e.version,
                           (SELECT COUNT(*) FROM observations o WHERE o.encounter_id = e.id)
                               AS observation_count,
                           (SELECT COUNT(*) FROM assessments a WHERE a.encounter_id = e.id)
                               AS assessment_count
                    FROM encounters e
                    JOIN patients p ON p.id = e.patient_id
                    ORDER BY e.occurred_at DESC
                    LIMIT 25
                    """
                ).fetchall()
            )
            audit = self.rows_to_dicts(
                connection.execute(
                    """
                    SELECT id, event_type, actor_id, object_type, object_id, occurred_at
                    FROM audit_events ORDER BY id DESC LIMIT 20
                    """
                ).fetchall()
            )
        return {"counts": counts, "patients": patients, "encounters": encounters, "audit": audit}
