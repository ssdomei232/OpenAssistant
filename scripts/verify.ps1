#!/usr/bin/env pwsh
# 全仓一致性校验：Server 测试、Agent 测试、App 测试与构建
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "==> Server pytest" -ForegroundColor Cyan
Push-Location server
python -m pytest tests
Pop-Location

Write-Host "==> Agent pytest" -ForegroundColor Cyan
$env:PYTHONPATH = "$root\agent" + [IO.Path]::PathSeparator + $env:PYTHONPATH
python -m pytest agent/tests

Write-Host "==> App test + build" -ForegroundColor Cyan
Push-Location app
if (-not (Test-Path node_modules)) { pnpm install }
pnpm test
pnpm build
Pop-Location

Write-Host "全部校验通过 ✅" -ForegroundColor Green
