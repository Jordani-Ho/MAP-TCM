# Alpha HTTP API

All payloads are JSON. The server binds to localhost by default.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/health` | Liveness and safety mode |
| GET | `/api/v1/overview` | Synthetic demo encounter and audit overview |
| GET | `/api/v1/security` | Non-identifying counts for the synthetic security prototype |
| GET | `/api/v1/knowledge` | Synthetic knowledge contributions, claims, reviews, and snapshots |
| POST | `/api/v1/knowledge/contributions` | Create a synthetic private contribution draft |
| POST | `/api/v1/knowledge/contributions/{id}/consent` | Record contributor-controlled consent |
| POST | `/api/v1/knowledge/contributions/{id}/withdraw` | Append withdrawal and retract linked claims |
| POST | `/api/v1/knowledge/claims` | Create a sourced claim or counterclaim |
| POST | `/api/v1/knowledge/claims/{id}/submit` | Submit a candidate claim for peer review |
| POST | `/api/v1/knowledge/claims/{id}/reviews` | Add a human or advisory AI review |
| POST | `/api/v1/knowledge/claims/{id}/snapshots` | Create provisional, validated, or disputed snapshot |
| POST | `/api/v1/encounters` | Create an encounter after active consent |
| POST | `/api/v1/encounters/{id}/observations` | Add a sourced fact or draft |
| POST | `/api/v1/encounters/{id}/assessments` | Add a physician assessment |
| POST | `/api/v1/encounters/{id}/review` | Move draft to doctor-reviewed |
| POST | `/api/v1/encounters/{id}/sign` | Sign a reviewed encounter |
| POST | `/api/v1/encounters/{id}/withdraw` | Withdraw without deleting history |

The API is intentionally narrow. It demonstrates contract and workflow enforcement, not production authentication or clinical interoperability.

## Synthetic knowledge rules

- Contributions require a `SYN-` subject code, `is_synthetic: true`, and `synthetic://` source references.
- A contribution starts as `private_draft`; only its non-AI contributor can grant consent.
- Only a consented `commons_candidate` contribution can produce a claim.
- Claims and counterclaims start as `deidentified_candidate` and move through append-only state events.
- AI reviews are stored as advisory evidence and do not count toward consensus thresholds.
- `provisional_knowledge` requires one supporting doctor and no unresolved opposition.
- `validated_snapshot` requires two distinct supporting doctors and no active counterclaim.
- `disputed` requires a doctor opposition or active counterclaim.
- Withdrawal never deletes history; it appends withdrawal and retraction events.

These endpoints are an engineering contract for synthetic tests. They do not authorize real patient contributions, public knowledge access, or medical decision automation.

## Synthetic security prototype

`GET /api/v1/security` returns only prototype status and aggregate counts. It never returns subject identifiers, invitation recipient bindings, token hashes, sessions or consent scope.

Invitation issuance, token acceptance, verification, relationship acceptance, consent change and object authorization currently exist as an internal Python service contract with automated negative tests. They are intentionally not exposed as HTTP write endpoints. The existing encounter write routes remain synthetic demonstrations and still accept `actor_role`; they must be replaced by server-side session context before any controlled real-data or clinical integration review.
