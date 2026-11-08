$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$state = Join-Path $root ".run"
foreach ($name in @("frontend", "worker", "backend")) {
    $file = Join-Path $state ($name + ".pid")
    if (-not (Test-Path $file)) { continue }
    $id = 0
    if (-not [int]::TryParse((Get-Content -Raw $file).Trim(), [ref]$id)) { Remove-Item -LiteralPath $file -Force; continue }
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $id" -ErrorAction SilentlyContinue
    if ($proc) {
        $matchesApp = if ($name -eq "backend") { $proc.Name -eq "python.exe" -and $proc.CommandLine -match "manage.py.*runserver 127.0.0.1:8000" } elseif ($name -eq "worker") { $proc.Name -eq "python.exe" -and $proc.CommandLine -match "manage.py.*assistant_worker" } else { $proc.Name -eq "node.exe" -and $proc.CommandLine -like "*frontend*node_modules*vite*" }
        if ($matchesApp) { Stop-Process -Id $id -Force -ErrorAction SilentlyContinue; Write-Host "Stopped $name service." }
        else { Write-Warning "PID $id no longer matches the managed $name process; left it running." }
    }
    Remove-Item -LiteralPath $file -Force
}
