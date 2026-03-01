# LavaSR Fast Enhancer

<p align="center">
  <img src="assets/toollogo.png" alt="LavaSR Fast Enhancer Logo" width="96" />
</p>

<p align="center">
  One-click Windows app to restore high-frequency detail and export clean <b>48 kHz WAV</b> files using <a href="https://github.com/ysharma3501/LavaSR">LavaSR</a>.
</p>

<p align="center">
  <a href="https://github.com/faxlab/LavaSR-Fast-Enhancer/releases">Releases</a> |
  <a href="#install-users">Install</a> |
  <a href="#how-to-use">How To Use</a> |
  <a href="#build-windows-exe-installer-maintainers">Build</a>
</p>

![LavaSR Fast Enhancer screenshot](assets/appscreenshot.png)

## Why This Tool

- Minimal clicks: drop files, press `Enhance`, drag outputs out
- Batch-ready for multiple files from different folders
- Practical for real production workflows (DAW/video/edit pipelines)
- Optional denoise, suffix control, and output-folder routing

## What `Enhance` Means

`Enhance` in this app means:

- restore missing high-frequency detail (LavaSR bandwidth extension)
- upsample output to `48 kHz WAV`
- optionally apply denoise first

## Typical Use Cases

- Voice/dialogue that sounds narrow, dull, or bandwidth-limited
- Old interview/podcast/call recordings that need more clarity
- Prepping stems/samples before music production
- Fast offline batch processing before final mix/master

## What It Is Not

- Not a mastering suite
- Not a real-time live processor
- Not a guaranteed fix for severe clipping/distortion/reverb

## Install (Users)

### Option A: Installer (Recommended)

1. Download latest `LavaSR-Fast-Enhancer-Setup-*.exe` from [Releases](https://github.com/faxlab/LavaSR-Fast-Enhancer/releases).
2. Run installer.
3. Launch **LavaSR Fast Enhancer** from Start Menu or desktop shortcut.

### Option B: Portable

Download `LavaSRFastEnhancer-Portable-*.zip` from Releases, extract it, then run:

- `LavaSRFastEnhancer.exe`

## How To Use

1. Add files
   - Drag/drop files anywhere in the app, or click `Select Files`
2. Configure options (optional)
   - Denoise
   - Suffix
   - Incrementing suffix
   - Output folder behavior
3. Click `Enhance`
4. Drag completed files directly from the output list into your DAW/folder/app

## Keyboard Shortcuts

- `Ctrl+O`: Select files
- `Delete`: Remove selected queued rows
- `Ctrl+L`: Clear queue
- `Ctrl+Enter`: Enhance / Cancel

## Auto Update

- Auto-check on launch is available and **OFF by default**
- App checks GitHub releases and prompts when a newer version exists

Update source is configured in `app_config.json`:

```json
{
  "github_repo": "faxlab/LavaSR-Fast-Enhancer",
  "release_asset_keyword": "setup"
}
```

## Build Windows `.exe` Installer (Maintainers)

Prereqs:

- Python 3.10+
- Inno Setup 6 (`ISCC.exe`)

Build command:

```powershell
.\release\build_installer.ps1 -RequireInstaller
```

If Inno Setup is missing and you do not require installer packaging:

```powershell
.\release\build_installer.ps1
```

Or run the helper batch file (prompts to install Inno Setup when missing):

```powershell
.\release\build_installer.bat
```

Outputs:

- Installer: `release/out/LavaSR-Fast-Enhancer-Setup-<version>.exe`
- Portable ZIP (when created by CI workflow): `release/out/LavaSRFastEnhancer-Portable-<version>.zip`

## GitHub Release Automation

Workflow: `.github/workflows/release.yml`

On tag push (`v*`) it:

- builds installer
- creates portable ZIP
- uploads both to GitHub release assets

## Versioning

- App version is set in `lavasr_gui.py` as `APP_VERSION`
- Release tag format: `v<same-version>` (example: `v1.0.1`)

## Support

If this tool helps your workflow, support development:

- https://ko-fi.com/faxcorp

## Legal And Licensing

- Project license: [LICENSE](LICENSE) (MIT)
- Third-party notices: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)
- Installer/portable packages include legal files

