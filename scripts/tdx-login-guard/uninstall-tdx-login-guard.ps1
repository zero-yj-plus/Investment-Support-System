[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium')]
param(
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'HermesTrader\TdxLoginGuard'),
    [string]$TaskName = 'HermesTrader-TdxLoginGuard',
    [switch]$RemoveFiles
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -ne $Task -and $PSCmdlet.ShouldProcess($TaskName, '停止并注销计划任务')) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "已注销计划任务：$TaskName"
}

if ($RemoveFiles) {
    $Resolved = [IO.Path]::GetFullPath($InstallRoot)
    $AllowedRoot = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'HermesTrader'))
    if (-not $Resolved.StartsWith($AllowedRoot, [StringComparison]::OrdinalIgnoreCase) -or $Resolved -eq $AllowedRoot) {
        throw "拒绝删除非预期目录：$Resolved"
    }
    if (Test-Path -LiteralPath $Resolved -PathType Container -and $PSCmdlet.ShouldProcess($Resolved, '删除守护程序文件和日志')) {
        Remove-Item -LiteralPath $Resolved -Recurse -Force
        Write-Host "已删除：$Resolved"
    }
}
else {
    Write-Host '保留守护程序配置和日志；如需删除，请增加 -RemoveFiles。'
}
