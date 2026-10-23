[CmdletBinding()]
param(
    [string]$Output,
    [switch]$DesktopShortcut
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$appRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
if (-not $Output) { $Output = Join-Path $appRoot 'SOPweave.exe' }
$Output = [IO.Path]::GetFullPath($Output)
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $compiler)) { throw 'The Windows .NET Framework C# compiler is unavailable.' }
$outputDirectory = [IO.Path]::GetDirectoryName($Output)
[IO.Directory]::CreateDirectory($outputDirectory) | Out-Null
$temporaryOutput = Join-Path $outputDirectory ('SOPweave-build-' + [Guid]::NewGuid().ToString('N') + '.exe')
$arguments = @(
    '/nologo', '/target:winexe', '/platform:x64', '/optimize+', '/codepage:65001',
    ('/out:' + $temporaryOutput),
    ('/win32manifest:' + (Join-Path $appRoot 'launcher\app.manifest')),
    ('/win32icon:' + (Join-Path $appRoot 'static\img\favicon.ico')),
    '/reference:System.dll', '/reference:System.Core.dll', '/reference:System.Drawing.dll',
    '/reference:System.Windows.Forms.dll', '/reference:System.Web.Extensions.dll'
)
$arguments += @(Get-ChildItem -LiteralPath (Join-Path $appRoot 'launcher') -Filter '*.cs' | Sort-Object Name | ForEach-Object FullName)
try {
    & $compiler @arguments
    if ($LASTEXITCODE -ne 0) { throw "Launcher compilation failed: $LASTEXITCODE" }
    Move-Item -LiteralPath $temporaryOutput -Destination $Output -Force
} finally {
    if ([IO.File]::Exists($temporaryOutput)) { [IO.File]::Delete($temporaryOutput) }
}
Write-Host ('Built: ' + $Output)
Write-Host ('SHA256: ' + (Get-FileHash -LiteralPath $Output -Algorithm SHA256).Hash)

if ($DesktopShortcut) {
    $desktop = [Environment]::GetFolderPath('Desktop')
    $shortcutPath = Join-Path $desktop 'SOPweave.lnk'
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($shortcutPath)
    if ([IO.File]::Exists($shortcutPath) -and $shortcut.TargetPath -ne $Output) {
        throw "An unrelated shortcut already exists: $shortcutPath"
    }
    $shortcut.TargetPath = $Output
    $shortcut.WorkingDirectory = $appRoot
    $shortcut.IconLocation = $Output + ',0'
    $shortcut.Description = 'Launch SOPweave in your default browser'
    if ([IO.Path]::GetDirectoryName($Output) -ne $appRoot) {
        $shortcut.Arguments = '--app-dir "' + $appRoot + '"'
    }
    $shortcut.Save()
    Write-Host ('Desktop shortcut: ' + $shortcutPath)
}
