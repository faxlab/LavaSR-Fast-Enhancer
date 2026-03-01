[CmdletBinding()]
param(
  [string]$Version = "",
  [switch]$PortableOnly,
  [switch]$RequireInstaller,
  [switch]$InstallInnoSetupIfMissing
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

$iconPng = Join-Path $root "assets\\toollogo.png"
$iconIco = Join-Path $root "assets\\toollogo.ico"
if ((Test-Path $iconPng) -and -not (Test-Path $iconIco)) {
  Write-Host "Generating icon file from assets\\toollogo.png..."
  try {
    & $python -c "from PIL import Image; img = Image.open(r'$iconPng'); img.save(r'$iconIco', format='ICO', sizes=[(256,256),(128,128),(64,64),(48,48),(32,32),(16,16)])"
  } catch {
    Write-Warning "Failed to generate .ico icon: $($_.Exception.Message)"
  }
}

$pyInstallerArgs = @(
  "-m", "PyInstaller",
  "--noconfirm",
  "--clean",
  "--windowed",
  "--name", "LavaSRFastEnhancer",
  "--collect-data", "librosa",
  "--collect-data", "lazy_loader",
  "--copy-metadata", "librosa",
  "--copy-metadata", "lazy_loader",
  "--add-data", "app_config.json;.",
  "--add-data", "assets\\toollogo.png;assets",
  "--add-data", "README.md;.",
  "--add-data", "LICENSE;.",
  "--add-data", "THIRD_PARTY_NOTICES.md;."
)
if (Test-Path $iconIco) {
  $pyInstallerArgs += @("--icon", $iconIco)
}
$pyInstallerArgs += ".\\lavasr_gui.py"
& $python @pyInstallerArgs

function Resolve-IsccPath {
  $localIscc = $null
  $isccCommand = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
  if (-not $isccCommand) { $isccCommand = Get-Command "iscc" -ErrorAction SilentlyContinue }
  if ($isccCommand) { $localIscc = $isccCommand.Source }

  if (-not $localIscc) {
    $isccCandidates = @(
      "$env:ProgramFiles(x86)\\Inno Setup 6\\ISCC.exe",
      "$env:ProgramFiles\\Inno Setup 6\\ISCC.exe",
      "$env:LOCALAPPDATA\\Programs\\Inno Setup 6\\ISCC.exe"
    )
    $localIscc = $isccCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
  }

  if (-not $localIscc) {
    $registryInstall = Get-ItemProperty `
      'HKLM:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*', `
      'HKLM:\\Software\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*', `
      'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*' `
      -ErrorAction SilentlyContinue |
      Where-Object { $_.DisplayName -like 'Inno Setup version*' } |
      Select-Object -First 1
    if ($registryInstall -and $registryInstall.InstallLocation) {
      $registryPath = Join-Path $registryInstall.InstallLocation "ISCC.exe"
      if (Test-Path $registryPath) {
        $localIscc = $registryPath
      }
    }
  }

  return $localIscc
}

$iscc = Resolve-IsccPath
if (-not $iscc) {
  $winget = Get-Command "winget" -ErrorAction SilentlyContinue
  $choco = Get-Command "choco" -ErrorAction SilentlyContinue
  if ($InstallInnoSetupIfMissing) {
    try {
      if ($winget) {
        Write-Host "Attempting to install Inno Setup with winget (may prompt for admin)..."
        Start-Process -FilePath "winget" -ArgumentList @(
          "install",
          "--id", "JRSoftware.InnoSetup",
          "-e",
          "--accept-package-agreements",
          "--accept-source-agreements"
        ) -Verb RunAs -Wait
      } elseif ($choco) {
        Write-Host "Attempting to install Inno Setup with choco (may prompt for admin)..."
        Start-Process -FilePath "choco" -ArgumentList @(
          "install",
          "innosetup",
          "-y",
          "--no-progress"
        ) -Verb RunAs -Wait
      }
    } catch {
      Write-Warning "Automatic Inno Setup installation failed: $($_.Exception.Message)"
    }
    $iscc = Resolve-IsccPath
    if ($iscc) {
      Write-Host "Inno Setup detected after installation."
    }
  }

  if (-not $iscc) {
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
    if ($PortableOnly -or -not $RequireInstaller) {
      if ($PortableOnly) {
        Write-Host "PortableOnly set. Skipping installer packaging."
      } else {
        Write-Host "Installer packaging skipped because Inno Setup is not installed."
        Write-Host "Use -RequireInstaller to fail when installer packaging cannot run."
      }
      exit 0
    }
    throw "Inno Setup ISCC.exe not found. Install Inno Setup 6 and retry."
  }
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
