<#
.SYNOPSIS
    Reads scripts/start.ps1 without running it, and exercises the functions it
    declares. Writes one JSON document to -OutFile; tests/test_start_script.py
    does the asserting.

.DESCRIPTION
    start.ps1 is the one file here no pytest can run: it is PowerShell, it starts
    two servers and it opens a browser. What it *can* be asked is whether it
    parses, what it declares, and how the four helpers at the top of it behave --
    which is most of what goes wrong in a script, and all of it out of reach from
    Python.

    Nothing here runs the script's body. The file is parsed into an AST, the
    function definitions are lifted out of that AST and dot-sourced on their own,
    and the rest of the file -- the part that installs, downloads and serves --
    never executes. `Answers` is given a stubbed clock and a stubbed
    `Invoke-WebRequest`, so its polling loop is tested without a network or a
    wait; `Run` is given this suite's own interpreter, so its exit-code check is
    tested against a real process on either platform.

    One JSON document rather than one process per assertion: PowerShell costs
    about half a second to start, and the suite it is joining runs in three.
#>

[CmdletBinding()]
param(
    # The script under test, scripts/start.ps1.
    [Parameter(Mandatory)][string]$Script,
    # A python.exe to run as a real child process, for Run's exit-code check.
    [Parameter(Mandatory)][string]$Python,
    # Where to write the JSON. Written rather than printed, so that a command
    # that says something unexpected cannot be mistaken for part of the answer.
    [Parameter(Mandatory)][string]$OutFile
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$tokens = $null
$errors = $null
$parser = [System.Management.Automation.Language.Parser]
$ast = $parser::ParseFile($Script, [ref]$tokens, [ref]$errors)

function Nodes([type]$kind) {
    $ast.FindAll({ param($node) $node -is $kind }, $true)
}

function Enclosing($node) {
    # The name of the function a node sits in, or $null at the top level.
    for ($parent = $node.Parent; $parent; $parent = $parent.Parent) {
        if ($parent -is [System.Management.Automation.Language.FunctionDefinitionAst]) {
            return $parent.Name
        }
    }
    return $null
}

$facts = [ordered]@{
    parseErrors = @($errors | ForEach-Object { "$($_.Extent.StartLineNumber): $($_.Message)" })
    functions   = @()
    invocations = @()
    cleanups    = @()
    cases       = [ordered]@{}
}

if ($facts.parseErrors.Count -eq 0) {
    $definitions = Nodes ([System.Management.Automation.Language.FunctionDefinitionAst])
    $facts.functions = @($definitions | ForEach-Object { $_.Name })

    $commands = Nodes ([System.Management.Automation.Language.CommandAst])
    $facts.invocations = @($commands | ForEach-Object {
            [ordered]@{
                operator = $_.InvocationOperator.ToString()
                name     = $_.GetCommandName()
                line     = $_.Extent.StartLineNumber
                text     = $_.Extent.Text
                function = Enclosing $_
            }
        })

    $facts.cleanups = @(Nodes ([System.Management.Automation.Language.TryStatementAst]) |
            Where-Object { $_.Finally } | ForEach-Object { $_.Finally.Extent.Text })

    # The helpers, defined here and nothing else: dot-sourcing the extent of each
    # function definition is what leaves the body of the script unrun.
    foreach ($definition in $definitions) {
        . ([scriptblock]::Create($definition.Extent.Text))
    }
}

function Case([string]$name, [scriptblock]$body) {
    try {
        $facts.cases[$name] = & $body
    } catch {
        $facts.cases[$name] = [ordered]@{ error = $_.Exception.Message }
    }
}

function Poll([int]$seconds, [int]$failures) {
    <#
        `Answers` against a clock that only moves when it sleeps: the loop then
        ends on the attempt it would end on in a minute of real waiting, and the
        count of attempts says which one that was.
    #>
    $log = @{ attempts = 0; waits = 0; now = [datetime]'2026-01-01T00:00:00Z' }
    & {
        function Get-Date { $log.now }
        function Start-Sleep {
            param([int]$Milliseconds)
            $log.waits++
            $log.now = $log.now.AddMilliseconds($Milliseconds)
        }
        function Invoke-WebRequest {
            param([string]$Uri, [switch]$UseBasicParsing, [int]$TimeoutSec)
            $log.attempts++
            if ($log.attempts -le $failures) { throw "nothing is listening on $Uri" }
        }

        $answered = Answers 'http://127.0.0.1:11434' $seconds
        [ordered]@{ answered = $answered; attempts = $log.attempts; waits = $log.waits }
    }
}

if ($facts.parseErrors.Count -eq 0) {
    Case 'run_hands_back_what_the_command_printed' {
        # Quoted with '' and not "", the way start.ps1 quotes its own `python -c`.
        # Windows PowerShell 5.1 strips a double quote out of a native command's
        # arguments instead of escaping it, so python would be handed
        # print(gemma4:12b) -- a SyntaxError, a non-zero exit, and a Run that
        # throws the failure string rather than returning anything. pwsh 7 passes
        # it through, so a probe written that way passes in CI and fails on the
        # Windows this script is for.
        [ordered]@{ output = (Run $Python @('-c', 'print(''gemma4:12b'')') 'unused') }
    }

    Case 'run_throws_its_own_message_on_a_non_zero_exit' {
        $threw = $false
        $message = ''
        try {
            Run $Python @('-c', 'import sys; sys.exit(3)') 'could not pull the model' | Out-Null
        } catch {
            $threw = $true
            $message = $_.Exception.Message
        }
        [ordered]@{ threw = $threw; message = $message }
    }

    Case 'have_finds_a_command_that_is_there' { [ordered]@{ found = (Have 'Get-Command') } }
    Case 'have_is_false_for_one_that_is_not' { [ordered]@{ found = (Have 'ollama-a5f31c7b') } }

    Case 'answers_stops_at_the_first_reply' { Poll 1 0 }
    Case 'answers_keeps_polling_until_it_gets_one' { Poll 5 2 }
    Case 'answers_gives_up_at_the_deadline' { Poll 1 99 }
}

$facts | ConvertTo-Json -Depth 6 | Set-Content -Path $OutFile -Encoding utf8
