# Builds the release: runs the tests, builds both programs with PyInstaller,
# then the installer with Inno Setup (if ISCC.exe is available).
#
#   .\build.ps1
#
# Output in dist\:
#   EpsonWasteInk.exe                  GUI, portable
#   reset-waste-ink.exe                command line, portable
#   EpsonWasteInk-<version>-setup.exe  installer

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$python = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" } else { "python" }
$version = & $python -c "import waste_ink; print(waste_ink.__version__)"
Write-Host "Building version $version"

& $python -m unittest discover tests
if ($LASTEXITCODE -ne 0) { throw "Tests failed" }

$common = @("--noconfirm", "--onefile", "--paths", "vendor", "--collect-submodules", "pysnmp")
& $python -m PyInstaller @common --windowed --name EpsonWasteInk gui.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller (GUI) failed" }
& $python -m PyInstaller @common --console --name reset-waste-ink reset_waste_ink.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller (CLI) failed" }

& "dist\reset-waste-ink.exe" --version
if ($LASTEXITCODE -ne 0) { throw "The built command line program does not start" }

$iscc = @(
    (Get-Command iscc -ErrorAction SilentlyContinue).Source,
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1

if ($iscc) {
    & $iscc "/DAppVersion=$version" installer.iss
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
} else {
    Write-Warning "Inno Setup (ISCC.exe) not found: skipped the installer. Install it with: winget install JRSoftware.InnoSetup"
}

Get-ChildItem dist -File | Format-Table Name, @{n = "MB"; e = { [math]::Round($_.Length / 1MB, 1) } }
