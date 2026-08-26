<#
.SYNOPSIS
    Starts buy_agent's web UI on this machine, from cold. Takes no arguments.

.DESCRIPTION
    The three things README's "Starting it on localhost" walks through by hand:
    an Ollama serving the default model, the Angular build, and the server that
    serves that build alongside the API -- each one skipped if it is already
    there, so a second run costs the seconds pip needs to say it has nothing to
    do. Ends by opening the page and staying in the foreground with the server's
    log lines; Ctrl+C stops the server, and the Ollama too if this script was
    what started it.

    Deliberately without parameters: everything it could ask is already a
    setting somewhere the rest of the project reads it from. The model and the
    Ollama server come from buy_agent.config -- which is to say from
    $env:OLLAMA_MODEL and $env:OLLAMA_HOST -- and anything past that is what
    `python -m buy_agent.server --help` takes.

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

function Answers([string]$probe, [int]$seconds) {
    # Polled rather than assumed: both servers are started as processes, and a
    # port that is not listening yet is indistinguishable from one that never will.
    $deadline = (Get-Date).AddSeconds($seconds)
    do {
        try {
            Invoke-WebRequest -Uri $probe -UseBasicParsing -TimeoutSec 2 | Out-Null
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
            throw 'python is not on PATH -- install Python 3.13 from https://www.python.org/downloads/'
        }
        Run 'python' @('-m', 'venv', '.venv') 'could not create .venv'
    }
    Run $python @(
        '-m', 'pip', 'install', '--quiet', '--disable-pip-version-check',
        '-r', 'requirements.txt'
    ) 'could not install requirements.txt'

    # The defaults live in one place and read $OLLAMA_MODEL / $OLLAMA_HOST there;
    # a tag repeated here would be a second default, silently disagreeing.
    $model = Run $python @(
        '-c', 'from buy_agent.config import DEFAULT_MODEL; print(DEFAULT_MODEL)'
    ) 'could not read DEFAULT_MODEL out of buy_agent.config'
    $ollama = (Run $python @(
        '-c', 'from buy_agent.config import DEFAULT_BASE_URL; print(DEFAULT_BASE_URL)'
    ) 'could not read DEFAULT_BASE_URL out of buy_agent.config').TrimEnd('/')

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

    Step 'Angular build'
    if (Test-Path $built) {
        Note 'ui\dist\ui\browser is already built -- rebuild with `npm run build` in ui\ after changing it'
    } elseif (-not (Have 'npm')) {
        Note 'npm is not on PATH, so the page will be a 503 -- the API still answers'
        Note 'install Node 22.22.3+ from https://nodejs.org and run this again for the page'
    } else {
        Push-Location (Join-Path $root 'ui')
        try {
            if (-not (Test-Path 'node_modules')) { Run 'npm' @('install') 'npm install failed' }
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
