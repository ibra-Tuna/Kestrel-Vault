#Requires -Version 5.1
<#
.SYNOPSIS
    Build dist\KestrelVault.exe  (one-file).
.DESCRIPTION
    Creates .venv if missing, installs dependencies, runs PyInstaller.
    Requires Python 3.10-3.13 on PATH.
#>
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    # 1. Virtual environment (idempotent)
    if (-not (Test-Path '.venv\Scripts\python.exe')) {
        Write-Host 'Creating virtual environment…'
        python -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'Could not create .venv — is Python on PATH?' }
    }
    $py = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'

    # 2. Dependencies
    Write-Host 'Installing dependencies…'
    & $py -m pip install --quiet --upgrade pip
    & $py -m pip install --quiet -r requirements.txt pyinstaller
    if ($LASTEXITCODE -ne 0) { throw 'pip install failed' }

    # 3. Sanity checks
    if (-not (Test-Path 'kestrel_vault.spec')) { throw 'kestrel_vault.spec not found in repo root.' }
    if (-not (Test-Path 'kestrel.ico')) {
        Write-Warning 'kestrel.ico is missing — the .exe will have no icon.'
    }

    # 4. Build
    Write-Host 'Building…'
    & $py -m PyInstaller --noconfirm --clean kestrel_vault.spec
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed' }

    # 5. Confirm
    $exe = Join-Path $PSScriptRoot 'dist\KestrelVault.exe'
    if (-not (Test-Path $exe)) { throw "Build succeeded but $exe is missing." }
    $mb = [math]::Round((Get-Item $exe).Length / 1MB, 1)
    Write-Host ''
    Write-Host "Done: dist\KestrelVault.exe ($mb MB)" -ForegroundColor Green
}
finally {
    Pop-Location
}