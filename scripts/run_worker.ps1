$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
Push-Location "$projectRoot/backend"
try { & "$projectRoot/.venv/Scripts/python.exe" -m app.execution.worker @args }
finally { Pop-Location }
exit $LASTEXITCODE
