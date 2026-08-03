param(
    [Parameter(Mandatory = $false)]
    [string]$ProjectRoot = "H:\项目2026\中医"
)

$ErrorActionPreference = "Stop"
$reportPath = Join-Path $PSScriptRoot "..\LOCAL_RECOVERY_REPORT.txt"
$lines = [System.Collections.Generic.List[string]]::new()

$lines.Add("MAP-TCM Local Recovery Report")
$lines.Add("Generated: $(Get-Date -Format o)")
$lines.Add("ProjectRoot: $ProjectRoot")
$lines.Add("")

if (-not (Test-Path -LiteralPath $ProjectRoot)) {
    $lines.Add("STATUS: PROJECT ROOT NOT FOUND")
    $lines | Set-Content -LiteralPath $reportPath -Encoding UTF8
    Write-Host "Report written to $reportPath"
    exit 1
}

$lines.Add("STATUS: PROJECT ROOT FOUND")
$lines.Add("")
$lines.Add("Top-level entries:")
Get-ChildItem -LiteralPath $ProjectRoot -Force | Sort-Object Name | ForEach-Object {
    $lines.Add(("{0}`t{1}`t{2}" -f $_.Mode, $_.Length, $_.Name))
}

$lines.Add("")
$lines.Add("Core MAP documents:")
Get-ChildItem -LiteralPath (Join-Path $ProjectRoot "docs") -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -like "MAP-*.md" } |
    Sort-Object Name |
    ForEach-Object { $lines.Add(("{0}`t{1}" -f $_.Length, $_.Name)) }

$lines.Add("")
$lines.Add("Git probe:")
$git = Get-Command git -ErrorAction SilentlyContinue
if ($null -eq $git) {
    $lines.Add("git command not found on PATH")
} else {
    $gitOutput = & git -C $ProjectRoot status --short --branch 2>&1
    $lines.Add(($gitOutput | Out-String).Trim())
}

$lines.Add("")
$lines.Add("Privacy note: file contents were not included in this report.")
$lines | Set-Content -LiteralPath $reportPath -Encoding UTF8
Write-Host "Report written to $reportPath"
