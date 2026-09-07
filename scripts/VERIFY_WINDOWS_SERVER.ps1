$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Brak środowiska .venv. Najpierw uruchom scripts\SETUP_ENV.bat."
}

$os = Get-CimInstance Win32_OperatingSystem
Write-Host "System: $($os.Caption) $($os.Version)"
Write-Host "ProductType: $($os.ProductType) (1=client, 2/3=Windows Server)"

$env:QT_QPA_PLATFORM = "offscreen"
& $python -m py_compile main.py app\simple_window.py app\updater.py
if ($LASTEXITCODE -ne 0) { throw "Kompilacja modułów nie powiodła się." }
# Each GUI check needs a fresh initialized database on clean CI runners.
$previousQaOutput = $env:SIEKACZ_QA_OUTPUT
try {
    $env:SIEKACZ_QA_OUTPUT = "quality-windows-server"
    & $python scripts\verify_quality.py tests\test_ui_apply_result.py tests\test_updater_guard.py
    if ($LASTEXITCODE -ne 0) { throw "Testy GUI lub aktualizatora nie powiodły się." }
} finally {
    $env:SIEKACZ_QA_OUTPUT = $previousQaOutput
}

Write-Host "SIEKACZ przeszedł test zgodności GUI na Windows/Windows Server Desktop Experience."
