# Synthetic-data-only engineering policy

The current application is an engineering workbench. It is not approved to store real or re-identifiable patient information.

## Enforced controls

- Patient identifiers must begin with `SYN-`.
- Records carry an explicit synthetic-data flag.
- An active consent record is required before creating an encounter.
- AI-authored observations remain drafts and cannot become physician assessments.
- Only the doctor role can review, sign, or withdraw an encounter.
- All mutations create immutable audit events.

## Not yet implemented

Production identity, role-based session enforcement, encryption and key management, retention controls, incident response, backup policy, regulatory validation, and clinical deployment approval are outside this skeleton. Their absence is a hard barrier to real-patient use.
