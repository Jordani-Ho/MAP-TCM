param(
    [Parameter(Mandatory = $false)]
    [string]$ProjectRoot = "H:\项目2026\中医"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $ProjectRoot)) {
    throw "Project root not found: $ProjectRoot"
}

& git -C $ProjectRoot rev-parse --is-inside-work-tree *> $null
if ($LASTEXITCODE -eq 0) {
    Write-Host "A valid Git repository already exists. No changes made."
    exit 0
}

$gitPath = Join-Path $ProjectRoot ".git"
if (Test-Path -LiteralPath $gitPath) {
    throw "An invalid or incomplete .git path already exists. Back up the project and rename that path manually before initializing Git. No changes made."
}

& git -C $ProjectRoot init
if ($LASTEXITCODE -ne 0) {
    throw "git init failed"
}

Write-Host "Git repository initialized. Review files and privacy boundaries before running git add or git commit."
