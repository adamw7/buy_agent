<#
.SYNOPSIS
    Sets this checkout up to be worked on: both halves, everything the gate needs.
    Takes no arguments.

.DESCRIPTION
    What .claude/hooks/session-start.sh does for a remote session, for a machine a
    person sits at: a .venv holding requirements-dev.txt and the AP2 SDK, and ui\
    installed the way ci.yml installs it. scripts/start.ps1 is the other script
    here and answers a different question -- it installs what a *run* needs and
    serves the page -- so a checkout it set up passes nothing: no pytest, no
    linter, and no SDK unless the environment asked to pay.

    The SDK is the one install worth saying out loud. It is optional to a run and
    not to the suite: without it the payment tests skip, and the coverage floor
    cannot be reached with them sitting out -- so a checkout without it reports a
    red gate that CI would pass. It is installed in two commands, for the reasons
    requirements-ap2.txt gives, and then asked for, pip exiting 0 for an install
    that cannot be imported being this dependency's documented failure.

    Python and Node are what it will not install, and it checks both against the
    versions ci.yml pins, read out of that file rather than written down again:
    it is the one pin the Dockerfile, the start script and docs/testing.md
    already chase. An older Node is refused -- the Angular CLI refuses it too, a
    step later and less clearly -- while an older Python is named and used, the
    difference being the platform difference ci.yml matrixes for and not a
    checkout that cannot run the suite.

    Each step is skipped where it is already done, so it is also what to run
    after a pull that moved a requirements file or the lockfile. Ends by saying
    what to run next: scripts/preflight.ps1, the gate CI applies.

.EXAMPLE
    .\scripts\setup.ps1
#>

#Requires -Version 5.1

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$ci = Join-Path $root '.github\workflows\ci.yml'
$venv = Join-Path $root '.venv'

function Step([string]$text) {
    Write-Host ''
    Write-Host "== $text" -ForegroundColor Cyan
}

function Note([string]$text) {
    Write-Host "   $text" -ForegroundColor DarkGray
}

function Have([string]$command) {
    [bool](Get-Command $command -ErrorAction SilentlyContinue)
}

function Run([string]$exe, [string[]]$arguments, [string]$failure) {
    & $exe @arguments
    if ($LASTEXITCODE -ne 0) { throw $failure }
}

function Stale([string]$made, [System.IO.FileInfo[]]$from) {
    # Whether $made is missing, or older than any of the files it is made from --
    # start.ps1's helper, for the same two questions: is node_modules older than
    # the lockfile, and is the venv older than a requirements file.
    if (-not (Test-Path -LiteralPath $made)) { return $true }
    $since = (Get-Item -LiteralPath $made -Force).LastWriteTime
    [bool]($from | Where-Object { $_.LastWriteTime -gt $since } | Select-Object -First 1)
}

