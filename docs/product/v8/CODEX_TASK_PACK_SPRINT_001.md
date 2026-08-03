# Codex Task Pack — Sprint 001

## Sprint goal

Turn the recovered design repository into a governed, runnable, synthetic-data-only Alpha foundation.

This task pack covers only the first implementation slice. The canonical twelve-task V8 plan is maintained in `V8_ENGINEERING_BACKLOG.md`.

| Task | Result | Acceptance |
| --- | --- | --- |
| TCM-ENG-001 | Repository governance and developer baseline | Implemented; under review in PR #1 |
| TCM-ENG-002 | Runnable application skeleton | Implemented; under review in PR #1 |

## Definition of done

- Existing MAP-000 through MAP-007 are preserved.
- No real patient data is introduced.
- State transitions and doctor-only signing are executable and tested.
- Local startup and health endpoint are verified.
- Work is committed to a feature branch and submitted as a draft PR.

## Next sprint candidate

TCM-CLIN-001 must first define and validate the initial/follow-up field dictionary with the first collaborating physician. TCM-ENG-003 follows that gate and remains synthetic-data-only.
