# Local development

## Prerequisites

- Windows PowerShell
- Python 3.12

The scripts use `MAP_TCM_PYTHON` when set, then a `python` command, then the Python runtime bundled with Codex Desktop when present.

## Verify

```powershell
PowerShell -ExecutionPolicy Bypass -File .\scripts\dev\check.ps1
```

## Run

```powershell
PowerShell -ExecutionPolicy Bypass -File .\scripts\dev\run.ps1
```

Open `http://127.0.0.1:8765`. The local database is created at `.local/map_tcm_alpha.db` and is excluded from Git.

## Safety

Use synthetic identifiers beginning with `SYN-`. Never enter a real name, phone number, identity number, address, medical record number, image, audio recording, or other identifiable health information.
