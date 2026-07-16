[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Destination,
    [string]$CargoPath = "cargo",
    [string]$BootstrapPython = "python",
    [string]$Wheelhouse = "",
    [switch]$OfflineCargo
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$sourceRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
if (-not [System.IO.Path]::IsPathRooted($Destination)) {
    throw "Clean-room destination must be absolute"
}
$destinationRoot = [System.IO.Path]::GetFullPath($Destination)
if ($destinationRoot -eq $sourceRoot -or $destinationRoot.StartsWith(
        $sourceRoot + [System.IO.Path]::DirectorySeparatorChar,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
    throw "Clean-room destination must be outside the source repository"
}
if (Test-Path -LiteralPath $destinationRoot) {
    throw "Clean-room destination already exists: $destinationRoot"
}
$trackedAndNew = & git -C $sourceRoot ls-files --cached --others --exclude-standard
if ($LASTEXITCODE -ne 0 -or -not $trackedAndNew) {
    throw "Unable to enumerate clean-room source files"
}
New-Item -ItemType Directory -Path $destinationRoot | Out-Null
foreach ($relative in $trackedAndNew) {
    if ([System.IO.Path]::IsPathRooted($relative) -or $relative.Split('/').Contains('..')) {
        throw "Unsafe source path from Git: $relative"
    }
    $source = [System.IO.Path]::GetFullPath((Join-Path $sourceRoot $relative))
    $destination = [System.IO.Path]::GetFullPath((Join-Path $destinationRoot $relative))
    if (-not $source.StartsWith(
            $sourceRoot + [System.IO.Path]::DirectorySeparatorChar,
            [System.StringComparison]::OrdinalIgnoreCase
        ) -or -not $destination.StartsWith(
            $destinationRoot + [System.IO.Path]::DirectorySeparatorChar,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
        throw "Clean-room copy path escaped its root: $relative"
    }
    $parent = Split-Path -Parent $destination
    if (-not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    Copy-Item -LiteralPath $source -Destination $destination
}

$tempRoot = Join-Path $destinationRoot ".verify-temp"
$venvRoot = Join-Path $destinationRoot ".verify-venv"
$workspace = Join-Path $destinationRoot ".verify-workspace"
New-Item -ItemType Directory -Path $tempRoot | Out-Null
New-Item -ItemType Directory -Path $workspace | Out-Null
$previousTmpDir = $env:TMPDIR
$previousTemp = $env:TEMP
$previousTmp = $env:TMP
try {
    $env:TMPDIR = $tempRoot
    $env:TEMP = $tempRoot
    $env:TMP = $tempRoot

    $buildArguments = @{
        CargoPath = $CargoPath
        PythonPath = $BootstrapPython
        Wheel = $true
    }
    if ($OfflineCargo) {
        $buildArguments["Offline"] = $true
    }
    & (Join-Path $destinationRoot "scripts\build_windows_helper.ps1") @buildArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Clean-room Helper/wheel build failed"
    }

    $basePythonOutput = @(& $BootstrapPython -c "import sys; print(sys._base_executable)")
    $basePython = [string]($basePythonOutput | Select-Object -Last 1)
    $basePython = $basePython.Trim()
    if (
        $LASTEXITCODE -ne 0 -or
        -not $basePython -or
        -not (Test-Path -LiteralPath $basePython -PathType Leaf)
    ) {
        throw "Unable to resolve the base Python interpreter from: $BootstrapPython"
    }
    & $basePython -m venv $venvRoot
    if ($LASTEXITCODE -ne 0) {
        throw "Clean-room virtual environment creation failed"
    }
    $python = Join-Path $venvRoot "Scripts\python.exe"
    $mewcode = Join-Path $venvRoot "Scripts\mewcode.exe"
    $wheelArtifact = Get-ChildItem `
        -LiteralPath (Join-Path $destinationRoot "native\windows-sandbox-helper\target\wheel") `
        -Filter "mewcode-*-win_amd64.whl" |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if ($null -eq $wheelArtifact) {
        throw "Clean-room Windows wheel was not produced"
    }
    $pipArguments = @("-m", "pip", "install", "--no-cache-dir")
    if ($Wheelhouse) {
        $resolvedWheelhouse = [System.IO.Path]::GetFullPath($Wheelhouse)
        if (-not (Test-Path -LiteralPath $resolvedWheelhouse -PathType Container)) {
            throw "Wheelhouse does not exist: $resolvedWheelhouse"
        }
        $pipArguments += @("--no-index", "--find-links", $resolvedWheelhouse)
    }
    $pipArguments += $wheelArtifact.FullName
    & $python @pipArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Clean-room wheel installation failed"
    }

    & $mewcode sandbox setup
    if ($LASTEXITCODE -ne 0) {
        throw "Clean-room sandbox setup failed"
    }
    & $mewcode sandbox diagnose
    if ($LASTEXITCODE -ne 0) {
        throw "Clean-room sandbox diagnose failed"
    }

    $helper = Join-Path $destinationRoot `
        "mewcode\native\windows-x86_64\mewcode-windows-sandbox.exe"
    $smoke = Join-Path $destinationRoot "scripts\smoke_windows_helper.py"
    foreach ($scenario in @(
            "diagnose", "write", "read", "edit", "search", "find",
            "env", "acl", "nonzero", "network", "timeout"
        )) {
        & $python $smoke `
            --helper $helper `
            --python $python `
            --workspace $workspace `
            --scenario $scenario
        if ($LASTEXITCODE -ne 0) {
            throw "Clean-room smoke failed: $scenario"
        }
    }

    $outsideRoot = Join-Path $destinationRoot ".verify-outside"
    $outsideFile = Join-Path $outsideRoot "secret.txt"
    New-Item -ItemType Directory -Path $outsideRoot | Out-Null
    [System.IO.File]::WriteAllText($outsideFile, "host-only")
    foreach ($scenario in @("outside", "outside_read")) {
        & $python $smoke `
            --helper $helper `
            --python $python `
            --workspace $workspace `
            --scenario $scenario `
            --path $outsideFile
        if ($LASTEXITCODE -ne 0) {
            throw "Clean-room boundary smoke failed: $scenario"
        }
    }
    if ([System.IO.File]::ReadAllText($outsideFile) -ne "host-only") {
        throw "Clean-room boundary smoke modified the outside file"
    }
}
finally {
    $env:TMPDIR = $previousTmpDir
    $env:TEMP = $previousTemp
    $env:TMP = $previousTmp
}

Write-Output "CLEAN_ROOM_READY=$destinationRoot"
Write-Output "CLEAN_ROOM_WHEEL=$($wheelArtifact.Name)"
