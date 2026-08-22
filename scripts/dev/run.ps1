$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
. (Join-Path $PSScriptRoot "resolve_python.ps1")

$env:PYTHONPATH = Join-Path $root "src"
$database = Join-Path $root ".local\map_tcm_alpha.db"

Write-Host "Starting MAP-TCM Alpha at http://127.0.0.1:8765"
Write-Host "Synthetic data only. Press Ctrl+C to stop."
& $script:MapPython -m map_tcm.server --host 127.0.0.1 --port 8765 --db $database
