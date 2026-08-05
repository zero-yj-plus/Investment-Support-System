[CmdletBinding()]
param(
    [string]$Config = '.\config.yaml',
    [string]$HostAddress = '',
    [int]$Port = 0
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ProjectRoot = $PSScriptRoot
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'

function Import-DotEnv {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return
    }
    foreach ($RawLine in Get-Content -LiteralPath $Path -Encoding utf8) {
        $Line = $RawLine.Trim()
        if (-not $Line -or $Line.StartsWith('#')) {
            continue
        }
        $Parts = $Line.Split('=', 2)
        if ($Parts.Count -ne 2) {
            throw "无效的 .env 行：$RawLine"
        }
        $Name = $Parts[0].Trim()
        if ($Name -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') {
            throw "无效的 .env 变量名：$Name"
        }
        $Value = $Parts[1].Trim()
        if ($Value.Length -ge 2 -and (($Value.StartsWith('"') -and $Value.EndsWith('"')) -or ($Value.StartsWith("'") -and $Value.EndsWith("'")))) {
            $Value = $Value.Substring(1, $Value.Length - 2)
        }
        [Environment]::SetEnvironmentVariable($Name, $Value, 'Process')
    }
}

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw '尚未安装项目虚拟环境，请先运行 .\install.ps1。'
}

$ConfigPath = if ([System.IO.Path]::IsPathRooted($Config)) { $Config } else { Join-Path $ProjectRoot $Config }
if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
    throw "找不到配置文件：$ConfigPath。请先运行 .\install.ps1 并填写配置。"
}
if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot 'frontend-vue\dist\index.html') -PathType Leaf)) {
    throw '部署包缺少预构建前端 frontend-vue\dist\index.html。'
}

Import-DotEnv -Path (Join-Path $ProjectRoot '.env')
Set-Location -LiteralPath $ProjectRoot

$Arguments = @('-m', 'hermes_trader', '--config', $ConfigPath)
if ($HostAddress) {
    $Arguments += @('--host', $HostAddress)
}
if ($Port -gt 0) {
    $Arguments += @('--port', [string]$Port)
}

Write-Host '正在前台启动 Hermes Trader；按 Ctrl+C 停止。'
& $Python @Arguments
exit $LASTEXITCODE

