[CmdletBinding()]
param(
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'HermesTrader\TdxLoginGuard')
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ResolvedRoot = [IO.Path]::GetFullPath($InstallRoot)
$AllowedRoot = [IO.Path]::GetFullPath($env:LOCALAPPDATA)
if (-not $ResolvedRoot.StartsWith($AllowedRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'InstallRoot 必须位于当前用户 LOCALAPPDATA 下。'
}

New-Item -ItemType Directory -Path $ResolvedRoot -Force | Out-Null
$Credential = Get-Credential -Message '请输入通达信账号和密码。凭据仅使用当前 Windows 用户 DPAPI 加密保存在本机。'
if ($null -eq $Credential) {
    Write-Host '已取消，未保存凭据。'
    exit 1
}

$Protected = [pscustomobject]@{
    Username = ConvertTo-SecureString -String $Credential.UserName -AsPlainText -Force
    Password = $Credential.Password
}
$CredentialPath = Join-Path $ResolvedRoot 'tdx-login-credential.clixml'
$Protected | Export-Clixml -LiteralPath $CredentialPath -Force
$Credential = $null
$Protected = $null
Write-Host '通达信凭据已用当前 Windows 用户 DPAPI 加密保存。'
Start-Sleep -Seconds 3