function Pinned([string]$key) {
    # The version ci.yml sets up under $key -- read and never written here, so a
    # pin Renovate bumps there is the pin this checks against the same day.
    $found = Select-String -LiteralPath $ci -Pattern "^\s+${key}:\s*`"([0-9.]+)`"" |
        Select-Object -First 1
    if (-not $found) { throw "ci.yml pins no $key; this script has outlived its rule" }
    $found.Matches[0].Groups[1].Value
}

function Interpreter {
    # The venv's python, spelt the way whichever platform made it spells it: a
    # pwsh on Linux finds bin/, the Windows this is mostly run on Scripts\.
    @('.venv\Scripts\python.exe', '.venv/bin/python') |
        ForEach-Object { Join-Path $root $_ } |
        Where-Object { Test-Path -LiteralPath $_ } |
        Select-Object -First 1
}

function AtLeast([string]$have, [string]$want) {
    # Compared as versions, so 3.9 is under 3.10 rather than over it.
    [version]($have -replace '[^0-9.].*$', '') -ge [version]$want
}

Push-Location $root
try {
    $wantPython = Pinned 'python-version'
    $wantNode = Pinned 'node-version'

    Step "Python (ci.yml pins $wantPython)"
    $python = Interpreter
    if ($python) {
        Note '.venv is already there'
    } else {
        if (-not (Have 'python')) {
            throw "python is not on PATH -- install Python $wantPython from https://www.python.org/downloads/"
        }
        Run 'python' @('-m', 'venv', '.venv') 'could not create .venv'
        $python = Interpreter
    }
    $havePython = Run $python @('-c', 'import platform; print(platform.python_version())') `
        'the .venv interpreter will not run -- delete .venv and run this again'
    if (AtLeast $havePython $wantPython) {
        Note ".venv runs Python $havePython"
    } else {
        Note ".venv runs Python $havePython, under the $wantPython ci.yml pins -- using it anyway;"
        Note "delete .venv and run this again with Python $wantPython on PATH to match CI"
    }

    Step 'Python dependencies'
    # The stamp is session-start.sh's, and its argument: written last, so a
    # requirements file newer than it is one a pull has moved since.
    $stamp = Join-Path $venv '.setup-installed'
    $requirements = @(Get-Item 'requirements.txt', 'requirements-dev.txt',
        'requirements-ap2-deps.txt', 'requirements-ap2.txt')
    if (-not (Stale $stamp $requirements)) {
        Note 'installed, and no requirements file has moved since'
    } else {
        Run $python @(
            '-m', 'pip', 'install', '--quiet', '--disable-pip-version-check',
            '-r', 'requirements-dev.txt'
        ) 'could not install requirements-dev.txt'
        # Two commands and not one: --no-deps is not a per-line option, and the
        # SDK is the only thing here that needs it.
        Run $python @(
            '-m', 'pip', 'install', '--quiet', '--disable-pip-version-check',
            '-r', 'requirements-ap2-deps.txt'
        ) 'could not install requirements-ap2-deps.txt'
        Run $python @(
            '-m', 'pip', 'install', '--quiet', '--disable-pip-version-check',
            '--no-deps', '-r', 'requirements-ap2.txt'
        ) 'could not install requirements-ap2.txt'
        Note 'requirements-dev.txt and the AP2 SDK are installed'
    }
    $asked = @('-c', 'from buy_agent.mandates import available; print(available())')
    if ((Run $python $asked 'could not ask whether the AP2 SDK is installed') -ne 'True') {
        throw 'the AP2 SDK will not import, and without it the coverage floor fails; the pip output is above'
    }
    Set-Content -LiteralPath $stamp -Value '' -Encoding ascii

    Step "Node (ci.yml pins $wantNode)"
    if (-not ((Have 'node') -and (Have 'npm'))) {
        throw "node and npm are not both on PATH -- install Node $wantNode or later from https://nodejs.org"
    }
    $haveNode = (Run 'node' @('--version') 'could not ask node its version').TrimStart('v')
    if (-not (AtLeast $haveNode $wantNode)) {
        throw "node $haveNode is under the $wantNode ci.yml pins, and the Angular CLI refuses it -- " +
            "install Node $wantNode or later from https://nodejs.org"
    }
    Note "node $haveNode"

    Step 'UI dependencies'
    Push-Location (Join-Path $root 'ui')
    try {
        # npm writes its own record of an install after the lockfile, so a lockfile
        # newer than that record is one a pull changed since. `npm ci` rather than
        # `npm install`: it is what ci.yml runs, and it installs the lockfile as
        # written instead of rewriting it.
        if (Stale 'node_modules\.package-lock.json' @(Get-Item 'package-lock.json')) {
            Run 'npm' @('ci', '--no-audit', '--no-fund') 'npm ci failed'
        } else {
            Note 'ui\node_modules is newer than the lockfile'
        }
    } finally {
        Pop-Location
    }

    # .gitattributes checks every text file out with LF, but only from the next
    # time git writes it: a clone made before that file existed keeps its CRLF
    # copies, and `npm run format:check` fails on every one of them. Named and not
    # fixed, the fix overwriting whatever in the tree is uncommitted.
    Step 'Line endings'
    if (-not (Have 'git')) {
        Note 'git is not on PATH, so the checkout is not checked'
    } else {
        $crlf = @(Run 'git' @('ls-files', '--eol') 'could not ask git how files are checked out' |
                Where-Object { $_ -match '\sw/crlf\s' })
        if ($crlf.Count -eq 0) {
            Note 'every text file is checked out with LF'
        } else {
            Note "$($crlf.Count) files are checked out with CRLF, from before .gitattributes said LF,"
            Note 'and the formatting check fails on them. With nothing uncommitted, rewrite them with:'
            Note '    git rm -r --cached -q . ; git reset --hard'
            # Not `git checkout-index --force --all`, which reads a file whose size
            # and time match the index as up to date and writes nothing at all.
        }
    }

    Step 'Optional'
    if (Have 'ollama') {
        Note 'ollama is installed -- `ollama pull qwen3:0.6b` is what `pytest integration` runs on'
    } else {
        Note 'no ollama: neither suite needs one, but running the agent does -- https://ollama.com/download'
    }
    Note 'a picture of each page in the web UI: see README.md, "A picture of each page"'

    Step 'Done'
    Note 'activate the venv with  .venv\Scripts\Activate.ps1'
    Note 'and run the gate CI applies with  .\scripts\preflight.ps1'
} finally {
    Pop-Location
}
