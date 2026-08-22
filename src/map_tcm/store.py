from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from contextvars import ContextVar
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

CREATE TABLE IF NOT EXISTS clinical_record_versions (
    id TEXT PRIMARY KEY,
    encounter_id TEXT NOT NULL REFERENCES encounters(id),
    version_number INTEGER NOT NULL CHECK (version_number >= 1),
    status TEXT NOT NULL CHECK (status IN ('draft', 'doctor_reviewed', 'signed')),
    supersedes_version_id TEXT REFERENCES clinical_record_versions(id),
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    reviewed_by TEXT,
    reviewed_at TEXT,
    signed_by TEXT,
    signed_at TEXT,
    UNIQUE (encounter_id, version_number),
    CHECK (id <> supersedes_version_id)
);

CREATE TABLE IF NOT EXISTS clinical_field_values (
    id TEXT PRIMARY KEY,
    record_version_id TEXT NOT NULL REFERENCES clinical_record_versions(id),
    field_id TEXT NOT NULL,
    value_json TEXT NOT NULL,
    value_hash TEXT NOT NULL,
    source_type TEXT NOT NULL CHECK (
        source_type IN ('patient', 'doctor', 'file', 'ai', 'system')
    ),
    source_ref TEXT NOT NULL,
    source_anchor_json TEXT NOT NULL,
    author_id TEXT NOT NULL,
    supersedes_field_value_id TEXT REFERENCES clinical_field_values(id),
    created_at TEXT NOT NULL,
    CHECK (id <> supersedes_field_value_id)
);

CREATE TABLE IF NOT EXISTS clinical_field_registry_versions (
    id TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status = 'candidate_for_doctor_review'),
    language TEXT NOT NULL,
    synthetic_only INTEGER NOT NULL CHECK (synthetic_only = 1),
    clinical_approval INTEGER NOT NULL CHECK (clinical_approval = 0),
    contract_hash TEXT NOT NULL,
    loaded_at TEXT NOT NULL,
    UNIQUE (contract_id, schema_version)
);

CREATE TABLE IF NOT EXISTS clinical_field_definitions (
    registry_version_id TEXT NOT NULL REFERENCES clinical_field_registry_versions(id),
    field_id TEXT NOT NULL,
    encounters_json TEXT NOT NULL,
    section TEXT NOT NULL,
    label_zh TEXT NOT NULL,
    value_type TEXT NOT NULL,
    requiredness TEXT NOT NULL CHECK (
        requiredness IN ('required', 'recommended', 'conditional', 'optional')
    ),
    allow_unknown INTEGER NOT NULL CHECK (allow_unknown IN (0, 1)),
    evidence_tag TEXT NOT NULL,
    source_roles_json TEXT NOT NULL,
    review_owner TEXT NOT NULL,
    sensitivity TEXT NOT NULL,
    condition_text TEXT,
    display_order INTEGER NOT NULL,
    PRIMARY KEY (registry_version_id, field_id)
);

CREATE TABLE IF NOT EXISTS clinical_field_registry_links (
    field_value_id TEXT PRIMARY KEY REFERENCES clinical_field_values(id),
    registry_version_id TEXT NOT NULL REFERENCES clinical_field_registry_versions(id),
    field_id TEXT NOT NULL,
    unknown_state TEXT CHECK (
        unknown_state IS NULL OR unknown_state IN (
            'unknown', 'not_collected', 'not_applicable', 'declined'
        )
    ),
    evidence_tag TEXT NOT NULL,
    source_role TEXT NOT NULL CHECK (
        source_role IN ('patient', 'doctor', 'file', 'system')
    ),
    review_status TEXT NOT NULL CHECK (
        review_status IN ('draft', 'doctor_reviewed', 'signed', 'withdrawn')
    ),
    created_at TEXT NOT NULL,
    FOREIGN KEY (registry_version_id, field_id)
        REFERENCES clinical_field_definitions(registry_version_id, field_id)
);

CREATE TRIGGER IF NOT EXISTS clinical_field_registry_version_no_update
BEFORE UPDATE ON clinical_field_registry_versions
BEGIN
    SELECT RAISE(ABORT, 'clinical_field_registry_version_is_immutable');
END;

CREATE TRIGGER IF NOT EXISTS clinical_field_registry_version_no_delete
BEFORE DELETE ON clinical_field_registry_versions
BEGIN
    SELECT RAISE(ABORT, 'clinical_field_registry_version_is_append_only');
END;

CREATE TRIGGER IF NOT EXISTS clinical_field_definition_no_update
BEFORE UPDATE ON clinical_field_definitions
BEGIN
    SELECT RAISE(ABORT, 'clinical_field_definition_is_immutable');
END;

CREATE TRIGGER IF NOT EXISTS clinical_field_definition_no_delete
BEFORE DELETE ON clinical_field_definitions
BEGIN
    SELECT RAISE(ABORT, 'clinical_field_definition_is_append_only');
END;

CREATE TRIGGER IF NOT EXISTS clinical_field_registry_link_no_update
BEFORE UPDATE ON clinical_field_registry_links
BEGIN
    SELECT RAISE(ABORT, 'clinical_field_registry_link_is_immutable');
END;

CREATE TRIGGER IF NOT EXISTS clinical_field_registry_link_no_delete
BEFORE DELETE ON clinical_field_registry_links
BEGIN
    SELECT RAISE(ABORT, 'clinical_field_registry_link_is_append_only');
END;

