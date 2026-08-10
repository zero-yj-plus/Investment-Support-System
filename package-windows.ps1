[CmdletBinding()]
param(
    [string]$Version = '1.4.1'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ProjectRoot = $PSScriptRoot
$OutputRoot = Join-Path $ProjectRoot 'dist'
$StageRoot = Join-Path $env:TEMP ('wudi-stage-' + [guid]::NewGuid().ToString('N'))
$VerifyRoot = Join-Path $env:TEMP ('wudi-verify-' + [guid]::NewGuid().ToString('N'))
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$Timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$ZipName = "WUDI-$Version-Windows-deploy-$Timestamp.zip"
$ZipPath = Join-Path $OutputRoot $ZipName

function Copy-ReleaseFile {
    param([string]$RelativePath)

    $Source = Join-Path $ProjectRoot $RelativePath
    if (-not (Test-Path -LiteralPath $Source -PathType Leaf)) {
        throw "Missing release file: $RelativePath"
    }

    $Destination = Join-Path $StageRoot $RelativePath
    New-Item -ItemType Directory `
        -Path (Split-Path -Parent $Destination) `
        -Force | Out-Null

    Copy-Item -LiteralPath $Source -Destination $Destination -Force
}

function Copy-ReleaseTree {
    param([string]$RelativePath)

    $SourceRoot = Join-Path $ProjectRoot $RelativePath
    if (-not (Test-Path -LiteralPath $SourceRoot -PathType Container)) {
        throw "Missing release directory: $RelativePath"
    }

    Get-ChildItem -LiteralPath $SourceRoot -Recurse -File |
        Where-Object {
            $_.FullName -notmatch '\\__pycache__(\\|$)' -and
            $_.Extension -notin @('.pyc', '.pyo')
        } |
        ForEach-Object {
            $RelativeFile = $_.FullName.Substring(
                $ProjectRoot.Length
            ).TrimStart([char]'\')

            $Destination = Join-Path $StageRoot $RelativeFile
            New-Item -ItemType Directory `
                -Path (Split-Path -Parent $Destination) `
                -Force | Out-Null

            Copy-Item -LiteralPath $_.FullName `
                -Destination $Destination `
                -Force
        }
}

try {
    New-Item -ItemType Directory -Path $OutputRoot -Force | Out-Null
    New-Item -ItemType Directory -Path $StageRoot -Force | Out-Null

    $ReleaseFiles = @(
        '.env.example',
        'config.example.yaml',
        'requirements.txt',
        'README.md',
        'SOURCE_DELIVERY.txt',
        'install.bat',
        'start.bat',
        'start-tdx-bridge.bat',
        'check.bat',
        'upgrade.bat',
        'docs\TQ_TDX_BRIDGE.md'
    )

    $ReleaseDirectories = @(
        'hermes_trader',
        'scripts',
        'frontend-vue\dist'
    )

    foreach ($File in $ReleaseFiles) {
        Copy-ReleaseFile $File
    }

    foreach ($Directory in $ReleaseDirectories) {
        Copy-ReleaseTree $Directory
    }

    # Windows PowerShell 5.1 needs BOM for scripts containing Chinese.
    $Utf8Bom = New-Object System.Text.UTF8Encoding -ArgumentList (,$true)

    Get-ChildItem -LiteralPath $StageRoot -Recurse -Filter '*.ps1' -File |
        ForEach-Object {
            $Text = Get-Content -LiteralPath $_.FullName -Raw -Encoding UTF8
            [System.IO.File]::WriteAllText($_.FullName, $Text, $Utf8Bom)
        }

    # Keep CMD entrypoints ASCII + CRLF for old Windows Server cmd.exe.
    $Ascii = New-Object System.Text.ASCIIEncoding
    Get-ChildItem -LiteralPath $StageRoot -Recurse -Filter '*.bat' -File |
        ForEach-Object {
            $Text = Get-Content -LiteralPath $_.FullName -Raw -Encoding UTF8
            $Text = $Text -replace "`r?`n", "`r`n"
            [System.IO.File]::WriteAllText($_.FullName, $Text, $Ascii)
        }

    $RequiredPaths = @(
        'hermes_trader\__init__.py',
        'hermes_trader\main.py',
        'hermes_trader\data\__init__.py',
        'frontend-vue\dist\index.html',
        'install.bat',
        'start.bat',
        'start-tdx-bridge.bat',
        'scripts\tdx-login-guard\tdx-login-guard.ps1',
        'scripts\tdx-login-guard\tdx-login-guard.config.example.json',
        'scripts\tdx-login-guard\set-tdx-login-credential.ps1',
        'scripts\tdx-login-guard\install-tdx-login-guard.ps1',
        'scripts\tdx-login-guard\uninstall-tdx-login-guard.ps1',
        'check.bat',
        'upgrade.bat',
        'scripts\windows_upgrade.py',
        'docs\TQ_TDX_BRIDGE.md',
        'requirements.txt'
    )

    foreach ($RequiredPath in $RequiredPaths) {
        if (-not (Test-Path -LiteralPath (Join-Path $StageRoot $RequiredPath))) {
            throw "Package validation failed: missing $RequiredPath"
        }
    }

    # Generate package file manifest.
    $ManifestPath = Join-Path $StageRoot 'RELEASE_FILES.sha256'

    $ManifestLines = Get-ChildItem -LiteralPath $StageRoot -Recurse -File |
        Sort-Object FullName |
        ForEach-Object {
            $Relative = $_.FullName.Substring(
                $StageRoot.Length
            ).TrimStart([char]'\').Replace('\', '/')

            $Hash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).
                Hash.ToLowerInvariant()

            "$Hash  ./$Relative"
        }

    Set-Content -LiteralPath $ManifestPath `
        -Value $ManifestLines `
        -Encoding UTF8

    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
        throw 'Run install.bat in the source project before packaging.'
    }

    # Verify imports from staging, not from the source repository.
    $env:PYTHONDONTWRITEBYTECODE = '1'

    Push-Location $StageRoot
    try {
        & $Python -c "import hermes_trader.data; from hermes_trader.main import main; print('staging import OK')"
        if ($LASTEXITCODE -ne 0) {
            throw 'Staging import validation failed.'
        }
    }
    finally {
        Pop-Location
    }

    # Use .NET ZIP so dotfiles such as .env.example are included.
    Add-Type -AssemblyName System.IO.Compression.FileSystem

    [System.IO.Compression.ZipFile]::CreateFromDirectory(
        $StageRoot,
        $ZipPath,
        [System.IO.Compression.CompressionLevel]::Optimal,
        $false
    )

    # Extract and validate the real ZIP.
    Expand-Archive -LiteralPath $ZipPath `
        -DestinationPath $VerifyRoot `
        -Force

    Push-Location $VerifyRoot
    try {
        & $Python -c "import hermes_trader.data; from hermes_trader.main import main; print('zip import OK')"
        if ($LASTEXITCODE -ne 0) {
            throw 'ZIP import validation failed.'
        }
    }
    finally {
        Pop-Location
    }

    $ZipHash = (Get-FileHash -LiteralPath $ZipPath -Algorithm SHA256).
        Hash.ToLowerInvariant()

    Set-Content -LiteralPath "$ZipPath.sha256" `
        -Value "$ZipHash  $ZipName" `
        -Encoding ASCII

    $UpgradeBatName = "WUDI-$Version-Windows-upgrade-$Timestamp.bat"
    $UpgradeBatPath = Join-Path $OutputRoot $UpgradeBatName
    $UpgradeBatText = Get-Content `
        -LiteralPath (Join-Path $ProjectRoot 'upgrade.bat') `
        -Raw `
        -Encoding UTF8
    $UpgradeBatText = $UpgradeBatText -replace "`r?`n", "`r`n"
    [System.IO.File]::WriteAllText($UpgradeBatPath, $UpgradeBatText, $Ascii)

    Write-Host "Package created:"
    Write-Host "  $ZipPath"
    Write-Host "Upgrade bootstrap:"
    Write-Host "  $UpgradeBatPath"
    Write-Host "SHA256:"
    Write-Host "  $ZipHash"
}
finally {
    if (Test-Path -LiteralPath $StageRoot) {
        Remove-Item -LiteralPath $StageRoot -Recurse -Force
    }

    if (Test-Path -LiteralPath $VerifyRoot) {
        Remove-Item -LiteralPath $VerifyRoot -Recurse -Force
    }
}
