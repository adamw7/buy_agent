<#
.SYNOPSIS
    Runs the gate .github/workflows/ci.yml applies, on this machine.

.DESCRIPTION
    Both of ci.yml's jobs, step for step and in its order: the Python suite under
    coverage, the floor, pylint and mypy; then the UI's tests under coverage, the
    build, the linter and the formatting check. A job stops at its first failing
    step, as it does on a runner -- `coverage report` after a red suite reports a
    floor nobody can act on -- and the other job runs anyway, as the other runner
    would, so one run says everything that is wrong.

    The commands are written out rather than read off ci.yml, which is a YAML file
    this script has no parser for. tests/test_conventions.py holds the two to the
    same list instead, so a step added there and not here is a red test.

    Run scripts/setup.ps1 first: the Python half is run with the .venv's own
    interpreter, activated or not, and needs the AP2 SDK for the coverage floor to
    be reachable at all.

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
    # Runs each step in $directory until one exits non-zero, and adds the command
    # that did to $failed. Not `Run`, which throws: a failure here is a result to
    # report beside the other job's, not a stop. Nor an answer handed back, which
    # would mean capturing the pipeline -- and with it every line the tools print,
    # which is the output this script exists to show.
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
            , @('-m', 'coverage', 'run', '-m', 'pytest')
            , @('-m', 'coverage', 'report')
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