CREATE TABLE IF NOT EXISTS clinical_record_responsibility_snapshots (
    id TEXT PRIMARY KEY,
    record_version_id TEXT NOT NULL UNIQUE REFERENCES clinical_record_versions(id),
    field_manifest_json TEXT NOT NULL,
    manifest_hash TEXT NOT NULL,
    signed_by TEXT NOT NULL,
    raw_source_content_persisted INTEGER NOT NULL CHECK (raw_source_content_persisted = 0),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS clinical_record_withdrawal_events (
    id TEXT PRIMARY KEY,
    encounter_id TEXT NOT NULL REFERENCES encounters(id),
    signed_version_id TEXT NOT NULL REFERENCES clinical_record_versions(id),
    reason TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS clinical_record_version_no_delete
BEFORE DELETE ON clinical_record_versions
BEGIN
    SELECT RAISE(ABORT, 'clinical_record_version_is_append_only');
END;

CREATE TRIGGER IF NOT EXISTS clinical_record_signed_immutable
BEFORE UPDATE ON clinical_record_versions
WHEN OLD.status = 'signed'
BEGIN
    SELECT RAISE(ABORT, 'signed_clinical_record_version_is_immutable');
END;

CREATE TRIGGER IF NOT EXISTS clinical_record_version_transition_guard
BEFORE UPDATE ON clinical_record_versions
WHEN OLD.status <> 'signed' AND (
    NOT (
        (OLD.status = 'draft' AND NEW.status = 'doctor_reviewed')
        OR (OLD.status = 'doctor_reviewed' AND NEW.status = 'signed')
    )
    OR NEW.id <> OLD.id
    OR NEW.encounter_id <> OLD.encounter_id
    OR NEW.version_number <> OLD.version_number
    OR NEW.supersedes_version_id IS NOT OLD.supersedes_version_id
    OR NEW.created_by <> OLD.created_by
    OR NEW.created_at <> OLD.created_at
)
BEGIN
    SELECT RAISE(ABORT, 'invalid_clinical_record_version_transition');
END;

CREATE TRIGGER IF NOT EXISTS clinical_field_value_no_update
BEFORE UPDATE ON clinical_field_values
BEGIN
    SELECT RAISE(ABORT, 'clinical_field_value_is_immutable');
END;

CREATE TRIGGER IF NOT EXISTS clinical_field_value_no_delete
BEFORE DELETE ON clinical_field_values
BEGIN
    SELECT RAISE(ABORT, 'clinical_field_value_is_append_only');
END;

CREATE TRIGGER IF NOT EXISTS clinical_snapshot_no_update
BEFORE UPDATE ON clinical_record_responsibility_snapshots
BEGIN
    SELECT RAISE(ABORT, 'clinical_responsibility_snapshot_is_immutable');
END;

CREATE TRIGGER IF NOT EXISTS clinical_snapshot_no_delete
BEFORE DELETE ON clinical_record_responsibility_snapshots
BEGIN
    SELECT RAISE(ABORT, 'clinical_responsibility_snapshot_is_append_only');
END;

CREATE TRIGGER IF NOT EXISTS clinical_withdrawal_no_update
BEFORE UPDATE ON clinical_record_withdrawal_events
BEGIN
    SELECT RAISE(ABORT, 'clinical_withdrawal_event_is_immutable');
END;

CREATE TRIGGER IF NOT EXISTS clinical_withdrawal_no_delete
BEFORE DELETE ON clinical_record_withdrawal_events
BEGIN
    SELECT RAISE(ABORT, 'clinical_withdrawal_event_is_append_only');
END;

CREATE TABLE IF NOT EXISTS security_subjects (
    id TEXT PRIMARY KEY,
    display_code TEXT NOT NULL UNIQUE,
    role TEXT NOT NULL CHECK (
        role IN (
            'patient', 'verified_doctor', 'care_executor', 'clinic_admin',
            'founder_operator', 'knowledge_steward', 'delegated_agent'
        )
    ),
    account_status TEXT NOT NULL CHECK (
        account_status IN ('identity_pending', 'active', 'suspended', 'rejected')
    ),
    verification_status TEXT NOT NULL CHECK (
        verification_status IN ('pending', 'verified', 'rejected')
    ),
    credential_status TEXT NOT NULL CHECK (
        credential_status IN ('not_applicable', 'pending', 'active', 'expired', 'suspended')
    ),
    is_synthetic INTEGER NOT NULL CHECK (is_synthetic = 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS security_sessions (
    id TEXT PRIMARY KEY,
    subject_id TEXT NOT NULL REFERENCES security_subjects(id),
    status TEXT NOT NULL CHECK (status IN ('active', 'revoked', 'expired')),
    authenticated_at TEXT NOT NULL,
    step_up_at TEXT,
    expires_at TEXT NOT NULL,
    revoked_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS security_invitations (
    id TEXT PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE,
    expected_role TEXT NOT NULL CHECK (
        expected_role IN (
            'patient', 'verified_doctor', 'care_executor', 'clinic_admin',
            'founder_operator', 'knowledge_steward', 'delegated_agent'
        )
    ),
    inviter_subject_id TEXT NOT NULL REFERENCES security_subjects(id),
    accepted_subject_id TEXT REFERENCES security_subjects(id),
    intended_recipient_binding TEXT NOT NULL,
    purpose TEXT NOT NULL,
    issued_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    maximum_uses INTEGER NOT NULL CHECK (maximum_uses = 1),
    use_count INTEGER NOT NULL DEFAULT 0 CHECK (use_count BETWEEN 0 AND maximum_uses),
    status TEXT NOT NULL CHECK (
        status IN (
            'issued', 'accepted', 'identity_pending', 'active',
            'expired', 'revoked', 'rejected', 'suspended'
        )
    )
);

CREATE TABLE IF NOT EXISTS security_invitation_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invitation_id TEXT NOT NULL REFERENCES security_invitations(id),
    from_state TEXT,
    to_state TEXT NOT NULL CHECK (
        to_state IN (
            'issued', 'accepted', 'identity_pending', 'active',
            'expired', 'revoked', 'rejected', 'suspended'
        )
    ),
    actor_id TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS care_relationships (
    id TEXT PRIMARY KEY,
    patient_subject_id TEXT NOT NULL REFERENCES security_subjects(id),
    practitioner_subject_id TEXT NOT NULL REFERENCES security_subjects(id),
    status TEXT NOT NULL CHECK (status IN ('pending', 'active', 'ended', 'revoked')),
    purpose TEXT NOT NULL,
    data_categories_json TEXT NOT NULL,
    effective_at TEXT,
    expires_at TEXT,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (patient_subject_id <> practitioner_subject_id)
);

CREATE TABLE IF NOT EXISTS security_consents (
    id TEXT PRIMARY KEY,
    relationship_id TEXT NOT NULL REFERENCES care_relationships(id),
    patient_subject_id TEXT NOT NULL REFERENCES security_subjects(id),
    purpose TEXT NOT NULL,
    data_categories_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'revoked', 'expired')),
    effective_at TEXT NOT NULL,
    expires_at TEXT,
    revoked_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge_contributions (
    id TEXT PRIMARY KEY,
    subject_code TEXT NOT NULL,
    is_synthetic INTEGER NOT NULL CHECK (is_synthetic = 1),
    contributor_id TEXT NOT NULL,
    contributor_role TEXT NOT NULL CHECK (
        contributor_role IN ('patient', 'doctor', 'researcher', 'ai')
    ),
    content TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    access_level TEXT NOT NULL CHECK (
        access_level IN ('private', 'restricted', 'commons_candidate')
    ),
    withdrawal_rule TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge_contribution_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contribution_id TEXT NOT NULL REFERENCES knowledge_contributions(id),
    state TEXT NOT NULL CHECK (
        state IN ('private_draft', 'consented_contribution', 'withdrawn')
    ),
    actor_id TEXT NOT NULL,
    actor_role TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge_claims (
    id TEXT PRIMARY KEY,
    contribution_id TEXT NOT NULL REFERENCES knowledge_contributions(id),
    claim_kind TEXT NOT NULL CHECK (claim_kind IN ('claim', 'counterclaim')),
    parent_claim_id TEXT REFERENCES knowledge_claims(id),
    statement TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    author_id TEXT NOT NULL,
    author_role TEXT NOT NULL CHECK (
        author_role IN ('patient', 'doctor', 'researcher', 'ai')
    ),
    created_at TEXT NOT NULL,
    CHECK (
        (claim_kind = 'claim' AND parent_claim_id IS NULL)
        OR (claim_kind = 'counterclaim' AND parent_claim_id IS NOT NULL)
    )
);

CREATE TABLE IF NOT EXISTS knowledge_claim_state_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_id TEXT NOT NULL REFERENCES knowledge_claims(id),
    from_state TEXT,
    to_state TEXT NOT NULL CHECK (
        to_state IN (
            'deidentified_candidate', 'peer_review', 'provisional_knowledge',
            'validated_snapshot', 'disputed', 'retracted'
        )
    ),
    actor_id TEXT NOT NULL,
    actor_role TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge_reviews (
    id TEXT PRIMARY KEY,
    claim_id TEXT NOT NULL REFERENCES knowledge_claims(id),
    reviewer_id TEXT NOT NULL,
    reviewer_role TEXT NOT NULL CHECK (
        reviewer_role IN ('patient', 'doctor', 'researcher', 'ai')
    ),
    verdict TEXT NOT NULL CHECK (
        verdict IN ('support', 'oppose', 'needs_evidence')
    ),
    rationale TEXT NOT NULL,
    is_advisory INTEGER NOT NULL CHECK (is_advisory IN (0, 1)),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS consensus_snapshots (
    id TEXT PRIMARY KEY,
    claim_id TEXT NOT NULL REFERENCES knowledge_claims(id),
    state TEXT NOT NULL CHECK (
        state IN ('provisional_knowledge', 'validated_snapshot', 'disputed')
    ),
    summary TEXT NOT NULL,
    review_count INTEGER NOT NULL,
    doctor_support_count INTEGER NOT NULL,
    doctor_oppose_count INTEGER NOT NULL,
    active_counterclaim_count INTEGER NOT NULL,
    created_by TEXT NOT NULL,
    created_by_role TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_nodes (
    id TEXT PRIMARY KEY,
    node_type TEXT NOT NULL CHECK (
        node_type IN ('physician_apprentice', 'patient_tutor')
    ),
    owner_subject_code TEXT NOT NULL,
    teacher_subject_code TEXT,
    is_synthetic INTEGER NOT NULL CHECK (is_synthetic = 1),
    status TEXT NOT NULL CHECK (status IN ('draft', 'active', 'suspended')),
    created_at TEXT NOT NULL,
    CHECK (
        (node_type = 'physician_apprentice' AND teacher_subject_code IS NOT NULL)
        OR node_type = 'patient_tutor'
    )
);

CREATE TABLE IF NOT EXISTS patient_private_vaults (
    id TEXT PRIMARY KEY,
    patient_subject_code TEXT NOT NULL UNIQUE,
    is_synthetic INTEGER NOT NULL CHECK (is_synthetic = 1),
    status TEXT NOT NULL CHECK (status IN ('active', 'suspended', 'closed')),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS patient_record_subject_bindings (
    patient_id TEXT PRIMARY KEY REFERENCES patients(id),
    patient_subject_id TEXT NOT NULL UNIQUE REFERENCES security_subjects(id),
    is_synthetic INTEGER NOT NULL CHECK (is_synthetic = 1),
    bound_by TEXT NOT NULL,
    bound_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS vaults (
    id TEXT PRIMARY KEY,
    vault_type TEXT NOT NULL CHECK (vault_type IN ('patient_private', 'doctor_private')),
    owner_subject_id TEXT NOT NULL REFERENCES security_subjects(id),
    patient_private_vault_id TEXT UNIQUE REFERENCES patient_private_vaults(id),
    key_reference TEXT,
    is_synthetic INTEGER NOT NULL CHECK (is_synthetic = 1),
    status TEXT NOT NULL CHECK (status IN ('active', 'suspended', 'closed')),
    created_at TEXT NOT NULL,
    UNIQUE (vault_type, owner_subject_id),
    CHECK (
        (vault_type = 'patient_private' AND patient_private_vault_id IS NOT NULL)
        OR (vault_type = 'doctor_private' AND patient_private_vault_id IS NULL)
    )
);

CREATE TABLE IF NOT EXISTS source_objects (
    id TEXT PRIMARY KEY,
    object_key TEXT NOT NULL,
    owner_vault_id TEXT NOT NULL REFERENCES vaults(id),
    source_type TEXT NOT NULL CHECK (
        source_type IN (
            'text', 'audio', 'video', 'image', 'document',
            'structured_form', 'external_reference'
        )
    ),
    object_version INTEGER NOT NULL CHECK (object_version >= 1),
    storage_reference TEXT,
    content_hash TEXT NOT NULL,
    created_by_subject_id TEXT NOT NULL REFERENCES security_subjects(id),
    consent_grant_id TEXT REFERENCES security_consents(id),
    supersedes_source_object_id TEXT REFERENCES source_objects(id),
    status TEXT NOT NULL CHECK (status IN ('active', 'superseded', 'withdrawn')),
    is_synthetic INTEGER NOT NULL CHECK (is_synthetic = 1),
    created_at TEXT NOT NULL,
    UNIQUE (owner_vault_id, object_key, object_version),
    CHECK (id <> supersedes_source_object_id)
);

CREATE TABLE IF NOT EXISTS source_anchors (
    id TEXT PRIMARY KEY,
    source_object_id TEXT NOT NULL REFERENCES source_objects(id),
    source_object_version INTEGER NOT NULL CHECK (source_object_version >= 1),
    anchor_type TEXT NOT NULL CHECK (
        anchor_type IN ('text_range', 'time_range', 'page_bbox', 'object_version')
    ),
    anchor_json TEXT NOT NULL,
    extracted_text TEXT,
    confidence REAL CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    transform_chain_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS clinical_field_source_links (
    field_value_id TEXT PRIMARY KEY REFERENCES clinical_field_values(id),
    source_object_id TEXT REFERENCES source_objects(id),
    source_anchor_id TEXT REFERENCES source_anchors(id),
    link_status TEXT NOT NULL CHECK (link_status IN ('anchored', 'source_pending')),
    created_at TEXT NOT NULL,
    CHECK (
        (link_status = 'anchored' AND source_object_id IS NOT NULL AND source_anchor_id IS NOT NULL)
        OR (link_status = 'source_pending' AND source_object_id IS NULL AND source_anchor_id IS NULL)
    )
);

CREATE TRIGGER IF NOT EXISTS patient_record_subject_binding_no_update
BEFORE UPDATE ON patient_record_subject_bindings
BEGIN
    SELECT RAISE(ABORT, 'patient_record_subject_binding_is_immutable');
END;

CREATE TRIGGER IF NOT EXISTS patient_record_subject_binding_no_delete
BEFORE DELETE ON patient_record_subject_bindings
BEGIN
    SELECT RAISE(ABORT, 'patient_record_subject_binding_is_immutable');
END;

CREATE TRIGGER IF NOT EXISTS source_object_no_update
BEFORE UPDATE ON source_objects
BEGIN
    SELECT RAISE(ABORT, 'source_object_is_immutable');
END;

CREATE TRIGGER IF NOT EXISTS source_object_no_delete
BEFORE DELETE ON source_objects
BEGIN
    SELECT RAISE(ABORT, 'source_object_is_append_only');
END;

CREATE TRIGGER IF NOT EXISTS source_anchor_no_update
BEFORE UPDATE ON source_anchors
BEGIN
    SELECT RAISE(ABORT, 'source_anchor_is_immutable');
END;

CREATE TRIGGER IF NOT EXISTS source_anchor_no_delete
BEFORE DELETE ON source_anchors
BEGIN
    SELECT RAISE(ABORT, 'source_anchor_is_append_only');
END;

CREATE TRIGGER IF NOT EXISTS clinical_field_source_link_no_update
BEFORE UPDATE ON clinical_field_source_links
BEGIN
    SELECT RAISE(ABORT, 'clinical_field_source_link_is_immutable');
END;

CREATE TRIGGER IF NOT EXISTS clinical_field_source_link_no_delete
BEFORE DELETE ON clinical_field_source_links
BEGIN
    SELECT RAISE(ABORT, 'clinical_field_source_link_is_immutable');
END;

CREATE TABLE IF NOT EXISTS patient_vault_entries (
    id TEXT PRIMARY KEY,
    vault_id TEXT NOT NULL REFERENCES patient_private_vaults(id),
    category TEXT NOT NULL CHECK (
        category IN (
            'identity_profile', 'birth_context', 'family_history',
            'medical_record', 'test_report', 'prescription',
            'medication_history', 'allergy_history', 'procedure_history',
            'image', 'video', 'audio', 'patient_observation',
            'lifestyle', 'environment', 'doctor_guidance', 'outcome'
        )
    ),
    title TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    source_type TEXT NOT NULL CHECK (
        source_type IN ('patient', 'doctor', 'file', 'device', 'system', 'external')
    ),
    source_ref TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version >= 1),
    supersedes_id TEXT REFERENCES patient_vault_entries(id),
    status TEXT NOT NULL CHECK (status IN ('active', 'superseded', 'withdrawn')),
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    CHECK (id <> supersedes_id)
);

CREATE TABLE IF NOT EXISTS patient_health_timeline_events (
    id TEXT PRIMARY KEY,
    vault_id TEXT NOT NULL REFERENCES patient_private_vaults(id),
    primary_entry_id TEXT NOT NULL REFERENCES patient_vault_entries(id),
    event_kind TEXT NOT NULL CHECK (
        event_kind IN (
            'identity_baseline', 'clinical_encounter', 'observation',
            'investigation', 'intervention', 'outcome', 'lifestyle',
            'environment'
        )
    ),
    summary TEXT NOT NULL,
    provenance_role TEXT NOT NULL CHECK (
        provenance_role IN ('patient', 'doctor', 'file', 'device', 'system')
    ),
    confirmation_status TEXT NOT NULL CHECK (
        confirmation_status IN (
            'patient_reported', 'doctor_confirmed', 'external_document',
            'device_recorded', 'system_recorded'
        )
    ),
    occurred_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS health_context_observations (
    id TEXT PRIMARY KEY,
    vault_id TEXT NOT NULL REFERENCES patient_private_vaults(id),
    timeline_event_id TEXT NOT NULL UNIQUE REFERENCES patient_health_timeline_events(id),
    source_entry_id TEXT NOT NULL REFERENCES patient_vault_entries(id),
    observation_kind TEXT NOT NULL CHECK (
        observation_kind IN (
            'diet', 'sleep', 'elimination', 'emotion_stress', 'activity',
            'clothing_thermal', 'body_sensation', 'climate', 'season',
            'solar_term', 'location_context', 'housing_environment'
        )
    ),
    observation_state TEXT NOT NULL CHECK (
        observation_state IN (
            'observed', 'unknown', 'not_collected', 'not_applicable', 'declined'
        )
    ),
    value_json TEXT NOT NULL,
    observed_start_at TEXT NOT NULL,
    observed_end_at TEXT NOT NULL,
    timezone_name TEXT,
    location_granularity TEXT NOT NULL CHECK (
        location_granularity IN ('none', 'climate_region', 'city_level')
    ),
    location_context_json TEXT NOT NULL,
    provenance_role TEXT NOT NULL CHECK (
        provenance_role IN ('patient', 'doctor', 'file', 'device', 'system')
    ),
    confirmation_status TEXT NOT NULL CHECK (
        confirmation_status IN (
            'patient_reported', 'doctor_confirmed', 'external_document',
            'device_recorded', 'system_recorded'
        )
    ),
    causal_inference INTEGER NOT NULL CHECK (causal_inference = 0),
    clinical_authority INTEGER NOT NULL CHECK (clinical_authority = 0),
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    CHECK (observed_start_at <= observed_end_at),
    CHECK (
        (observation_state = 'observed' AND value_json <> '{}') OR
        (observation_state <> 'observed' AND value_json = '{}')
    )
);

CREATE TABLE IF NOT EXISTS patient_derived_knowledge (
    id TEXT PRIMARY KEY,
    vault_id TEXT NOT NULL REFERENCES patient_private_vaults(id),
    knowledge_kind TEXT NOT NULL CHECK (
        knowledge_kind IN ('summary', 'trend', 'reminder', 'question', 'learning_note')
    ),
    content TEXT NOT NULL,
    source_entry_ids_json TEXT NOT NULL,
    author_role TEXT NOT NULL CHECK (author_role IN ('patient', 'doctor', 'ai')),
    status TEXT NOT NULL CHECK (
        status IN ('ai_draft', 'patient_confirmed', 'doctor_confirmed', 'superseded', 'withdrawn')
    ),
    clinical_authority INTEGER NOT NULL CHECK (clinical_authority = 0),
    version INTEGER NOT NULL CHECK (version >= 1),
    supersedes_id TEXT REFERENCES patient_derived_knowledge(id),
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    CHECK (id <> supersedes_id),
    CHECK ((author_role = 'ai' AND status = 'ai_draft') OR author_role <> 'ai')
);

CREATE TABLE IF NOT EXISTS physician_patient_access_sessions (
    id TEXT PRIMARY KEY,
    physician_agent_node_id TEXT NOT NULL REFERENCES agent_nodes(id),
    doctor_security_session_id TEXT NOT NULL REFERENCES security_sessions(id),
    doctor_subject_id TEXT NOT NULL REFERENCES security_subjects(id),
    patient_subject_id TEXT NOT NULL REFERENCES security_subjects(id),
    vault_id TEXT NOT NULL REFERENCES patient_private_vaults(id),
    care_relationship_id TEXT NOT NULL REFERENCES care_relationships(id),
    consent_id TEXT NOT NULL REFERENCES security_consents(id),
    purpose TEXT NOT NULL,
    data_categories_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'closed', 'expired', 'revoked')),
    temporary_copy_persisted INTEGER NOT NULL CHECK (temporary_copy_persisted = 0),
    started_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    closed_at TEXT
);

CREATE TABLE IF NOT EXISTS physician_access_responsibility_snapshots (
    id TEXT PRIMARY KEY,
    access_session_id TEXT NOT NULL REFERENCES physician_patient_access_sessions(id),
    source_manifest_json TEXT NOT NULL,
    ai_draft_id TEXT,
    final_record_ref TEXT,
    raw_patient_content_persisted INTEGER NOT NULL CHECK (raw_patient_content_persisted = 0),
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_knowledge_items (
    id TEXT PRIMARY KEY,
    agent_node_id TEXT NOT NULL REFERENCES agent_nodes(id),
    knowledge_domain TEXT NOT NULL CHECK (
        knowledge_domain IN (
            'classical_source', 'physician_framework', 'physician_experience',
            'patient_learning', 'patient_observation', 'commons_reference'
        )
    ),
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    source_layer TEXT NOT NULL CHECK (
        source_layer IN ('original', 'commentary', 'physician_view', 'ai_summary')
    ),
    access_level TEXT NOT NULL CHECK (
        access_level IN ('private', 'care_relationship', 'teaching_approved', 'commons')
    ),
    rights_status TEXT NOT NULL CHECK (
        rights_status IN ('owned', 'authorized', 'public_domain', 'citation_only', 'unknown')
    ),
    content_hash TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version >= 1),
    supersedes_id TEXT REFERENCES agent_knowledge_items(id),
    status TEXT NOT NULL CHECK (
        status IN ('candidate', 'teacher_confirmed', 'superseded', 'withdrawn')
    ),
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    CHECK (id <> supersedes_id)
);

CREATE TABLE IF NOT EXISTS agent_run_drafts (
    id TEXT PRIMARY KEY,
    agent_node_id TEXT NOT NULL REFERENCES agent_nodes(id),
    role_type TEXT NOT NULL CHECK (
        role_type IN ('physician_apprentice', 'patient_tutor')
    ),
    input_summary TEXT NOT NULL,
    output_json TEXT NOT NULL,
    cited_item_ids_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status = 'ai_draft'),
    clinical_authority INTEGER NOT NULL CHECK (clinical_authority = 0),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS clinical_authorization_evidence (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES security_sessions(id),
    subject_id TEXT NOT NULL REFERENCES security_subjects(id),
    server_role TEXT NOT NULL,
    capability TEXT NOT NULL,
    patient_subject_id TEXT NOT NULL REFERENCES security_subjects(id),
    purpose TEXT NOT NULL,
    data_categories_json TEXT NOT NULL,
    object_type TEXT NOT NULL,
    object_id TEXT NOT NULL,
    object_state TEXT,
    relationship_id TEXT REFERENCES care_relationships(id),
    consent_id TEXT REFERENCES security_consents(id),
    evidence_hash TEXT NOT NULL,
    allowed INTEGER NOT NULL CHECK (allowed = 1),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS clinical_write_evidence_links (
    id TEXT PRIMARY KEY,
    authorization_evidence_id TEXT NOT NULL UNIQUE
        REFERENCES clinical_authorization_evidence(id),
    clinical_object_type TEXT NOT NULL,
    clinical_object_id TEXT NOT NULL,
    action_type TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS clinical_outbox_events (
    id TEXT PRIMARY KEY,
    authorization_evidence_id TEXT NOT NULL UNIQUE
        REFERENCES clinical_authorization_evidence(id),
    write_link_id TEXT NOT NULL UNIQUE REFERENCES clinical_write_evidence_links(id),
    aggregate_type TEXT NOT NULL,
    aggregate_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    raw_clinical_content_persisted INTEGER NOT NULL
        CHECK (raw_clinical_content_persisted = 0),
    status TEXT NOT NULL CHECK (status = 'pending'),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS intervention_plan_versions (
    id TEXT PRIMARY KEY,
    encounter_id TEXT NOT NULL REFERENCES encounters(id),
    signed_record_version_id TEXT NOT NULL REFERENCES clinical_record_versions(id),
    version_number INTEGER NOT NULL CHECK (version_number >= 1),
    action_summary TEXT NOT NULL,
    instructions TEXT NOT NULL,
    follow_up_due_at TEXT,
    signed_by TEXT NOT NULL,
    plan_hash TEXT NOT NULL,
    is_synthetic INTEGER NOT NULL CHECK (is_synthetic = 1),
    created_at TEXT NOT NULL,
    UNIQUE (encounter_id, version_number)
);

CREATE TABLE IF NOT EXISTS intervention_execution_events (
    id TEXT PRIMARY KEY,
    plan_version_id TEXT NOT NULL REFERENCES intervention_plan_versions(id),
    patient_subject_id TEXT NOT NULL REFERENCES security_subjects(id),
    event_type TEXT NOT NULL CHECK (
        event_type IN ('started', 'completed', 'missed', 'stopped', 'deviation')
    ),
    detail_json TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    event_hash TEXT NOT NULL,
    is_synthetic INTEGER NOT NULL CHECK (is_synthetic = 1),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS patient_feedback_events (
    id TEXT PRIMARY KEY,
    plan_version_id TEXT NOT NULL REFERENCES intervention_plan_versions(id),
    patient_subject_id TEXT NOT NULL REFERENCES security_subjects(id),
    response_code TEXT NOT NULL CHECK (
        response_code IN ('improved', 'unchanged', 'worsened', 'uncertain')
    ),
    adverse_events_json TEXT NOT NULL,
    narrative TEXT NOT NULL,
    feedback_hash TEXT NOT NULL,
    is_synthetic INTEGER NOT NULL CHECK (is_synthetic = 1),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS follow_up_record_links (
    follow_up_encounter_id TEXT PRIMARY KEY REFERENCES encounters(id),
    plan_version_id TEXT NOT NULL REFERENCES intervention_plan_versions(id),
    previous_signed_record_version_id TEXT NOT NULL REFERENCES clinical_record_versions(id),
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS care_event_idempotency_records (
    id TEXT PRIMARY KEY,
    patient_subject_id TEXT NOT NULL REFERENCES security_subjects(id),
    operation_type TEXT NOT NULL CHECK (
        operation_type IN ('execution', 'feedback')
    ),
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    result_object_type TEXT NOT NULL,
    result_object_id TEXT NOT NULL,
    response_json TEXT NOT NULL,
    authorization_evidence_id TEXT NOT NULL UNIQUE
        REFERENCES clinical_authorization_evidence(id),
    created_at TEXT NOT NULL,
    UNIQUE (patient_subject_id, operation_type, idempotency_key)
);

CREATE TRIGGER IF NOT EXISTS intervention_plan_no_update
BEFORE UPDATE ON intervention_plan_versions
BEGIN
    SELECT RAISE(ABORT, 'intervention_plan_version_is_immutable');
END;

CREATE TRIGGER IF NOT EXISTS intervention_plan_no_delete
BEFORE DELETE ON intervention_plan_versions
BEGIN
    SELECT RAISE(ABORT, 'intervention_plan_version_is_append_only');
END;

CREATE TRIGGER IF NOT EXISTS intervention_execution_no_update
BEFORE UPDATE ON intervention_execution_events
BEGIN
    SELECT RAISE(ABORT, 'intervention_execution_event_is_immutable');
END;

CREATE TRIGGER IF NOT EXISTS intervention_execution_no_delete
BEFORE DELETE ON intervention_execution_events
BEGIN
    SELECT RAISE(ABORT, 'intervention_execution_event_is_append_only');
END;

CREATE TRIGGER IF NOT EXISTS patient_feedback_no_update
BEFORE UPDATE ON patient_feedback_events
BEGIN
    SELECT RAISE(ABORT, 'patient_feedback_event_is_immutable');
END;

CREATE TRIGGER IF NOT EXISTS patient_feedback_no_delete
BEFORE DELETE ON patient_feedback_events
BEGIN
    SELECT RAISE(ABORT, 'patient_feedback_event_is_append_only');
END;

CREATE TRIGGER IF NOT EXISTS patient_health_timeline_no_update
BEFORE UPDATE ON patient_health_timeline_events
BEGIN
    SELECT RAISE(ABORT, 'patient_health_timeline_event_is_immutable');
END;

CREATE TRIGGER IF NOT EXISTS patient_health_timeline_no_delete
BEFORE DELETE ON patient_health_timeline_events
BEGIN
    SELECT RAISE(ABORT, 'patient_health_timeline_event_is_append_only');
END;

CREATE TRIGGER IF NOT EXISTS health_context_observation_no_update
BEFORE UPDATE ON health_context_observations
BEGIN
    SELECT RAISE(ABORT, 'health_context_observation_is_immutable');
END;

CREATE TRIGGER IF NOT EXISTS health_context_observation_no_delete
BEFORE DELETE ON health_context_observations
BEGIN
    SELECT RAISE(ABORT, 'health_context_observation_is_append_only');
END;

CREATE TRIGGER IF NOT EXISTS follow_up_record_link_no_update
BEFORE UPDATE ON follow_up_record_links
BEGIN
    SELECT RAISE(ABORT, 'follow_up_record_link_is_immutable');
END;

CREATE TRIGGER IF NOT EXISTS follow_up_record_link_no_delete
BEFORE DELETE ON follow_up_record_links
BEGIN
    SELECT RAISE(ABORT, 'follow_up_record_link_is_immutable');
END;

CREATE TRIGGER IF NOT EXISTS care_event_idempotency_no_update
BEFORE UPDATE ON care_event_idempotency_records
BEGIN
    SELECT RAISE(ABORT, 'care_event_idempotency_record_is_immutable');
END;

CREATE TRIGGER IF NOT EXISTS care_event_idempotency_no_delete
BEFORE DELETE ON care_event_idempotency_records
BEGIN
    SELECT RAISE(ABORT, 'care_event_idempotency_record_is_immutable');
END;

CREATE TRIGGER IF NOT EXISTS clinical_authorization_evidence_no_update
BEFORE UPDATE ON clinical_authorization_evidence
BEGIN
    SELECT RAISE(ABORT, 'clinical_authorization_evidence_is_immutable');
END;

CREATE TRIGGER IF NOT EXISTS clinical_authorization_evidence_no_delete
BEFORE DELETE ON clinical_authorization_evidence
BEGIN
    SELECT RAISE(ABORT, 'clinical_authorization_evidence_is_append_only');
END;

CREATE TRIGGER IF NOT EXISTS clinical_write_evidence_link_no_update
BEFORE UPDATE ON clinical_write_evidence_links
BEGIN
    SELECT RAISE(ABORT, 'clinical_write_evidence_link_is_immutable');
END;

CREATE TRIGGER IF NOT EXISTS clinical_write_evidence_link_no_delete
BEFORE DELETE ON clinical_write_evidence_links
BEGIN
    SELECT RAISE(ABORT, 'clinical_write_evidence_link_is_append_only');
END;

CREATE TRIGGER IF NOT EXISTS clinical_outbox_event_no_update
BEFORE UPDATE ON clinical_outbox_events
BEGIN
    SELECT RAISE(ABORT, 'clinical_outbox_event_is_immutable');
END;

CREATE TRIGGER IF NOT EXISTS clinical_outbox_event_no_delete
BEFORE DELETE ON clinical_outbox_events
BEGIN
    SELECT RAISE(ABORT, 'clinical_outbox_event_is_append_only');
END;

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
CREATE INDEX IF NOT EXISTS idx_clinical_versions_encounter
    ON clinical_record_versions(encounter_id, version_number);
CREATE INDEX IF NOT EXISTS idx_clinical_fields_version
    ON clinical_field_values(record_version_id, field_id, created_at);
CREATE INDEX IF NOT EXISTS idx_clinical_field_registry_definition
    ON clinical_field_definitions(registry_version_id, display_order);
CREATE INDEX IF NOT EXISTS idx_clinical_field_registry_link
    ON clinical_field_registry_links(registry_version_id, field_id);
CREATE INDEX IF NOT EXISTS idx_clinical_withdrawals_encounter
    ON clinical_record_withdrawal_events(encounter_id, created_at);
CREATE INDEX IF NOT EXISTS idx_knowledge_contribution_events
    ON knowledge_contribution_events(contribution_id, id);
CREATE INDEX IF NOT EXISTS idx_knowledge_claim_contribution
    ON knowledge_claims(contribution_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_claim_parent
    ON knowledge_claims(parent_claim_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_claim_states
    ON knowledge_claim_state_events(claim_id, id);
CREATE INDEX IF NOT EXISTS idx_knowledge_reviews_claim
    ON knowledge_reviews(claim_id);
CREATE INDEX IF NOT EXISTS idx_consensus_claim
    ON consensus_snapshots(claim_id, created_at);
CREATE INDEX IF NOT EXISTS idx_audit_object ON audit_events(object_type, object_id);
CREATE INDEX IF NOT EXISTS idx_security_sessions_subject
    ON security_sessions(subject_id, status);
CREATE INDEX IF NOT EXISTS idx_security_invitations_hash
    ON security_invitations(token_hash);
CREATE INDEX IF NOT EXISTS idx_security_invitation_events
    ON security_invitation_events(invitation_id, id);
CREATE INDEX IF NOT EXISTS idx_care_relationship_patient
    ON care_relationships(patient_subject_id, status);
CREATE INDEX IF NOT EXISTS idx_care_relationship_practitioner
    ON care_relationships(practitioner_subject_id, status);
CREATE INDEX IF NOT EXISTS idx_security_consents_relationship
    ON security_consents(relationship_id, status);
CREATE INDEX IF NOT EXISTS idx_agent_nodes_owner
    ON agent_nodes(owner_subject_code, node_type);
CREATE INDEX IF NOT EXISTS idx_patient_vault_entries
    ON patient_vault_entries(vault_id, category, status, occurred_at);
CREATE INDEX IF NOT EXISTS idx_patient_record_subject
    ON patient_record_subject_bindings(patient_subject_id);
CREATE INDEX IF NOT EXISTS idx_vault_owner
    ON vaults(owner_subject_id, vault_type, status);
CREATE INDEX IF NOT EXISTS idx_source_objects_vault
    ON source_objects(owner_vault_id, object_key, object_version);
CREATE INDEX IF NOT EXISTS idx_source_anchors_object
    ON source_anchors(source_object_id, source_object_version);
CREATE INDEX IF NOT EXISTS idx_clinical_field_source_object
    ON clinical_field_source_links(source_object_id, source_anchor_id);
CREATE INDEX IF NOT EXISTS idx_patient_timeline
    ON patient_health_timeline_events(vault_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_health_context_window
    ON health_context_observations(vault_id, observed_start_at, observation_kind);
CREATE INDEX IF NOT EXISTS idx_patient_derived
    ON patient_derived_knowledge(vault_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_physician_patient_access
    ON physician_patient_access_sessions(
        doctor_subject_id, patient_subject_id, status, expires_at
    );
CREATE INDEX IF NOT EXISTS idx_physician_responsibility_snapshot
    ON physician_access_responsibility_snapshots(access_session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_agent_knowledge_node
    ON agent_knowledge_items(agent_node_id, knowledge_domain, status, created_at);
CREATE INDEX IF NOT EXISTS idx_agent_runs_node
    ON agent_run_drafts(agent_node_id, created_at);
CREATE INDEX IF NOT EXISTS idx_clinical_authorization_object
    ON clinical_authorization_evidence(object_type, object_id, created_at);
CREATE INDEX IF NOT EXISTS idx_clinical_write_evidence_object
    ON clinical_write_evidence_links(clinical_object_type, clinical_object_id, created_at);
CREATE INDEX IF NOT EXISTS idx_clinical_outbox_pending
    ON clinical_outbox_events(status, created_at);
CREATE INDEX IF NOT EXISTS idx_intervention_plan_encounter
    ON intervention_plan_versions(encounter_id, version_number);
CREATE INDEX IF NOT EXISTS idx_intervention_execution_plan
    ON intervention_execution_events(plan_version_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_patient_feedback_plan
    ON patient_feedback_events(plan_version_id, created_at);
CREATE INDEX IF NOT EXISTS idx_care_event_idempotency_subject
    ON care_event_idempotency_records(patient_subject_id, operation_type, created_at);
"""


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class SQLiteStore:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self._active_connection: ContextVar[sqlite3.Connection | None] = ContextVar(
            f"map_tcm_connection_{id(self)}", default=None
        )

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        active = self._active_connection.get()
        if active is not None:
            yield active
            return
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        token = self._active_connection.set(connection)
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            self._active_connection.reset(token)
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
