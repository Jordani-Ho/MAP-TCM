$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
. (Join-Path $PSScriptRoot "resolve_python.ps1")

$env:PYTHONPATH = Join-Path $root "src"
Push-Location $root
try {
    & $script:MapPython -m compileall -q src tests
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & $script:MapPython -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    Write-Host "All MAP-TCM checks passed."
}
finally {
    Pop-Location
}
