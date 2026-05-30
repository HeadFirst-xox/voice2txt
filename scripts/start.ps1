# Windows PowerShell：转发到 start.py（例：.\scripts\start.ps1 stop）
Set-Location (Split-Path $PSScriptRoot -Parent)
python start.py @args
