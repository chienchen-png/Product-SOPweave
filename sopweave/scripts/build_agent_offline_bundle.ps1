param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$LockFile = Join-Path $ProjectRoot "requirements.lock"
$BundleDirectory = Join-Path $ProjectRoot "offline_packages"

if (-not (Test-Path -LiteralPath $LockFile -PathType Leaf)) {
    throw "Complete dependency lock file was not found: $LockFile"
}

New-Item -ItemType Directory -Force -Path $BundleDirectory | Out-Null

& $Python -m pip download `
    --require-hashes `
    --only-binary=:all: `
    --dest $BundleDirectory `
    --requirement $LockFile

if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Write-Host "Complete locked offline wheels are ready at $BundleDirectory"
