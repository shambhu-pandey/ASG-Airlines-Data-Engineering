$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$orchestrator = Join-Path $root "scripts\run_pipeline.py"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Project Python executable not found: $python"
}

Push-Location $root
try {
    & $python $orchestrator @args
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
