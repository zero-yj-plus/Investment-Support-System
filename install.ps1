[CmdletBinding()]
param(
    [switch]$SkipPipUpgrade
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ProjectRoot = $PSScriptRoot
$RequirementsPath = Join-Path $ProjectRoot 'requirements.txt'
$VenvRoot = Join-Path $ProjectRoot '.venv'
$VenvPython = Join-Path $VenvRoot 'Scripts\python.exe'

function Invoke-NativeChecked {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$Description
    )

    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Description 失败，退出码：$LASTEXITCODE"
    }
}

function Test-PythonCandidate {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$PrefixArguments = @()
    )

    $probe = 'import struct,sys; ok=sys.version_info >= (3,10) and struct.calcsize("P")*8 == 64; print(f"Python {sys.version.split()[0]} ({struct.calcsize(''P'')*8}-bit)"); raise SystemExit(0 if ok else 1)'
    & $FilePath @PrefixArguments -c $probe
    return ($LASTEXITCODE -eq 0)
}

if (-not (Test-Path -LiteralPath $RequirementsPath -PathType Leaf)) {
    throw "部署包不完整：缺少 $RequirementsPath"
}
if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot 'hermes_trader') -PathType Container)) {
    throw '部署包不完整：缺少 hermes_trader 目录'
}

Write-Host "部署目录：$ProjectRoot"

if (-not (Test-Path -LiteralPath $VenvPython -PathType Leaf)) {
    $PythonCommand = $null

    $PyLauncher = Get-Command 'py.exe' -ErrorAction SilentlyContinue
    if ($null -ne $PyLauncher -and (Test-PythonCandidate -FilePath $PyLauncher.Source -PrefixArguments @('-3'))) {
        $PythonCommand = [pscustomobject]@{ FilePath = $PyLauncher.Source; Arguments = @('-3') }
    }

    if ($null -eq $PythonCommand) {
        $Python = Get-Command 'python.exe' -ErrorAction SilentlyContinue
        if ($null -ne $Python -and (Test-PythonCandidate -FilePath $Python.Source)) {
            $PythonCommand = [pscustomobject]@{ FilePath = $Python.Source; Arguments = @() }
        }
    }

    if ($null -eq $PythonCommand) {
        throw '未找到可用的 64 位 Python 3.10+。请安装 64 位 Python，然后重新运行本脚本。'
    }

    Write-Host '正在创建项目虚拟环境 .venv ...'
    $VenvArguments = @($PythonCommand.Arguments) + @('-m', 'venv', $VenvRoot)
    Invoke-NativeChecked -FilePath $PythonCommand.FilePath -Arguments $VenvArguments -Description '创建虚拟环境'
}
else {
    Write-Host '检测到现有 .venv，将复用该环境。'
}

if (-not (Test-PythonCandidate -FilePath $VenvPython)) {
    throw '项目 .venv 不是可用的 64 位 Python 3.10+ 环境；请移走该目录后重新安装。'
}

if (-not $SkipPipUpgrade) {
    Write-Host '正在升级 pip ...'
    Invoke-NativeChecked -FilePath $VenvPython -Arguments @('-m', 'pip', 'install', '--upgrade', 'pip') -Description '升级 pip'
}

Write-Host '正在安装生产依赖 ...'
Invoke-NativeChecked -FilePath $VenvPython -Arguments @('-m', 'pip', 'install', '-r', $RequirementsPath) -Description '安装生产依赖'
Invoke-NativeChecked -FilePath $VenvPython -Arguments @('-m', 'pip', 'check') -Description '检查 Python 依赖'

$ConfigPath = Join-Path $ProjectRoot 'config.yaml'
$ConfigExample = Join-Path $ProjectRoot 'config.example.yaml'
if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
    Copy-Item -LiteralPath $ConfigExample -Destination $ConfigPath
    Write-Host '已从 config.example.yaml 创建 config.yaml。'
}
else {
    Write-Host '保留现有 config.yaml，未覆盖。'
}

$EnvPath = Join-Path $ProjectRoot '.env'
$EnvExample = Join-Path $ProjectRoot '.env.example'
if (-not (Test-Path -LiteralPath $EnvPath -PathType Leaf)) {
    Copy-Item -LiteralPath $EnvExample -Destination $EnvPath
    Write-Host '已从 .env.example 创建 .env。'
}
else {
    Write-Host '保留现有 .env，未覆盖。'
}

Write-Host ''
Write-Host '安装完成。请先填写 config.yaml 和 .env，再运行：'
Write-Host '  .\start.ps1'
Write-Host '如使用本机通达信 TQ 服务，再单独运行：'
Write-Host '  .\start-tdx-bridge.ps1'

