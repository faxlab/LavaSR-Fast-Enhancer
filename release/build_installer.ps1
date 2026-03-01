[CmdletBinding()]
param(
  [string]$Version = "",
  [switch]$PortableOnly
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$python = Join-Path $root ".venv\\Scripts\\python.exe"
if (-not (Test-Path $python)) {
  Write-Host "Creating virtual environment..."
  py -3 -m venv .venv
}

$python = Join-Path $root ".venv\\Scripts\\python.exe"

Write-Host "Installing dependencies..."
& $python -m pip install --upgrade pip
& $python -m pip install -r requirements.txt -r requirements-dev.txt

if ([string]::IsNullOrWhiteSpace($Version)) {
  $versionLine = Select-String -Path ".\\lavasr_gui.py" -Pattern '^\s*APP_VERSION\s*=\s*"([^"]+)"\s*$' | Select-Object -First 1
  if (-not $versionLine) { throw "APP_VERSION not found in lavasr_gui.py" }
  $Version = $versionLine.Matches[0].Groups[1].Value
}

Write-Host "Building executable (version $Version)..."
if (Test-Path ".\\dist") { Remove-Item ".\\dist" -Recurse -Force }
if (Test-Path ".\\build") { Remove-Item ".\\build" -Recurse -Force }

& $python -m PyInstaller `
  --noconfirm `
  --clean `
  --windowed `
  --name "LavaSRFastEnhancer" `
  --collect-data "librosa" `
  --collect-data "lazy_loader" `
  --copy-metadata "librosa" `
  --copy-metadata "lazy_loader" `
  --add-data "app_config.json;." `
  --add-data "assets\\toollogo.png;assets" `
  --add-data "README.md;." `
  --add-data "LICENSE;." `
  --add-data "THIRD_PARTY_NOTICES.md;." `
  .\\lavasr_gui.py

$iscc = $null
$isccCommand = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
if (-not $isccCommand) { $isccCommand = Get-Command "iscc" -ErrorAction SilentlyContinue }
if ($isccCommand) { $iscc = $isccCommand.Source }

if (-not $iscc) {
  $isccCandidates = @(
    "$env:ProgramFiles(x86)\\Inno Setup 6\\ISCC.exe",
    "$env:ProgramFiles\\Inno Setup 6\\ISCC.exe"
  )
  $iscc = $isccCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
}

if (-not $iscc) {
  $winget = Get-Command "winget" -ErrorAction SilentlyContinue
  $choco = Get-Command "choco" -ErrorAction SilentlyContinue

  Write-Warning "Inno Setup ISCC.exe not found."
  Write-Host "Portable build is ready at: $(Join-Path $root 'dist\\LavaSRFastEnhancer\\LavaSRFastEnhancer.exe')"
  Write-Host "Install Inno Setup 6 and rerun to produce installer."
  if ($winget) {
    Write-Host "Install command (admin): winget install --id JRSoftware.InnoSetup -e --accept-package-agreements --accept-source-agreements"
  } elseif ($choco) {
    Write-Host "Install command (admin): choco install innosetup -y --no-progress"
  } else {
    Write-Host "Install manually: https://jrsoftware.org/isinfo.php"
  }
  if ($PortableOnly) {
    Write-Host "PortableOnly set. Skipping installer packaging."
    exit 0
  }
  throw "Inno Setup ISCC.exe not found. Install Inno Setup 6 and retry."
}

$sourceDir = Join-Path $root "dist\\LavaSRFastEnhancer"
$outputDir = Join-Path $root "release\\out"
New-Item -ItemType Directory -Force $outputDir | Out-Null

Write-Host "Building installer..."
& $iscc `
  "/DMyAppVersion=$Version" `
  "/DSourceDir=$sourceDir" `
  "/DOutputDir=$outputDir" `
  ".\\release\\LavaSRFastEnhancer.iss"

$installer = Get-ChildItem $outputDir -Filter "LavaSR-Fast-Enhancer-Setup-$Version*.exe" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $installer) {
  throw "Installer output not found."
}

Write-Host ""
Write-Host "Build completed."
Write-Host "Installer: $($installer.FullName)"
