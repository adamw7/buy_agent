<#
.SYNOPSIS
    Runs the gate .github/workflows/ci.yml applies, on this machine.

.DESCRIPTION
    Both of ci.yml's jobs, step for step and in its order. A job stops at its
    first failing step and the other runs anyway, as on runners. The commands are
    written out here and held to ci.yml by tests/test_conventions.py. Run
    scripts/setup.ps1 first.

.PARAMETER Only
    One job rather than both -- `python` or `ui` -- for a change that touched one.

.EXAMPLE
    .\scripts\preflight.ps1

.EXAMPLE
    .\scripts\preflight.ps1 -Only ui
#>

#Requires -Version 5.1

[CmdletBinding()]
param(
    [ValidateSet('python', 'ui')][string[]]$Only = @('python', 'ui')
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot

function Step([string]$text) {
    Write-Host ''
    Write-Host "== $text" -ForegroundColor Cyan
}

function Note([string]$text) {
    Write-Host "   $text" -ForegroundColor DarkGray
}

function Interpreter {
    # The venv's python, spelt the way whichever platform made it spells it.
    @('.venv\Scripts\python.exe', '.venv/bin/python') |
        ForEach-Object { Join-Path $root $_ } |
        Where-Object { Test-Path -LiteralPath $_ } |
        Select-Object -First 1
}

function Job([string]$name, [string]$directory, [string]$exe, [string[][]]$steps) {
    # Runs each step until one fails and records it in $failed, without throwing
    # or capturing the tools' output.
    Push-Location $directory
    try {
        foreach ($arguments in $steps) {
            $command = "$name $($arguments -join ' ')"
            Step $command
            & $exe @arguments
            if ($LASTEXITCODE -ne 0) {
                $script:failed += $command
                return
            }
        }
    } finally {
        Pop-Location
    }
}

$failed = @()

if ($Only -contains 'python') {
    $python = Interpreter
    if (-not $python) {
        $failed += 'python: there is no .venv -- run .\scripts\setup.ps1'
    } else {
        Job 'python' $root $python @(
            , @('-m', 'pytest', '-n', '3', '--cov')
            , @('-m', 'pylint', 'buy_agent')
            , @('-m', 'mypy', 'buy_agent')
        )
    }
}

if ($Only -contains 'ui') {
    if (-not (Get-Command 'npm' -ErrorAction SilentlyContinue)) {
        $failed += 'npm: not on PATH -- run .\scripts\setup.ps1'
    } else {
        Job 'npm' (Join-Path $root 'ui') 'npm' @(
            , @('run', 'test:coverage')
            , @('run', 'build')
            , @('run', 'lint')
            , @('run', 'format:check')
        )
    }
}

Write-Host ''
if ($failed) {
    Write-Host 'The gate is red:' -ForegroundColor Red
    $failed | ForEach-Object { Write-Host "   $_" -ForegroundColor Red }
    Note 'a formatting failure is fixed by  cd ui; npm run format'
    exit 1
}
Write-Host "The gate is green ($($Only -join ' and '))." -ForegroundColor Green
