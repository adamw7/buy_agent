<#
.SYNOPSIS
    Starts buy_agent's web UI on this machine, from cold. Takes no arguments.

.DESCRIPTION
    Everything README's "Starting it on localhost" does by hand: Ollama serving the
    default model, the Angular build, and the server -- each skipped where it is
    already done, the build only while newer than its sources. Ends in the
    foreground with the page open; Ctrl+C stops the server, and Ollama if this
    started it.

    Only Ollama is started; a vLLM or LiteLLM proxy named by
    $env:BUY_AGENT_PROVIDER is waited for at its address. The AP2 SDK is installed
    only when $env:BUY_AGENT_RAIL, $env:BUY_AGENT_MERCHANT_URL,
    $env:BUY_AGENT_AP2_KEY or $env:BUY_AGENT_AP2_MANDATE says a payment is meant.

    No parameters: the provider, model and address come from buy_agent.config
    ($env:BUY_AGENT_PROVIDER, $env:OLLAMA_MODEL and $env:OLLAMA_HOST, or the VLLM_
    and LITELLM_ pairs), and the rest is `python -m buy_agent.server --help`.

.EXAMPLE
    .\scripts\start.ps1
#>

#Requires -Version 5.1

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
$built = Join-Path $root 'ui\dist\ui\browser\index.html'
$url = 'http://127.0.0.1:8000'

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
    # Whether $made is missing, or older than any file it is made from: a pull
    # leaves changed sources newer than a build made before it.
    if (-not (Test-Path -LiteralPath $made)) { return $true }
    $since = (Get-Item -LiteralPath $made -Force).LastWriteTime
    [bool]($from | Where-Object { $_.LastWriteTime -gt $since } | Select-Object -First 1)
}

function Answers([string]$probe, [int]$seconds) {
    # Polled: a port not listening yet looks like one that never will. The
    # per-request timeout is loose because localhost tries ::1 first, which costs
    # most of two seconds on Windows against an IPv4-only Ollama.
    $deadline = (Get-Date).AddSeconds($seconds)
    do {
        try {
            Invoke-WebRequest -Uri $probe -UseBasicParsing -TimeoutSec 10 | Out-Null
            return $true
        } catch {
            Start-Sleep -Milliseconds 500
        }
    } while ((Get-Date) -lt $deadline)
    return $false
}

$ollamaProcess = $null
$serverProcess = $null

