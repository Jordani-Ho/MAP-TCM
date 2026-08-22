# ADR-002: Synthetic knowledge state machine (migration required)

- Status: Migration Required under ADR-004
- Date: 2026-08-03

> The append-only event and provenance mechanisms remain useful, but the shared-commons and global-consensus semantics are superseded. The implementation must be scoped to one owning cognitive node and use local snapshots; cross-node exchange is governed by ADR-004 knowledge envelopes.

## Context

MAP-008 v0.2 originally defined a shared health knowledge commons. MAP-008 v0.3 now defines independent cognitive nodes with no common brain or global knowledge store. MAP-KNOW-002 remains a synthetic contract for append-only provenance, disagreement and withdrawal, but must no longer imply central contribution or community-wide consensus.

## Decision

The Alpha stores knowledge contributions, contribution state events, claims, claim state events, reviews, and consensus snapshots in separate SQLite tables. Source objects are immutable. State changes are append-only events; withdrawals and retractions do not delete prior records.

The first implementation is synthetic-only:

```text
private_draft
→ consented_contribution
→ deidentified_candidate
→ peer_review
→ provisional_knowledge
→ validated_snapshot
```

`disputed` and `retracted` are explicit terminal states for this Alpha. Resolution into a new consensus requires a later protocol rather than rewriting history.

## Governance enforcement

- Only the contributor can authorize a draft; AI cannot provide consent.
- Only doctors or knowledge stewards can submit claims for review.
- AI reviews remain advisory and do not satisfy promotion thresholds.
- Provisional knowledge needs at least one distinct doctor support.
- Validated snapshots need at least two distinct doctor supports and a doctor publisher.
- Doctor opposition or an active counterclaim blocks promotion and permits a disputed snapshot.
- Contribution withdrawal retracts linked claims through new events and preserves snapshots as historical evidence.
- Every mutation writes one or more audit events.

## Consequences

The Alpha can test provenance, consent, disagreement, consensus, withdrawal, and audit behavior using synthetic records. It does not solve production identity, contributor contracts, re-identification risk, cross-institution governance, or how a withdrawn contribution affects previously aggregated real-world knowledge. Those remain separate gates before a real pilot.
