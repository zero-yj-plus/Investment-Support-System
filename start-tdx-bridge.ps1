[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ProjectRoot = $PSScriptRoot
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$BridgeScript = Join-Path $ProjectRoot 'scripts\tq_tdx_bridge.py'

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
if (-not (Test-Path -LiteralPath $BridgeScript -PathType Leaf)) {
    throw '部署包不完整：缺少 scripts\tq_tdx_bridge.py。'
}

Import-DotEnv -Path (Join-Path $ProjectRoot '.env')

$BridgeKey = [Environment]::GetEnvironmentVariable('TQ_BRIDGE_API_KEY', 'Process')
if (-not $BridgeKey) {
    $BridgeKey = [Environment]::GetEnvironmentVariable('TDXAPI_KEY', 'Process')
}
if (-not $BridgeKey) {
    throw '请先在 .env 中设置 TQ_BRIDGE_API_KEY 或 TDXAPI_KEY；桥接服务拒绝无密钥启动。'
}

$TdxBase = [Environment]::GetEnvironmentVariable('TDXAPI_BASE', 'Process')
if (-not $TdxBase) {
    Write-Warning 'TDXAPI_BASE 尚未配置。主服务不会使用此桥；建议设置为 http://127.0.0.1:18879。'
}

Set-Location -LiteralPath $ProjectRoot
Write-Host '正在前台启动只读 TQ/TDX 兼容桥；按 Ctrl+C 停止。'
& $Python $BridgeScript
exit $LASTEXITCODE

