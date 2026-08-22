$ErrorActionPreference = "Stop"

if ($env:MAP_TCM_PYTHON -and (Test-Path -LiteralPath $env:MAP_TCM_PYTHON)) {
    $script:MapPython = $env:MAP_TCM_PYTHON
    return
}

$bundledPython = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if (Test-Path -LiteralPath $bundledPython) {
    $script:MapPython = $bundledPython
    return
}

$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
if ($pythonCommand -and $pythonCommand.Source -notlike "*WindowsApps*") {
    $script:MapPython = $pythonCommand.Source
    return
}

throw "Python 3.12 was not found. Install Python 3.12 or set MAP_TCM_PYTHON to python.exe."
