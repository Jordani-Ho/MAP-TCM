# TCM Copilot V8.0 Alpha PRD v0.1

## Product promise

Help a Chinese-medicine physician create a traceable longitudinal clinical record without transferring clinical authority to AI.

## Alpha user

One authorized physician working with synthetic or appropriately controlled records. The current engineering build supports synthetic data only.

## P0 workflow

1. Establish patient identity and active consent.
2. Create an initial, follow-up, or monitoring encounter.
3. Record sourced observations separately from interpretation.
4. Add physician assessment and treatment intent.
5. Compare with prior encounters.
6. Doctor reviews and signs; later corrections create new history rather than deletion.
7. Produce a patient-facing summary only after doctor approval.

## Safety invariants

- AI never diagnoses, prescribes, reviews, or signs autonomously.
- Facts, external evidence, AI drafts, and physician judgment remain attributable.
- Signed records are not silently overwritten.
- Consent, role, and audit checks occur at the application boundary.
- Engineering and test artifacts contain no identifiable patient data.

## Current acceptance criteria

- A synthetic patient with active consent can complete the draft-review-sign lifecycle.
- Missing consent blocks encounter creation.
- An encounter without observations cannot be reviewed.
- An encounter without a physician assessment cannot be signed.
- Every mutation appears in the audit trail.
- The workbench starts locally and the automated test suite passes without third-party runtime packages.

## Explicitly out of scope

Real-patient deployment, autonomous diagnosis, prescription generation, public-network hosting, production authentication, billing, and model training on patient records.
