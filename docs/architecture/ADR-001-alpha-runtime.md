# ADR-001: Alpha runtime and storage

- Status: Accepted
- Date: 2026-08-02

## Decision

The first runnable Alpha uses Python 3.12 standard-library components, a WSGI HTTP boundary, and SQLite storage. The browser workbench is served as static HTML, CSS, and JavaScript by the same local process.

## Why

This keeps the recovered project immediately runnable without installing third-party packages, while preserving explicit service, storage, and HTTP boundaries that can later be replaced independently.

## Constraints

- Localhost is the default bind address.
- The engineering database accepts synthetic records only.
- Clinical facts, physician judgment, and AI drafts remain separate record types.
- AI output cannot review or sign an encounter.
- Every write creates an audit event.
- This is an engineering skeleton, not a production medical system.

## Consequences

The Alpha can validate workflows and data contracts now. Authentication, encryption at rest, migrations, production observability, and deployment controls remain future work and are required before any clinical pilot.