Push-Location $root
try {
    Step 'Python environment'
    if (Test-Path $python) {
        Note '.venv is already there'
    } else {
        if (-not (Have 'python')) {
            throw 'python is not on PATH -- install Python 3.14 from https://www.python.org/downloads/'
        }
        Run 'python' @('-m', 'venv', '.venv') 'could not create .venv'
    }
    Run $python @(
        '-m', 'pip', 'install', '--quiet', '--disable-pip-version-check',
        '-r', 'requirements.txt'
    ) 'could not install requirements.txt'

    # Only where the environment asks to pay. `mandates.available()` is what both
    # doors ask, so a pip that succeeded with nothing importable is caught here.
    Step 'Paying'
    $asked = @('-c', 'from buy_agent.mandates import available; print(available())')
    $paying = @(
        $env:BUY_AGENT_RAIL
        $env:BUY_AGENT_MERCHANT_URL
        $env:BUY_AGENT_AP2_KEY
        $env:BUY_AGENT_AP2_MANDATE
    ) | Where-Object { $_ }
    if ((Run $python $asked 'could not ask whether the AP2 SDK is installed') -eq 'True') {
        Note 'the AP2 SDK is here -- the page will offer to buy what it finds'
    } elseif ($paying) {
        Note 'an AP2 setting is set, so this run means to pay -- installing what that needs'
        Run $python @(
            '-m', 'pip', 'install', '--quiet', '--disable-pip-version-check',
            '-r', 'requirements-ap2-deps.txt'
        ) 'could not install requirements-ap2-deps.txt'
        Run $python @(
            '-m', 'pip', 'install', '--quiet', '--disable-pip-version-check',
            '--no-deps', '-r', 'requirements-ap2.txt'
        ) 'could not install requirements-ap2.txt'
        if ((Run $python $asked 'could not ask whether the AP2 SDK is installed') -ne 'True') {
            throw 'the AP2 SDK still will not import; the pip output is above'
        }
        Note 'installed -- the page will offer to buy what it finds'
    } else {
        Note "not set up, so nothing here offers to buy -- set `$env:BUY_AGENT_RAIL = 'dry-run'"
        Note 'and run this again; that rail signs a real authorisation and charges nobody'
    }

    # Read whole off an AgentConfig, so the provider picks its own model and host.
    $provider = Run $python @(
        '-c', 'from buy_agent.config import AgentConfig; print(AgentConfig().provider)'
    ) 'could not read the provider out of buy_agent.config'
    $model = Run $python @(
        '-c', 'from buy_agent.config import AgentConfig; print(AgentConfig().model)'
    ) 'could not read the model out of buy_agent.config'
    $llm = (Run $python @(
        '-c', 'from buy_agent.config import AgentConfig; print(AgentConfig().base_url)'
    ) 'could not read the model server out of buy_agent.config').TrimEnd('/')

    if ($provider -eq 'ollama') {
        $ollama = $llm

        Step "Ollama at $ollama"
        if (Answers $ollama 1) {
            Note 'already running'
        } elseif (([uri]$ollama).Host -in @('localhost', '127.0.0.1', '::1', '[::1]')) {
            if (-not (Have 'ollama')) {
                throw "nothing is serving $ollama -- install Ollama from https://ollama.com/download"
            }
            $ollamaProcess = Start-Process 'ollama' -ArgumentList 'serve' -PassThru -WindowStyle Minimized
            if (-not (Answers $ollama 30)) { throw "ollama serve did not come up on $ollama" }
            Note 'started, and stopped again when this script ends'
        } else {
            throw "nothing is answering at $ollama -- start it there, or unset `$env:OLLAMA_HOST"
        }

        Step "Model $model"
        $env:OLLAMA_HOST = $ollama
        $wanted = if ($model -like '*:*') { $model } else { "${model}:latest" }
        $pulled = @((Invoke-RestMethod "$ollama/api/tags").models | ForEach-Object { $_.name })
        if ($pulled -contains $wanted) {
            Note 'already pulled'
        } else {
            Note 'not pulled yet -- this one is a several-gigabyte download'
            Run 'ollama' @('pull', $model) "could not pull $model"
        }
    } else {
        # Waited for, not started. /models, since the API root answers 404.
        Step "$provider at $llm"
        if (Answers "$llm/models" 1) {
            Note "already running -- this run will ask it for $model"
        } else {
            throw "nothing is answering at $llm -- start $provider there, serving $model, " +
                "or unset `$env:BUY_AGENT_PROVIDER to go back to Ollama"
        }
    }

    Step 'Angular build'
    $ui = Join-Path $root 'ui'
    # The app, and the workspace files at the top of ui\ (not node_modules).
    $sources = @(Get-ChildItem (Join-Path $ui 'src') -File -Recurse) +
        @(Get-ChildItem $ui -File -Filter '*.json')
    if (-not (Stale $built $sources)) {
        Note 'ui\dist\ui\browser is newer than everything it is built from'
    } elseif (-not (Have 'npm')) {
        if (Test-Path $built) {
            Note 'ui\ has changed since ui\dist\ui\browser was built, and npm is not on PATH to'
            Note 'build it again -- serving the old build, which may not match this API'
        } else {
            Note 'npm is not on PATH, so the page will be a 503 -- the API still answers'
        }
        Note 'install Node 22.23.3+ from https://nodejs.org and run this again for the page'
    } else {
        Push-Location $ui
        try {
            # npm's own install record is older than a lockfile a pull changed.
            if (Stale 'node_modules\.package-lock.json' @(Get-Item 'package-lock.json')) {
                Run 'npm' @('install') 'npm install failed'
            }
            Run 'npm' @('run', 'build') 'npm run build failed'
        } finally {
            Pop-Location
        }
    }

    Step "Server on $url"
    $serverProcess = Start-Process $python -ArgumentList '-m', 'buy_agent.server' -PassThru -NoNewWindow
    if (Answers "$url/api/config" 30) {
        Start-Process $url
        Note 'opened in your browser -- Ctrl+C here stops everything this script started'
    } else {
        throw 'the server did not answer; its output is above'
    }
    $serverProcess.WaitForExit()
} finally {
    if ($serverProcess -and -not $serverProcess.HasExited) { $serverProcess.Kill() }
    if ($ollamaProcess -and -not $ollamaProcess.HasExited) {
        Write-Host 'Stopping the Ollama this script started'
        $ollamaProcess.Kill()
    }
    Pop-Location
}
