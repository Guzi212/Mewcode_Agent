[CmdletBinding()]
param(
    [string]$CargoPath = "cargo",
    [string]$PythonPath = "python",
    [switch]$Package,
    [switch]$Wheel,
    [switch]$Offline
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent $PSScriptRoot
$helperRoot = Join-Path $repoRoot "native\windows-sandbox-helper"
$releaseRoot = Join-Path $helperRoot "target\release"
$helperName = "mewcode-windows-sandbox.exe"
$helperPath = Join-Path $releaseRoot $helperName
$pythonVersionFile = Join-Path $repoRoot "mewcode\__init__.py"
$rustProtocolFile = Join-Path $helperRoot "src\protocol.rs"
$pythonProtocolFile = Join-Path $repoRoot "mewcode\sandbox\windows_protocol.py"

if (-not (Test-Path -LiteralPath $CargoPath -PathType Leaf) -and -not (Get-Command $CargoPath -ErrorAction SilentlyContinue)) {
    throw "Cargo was not found: $CargoPath"
}

$pythonVersionText = Get-Content -LiteralPath $pythonVersionFile -Raw -Encoding UTF8
$versionMatch = [regex]::Match($pythonVersionText, '(?m)^__version__\s*=\s*"([^"]+)"\s*$')
if (-not $versionMatch.Success) {
    throw "Unable to read the MewCode application version"
}
$appVersion = $versionMatch.Groups[1].Value

$rustProtocolText = Get-Content -LiteralPath $rustProtocolFile -Raw -Encoding UTF8
$pythonProtocolText = Get-Content -LiteralPath $pythonProtocolFile -Raw -Encoding UTF8
$rustProtocolMatch = [regex]::Match($rustProtocolText, '(?m)^pub const PROTOCOL_VERSION: u32 = (\d+);\s*$')
$pythonProtocolMatch = [regex]::Match($pythonProtocolText, '(?m)^PROTOCOL_VERSION\s*=\s*(\d+)\s*$')
if (-not $rustProtocolMatch.Success -or -not $pythonProtocolMatch.Success) {
    throw "Unable to read the Windows Helper protocol version"
}
if ($rustProtocolMatch.Groups[1].Value -ne $pythonProtocolMatch.Groups[1].Value) {
    throw "The Python and Rust protocol versions do not match"
}
$protocolVersion = [int]$rustProtocolMatch.Groups[1].Value

$cargoArguments = @("build", "--release", "--locked")
if ($Offline) {
    $cargoArguments += "--offline"
}
& $CargoPath @cargoArguments --manifest-path (Join-Path $helperRoot "Cargo.toml")
if ($LASTEXITCODE -ne 0) {
    throw "The Windows Helper build failed"
}
if (-not (Test-Path -LiteralPath $helperPath -PathType Leaf)) {
    throw "The build did not produce $helperName"
}

$cargoMetadata = & $CargoPath metadata --no-deps --format-version 1 --manifest-path (Join-Path $helperRoot "Cargo.toml")
if ($LASTEXITCODE -ne 0) {
    throw "Unable to read the Windows Helper version"
}
$metadata = $cargoMetadata | ConvertFrom-Json
$helperVersion = [string]$metadata.packages[0].version

function Write-Manifest {
    param(
        [Parameter(Mandatory = $true)][string]$Directory,
        [Parameter(Mandatory = $true)][bool]$Development
    )

    $binary = Join-Path $Directory $helperName
    $manifest = [ordered]@{
        protocol_version = $protocolVersion
        app_version = $appVersion
        helper_version = $helperVersion
        target = "windows-x86_64"
        sha256 = (Get-FileHash -LiteralPath $binary -Algorithm SHA256).Hash.ToLowerInvariant()
        development = $Development
    }
    $json = $manifest | ConvertTo-Json -Compress
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText((Join-Path $Directory "manifest.json"), $json + "`n", $encoding)
}

Write-Manifest -Directory $releaseRoot -Development $true

if ($Package -or $Wheel) {
    $packageRoot = Join-Path $repoRoot "mewcode\native\windows-x86_64"
    New-Item -ItemType Directory -Path $packageRoot -Force | Out-Null
    Copy-Item -LiteralPath $helperPath -Destination (Join-Path $packageRoot $helperName) -Force
    Write-Manifest -Directory $packageRoot -Development $false
}

if ($Wheel) {
    if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf) -and -not (Get-Command $PythonPath -ErrorAction SilentlyContinue)) {
        throw "Python was not found: $PythonPath"
    }
    $wheelRoot = Join-Path $helperRoot "target\wheel"
    $wheelTempRoot = Join-Path $wheelRoot "tmp"
    New-Item -ItemType Directory -Path $wheelRoot -Force | Out-Null
    New-Item -ItemType Directory -Path $wheelTempRoot -Force | Out-Null
    $previousTmpDir = $env:TMPDIR
    $previousTemp = $env:TEMP
    $previousTmp = $env:TMP
    try {
        # Keep build temp files on the current workspace drive.
        $env:TMPDIR = $wheelTempRoot
        $env:TEMP = $wheelTempRoot
        $env:TMP = $wheelTempRoot
        $pythonTempOutput = @(& $PythonPath -c "import os, tempfile; print(os.environ.get('TMPDIR', '')); print(tempfile.gettempdir())")
        $pythonTempExitCode = $LASTEXITCODE
        $resolvedPythonTemp = [string]($pythonTempOutput | Select-Object -Last 1)
        $resolvedPythonTemp = $resolvedPythonTemp.Trim()
        $pythonEnvironmentTemp = if ($pythonTempOutput.Count -ge 2) {
            [string]$pythonTempOutput[$pythonTempOutput.Count - 2]
        }
        else {
            ""
        }
        $pythonEnvironmentTemp = $pythonEnvironmentTemp.Trim()
        $resolvedPythonTempPath = if ($resolvedPythonTemp) {
            [System.IO.Path]::GetFullPath($resolvedPythonTemp)
        }
        else {
            ""
        }
        $expectedPythonTemp = [System.IO.Path]::GetFullPath($wheelTempRoot)
        if (
            $pythonTempExitCode -ne 0 -or
            -not $resolvedPythonTempPath -or
            -not $pythonEnvironmentTemp -or
            -not $resolvedPythonTempPath.Equals(
                $expectedPythonTemp,
                [System.StringComparison]::OrdinalIgnoreCase
            ) -or
            -not [System.IO.Path]::GetFullPath($pythonEnvironmentTemp).Equals(
                $expectedPythonTemp,
                [System.StringComparison]::OrdinalIgnoreCase
            )
        ) {
            throw "Python wheel temp directory mismatch: expected '$expectedPythonTemp', inherited '$pythonEnvironmentTemp', selected '$resolvedPythonTempPath'"
        }
        & $PythonPath -m pip wheel $repoRoot --no-deps --no-build-isolation `
            --no-cache-dir `
            "--config-settings=--build-option=--plat-name=win_amd64" `
            --wheel-dir $wheelRoot
    }
    finally {
        $env:TMPDIR = $previousTmpDir
        $env:TEMP = $previousTemp
        $env:TMP = $previousTmp
    }
    if ($LASTEXITCODE -ne 0) {
        throw "The Windows wheel build failed"
    }
    $wheelArtifact = Get-ChildItem -LiteralPath $wheelRoot -Filter "mewcode-*-win_amd64.whl" |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if ($null -eq $wheelArtifact) {
        throw "The build did not produce a win_amd64 wheel"
    }
    Write-Output "Windows wheel built: $($wheelArtifact.FullName)"
}

Write-Output "Windows Helper built: $helperPath"
