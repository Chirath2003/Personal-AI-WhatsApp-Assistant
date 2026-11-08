$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$backend = Join-Path $root "backend"
$python = Join-Path $backend ".venv\Scripts\python.exe"
$node = (Get-Command node.exe -ErrorAction Stop).Source
$vite = Join-Path $root "frontend\node_modules\vite\bin\vite.js"
$whatsappSidecar = Join-Path $backend "whatsapp-web\server.js"
$whatsappModules = Join-Path $backend "whatsapp-web\node_modules\whatsapp-web.js"
$state = Join-Path $root ".run"
New-Item -ItemType Directory -Force -Path $state | Out-Null
if (-not (Test-Path $python)) { throw "Backend environment missing. Follow the backend setup in README.md first." }
if (-not (Test-Path $vite)) { throw "Frontend dependencies missing. Run npm install in the frontend folder first." }
if (-not (Test-Path $whatsappModules)) { throw "WhatsApp Web sidecar dependencies missing. Run npm install in backend\whatsapp-web first." }
foreach ($port in @(8000, 5173, 8765)) {
    if (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) { throw "Port $port is already in use. Stop the existing service before using start.ps1." }
}
& $python (Join-Path $backend "manage.py") migrate --noinput
if ($LASTEXITCODE -ne 0) { throw "Database migration failed." }
$backendPidFile = Join-Path $state "backend.pid"
$frontendPidFile = Join-Path $state "frontend.pid"
$whatsappPidFile = Join-Path $state "whatsapp-web.pid"
if ((Test-Path $backendPidFile) -or (Test-Path $frontendPidFile) -or (Test-Path $whatsappPidFile)) { throw "A managed instance may already be running. Run stop.ps1 before starting again." }
if (-not (Test-Path (Join-Path $state "bridge-token"))) {
    ([guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N")) | Set-Content -Path (Join-Path $state "bridge-token") -Encoding ascii
}
$session = Join-Path $env:LOCALAPPDATA "PersonalAIWhatsAppAssistant\session"
New-Item -ItemType Directory -Force -Path $session | Out-Null
& icacls.exe $session /inheritance:r /grant:r "$($env:USERNAME):(OI)(CI)F" /T /C /Q | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Could not restrict the WhatsApp session directory to the current Windows user." }
$backendLog = Join-Path $state "backend.log"
$backendErr = Join-Path $state "backend-error.log"
$frontendLog = Join-Path $state "frontend.log"
$frontendErr = Join-Path $state "frontend-error.log"
$workerLog = Join-Path $state "worker.log"
$workerErr = Join-Path $state "worker-error.log"
$whatsappLog = Join-Path $state "whatsapp-web.log"
$whatsappErr = Join-Path $state "whatsapp-web-error.log"
$b = Start-Process -FilePath $python -ArgumentList @("manage.py", "runserver", "127.0.0.1:8000") -WorkingDirectory $backend -WindowStyle Hidden -RedirectStandardOutput $backendLog -RedirectStandardError $backendErr -PassThru
$b.Id | Set-Content -Path $backendPidFile -Encoding ascii
$viteArgument = "`"$vite`""
$f = Start-Process -FilePath $node -ArgumentList @($viteArgument, "--host", "127.0.0.1", "--port", "5173", "--strictPort") -WorkingDirectory (Join-Path $root "frontend") -WindowStyle Hidden -RedirectStandardOutput $frontendLog -RedirectStandardError $frontendErr -PassThru
$f.Id | Set-Content -Path $frontendPidFile -Encoding ascii
$w = Start-Process -FilePath $python -ArgumentList @("manage.py", "assistant_worker") -WorkingDirectory $backend -WindowStyle Hidden -RedirectStandardOutput $workerLog -RedirectStandardError $workerErr -PassThru
$w.Id | Set-Content -Path (Join-Path $state "worker.pid")
$whatsappSidecarArgument = "`"$whatsappSidecar`""
$wa = Start-Process -FilePath $node -ArgumentList @($whatsappSidecarArgument) -WorkingDirectory (Join-Path $backend "whatsapp-web") -WindowStyle Hidden -RedirectStandardOutput $whatsappLog -RedirectStandardError $whatsappErr -PassThru
$wa.Id | Set-Content -Path $whatsappPidFile -Encoding ascii
$bridgeReady = $false
for ($i = 0; $i -lt 10; $i++) {
    Start-Sleep -Milliseconds 500
    try { $null = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:8765/health" -TimeoutSec 2; $bridgeReady = $true; break } catch { }
}
if (-not $bridgeReady) {
    Write-Warning "The WhatsApp Web bridge did not start. Review .run/whatsapp-web-error.log."
    if (Test-Path $whatsappErr) { Get-Content -Path $whatsappErr -Tail 10 }
}
$ready = $false
for ($i = 0; $i -lt 25; $i++) {
    Start-Sleep -Seconds 1
    try { $null = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:8000/api/health/" -TimeoutSec 2; $ready = $true; break } catch { }
}
if (-not $ready) { Write-Warning "The backend did not become ready. Review .run/backend-error.log." }
Write-Host "Assistant started. Dashboard: http://127.0.0.1:5173"
Write-Host "API health: http://127.0.0.1:8000/api/health/"
Write-Host "WhatsApp Web bridge: http://127.0.0.1:8765 (loopback only)"
Write-Host "WhatsApp logs: .run\whatsapp-web.log and .run\whatsapp-web-error.log"
if (Test-Path $whatsappLog) { Get-Content -Path $whatsappLog -Tail 5 }
Write-Host "The app services run in the background. This PowerShell prompt is free; use .\stop.ps1 here to stop them."
Write-Host "Stop services with .\stop.ps1"
