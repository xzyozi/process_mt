# Generic external-command runner template for scheduler.py.
# Copy this file to tool/, edit the settings, and register the copy in
# process_schedule.csv.

# =========================
# User settings
# =========================

# Absolute path, or a path relative to this script's directory.
$WorkingDirectory = 'C:\Work\Project'

# Executable name or path. Examples: svn, git, python, cmd.exe.
$Command = 'CHANGE_ME'

# Keep arguments as an array so paths containing spaces are handled safely.
$Arguments = @()

# Optional process-level environment variables for the command.
$EnvironmentVariables = @{}

# Optional log file. An empty value keeps output in scheduler.py's log.
# Relative paths are based on WorkingDirectory.
$LogFile = ''

# Keep True for the first run. Set False after the configuration is confirmed.
$DryRun = $true

$ErrorActionPreference = 'Stop'
$script:LogPath = $null


function Write-RunMessage {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Message
    )

    Write-Output $Message
    if ($script:LogPath) {
        Add-Content -LiteralPath $script:LogPath -Value $Message -Encoding UTF8
    }
}


if ([string]::IsNullOrWhiteSpace($WorkingDirectory) -or
    $WorkingDirectory -eq 'C:\Work\Project') {
    Write-Output '[ERROR] Set WorkingDirectory before running this script.'
    exit 2
}

if ([string]::IsNullOrWhiteSpace($Command) -or $Command -eq 'CHANGE_ME') {
    Write-Output '[ERROR] Set Command before running this script.'
    exit 2
}

$scriptDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
if ([System.IO.Path]::IsPathRooted($WorkingDirectory)) {
    $workingDirectoryCandidate = $WorkingDirectory
} else {
    $workingDirectoryCandidate = Join-Path $scriptDirectory $WorkingDirectory
}

try {
    $resolvedWorkingDirectory = (Resolve-Path -LiteralPath $workingDirectoryCandidate -ErrorAction Stop).Path
} catch {
    Write-Output "[ERROR] WorkingDirectory does not exist: $workingDirectoryCandidate"
    exit 2
}

if ($LogFile) {
    if ([System.IO.Path]::IsPathRooted($LogFile)) {
        $script:LogPath = [System.IO.Path]::GetFullPath($LogFile)
    } else {
        $script:LogPath = Join-Path $resolvedWorkingDirectory $LogFile
    }

    $logDirectory = Split-Path -Parent $script:LogPath
    if ($logDirectory -and -not (Test-Path -LiteralPath $logDirectory -PathType Container)) {
        New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
    }
}

$argumentCount = @($Arguments).Count
$exitCode = 0
$outputText = ''
$locationPushed = $false
$environmentBackup = @{}

try {
    Push-Location -LiteralPath $resolvedWorkingDirectory
    $locationPushed = $true

    foreach ($name in $EnvironmentVariables.Keys) {
        $environmentBackup[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
        [Environment]::SetEnvironmentVariable(
            $name,
            [string]$EnvironmentVariables[$name],
            'Process'
        )
    }

    Write-RunMessage "Working directory: $resolvedWorkingDirectory"
    Write-RunMessage "Command: $Command"
    Write-RunMessage "Argument count: $argumentCount"

    if ($DryRun) {
        Write-RunMessage '[DRY-RUN] Command was not executed.'
    } else {
        $outputText = (& $Command @Arguments 2>&1 | Out-String).TrimEnd()
        if ($null -ne $LASTEXITCODE) {
            $exitCode = [int]$LASTEXITCODE
        }
    }
} catch {
    $exitCode = 1
    $outputText = "[ERROR] $($_.Exception.Message)"
} finally {
    foreach ($name in $environmentBackup.Keys) {
        [Environment]::SetEnvironmentVariable(
            $name,
            $environmentBackup[$name],
            'Process'
        )
    }

    if ($locationPushed) {
        Pop-Location
    }
}

if (-not [string]::IsNullOrWhiteSpace($outputText)) {
    Write-RunMessage $outputText
}

if ($exitCode -eq 0) {
    Write-RunMessage '[SUCCESS] Command completed.'
} else {
    Write-RunMessage "[ERROR] Command exited with code: $exitCode"
}

exit $exitCode
