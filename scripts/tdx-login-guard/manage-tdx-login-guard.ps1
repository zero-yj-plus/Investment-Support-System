[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Setup', 'SetCredential', 'Inspect', 'Test', 'Start', 'Stop', 'Status', 'EditConfig', 'Uninstall')]
    [string]$Action,
    [string]$TdxPath = 'D:\tdx\TdxW.exe',
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'HermesTrader\TdxLoginGuard'),
    [string]$TaskName = 'HermesTrader-TdxLoginGuard'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ResolvedInstallRoot = [IO.Path]::GetFullPath($InstallRoot)
$AllowedRoot = [IO.Path]::GetFullPath($env:LOCALAPPDATA)
if (-not $ResolvedInstallRoot.StartsWith($AllowedRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'InstallRoot must be under the current user LOCALAPPDATA directory.'
}

$Installer = Join-Path $PSScriptRoot 'install-tdx-login-guard.ps1'
$Uninstaller = Join-Path $PSScriptRoot 'uninstall-tdx-login-guard.ps1'
$InstalledGuard = Join-Path $ResolvedInstallRoot 'tdx-login-guard.ps1'
$InstalledConfig = Join-Path $ResolvedInstallRoot 'tdx-login-guard.json'
$InstalledCredentialSetup = Join-Path $ResolvedInstallRoot 'set-tdx-login-credential.ps1'
$CredentialFile = Join-Path $ResolvedInstallRoot 'tdx-login-credential.clixml'
$LogFile = Join-Path $ResolvedInstallRoot 'tdx-login-guard.log'
$PowerShellExecutable = (Get-Process -Id $PID).Path

function Get-GuardTask {
    return Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
}

function Assert-GuardInstalled {
    foreach ($Path in @($InstalledGuard, $InstalledConfig)) {
        if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
            throw "TDX login guard is not installed. Missing: $Path"
        }
    }
}

function Test-StoredCredential {
    if (-not (Test-Path -LiteralPath $CredentialFile -PathType Leaf)) {
        return $false
    }
    try {
        $Protected = Import-Clixml -LiteralPath $CredentialFile
        return (
            $Protected.Username -is [Security.SecureString] -and
            $Protected.Password -is [Security.SecureString]
        )
    }
    catch {
        return $false
    }
}

function Invoke-CredentialSetup {
    Assert-GuardInstalled
    if (-not (Test-Path -LiteralPath $InstalledCredentialSetup -PathType Leaf)) {
        throw "Credential setup script is missing: $InstalledCredentialSetup"
    }
    & $InstalledCredentialSetup -InstallRoot $ResolvedInstallRoot
}

function Set-AutomaticLoginConfig {
    Assert-GuardInstalled
    $Settings = Get-Content -Raw -LiteralPath $InstalledConfig -Encoding utf8 | ConvertFrom-Json
    $Settings.recovery_mode = 'login_then_close'
    $Settings.start_tdx_if_missing = $true
    $Settings.credential_file = $CredentialFile
    $Settings | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $InstalledConfig -Encoding utf8
}

function Invoke-WithPausedTask {
    param([Parameter(Mandatory = $true)][scriptblock]$Operation)

    $Task = Get-GuardTask
    $WasRunning = $null -ne $Task -and [string]$Task.State -eq 'Running'
    if ($WasRunning) {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 500
    }
    try {
        & $Operation
    }
    finally {
        if ($WasRunning) {
            Start-ScheduledTask -TaskName $TaskName
        }
    }
}

function Invoke-InstalledGuard {
    param([string[]]$AdditionalArguments = @())

    $Arguments = @(
        '-NoProfile',
        '-ExecutionPolicy', 'Bypass',
        '-File', $InstalledGuard,
        '-Config', $InstalledConfig
    ) + $AdditionalArguments
    & $PowerShellExecutable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "TDX login guard exited with code $LASTEXITCODE."
    }
}

switch ($Action) {
    'Setup' {
        $ResolvedTdxPath = [IO.Path]::GetFullPath($TdxPath)
        if (-not (Test-Path -LiteralPath $ResolvedTdxPath -PathType Leaf)) {
            throw "TdxW.exe was not found: $ResolvedTdxPath"
        }
        & $Installer -TdxPath $ResolvedTdxPath -InstallRoot $ResolvedInstallRoot -TaskName $TaskName -NoStart
        if (-not (Test-StoredCredential)) {
            Write-Host 'No usable DPAPI credential was found. Opening the secure credential prompt.'
            Invoke-CredentialSetup
        }
        else {
            Write-Host 'Existing current-user DPAPI credential was preserved.'
        }
        Set-AutomaticLoginConfig
        Start-ScheduledTask -TaskName $TaskName
        Write-Host 'Automatic login is enabled and the guard task has started.'
        Write-Host 'Run Inspect after a TDX client version change, then run one real Test.'
    }
    'SetCredential' {
        Invoke-CredentialSetup
    }
    'Inspect' {
        Assert-GuardInstalled
        Invoke-WithPausedTask { Invoke-InstalledGuard -AdditionalArguments @('-Inspect') }
    }
    'Test' {
        Assert-GuardInstalled
        if (-not (Test-StoredCredential)) {
            throw 'The current Windows user has no usable DPAPI credential. Run SetCredential first.'
        }
        Invoke-WithPausedTask { Invoke-InstalledGuard }
    }
    'Start' {
        Assert-GuardInstalled
        if ($null -eq (Get-GuardTask)) {
            throw "Scheduled task was not found: $TaskName"
        }
        Start-ScheduledTask -TaskName $TaskName
        Write-Host 'Guard task start requested.'
    }
    'Stop' {
        if ($null -eq (Get-GuardTask)) {
            throw "Scheduled task was not found: $TaskName"
        }
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        Write-Host 'Guard task stopped. It remains enabled for the next Windows logon.'
    }
    'Status' {
        $Task = Get-GuardTask
        $Process = @(Get-Process -Name 'TdxW' -ErrorAction SilentlyContinue)
        $Settings = $null
        if (Test-Path -LiteralPath $InstalledConfig -PathType Leaf) {
            $Settings = Get-Content -Raw -LiteralPath $InstalledConfig -Encoding utf8 | ConvertFrom-Json
        }
        [pscustomobject]@{
            TaskName = $TaskName
            TaskState = if ($null -eq $Task) { 'NotInstalled' } else { [string]$Task.State }
            TdxProcessCount = $Process.Count
            TdxPath = if ($null -eq $Settings) { $null } else { [string]$Settings.tdx_exe }
            RecoveryMode = if ($null -eq $Settings) { $null } else { [string]$Settings.recovery_mode }
            StartIfMissing = if ($null -eq $Settings) { $null } else { [bool]$Settings.start_tdx_if_missing }
            CredentialExists = Test-StoredCredential
        } | Format-List
        if (Test-Path -LiteralPath $LogFile -PathType Leaf) {
            Write-Host 'Recent guard log:'
            Get-Content -LiteralPath $LogFile -Encoding utf8 -Tail 30
        }
        else {
            Write-Host "Guard log does not exist yet: $LogFile"
        }
        Write-Host 'TaskState=Running does not prove that TDX is logged in.'
    }
    'EditConfig' {
        Assert-GuardInstalled
        Start-Process -FilePath 'notepad.exe' -ArgumentList ('"{0}"' -f $InstalledConfig) -Wait
    }
    'Uninstall' {
        & $Uninstaller -InstallRoot $ResolvedInstallRoot -TaskName $TaskName -Confirm:$false
    }
}
