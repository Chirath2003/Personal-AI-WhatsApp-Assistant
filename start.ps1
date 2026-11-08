$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$backend = Join-Path $root "backend"
$python = Join-Path $backend ".venv\Scripts\python.exe"
$node = (Get-Command node.exe -ErrorAction Stop).Source
$vite = Join-Path $root "frontend\node_modules\vite\bin\vite.js"
$state = Join-Path $root ".run"
New-Item -ItemType Directory -Force -Path $state | Out-Null
if (-not (Test-Path $python)) { throw "Backend environment missing. Follow the backend setup in README.md first." }
if (-not (Test-Path $vite)) { throw "Frontend dependencies missing. Run npm install in the frontend folder first." }
foreach ($port in @(8000, 5173)) {
    if (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) { throw "Port $port is already in use. Stop the existing service before using start.ps1." }
}
& $python (Join-Path $backend "manage.py") migrate --noinput
if ($LASTEXITCODE -ne 0) { throw "Database migration failed." }
$backendPidFile = Join-Path $state "backend.pid"
$frontendPidFile = Join-Path $state "frontend.pid"
if ((Test-Path $backendPidFile) -or (Test-Path $frontendPidFile)) { throw "A managed instance may already be running. Run stop.ps1 before starting again." }
$backendLog = Join-Path $state "backend.log"
$backendErr = Join-Path $state "backend-error.log"
$frontendLog = Join-Path $state "frontend.log"
$frontendErr = Join-Path $state "frontend-error.log"
$workerLog = Join-Path $state "worker.log"
$workerErr = Join-Path $state "worker-error.log"
$b = Start-Process -FilePath $python -ArgumentList @("manage.py", "runserver", "127.0.0.1:8000") -WorkingDirectory $backend -WindowStyle Hidden -RedirectStandardOutput $backendLog -RedirectStandardError $backendErr -PassThru
$b.Id | Set-Content -Path $backendPidFile -Encoding ascii
$viteArgument = "`"$vite`""
$f = Start-Process -FilePath $node -ArgumentList @($viteArgument, "--host", "127.0.0.1", "--port", "5173", "--strictPort") -WorkingDirectory (Join-Path $root "frontend") -WindowStyle Hidden -RedirectStandardOutput $frontendLog -RedirectStandardError $frontendErr -PassThru
$f.Id | Set-Content -Path $frontendPidFile -Encoding ascii
$w = Start-Process -FilePath $python -ArgumentList @("manage.py", "assistant_worker") -WorkingDirectory $backend -WindowStyle Hidden -RedirectStandardOutput $workerLog -RedirectStandardError $workerErr -PassThru
$w.Id | Set-Content -Path (Join-Path $state "worker.pid")
$ready = $false
for ($i = 0; $i -lt 25; $i++) {
    Start-Sleep -Seconds 1
    try { $null = Invoke-WebRequest -Uri "http://127.0.0.1:8000/api/health/" -TimeoutSec 2; $ready = $true; break } catch { }
}
if (-not $ready) { Write-Warning "The backend did not become ready. Review .run/backend-error.log." }
Write-Host "Assistant started. Dashboard: http://127.0.0.1:5173"
Write-Host "API health: http://127.0.0.1:8000/api/health/"
Write-Host "Stop services with .\stop.ps1"
