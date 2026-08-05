[CmdletBinding()]
param(
    [string]$BaseUrl = 'http://127.0.0.1:18869',
    [switch]$SkipBridge,
    [int]$TimeoutSec = 10
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ProjectRoot = $PSScriptRoot
$Failures = [System.Collections.Generic.List[string]]::new()

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

function Add-Pass {
    param([Parameter(Mandatory = $true)][string]$Message)
    Write-Host "[PASS] $Message" -ForegroundColor Green
}

function Add-Skip {
    param([Parameter(Mandatory = $true)][string]$Message)
    Write-Host "[SKIP] $Message" -ForegroundColor Yellow
}

function Add-Failure {
    param([Parameter(Mandatory = $true)][string]$Message)
    $script:Failures.Add($Message)
    Write-Host "[FAIL] $Message" -ForegroundColor Red
}

if ($TimeoutSec -lt 1 -or $TimeoutSec -gt 60) {
    throw 'TimeoutSec 必须在 1 到 60 秒之间。'
}

$RequiredPaths = @(
    '.venv\Scripts\python.exe',
    'config.yaml',
    'requirements.txt',
    'hermes_trader\__init__.py',
    'frontend-vue\dist\index.html'
)
foreach ($RelativePath in $RequiredPaths) {
    $FullPath = Join-Path $ProjectRoot $RelativePath
    if (Test-Path -LiteralPath $FullPath) {
        Add-Pass $RelativePath
    }
    else {
        Add-Failure "缺少 $RelativePath"
    }
}

$BaseUrl = $BaseUrl.TrimEnd('/')
try {
    $Home = Invoke-WebRequest -Uri "$BaseUrl/" -Method Get -TimeoutSec $TimeoutSec -UseBasicParsing
    if ($Home.StatusCode -eq 200) {
        Add-Pass "首页 $BaseUrl/"
    }
    else {
        Add-Failure "首页返回 HTTP $($Home.StatusCode)"
    }
}
catch {
    Add-Failure "首页不可达：$($_.Exception.Message)"
}

foreach ($Endpoint in @('/api/status', '/api/datasources')) {
    try {
        $null = Invoke-RestMethod -Uri "$BaseUrl$Endpoint" -Method Get -TimeoutSec $TimeoutSec
        Add-Pass $Endpoint
    }
    catch {
        Add-Failure "$Endpoint 不可达：$($_.Exception.Message)"
    }
}

Import-DotEnv -Path (Join-Path $ProjectRoot '.env')
if ($SkipBridge) {
    Add-Skip '按参数跳过 TQ/TDX 桥检查'
}
else {
    $TdxBase = [Environment]::GetEnvironmentVariable('TDXAPI_BASE', 'Process')
    $BridgeKey = [Environment]::GetEnvironmentVariable('TQ_BRIDGE_API_KEY', 'Process')
    if (-not $BridgeKey) {
        $BridgeKey = [Environment]::GetEnvironmentVariable('TDXAPI_KEY', 'Process')
    }

    if (-not $TdxBase -or -not $BridgeKey) {
        Add-Skip 'TQ/TDX 桥未配置'
    }
    elseif ($TdxBase -notmatch '^https?://(127\.0\.0\.1|localhost|\[::1\])(?::\d+)?/?$') {
        Add-Skip 'TDXAPI_BASE 不是本机桥地址，不发送本地桥健康检查'
    }
    else {
        try {
            $Headers = @{ 'X-API-Key' = $BridgeKey }
            $Health = Invoke-RestMethod -Uri "$($TdxBase.TrimEnd('/'))/v1/meta/health" -Headers $Headers -Method Get -TimeoutSec $TimeoutSec
            if ($Health.connected -eq $true) {
                Add-Pass 'TQ/TDX 桥及上游 TQ'
            }
            else {
                Add-Failure 'TQ/TDX 桥可达，但上游 TQ 未连接'
            }
        }
        catch {
            Add-Failure "TQ/TDX 桥检查失败：$($_.Exception.Message)"
        }
    }
}

Write-Host ''
if ($Failures.Count -gt 0) {
    Write-Host "检查失败：$($Failures.Count) 项。" -ForegroundColor Red
    exit 1
}

Write-Host '基础部署检查通过。' -ForegroundColor Green
exit 0

