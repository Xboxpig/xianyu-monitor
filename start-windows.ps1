param(
    [ValidateRange(1, 65535)][int]$Port = 8000,
    [switch]$Setup,
    [switch]$Build,
    [switch]$NoBrowser
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:SCRAPER_BACKEND = 'native_chrome'
$env:RUNNING_IN_DOCKER = 'false'
$env:SERVER_PORT = "$Port"

function Invoke-Checked {
    param([string]$Program, [string[]]$Arguments)
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed: $Program (exit $LASTEXITCODE)" }
}

$pythonExe = @('.venv\Scripts\python.exe', '.venv\python.exe') |
    Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $pythonExe) {
    if (-not $Setup) { throw 'Run .\start-windows.ps1 -Setup first (requires installed Python 3.11+).' }
    Invoke-Checked 'py' @('-3', '-m', 'venv', '.venv')
    $pythonExe = '.venv\Scripts\python.exe'
}
$pythonExe = (Resolve-Path -LiteralPath $pythonExe).Path
Invoke-Checked $pythonExe @('-c', 'import sys; assert sys.version_info >= (3, 11), "Python 3.11+ required"')
if ($Setup) {
    Invoke-Checked $pythonExe @('-m', 'pip', 'install', '-i', 'https://pypi.tuna.tsinghua.edu.cn/simple', '-r', 'requirements-native.txt')
    if (-not (Test-Path -LiteralPath '.env')) { Copy-Item -LiteralPath '.env.example' -Destination '.env' }
    if (-not (Test-Path -LiteralPath 'config.json')) { Copy-Item -LiteralPath 'config.json.example' -Destination 'config.json' }
}
$databasePath = & $pythonExe -c "from dotenv import load_dotenv; from pathlib import Path; import os; load_dotenv('.env', override=False); print(Path(os.getenv('APP_DATABASE_FILE', 'data/app.sqlite3')).resolve())"
if ($LASTEXITCODE -ne 0) { throw 'Failed to resolve the application database path.' }
$env:APP_DATABASE_FILE = $databasePath.Trim()
if ($Build -or -not (Test-Path -LiteralPath 'dist\index.html')) {
    if (-not (Test-Path -LiteralPath 'web-ui\node_modules')) {
        # Check routing before using a domestic npm mirror.
        $probes = Test-Connection 'registry.npmmirror.com' -Count 2 -ErrorAction SilentlyContinue
        $latency = $probes | ForEach-Object {
            if ($_.PSObject.Properties.Name -contains 'Latency') { $_.Latency } else { $_.ResponseTime }
        } | Measure-Object -Average
        if (-not $probes -or $latency.Average -gt 100) {
            throw 'npm mirror routing could not be verified (latency >100 ms or no response). Check Clash routing before installing frontend dependencies.'
        }
        Push-Location -LiteralPath 'web-ui'
        try { Invoke-Checked 'npm.cmd' @('ci', '--registry=https://registry.npmmirror.com') } finally { Pop-Location }
    }
    Push-Location -LiteralPath 'web-ui'
    try { Invoke-Checked 'npm.cmd' @('run', 'build') } finally { Pop-Location }
}
if (-not $NoBrowser) {
    Invoke-Checked $pythonExe @('-c', 'from src.services.native_chrome_launcher import find_chrome; find_chrome()')
    # Wait for the server before opening the interactive Chrome control page.
    Start-Process -FilePath $pythonExe -ArgumentList @('-m', 'src.services.native_chrome_launcher', '--wait-url', "http://127.0.0.1:$Port/settings?tab=browser") -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
}
Write-Host "Native Chrome backend: http://127.0.0.1:$Port"
Write-Host "Database: $env:APP_DATABASE_FILE"
Write-Host 'Keep this terminal running. Press Ctrl+C to stop the backend and its tasks.'
Invoke-Checked $pythonExe @('-m', 'uvicorn', 'src.app:app', '--host', '127.0.0.1', '--port', "$Port")
