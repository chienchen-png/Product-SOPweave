[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location -LiteralPath $ProjectRoot

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$Label,
        [Parameter(Mandatory = $true)][string]$File,
        [Parameter()][string[]]$Arguments = @()
    )
    Write-Host "[CHECK] $Label"
    & $File @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed with exit code $LASTEXITCODE"
    }
    Write-Host "[PASS]  $Label"
}

function Find-Python {
    $candidates = @(
        @{ File = (Join-Path $ProjectRoot ".runtime\Scripts\python.exe"); Args = @() },
        @{ File = (Join-Path $ProjectRoot ".venv\Scripts\python.exe"); Args = @() },
        @{ File = (Join-Path $ProjectRoot "..\.venv\Scripts\python.exe"); Args = @() },
        @{ File = "py"; Args = @("-3.14") },
        @{ File = "py"; Args = @("-3") },
        @{ File = "python"; Args = @() }
    )
    foreach ($candidate in $candidates) {
        $file = [string]$candidate.File
        if ([System.IO.Path]::IsPathRooted($file) -and -not (Test-Path -LiteralPath $file)) {
            continue
        }
        if (-not [System.IO.Path]::IsPathRooted($file) -and -not (Get-Command $file -ErrorAction SilentlyContinue)) {
            continue
        }
        $previousPreference = $ErrorActionPreference
        $ErrorActionPreference = "SilentlyContinue"
        & $file @($candidate.Args) -c "import sys, flask, openpyxl, agents, fastapi, uvicorn, yaml; raise SystemExit(sys.version_info[:2] != (3, 14))" *> $null
        $candidateExitCode = $LASTEXITCODE
        $ErrorActionPreference = $previousPreference
        if ($candidateExitCode -eq 0) {
            return $candidate
        }
    }
    throw "Python 3.14 x64 was not found"
}

Write-Host "SOPweave runtime acceptance"
Invoke-Checked "portable startup and dependency imports" (Join-Path $ProjectRoot "SOPweave.bat") @("--check")

$python = Find-Python
$pythonFile = [string]$python.File
$pythonArgs = @($python.Args)

Invoke-Checked "complete backend suite" $pythonFile `
    ($pythonArgs + @("-m", "unittest", "discover", "-s", "tests", "-t", ".", "-v"))

Invoke-Checked "Python source compilation" $pythonFile `
    ($pythonArgs + @("-m", "compileall", "-q", "app.py", "asgi.py", "agent_runtime", "routes", "services", "storage", "tools"))

if (-not (Get-Command "npm" -ErrorAction SilentlyContinue)) {
    throw "npm was not found"
}
$frontend = Join-Path $ProjectRoot "frontend"
Push-Location -LiteralPath $frontend
try {
    Invoke-Checked "complete frontend suite" "npm" @("test")
    Invoke-Checked "frontend production build" "npm" @("run", "build")
} finally {
    Pop-Location
}

$indexPath = Join-Path $frontend "dist\index.html"
if (-not (Test-Path -LiteralPath $indexPath)) {
    throw "frontend build artifact is missing"
}
Write-Host "[PASS]  frontend build artifact"
Write-Host "ALL CHECKS PASSED"
