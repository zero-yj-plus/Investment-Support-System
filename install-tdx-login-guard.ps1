[CmdletBinding()]
param(
    [string]$TdxPath = 'D:\tdx\TdxW.exe',
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'HermesTrader\TdxLoginGuard'),
    [string]$TaskName = 'HermesTrader-TdxLoginGuard',
    [switch]$NoStart
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$SourceGuard = Join-Path $PSScriptRoot 'tdx-login-guard.ps1'
$SourceConfig = Join-Path $PSScriptRoot 'tdx-login-guard.config.example.json'
$SourceCredentialSetup = Join-Path $PSScriptRoot 'set-tdx-login-credential.ps1'
$ProjectEnv = Join-Path $PSScriptRoot '.env'

foreach ($Required in @($SourceGuard, $SourceConfig, $SourceCredentialSetup, $TdxPath)) {
    if (-not (Test-Path -LiteralPath $Required -PathType Leaf)) {
        throw "找不到必需文件：$Required"
    }
}

$ResolvedInstallRoot = [IO.Path]::GetFullPath($InstallRoot)
if (-not $ResolvedInstallRoot.StartsWith([IO.Path]::GetFullPath($env:LOCALAPPDATA), [StringComparison]::OrdinalIgnoreCase)) {
    throw 'InstallRoot 必须位于当前用户 LOCALAPPDATA 下。'
}

New-Item -ItemType Directory -Path $ResolvedInstallRoot -Force | Out-Null
$InstalledGuard = Join-Path $ResolvedInstallRoot 'tdx-login-guard.ps1'
$InstalledConfig = Join-Path $ResolvedInstallRoot 'tdx-login-guard.json'
Copy-Item -LiteralPath $SourceGuard -Destination $InstalledGuard -Force
Copy-Item -LiteralPath $SourceCredentialSetup -Destination (Join-Path $ResolvedInstallRoot 'set-tdx-login-credential.ps1') -Force

if (Test-Path -LiteralPath $InstalledConfig -PathType Leaf) {
    $Settings = Get-Content -Raw -LiteralPath $InstalledConfig -Encoding utf8 | ConvertFrom-Json
    Write-Host '保留现有守护配置，并刷新程序文件。'
}
else {
    $Settings = Get-Content -Raw -LiteralPath $SourceConfig -Encoding utf8 | ConvertFrom-Json
}
$Settings.tdx_exe = [IO.Path]::GetFullPath($TdxPath)
$Settings.project_env_file = if (Test-Path -LiteralPath $ProjectEnv -PathType Leaf) { [IO.Path]::GetFullPath($ProjectEnv) } else { '' }
$CredentialFile = Join-Path $ResolvedInstallRoot 'tdx-login-credential.clixml'
if ($null -eq $Settings.PSObject.Properties['credential_file']) {
    $Settings | Add-Member -NotePropertyName 'credential_file' -NotePropertyValue $CredentialFile
}
else {
    $Settings.credential_file = $CredentialFile
}
$Settings.log_path = Join-Path $ResolvedInstallRoot 'tdx-login-guard.log'
$Settings | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $InstalledConfig -Encoding utf8

$PowerShell = Get-Command 'pwsh.exe' -ErrorAction SilentlyContinue
if ($null -eq $PowerShell) {
    $PowerShell = Get-Command 'powershell.exe' -ErrorAction Stop
}
$Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$InstalledGuard`" -Config `"$InstalledConfig`" -Watch"
$Action = New-ScheduledTaskAction -Execute $PowerShell.Source -Argument $Arguments -WorkingDirectory $ResolvedInstallRoot
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name)
$Principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
$TaskSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
$null = Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Principal $Principal -Settings $TaskSettings -Description '检测通达信 TQ 登录失效，按控件恢复登录并关闭扫码弹窗。' -Force

Write-Host "守护程序目录：$ResolvedInstallRoot"
Write-Host "计划任务：$TaskName"
Write-Host '默认不保存凭据；可运行 set-tdx-login-credential.ps1，用当前 Windows 用户 DPAPI 加密保存自动登录凭据。'
Write-Host "首次校准：pwsh.exe -NoProfile -File `"$InstalledGuard`" -Config `"$InstalledConfig`" -Inspect"

if (-not $NoStart) {
    Start-ScheduledTask -TaskName $TaskName
    Write-Host '计划任务已启动。'
}
