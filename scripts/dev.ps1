$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
& "$projectRoot/.venv/Scripts/python.exe" "$PSScriptRoot/dev.py" @args
exit $LASTEXITCODE
