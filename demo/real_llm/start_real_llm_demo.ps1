$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$envTemplate = Join-Path $PSScriptRoot ".env.real.example"
$envTarget = Join-Path $projectRoot ".env"

if (-not (Test-Path $envTemplate)) {
    throw "Missing template: $envTemplate"
}

if (-not (Test-Path $envTarget)) {
    Copy-Item $envTemplate $envTarget
    Write-Host "Created .env from demo/real_llm/.env.real.example"
    Write-Host "Fill ECOV3_OPENAI_API_KEY in .env, then rerun this script."
    exit 0
}

$envContent = Get-Content $envTarget -Raw
if ($envContent -match "ECOV3_OPENAI_API_KEY=\s*$") {
    Write-Host ".env exists but ECOV3_OPENAI_API_KEY is empty."
    Write-Host "Fill the key in .env, then rerun this script."
    exit 0
}

Set-Location $projectRoot
& ".\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
