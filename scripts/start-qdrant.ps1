$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw 'Docker Desktop is not installed or not on PATH. Install and start Docker Desktop, then retry.'
}
docker info --format '{{.OSType}}'
if ($LASTEXITCODE -ne 0) { throw 'Start Docker Desktop with Linux containers, then retry.' }
docker compose up -d qdrant
if ($LASTEXITCODE -ne 0) { throw 'Qdrant container startup failed.' }
$ready = $false
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    try {
        Invoke-RestMethod 'http://127.0.0.1:6333/readyz' -TimeoutSec 2 | Out-Null
        $ready = $true
        break
    } catch { Start-Sleep -Seconds 2 }
}
if (-not $ready) { throw 'Qdrant is not ready. Inspect docker compose logs qdrant.' }
& .\.venv\Scripts\python.exe -m scripts.migrate_qdrant
if ($LASTEXITCODE -ne 0) { throw 'Qdrant migration failed. Check QDRANT_URL and QDRANT_API_KEY.' }
Write-Host 'Qdrant ready: http://127.0.0.1:6333/dashboard'
