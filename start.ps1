param([int]$Port = 8017)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create virtual environment.' }
    & $pythonPath -m pip install -r requirements-lock.txt
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
}
& $pythonPath manage.py migrate
if ($LASTEXITCODE -ne 0) { throw 'Migration failed.' }
& $pythonPath manage.py seed_demo
if ($LASTEXITCODE -ne 0) { throw 'Demo setup failed.' }
Write-Host "EpiGuard: http://127.0.0.1:$Port"
Write-Host 'Local accounts: .demo-credentials.txt'
& $pythonPath manage.py runserver "127.0.0.1:$Port" --noreload
