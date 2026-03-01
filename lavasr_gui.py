from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import soundfile as sf
import torch
from huggingface_hub import snapshot_download

os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
# pythonw on Windows may set std streams to None; tqdm expects writable streams.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

from LavaSR.model import LavaEnhance, LavaEnhance2
from PySide6.QtCore import QMimeData, QSettings, QThread, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QAction, QDesktopServices, QDrag, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QSpinBox,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


AUDIO_EXTENSIONS = {
    ".wav",
    ".mp3",
    ".flac",
    ".ogg",
    ".m4a",
    ".aac",
    ".wma",
    ".aiff",
    ".aif",
}
OUTPUT_PATH_ROLE = Qt.UserRole
SOURCE_PATH_ROLE = Qt.UserRole + 1

APP_NAME = "LavaSR Fast Enhancer"
APP_VERSION = "1.0.1"
APP_ORG = "QATSISoft"
APP_SETTINGS_KEY = "LavaSRFastEnhancer"
DEFAULT_MODEL_PATH = "YatharthS/LavaSR"
KOFI_URL = "https://ko-fi.com/faxcorp"

if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).resolve().parent
else:
    APP_DIR = Path(__file__).resolve().parent

APP_CONFIG_PATH = APP_DIR / "app_config.json"
DEFAULT_APP_CONFIG = {
    "github_repo": "faxlab/LavaSR-Fast-Enhancer",
    "release_asset_keyword": "setup",
}
if os.name == "nt":
    APP_CACHE_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local"))) / APP_SETTINGS_KEY
else:
    APP_CACHE_DIR = Path.home() / ".cache" / APP_SETTINGS_KEY
MODEL_STORE_DIR = APP_CACHE_DIR / "models"
_MODEL_CACHE: dict[tuple[str, str, str], LavaEnhance | LavaEnhance2] = {}
_MODEL_CACHE_LOCK = threading.Lock()


def load_app_config() -> dict[str, str]:
    if APP_CONFIG_PATH.exists():
        try:
            raw = json.loads(APP_CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                merged = DEFAULT_APP_CONFIG.copy()
                for key, value in raw.items():
                    if isinstance(key, str) and isinstance(value, str):
                        merged[key] = value
                return merged
        except Exception:
            pass
    return DEFAULT_APP_CONFIG.copy()


@dataclass(frozen=True)
class EnhanceSettings:
    model_version: str
    model_path: str
    device: str
    input_sr: int
    duration: int
    cutoff: int | None
    enhance: bool
    denoise: bool
    batch: bool
    suffix_enabled: bool
    suffix_text: str
    increment_suffix: bool
    output_to_enhanced_folder: bool
    output_folder_name: str
    use_override_output_folder: bool
    override_output_folder: str


def get_available_devices() -> list[str]:
    devices = ["auto", "cpu"]
    if torch.cuda.is_available():
        devices.append("cuda")
    mps_backend = getattr(torch.backends, "mps", None)
    if mps_backend is not None and mps_backend.is_available():
        devices.append("mps")
    return devices


def resolve_device(device_choice: str) -> str:
    if device_choice != "auto":
        return device_choice
    if torch.cuda.is_available():
        return "cuda"
    mps_backend = getattr(torch.backends, "mps", None)
    if mps_backend is not None and mps_backend.is_available():
        return "mps"
    return "cpu"


def is_audio_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS


def next_output_path(base_path: Path) -> Path:
    if not base_path.exists():
        return base_path

    stem = base_path.stem
    extension = base_path.suffix
    index = 1
    while True:
        candidate = base_path.with_name(f"{stem}_{index:03d}{extension}")
        if not candidate.exists():
            return candidate
        index += 1


def resolve_output_directory(
    source_path: Path,
    output_to_enhanced_folder: bool,
    output_folder_name: str,
    use_override_output_folder: bool,
    override_output_folder: str,
) -> Path:
    base_dir = source_path.parent
    if use_override_output_folder and override_output_folder.strip():
        base_dir = Path(override_output_folder).expanduser()
    base_dir.mkdir(parents=True, exist_ok=True)

    if output_to_enhanced_folder:
        folder_name = sanitize_folder_name(output_folder_name)
        out_dir = base_dir / folder_name
        out_dir.mkdir(parents=True, exist_ok=True)
        return out_dir
    return base_dir


def get_output_path(source_path: Path, settings: EnhanceSettings) -> Path:
    output_dir = resolve_output_directory(
        source_path,
        settings.output_to_enhanced_folder,
        settings.output_folder_name,
        settings.use_override_output_folder,
        settings.override_output_folder,
    )
    base_name = source_path.stem
    if settings.suffix_enabled and settings.suffix_text:
        base_name = f"{base_name}{settings.suffix_text}"

    base_path = output_dir / f"{base_name}.wav"
    if settings.increment_suffix:
        return next_output_path(base_path)
    return base_path


def clip_text(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def sanitize_folder_name(name: str) -> str:
    invalid = '<>:"/\\|?*'
    cleaned = "".join("_" if ch in invalid else ch for ch in name).strip().strip(".")
    return cleaned or "enhanced"


def resolve_resource_path(*parts: str) -> Path:
    candidate_bases = [APP_DIR]
    if getattr(sys, "frozen", False):
        candidate_bases.append(APP_DIR / "_internal")
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidate_bases.insert(0, Path(meipass))
    for base in candidate_bases:
        candidate = base.joinpath(*parts)
        if candidate.exists():
            return candidate
    return candidate_bases[0].joinpath(*parts)


def looks_like_hf_repo_id(model_path: str) -> bool:
    value = model_path.strip()
    if not value or "://" in value:
        return False
    if value.startswith((".", "~")):
        return False
    path_candidate = Path(value).expanduser()
    if path_candidate.exists():
        return False
    parts = [part for part in value.split("/") if part]
    return len(parts) == 2


def resolve_model_root(model_path: str, status_cb, force_download: bool = False) -> str:
    raw_value = model_path.strip() or DEFAULT_MODEL_PATH
    path_candidate = Path(raw_value).expanduser()
    if path_candidate.exists():
        return str(path_candidate.resolve())

    if looks_like_hf_repo_id(raw_value):
        target_dir = MODEL_STORE_DIR / raw_value.replace("/", "__")
        target_dir.mkdir(parents=True, exist_ok=True)
        status_cb(f"Model cache: {target_dir}")
        downloaded = snapshot_download(raw_value, local_dir=target_dir, force_download=force_download)
        # Use POSIX separators to avoid mixed-path edge cases on frozen Windows builds.
        return Path(downloaded).resolve().as_posix()

    return raw_value


def parse_version_tuple(version_text: str) -> tuple[int, ...]:
    cleaned = version_text.strip().lower()
    if cleaned.startswith("v"):
        cleaned = cleaned[1:]
    parts = cleaned.split(".")
    numbers = []
    for part in parts:
        digits = []
        for char in part:
            if char.isdigit():
                digits.append(char)
            else:
                break
        numbers.append(int("".join(digits) or "0"))
    while len(numbers) < 3:
        numbers.append(0)
    return tuple(numbers)


def is_remote_newer(local_version: str, remote_version: str) -> bool:
    return parse_version_tuple(remote_version) > parse_version_tuple(local_version)


class UpdateCheckThread(QThread):
    update_available = Signal(dict)
    up_to_date = Signal(str)
    failed = Signal(str)

    def __init__(self, app_config: dict[str, str], current_version: str) -> None:
        super().__init__()
        self.app_config = app_config
        self.current_version = current_version

    def run(self) -> None:  # noqa: D401
        try:
            repo = self.app_config.get("github_repo", "").strip()
            if not repo or "/" not in repo:
                raise ValueError("Update repo is not configured. Set github_repo in app_config.json.")

            api_url = f"https://api.github.com/repos/{repo}/releases/latest"
            request = urllib.request.Request(
                api_url,
                headers={
                    "Accept": "application/vnd.github+json",
                    "User-Agent": f"{APP_NAME}/{APP_VERSION}",
                },
            )
            with urllib.request.urlopen(request, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8"))

            tag_name = str(payload.get("tag_name", "")).strip()
            if not tag_name:
                raise ValueError("Latest release has no tag_name.")

            if not is_remote_newer(self.current_version, tag_name):
                self.up_to_date.emit(tag_name)
                return

            assets = payload.get("assets", [])
            if not isinstance(assets, list) or not assets:
                raise ValueError("Latest release has no downloadable assets.")

            keyword = self.app_config.get("release_asset_keyword", "setup").lower()
            installer_asset = None
            for asset in assets:
                name = str(asset.get("name", ""))
                if name.lower().endswith(".exe") and keyword in name.lower():
                    installer_asset = asset
                    break
            if installer_asset is None:
                for asset in assets:
                    name = str(asset.get("name", ""))
                    if name.lower().endswith(".exe"):
                        installer_asset = asset
                        break
            if installer_asset is None:
                raise ValueError("No .exe installer asset found in latest release.")

            update_info = {
                "repo": repo,
                "version": tag_name,
                "release_name": str(payload.get("name", tag_name)),
                "release_url": str(payload.get("html_url", "")),
                "asset_name": str(installer_asset.get("name", "")),
                "asset_url": str(installer_asset.get("browser_download_url", "")),
                "notes": str(payload.get("body", "")),
            }
            if not update_info["asset_url"]:
                raise ValueError("Installer asset has no download URL.")

            self.update_available.emit(update_info)
        except Exception as exc:
            self.failed.emit(str(exc))


class UpdateDownloadThread(QThread):
    progress = Signal(int, int)
    finished_download = Signal(str)
    failed = Signal(str)

    def __init__(self, download_url: str, asset_name: str) -> None:
        super().__init__()
        self.download_url = download_url
        self.asset_name = asset_name

    def run(self) -> None:  # noqa: D401
        try:
            temp_dir = Path(tempfile.gettempdir()) / "LavaSRFastEnhancerUpdates"
            temp_dir.mkdir(parents=True, exist_ok=True)
            output_path = temp_dir / self.asset_name
            request = urllib.request.Request(
                self.download_url,
                headers={"User-Agent": f"{APP_NAME}/{APP_VERSION}"},
            )
            with urllib.request.urlopen(request, timeout=60) as response, output_path.open("wb") as out_file:
                total = int(response.headers.get("Content-Length", "0") or "0")
                downloaded = 0
                while True:
                    chunk = response.read(1024 * 256)
                    if not chunk:
                        break
                    out_file.write(chunk)
                    downloaded += len(chunk)
                    self.progress.emit(downloaded, total)
            self.finished_download.emit(str(output_path))
        except Exception as exc:
            self.failed.emit(str(exc))

def get_or_create_model(settings: EnhanceSettings, resolved_device: str, status_cb) -> LavaEnhance | LavaEnhance2:
    resolved_model_path = resolve_model_root(settings.model_path, status_cb, force_download=False)
    cache_key = (settings.model_version, resolved_model_path, resolved_device)
    with _MODEL_CACHE_LOCK:
        cached_model = _MODEL_CACHE.get(cache_key)
    if cached_model is not None:
        return cached_model

    status_cb("Loading LavaSR model (first run may download weights)...")
    try:
        if settings.model_version == "v1":
            model = LavaEnhance(model_path=resolved_model_path, device=resolved_device)
        else:
            model = LavaEnhance2(model_path=resolved_model_path, device=resolved_device)
    except OSError as exc:
        # In packaged Windows builds, occasionally the first downloaded snapshot can be in a bad state.
        if os.name == "nt" and exc.errno == 22 and looks_like_hf_repo_id(settings.model_path):
            status_cb("Model load failed with Windows path error, retrying with forced model refresh...")
            retry_model_path = resolve_model_root(settings.model_path, status_cb, force_download=True)
            if settings.model_version == "v1":
                model = LavaEnhance(model_path=retry_model_path, device=resolved_device)
            else:
                model = LavaEnhance2(model_path=retry_model_path, device=resolved_device)
            resolved_model_path = retry_model_path
        else:
            raise

    final_cache_key = (settings.model_version, resolved_model_path, resolved_device)
    with _MODEL_CACHE_LOCK:
        _MODEL_CACHE[final_cache_key] = model
    return model


class DraggableOutputList(QListWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setSelectionMode(QListWidget.ExtendedSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(False)
        self.setDefaultDropAction(Qt.CopyAction)

    def startDrag(self, supported_actions) -> None:  # noqa: N802
        selected_items = self.selectedItems()
        if not selected_items:
            return
        urls = []
        for item in selected_items:
            output_path = item.data(OUTPUT_PATH_ROLE)
            if output_path:
                urls.append(QUrl.fromLocalFile(output_path))
        if not urls:
            return
        mime = QMimeData()
        mime.setUrls(urls)

        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.CopyAction)


class EnhanceThread(QThread):
    status = Signal(str)
    progress = Signal(int, int)
    file_success = Signal(str, str, float, int, int)
    file_failure = Signal(str, str, int, int)
    batch_finished = Signal(float, int, int, bool)

    def __init__(self, source_files: list[Path], settings: EnhanceSettings) -> None:
        super().__init__()
        self.source_files = source_files
        self.settings = settings
        self._cancel_event = threading.Event()

    def request_cancel(self) -> None:
        self._cancel_event.set()

    def cancel_requested(self) -> bool:
        return self._cancel_event.is_set()

    def run(self) -> None:  # noqa: D401
        total_start = time.perf_counter()
        total = len(self.source_files)
        done = 0
        failed = 0

        try:
            resolved_device = resolve_device(self.settings.device)
            self.status.emit(f"Device: {resolved_device}")
            model = get_or_create_model(self.settings, resolved_device, self.status.emit)
        except Exception:
            self.file_failure.emit("model_setup", traceback.format_exc(), 0, total)
            self.batch_finished.emit(time.perf_counter() - total_start, done, total, False)
            return

        for index, source_file in enumerate(self.source_files, start=1):
            if self.cancel_requested():
                break

            file_start = time.perf_counter()
            try:
                self.status.emit(f"[{index}/{total}] Loading {source_file.name}")
                input_audio, _ = model.load_audio(
                    str(source_file),
                    input_sr=self.settings.input_sr,
                    duration=self.settings.duration,
                    cutoff=self.settings.cutoff,
                )
                if self.cancel_requested():
                    break

                self.status.emit(f"[{index}/{total}] Enhancing {source_file.name}")
                output_audio = model.enhance(
                    input_audio,
                    enhance=self.settings.enhance,
                    denoise=self.settings.denoise,
                    batch=self.settings.batch,
                )
                output_np = output_audio.detach().cpu().numpy().squeeze()
                output_path = get_output_path(source_file, self.settings)
                self.status.emit(f"[{index}/{total}] Saving {output_path.name}")
                sf.write(str(output_path), output_np, 48000)
                file_elapsed = time.perf_counter() - file_start
                done += 1
                self.file_success.emit(str(source_file), str(output_path), file_elapsed, index, total)
            except Exception:
                failed += 1
                self.file_failure.emit(str(source_file), traceback.format_exc(), index, total)

            self.progress.emit(index, total)

        total_elapsed = time.perf_counter() - total_start
        self.batch_finished.emit(total_elapsed, done, failed, self.cancel_requested())


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION}")
        self.setMinimumSize(860, 541)
        app_icon_path = resolve_resource_path("assets", "toollogo.png")
        if app_icon_path.exists():
            self.setWindowIcon(QIcon(str(app_icon_path)))
        self.setAcceptDrops(True)

        self.source_files: list[Path] = []
        self.enhance_thread: EnhanceThread | None = None
        self.settings_store = QSettings(APP_ORG, APP_SETTINGS_KEY)
        self.app_config = load_app_config()
        self.update_check_thread: UpdateCheckThread | None = None
        self.update_download_thread: UpdateDownloadThread | None = None
        self.pending_update_info: dict | None = None
        self.last_update_progress_pct = -1

        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(14, 14, 14, 14)
        root_layout.setSpacing(8)

        input_group = QGroupBox("Input Files")
        input_layout = QHBoxLayout(input_group)
        input_layout.setContentsMargins(10, 10, 10, 10)
        input_layout.setSpacing(8)

        self.select_button = QPushButton("Select Files")
        self.select_button.clicked.connect(self.open_files_dialog)
        self.select_button.setToolTip("Pick one or more source audio files to queue for enhancement.")

        self.clear_button = QPushButton("Clear")
        self.clear_button.clicked.connect(self.clear_files)
        self.clear_button.setToolTip("Clear the current queue.")

        self.remove_selected_button = QPushButton("Remove Selected")
        self.remove_selected_button.clicked.connect(self.remove_selected_files)
        self.remove_selected_button.setToolTip("Remove selected queued rows from the batch list.")

        self.open_output_folder_button = QPushButton("Open Output Folder")
        self.open_output_folder_button.clicked.connect(self.open_output_folder)
        self.open_output_folder_button.setToolTip(
            "Open the active output location (based on current output folder and override settings)."
        )

        self.drop_hint = QLabel("Drop audio files anywhere in this app, or click Select Files")
        self.drop_hint.setToolTip("Drag and drop from one or many folders. Existing queue entries are preserved.")

        input_layout.addWidget(self.select_button)
        input_layout.addWidget(self.clear_button)
        input_layout.addWidget(self.remove_selected_button)
        input_layout.addWidget(self.open_output_folder_button)
        input_layout.addWidget(self.drop_hint, 1)

        root_layout.addWidget(input_group)
        self.file_progress = QProgressBar()
        self.file_progress.setMinimum(0)
        self.file_progress.setMaximum(1)
        self.file_progress.setValue(0)
        self.file_progress.setTextVisible(True)
        self.file_progress.setFormat("No files queued")
        self.file_progress.setToolTip("Shows current file position in batch and all queued source paths.")
        root_layout.addWidget(self.file_progress)

        self.advanced_group = QGroupBox("Controls")

        self.model_version_combo = QComboBox()
        self.model_version_combo.addItem("LavaSR v2 (recommended)", "v2")
        self.model_version_combo.addItem("LavaSR v1", "v1")
        self.model_version_combo.setToolTip("Select the LavaSR model implementation.")

        self.model_path_edit = QLineEdit(DEFAULT_MODEL_PATH)
        self.model_path_edit.setToolTip(
            "Hugging Face model repo or local model path. First run may download model weights."
        )

        self.device_combo = QComboBox()
        for device in get_available_devices():
            self.device_combo.addItem(device, device)
        self.device_combo.setToolTip("Inference device. Auto prefers CUDA, then MPS, then CPU.")

        self.input_sr_spin = QSpinBox()
        self.input_sr_spin.setRange(8000, 48000)
        self.input_sr_spin.setSingleStep(1000)
        self.input_sr_spin.setValue(16000)
        self.input_sr_spin.setToolTip("Input sample rate used while loading the source audio.")

        self.duration_spin = QSpinBox()
        self.duration_spin.setRange(1, 100000)
        self.duration_spin.setValue(10000)
        self.duration_spin.setToolTip("Maximum audio duration (seconds) loaded from each source file.")

        self.cutoff_auto_checkbox = QCheckBox("Auto cutoff")
        self.cutoff_auto_checkbox.setChecked(True)
        self.cutoff_auto_checkbox.toggled.connect(self.on_cutoff_auto_changed)
        self.cutoff_auto_checkbox.setToolTip("Enable automatic cutoff frequency selection.")
        self.cutoff_spin = QSpinBox()
        self.cutoff_spin.setRange(100, 24000)
        self.cutoff_spin.setValue(4000)
        self.cutoff_spin.setEnabled(False)
        self.cutoff_spin.setToolTip("Manual cutoff frequency in Hz when auto cutoff is disabled.")

        cutoff_row = QHBoxLayout()
        cutoff_row.setContentsMargins(0, 0, 0, 0)
        cutoff_row.setSpacing(8)
        cutoff_row.addWidget(self.cutoff_auto_checkbox)
        cutoff_row.addWidget(self.cutoff_spin)
        cutoff_container = QWidget()
        cutoff_container.setLayout(cutoff_row)

        self.enable_enhance_checkbox = QCheckBox("Run enhancer")
        self.enable_enhance_checkbox.setChecked(True)
        self.enable_enhance_checkbox.setToolTip("Apply LavaSR enhancement stage.")

        self.denoise_checkbox = QCheckBox("Run denoiser")
        self.denoise_checkbox.setChecked(False)
        self.denoise_checkbox.setToolTip("Apply denoising stage. May increase processing time.")

        self.batch_checkbox = QCheckBox("Batch long audio")
        self.batch_checkbox.setChecked(False)
        self.batch_checkbox.setToolTip("Process long files in chunks to reduce memory pressure.")

        self.suffix_enabled_checkbox = QCheckBox("Add suffix")
        self.suffix_enabled_checkbox.setChecked(True)
        self.suffix_enabled_checkbox.toggled.connect(self.on_suffix_enabled_changed)
        self.suffix_enabled_checkbox.setToolTip("Append a suffix to output filenames.")
        self.suffix_edit = QLineEdit("_enhanced")
        self.suffix_edit.setMaximumWidth(130)
        self.suffix_edit.setToolTip("Suffix text appended to each output filename.")

        self.increment_suffix_checkbox = QCheckBox("Increment suffix")
        self.increment_suffix_checkbox.setChecked(True)
        self.increment_suffix_checkbox.setToolTip(
            "If the target file already exists, append _001, _002, ... instead of replacing it."
        )

        self.output_folder_checkbox = QCheckBox("Output to subfolder")
        self.output_folder_checkbox.setChecked(True)
        self.output_folder_checkbox.toggled.connect(self.on_output_folder_toggled)
        self.output_folder_checkbox.setToolTip(
            "Write outputs into a named subfolder under the selected base output location."
        )
        self.output_folder_name_edit = QLineEdit("enhanced")
        self.output_folder_name_edit.setMaximumWidth(130)
        self.output_folder_name_edit.setToolTip(
            "Subfolder name used when 'Output to subfolder' is enabled."
        )

        self.override_output_folder_checkbox = QCheckBox("Use override base folder")
        self.override_output_folder_checkbox.setChecked(False)
        self.override_output_folder_checkbox.toggled.connect(self.on_override_output_folder_toggled)
        self.override_output_folder_checkbox.setToolTip(
            "When enabled, all outputs use this folder as base instead of each source file folder."
        )
        self.override_output_folder_edit = QLineEdit("")
        self.override_output_folder_edit.setPlaceholderText("Select output base folder")
        self.override_output_folder_edit.setToolTip(
            "Custom base output folder. Used only when override is enabled."
        )
        self.override_output_folder_button = QPushButton("Browse...")
        self.override_output_folder_button.clicked.connect(self.browse_override_output_folder)
        self.override_output_folder_button.setToolTip("Choose the override base output folder.")

        self.auto_update_checkbox = QCheckBox("Auto-check updates on launch")
        self.auto_update_checkbox.setChecked(False)
        self.auto_update_checkbox.setToolTip("Check GitHub releases at startup and prompt if a newer version exists.")
        self.check_updates_button = QPushButton("Check Updates Now")
        self.check_updates_button.clicked.connect(self.check_for_updates_manual)
        self.check_updates_button.setToolTip("Check GitHub now for a new release.")

        controls_layout = QGridLayout(self.advanced_group)
        controls_layout.setContentsMargins(10, 10, 10, 10)
        controls_layout.setHorizontalSpacing(10)
        controls_layout.setVerticalSpacing(8)
        controls_layout.addWidget(QLabel("Model version"), 0, 0)
        controls_layout.addWidget(self.model_version_combo, 0, 1)
        controls_layout.addWidget(QLabel("Model path / HF repo"), 0, 2)
        controls_layout.addWidget(self.model_path_edit, 0, 3)
        controls_layout.addWidget(QLabel("Device"), 1, 0)
        controls_layout.addWidget(self.device_combo, 1, 1)
        controls_layout.addWidget(QLabel("Input SR (Hz)"), 1, 2)
        controls_layout.addWidget(self.input_sr_spin, 1, 3)
        controls_layout.addWidget(QLabel("Load duration (sec)"), 2, 0)
        controls_layout.addWidget(self.duration_spin, 2, 1)
        controls_layout.addWidget(QLabel("Cutoff"), 2, 2)
        controls_layout.addWidget(cutoff_container, 2, 3)

        process_flag_container = QWidget()
        process_flag_layout = QHBoxLayout(process_flag_container)
        process_flag_layout.setContentsMargins(0, 0, 0, 0)
        process_flag_layout.setSpacing(14)
        process_flag_layout.addWidget(self.enable_enhance_checkbox)
        process_flag_layout.addWidget(self.denoise_checkbox)
        process_flag_layout.addWidget(self.batch_checkbox)
        process_flag_layout.addStretch(1)
        controls_layout.addWidget(process_flag_container, 3, 0, 1, 4)

        naming_flag_container = QWidget()
        naming_flag_layout = QHBoxLayout(naming_flag_container)
        naming_flag_layout.setContentsMargins(0, 0, 0, 0)
        naming_flag_layout.setSpacing(14)
        naming_flag_layout.addWidget(self.suffix_enabled_checkbox)
        naming_flag_layout.addWidget(self.suffix_edit)
        naming_flag_layout.addWidget(self.increment_suffix_checkbox)
        naming_flag_layout.addStretch(1)
        controls_layout.addWidget(naming_flag_container, 4, 0, 1, 4)

        output_flag_container = QWidget()
        output_flag_layout = QHBoxLayout(output_flag_container)
        output_flag_layout.setContentsMargins(0, 0, 0, 0)
        output_flag_layout.setSpacing(10)
        output_flag_layout.addWidget(self.output_folder_checkbox)
        output_flag_layout.addWidget(QLabel("Folder name"))
        output_flag_layout.addWidget(self.output_folder_name_edit)
        output_flag_layout.addWidget(self.override_output_folder_checkbox)
        output_flag_layout.addWidget(self.override_output_folder_edit, 1)
        output_flag_layout.addWidget(self.override_output_folder_button)
        output_flag_layout.addStretch(1)
        controls_layout.addWidget(output_flag_container, 5, 0, 1, 4)

        update_row = QWidget()
        update_layout = QHBoxLayout(update_row)
        update_layout.setContentsMargins(0, 0, 0, 0)
        update_layout.setSpacing(14)
        update_layout.addWidget(self.auto_update_checkbox)
        update_layout.addWidget(self.check_updates_button)
        update_layout.addStretch(1)
        controls_layout.addWidget(update_row, 6, 0, 1, 4)
        controls_layout.setColumnStretch(1, 1)
        controls_layout.setColumnStretch(3, 1)
        self.advanced_group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        root_layout.addWidget(self.advanced_group)

        self.content_split = QSplitter(Qt.Horizontal)

        output_group = QGroupBox("Files (queue + output, drag completed files)")
        output_layout = QVBoxLayout(output_group)
        output_layout.setContentsMargins(10, 10, 10, 10)
        output_layout.setSpacing(6)
        self.output_list = DraggableOutputList()
        self.output_list.setAlternatingRowColors(True)
        self.output_list.itemDoubleClicked.connect(self.on_output_item_double_clicked)
        self.output_list.itemSelectionChanged.connect(self.update_action_states)
        self.output_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.output_list.customContextMenuRequested.connect(self.on_output_context_menu)
        self.output_list.setToolTip(
            "Queue and output list. Drag completed output files from here into other apps or folders."
        )
        output_layout.addWidget(self.output_list)
        self.content_split.addWidget(output_group)

        console_group = QGroupBox("Activity Console")
        console_layout = QVBoxLayout(console_group)
        console_layout.setContentsMargins(10, 10, 10, 10)
        console_layout.setSpacing(6)
        self.console_log = QPlainTextEdit()
        self.console_log.setReadOnly(True)
        self.console_log.setObjectName("consoleLog")
        self.console_log.setPlainText("Ready")
        self.console_log.document().setMaximumBlockCount(500)
        self.console_log.setToolTip("Runtime activity log.")
        console_layout.addWidget(self.console_log)
        self.content_split.addWidget(console_group)
        self.content_split.setStretchFactor(0, 1)
        self.content_split.setStretchFactor(1, 2)
        root_layout.addWidget(self.content_split, 1)

        self.enhance_button = QPushButton("Enhance")
        self.enhance_button.setObjectName("enhancePrimary")
        self.enhance_button.setEnabled(False)
        self.enhance_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.enhance_button.setMinimumHeight(54)
        self.enhance_button.clicked.connect(self.on_enhance_button_clicked)
        self.enhance_button.setToolTip("Start enhancement. While running, click again to cancel.")
        root_layout.addWidget(self.enhance_button)

        QShortcut(QKeySequence("Ctrl+O"), self, self.open_files_dialog)
        QShortcut(QKeySequence("Delete"), self, self.remove_selected_files)
        QShortcut(QKeySequence("Ctrl+L"), self, self.clear_files)
        QShortcut(QKeySequence("Ctrl+Return"), self, self.on_enhance_button_clicked)
        self.setup_help_menu()
        self.setup_status_footer()

        self.setStyleSheet(
            """
            QWidget { font-size: 13px; }
            QPushButton {
                min-height: 32px;
                padding: 4px 12px;
            }
            QPushButton:disabled { color: #888888; }
            #enhancePrimary {
                min-height: 54px;
                font-size: 16px;
                font-weight: 700;
                color: #ffffff;
                background: #2f7d56;
                border: 1px solid #286b4a;
                border-radius: 8px;
            }
            #enhancePrimary[running="true"] {
                background: #ab3f3f;
                border: 1px solid #8f3131;
            }
            QProgressBar {
                border: 1px solid #c5c9cd;
                border-radius: 6px;
                background: #f5f6f7;
                min-height: 22px;
                text-align: center;
                padding: 0 6px;
            }
            QProgressBar::chunk {
                background: #59a978;
                border-radius: 5px;
            }
            #consoleLog {
                font-family: Consolas, "Courier New", monospace;
                font-size: 12px;
            }
            """
        )

        self.load_ui_settings()
        self.on_override_output_folder_toggled(self.override_output_folder_checkbox.isChecked())
        self.set_enhance_running_state(False)
        self.refresh_file_summary()
        if self.auto_update_checkbox.isChecked():
            QTimer.singleShot(1200, lambda: self.check_for_updates(is_manual=False))

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                local_path = url.toLocalFile()
                if local_path and is_audio_file(Path(local_path)):
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event) -> None:  # noqa: N802
        dropped_files = []
        for url in event.mimeData().urls():
            local_path = url.toLocalFile()
            if local_path:
                dropped_files.append(local_path)
        self.add_source_files(dropped_files, replace=False)
        event.acceptProposedAction()

    def append_log(self, message: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        self.console_log.appendPlainText(f"[{timestamp}] {message}")
        scrollbar = self.console_log.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def update_progress_bar_text(self, index: int | None = None, total: int | None = None) -> None:
        names = [path.name for path in self.source_files]
        if not names:
            self.file_progress.setFormat("No files queued")
            self.file_progress.setToolTip("")
            return

        joined = ", ".join(names)
        clipped = clip_text(joined, 95)
        if index is not None and total is not None:
            self.file_progress.setFormat(f"{index}/{total}  {clipped}")
        else:
            self.file_progress.setFormat(clipped)
        self.file_progress.setToolTip("\n".join(str(path) for path in self.source_files))

    def set_enhance_running_state(self, running: bool) -> None:
        self.enhance_button.setProperty("running", running)
        self.enhance_button.style().unpolish(self.enhance_button)
        self.enhance_button.style().polish(self.enhance_button)
        self.enhance_button.update()

    def update_action_states(self) -> None:
        has_selection = len(self.output_list.selectedItems()) > 0
        can_edit_queue = self.enhance_thread is None
        self.remove_selected_button.setEnabled(can_edit_queue and has_selection)

    def resolve_current_output_folder(self) -> Path:
        if self.source_files:
            return resolve_output_directory(
                self.source_files[0],
                self.output_folder_checkbox.isChecked(),
                self.output_folder_name_edit.text(),
                self.override_output_folder_checkbox.isChecked(),
                self.override_output_folder_edit.text(),
            )

        if self.override_output_folder_checkbox.isChecked():
            override_path = self.override_output_folder_edit.text().strip()
            if override_path:
                base_dir = Path(override_path).expanduser()
            else:
                base_dir = APP_DIR
        else:
            base_dir = APP_DIR
        if self.output_folder_checkbox.isChecked():
            base_dir = base_dir / sanitize_folder_name(self.output_folder_name_edit.text())
        base_dir.mkdir(parents=True, exist_ok=True)
        return base_dir

    def open_output_folder(self) -> None:
        out_dir = self.resolve_current_output_folder()
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(out_dir)))

    def show_in_explorer(self, target_path: Path) -> None:
        target = target_path.expanduser().resolve()
        if sys.platform.startswith("win"):
            if target.is_file():
                subprocess.Popen(["explorer", "/select,", str(target)], shell=False)
            else:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))
            return

        if target.is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(target.parent)))
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    def on_output_context_menu(self, pos) -> None:
        item = self.output_list.itemAt(pos)
        if item is None:
            return

        output_path = item.data(OUTPUT_PATH_ROLE)
        source_path = item.data(SOURCE_PATH_ROLE)
        output_candidate = Path(output_path) if output_path else None
        source_candidate = Path(source_path) if source_path else None

        menu = QMenu(self)
        show_output_action = menu.addAction("Show Output in Explorer")
        show_source_action = menu.addAction("Show Source in Explorer")
        show_output_action.setEnabled(output_candidate is not None and output_candidate.exists())
        show_source_action.setEnabled(source_candidate is not None and source_candidate.exists())

        selected_action = menu.exec(self.output_list.viewport().mapToGlobal(pos))
        if selected_action is show_output_action and output_candidate is not None and output_candidate.exists():
            self.show_in_explorer(output_candidate)
        elif selected_action is show_source_action and source_candidate is not None and source_candidate.exists():
            self.show_in_explorer(source_candidate)

    def remove_selected_files(self) -> None:
        if self.enhance_thread is not None:
            self.append_log("Cannot remove files while enhancement is running.")
            return
        selected_items = self.output_list.selectedItems()
        if not selected_items:
            return

        selected_sources = {
            item.data(SOURCE_PATH_ROLE) for item in selected_items if item.data(SOURCE_PATH_ROLE) is not None
        }
        before = len(self.source_files)
        self.source_files = [path for path in self.source_files if str(path) not in selected_sources]
        removed = before - len(self.source_files)
        self.refresh_file_summary()
        self.update_action_states()
        if removed > 0:
            self.append_log(f"Removed {removed} queued file(s).")

    def on_output_item_double_clicked(self, item: QListWidgetItem) -> None:
        output_path = item.data(OUTPUT_PATH_ROLE)
        source_path = item.data(SOURCE_PATH_ROLE)
        if output_path:
            self.show_in_explorer(Path(output_path))
            return
        if source_path:
            self.show_in_explorer(Path(source_path))

    def on_cutoff_auto_changed(self, is_auto: bool) -> None:
        self.cutoff_spin.setEnabled(not is_auto)

    def on_suffix_enabled_changed(self, enabled: bool) -> None:
        self.suffix_edit.setEnabled(enabled)

    def on_output_folder_toggled(self, enabled: bool) -> None:
        self.output_folder_name_edit.setEnabled(enabled)

    def on_override_output_folder_toggled(self, enabled: bool) -> None:
        self.override_output_folder_edit.setEnabled(enabled)
        self.override_output_folder_button.setEnabled(enabled)

    def setup_help_menu(self) -> None:
        help_menu = self.menuBar().addMenu("Help")

        readme_action = QAction("Open README", self)
        readme_action.triggered.connect(lambda: self.open_help_document("README.md"))
        help_menu.addAction(readme_action)

        license_action = QAction("Open License", self)
        license_action.triggered.connect(lambda: self.open_help_document("LICENSE"))
        help_menu.addAction(license_action)

        notices_action = QAction("Open Third-Party Notices", self)
        notices_action.triggered.connect(lambda: self.open_help_document("THIRD_PARTY_NOTICES.md"))
        help_menu.addAction(notices_action)

        help_menu.addSeparator()
        support_action = QAction("Support on Ko-fi", self)
        support_action.triggered.connect(lambda: QDesktopServices.openUrl(QUrl(KOFI_URL)))
        help_menu.addAction(support_action)

    def setup_status_footer(self) -> None:
        support_link = QLabel(
            f'<a href="{KOFI_URL}" style="color:#6f757b;text-decoration:none;">Support on Ko-fi</a>'
        )
        support_link.setOpenExternalLinks(True)
        support_link.setToolTip("Optional support link")
        self.statusBar().addPermanentWidget(support_link)

    def open_help_document(self, filename: str) -> None:
        doc_path = resolve_resource_path(filename)
        if not doc_path.exists():
            self.append_log(f"Help file not found: {filename}")
            QMessageBox.warning(self, "File Not Found", f"Could not find bundled file:\n{filename}")
            return
        opened = QDesktopServices.openUrl(QUrl.fromLocalFile(str(doc_path)))
        if not opened:
            self.append_log(f"Could not open help file: {doc_path}")
            QMessageBox.warning(self, "Open Failed", f"Could not open file:\n{doc_path}")

    def browse_override_output_folder(self) -> None:
        start_dir = self.override_output_folder_edit.text().strip()
        if not start_dir:
            if self.source_files:
                start_dir = str(self.source_files[0].parent)
            else:
                start_dir = str(Path.home())

        selected_dir = QFileDialog.getExistingDirectory(
            self,
            "Select output base folder",
            start_dir,
        )
        if selected_dir:
            self.override_output_folder_edit.setText(selected_dir)
            self.override_output_folder_checkbox.setChecked(True)

    def check_for_updates_manual(self) -> None:
        self.check_for_updates(is_manual=True)

    def check_for_updates(self, is_manual: bool) -> None:
        if self.update_check_thread is not None:
            if is_manual:
                self.append_log("Update check already running.")
            return

        self.append_log("Checking for updates...")
        self.check_updates_button.setEnabled(False)
        self.update_check_thread = UpdateCheckThread(self.app_config, APP_VERSION)
        self.update_check_thread.update_available.connect(
            lambda info: self.on_update_available(info, is_manual=is_manual)
        )
        self.update_check_thread.up_to_date.connect(lambda remote: self.on_update_up_to_date(remote, is_manual))
        self.update_check_thread.failed.connect(lambda error: self.on_update_check_failed(error, is_manual))
        self.update_check_thread.finished.connect(self.on_update_check_finished)
        self.update_check_thread.start()

    def on_update_available(self, update_info: dict, is_manual: bool) -> None:
        self.pending_update_info = update_info
        version = update_info.get("version", "?")
        asset_name = update_info.get("asset_name", "installer.exe")
        self.append_log(f"Update available: {version} ({asset_name})")

        prompt_text = (
            f"A new version is available: {version}\n\n"
            f"Current version: {APP_VERSION}\n"
            f"Asset: {asset_name}\n\n"
            "Download and install now?"
        )
        answer = QMessageBox.question(
            self,
            "Update Available",
            prompt_text,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes if is_manual else QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            self.download_and_install_update(update_info)

    def on_update_up_to_date(self, remote_version: str, is_manual: bool) -> None:
        if is_manual:
            QMessageBox.information(self, "No Update", f"You are up to date.\nCurrent: {APP_VERSION}\nLatest: {remote_version}")
        self.append_log(f"Up to date. Current: {APP_VERSION}, Latest: {remote_version}")

    def on_update_check_failed(self, error: str, is_manual: bool) -> None:
        self.append_log(f"Update check failed: {error}")
        if is_manual:
            QMessageBox.warning(self, "Update Check Failed", error)

    def on_update_check_finished(self) -> None:
        thread = self.update_check_thread
        self.update_check_thread = None
        if thread is not None:
            thread.deleteLater()
        self.check_updates_button.setEnabled(True)

    def download_and_install_update(self, update_info: dict) -> None:
        if self.update_download_thread is not None:
            return
        asset_url = str(update_info.get("asset_url", "")).strip()
        asset_name = str(update_info.get("asset_name", "LavaSRFastEnhancerSetup.exe")).strip()
        if not asset_url:
            QMessageBox.warning(self, "Update Failed", "No installer download URL found.")
            return

        self.append_log(f"Downloading update: {asset_name}")
        self.check_updates_button.setEnabled(False)
        self.last_update_progress_pct = -1
        self.update_download_thread = UpdateDownloadThread(asset_url, asset_name)
        self.update_download_thread.progress.connect(self.on_update_download_progress)
        self.update_download_thread.finished_download.connect(self.on_update_download_finished)
        self.update_download_thread.failed.connect(self.on_update_download_failed)
        self.update_download_thread.finished.connect(self.on_update_download_thread_finished)
        self.update_download_thread.start()

    def on_update_download_progress(self, downloaded: int, total: int) -> None:
        if total > 0:
            pct = int((downloaded / total) * 100)
            if pct != self.last_update_progress_pct and (pct % 5 == 0 or pct == 100):
                self.last_update_progress_pct = pct
                self.append_log(
                    f"Update download: {pct}% ({downloaded // (1024 * 1024)}MB/{max(total // (1024 * 1024), 1)}MB)"
                )
        else:
            if downloaded // (1024 * 1024) > self.last_update_progress_pct:
                self.last_update_progress_pct = downloaded // (1024 * 1024)
                self.append_log(f"Update download: {downloaded // (1024 * 1024)}MB")

    def on_update_download_finished(self, installer_path: str) -> None:
        self.append_log(f"Update downloaded: {installer_path}")
        try:
            subprocess.Popen([installer_path], shell=False)
            self.append_log("Installer launched. Closing app for update.")
            QMessageBox.information(
                self,
                "Update Started",
                "Installer launched. Complete the installation, then reopen the app.",
            )
            self.close()
        except Exception as exc:
            self.append_log(f"Failed to launch installer: {exc}")
            QMessageBox.warning(self, "Update Launch Failed", str(exc))

    def on_update_download_failed(self, error: str) -> None:
        self.append_log(f"Update download failed: {error}")
        QMessageBox.warning(self, "Update Download Failed", error)

    def on_update_download_thread_finished(self) -> None:
        thread = self.update_download_thread
        self.update_download_thread = None
        self.last_update_progress_pct = -1
        if thread is not None:
            thread.deleteLater()
        self.check_updates_button.setEnabled(True)

    def open_files_dialog(self) -> None:
        selected_files, _ = QFileDialog.getOpenFileNames(
            self,
            "Select audio files",
            "",
            "Audio Files (*.wav *.mp3 *.flac *.ogg *.m4a *.aac *.wma *.aiff *.aif);;All Files (*)",
        )
        if selected_files:
            self.add_source_files(selected_files, replace=False)

    def clear_files(self) -> None:
        if self.enhance_thread is not None:
            self.append_log("Cannot clear files while enhancement is running.")
            return
        self.source_files.clear()
        self.refresh_file_summary()
        self.append_log("Cleared selected files.")

    def add_source_files(self, file_paths: list[str], replace: bool) -> None:
        if self.enhance_thread is not None:
            self.append_log("Batch is running. Cancel first to change queued files.")
            return

        valid_paths = []
        for file_path in file_paths:
            candidate = Path(file_path)
            if is_audio_file(candidate):
                valid_paths.append(candidate.resolve())

        if not valid_paths:
            self.append_log("No supported audio files found in selection.")
            return

        if replace:
            merged = list(dict.fromkeys(valid_paths))
        else:
            merged = list(dict.fromkeys(self.source_files + valid_paths))

        self.source_files = merged
        self.refresh_file_summary()
        self.append_log(f"Queued {len(valid_paths)} file(s). Total in batch: {len(self.source_files)}")

    def find_list_item_by_source(self, source_path: Path) -> QListWidgetItem | None:
        source_key = str(source_path)
        for row in range(self.output_list.count()):
            item = self.output_list.item(row)
            if item is not None and item.data(SOURCE_PATH_ROLE) == source_key:
                return item
        return None

    def refresh_file_summary(self) -> None:
        count = len(self.source_files)
        self.output_list.clear()
        for source_path in self.source_files:
            item = QListWidgetItem(f"Queued: {source_path.name}")
            item.setData(SOURCE_PATH_ROLE, str(source_path))
            item.setData(OUTPUT_PATH_ROLE, None)
            item.setToolTip(str(source_path))
            self.output_list.addItem(item)

        if count == 0:
            self.drop_hint.setText("Drop audio files anywhere in this app, or click Select Files")
            self.file_progress.setMaximum(1)
            self.file_progress.setValue(0)
        else:
            self.drop_hint.setText(f"{count} queued file(s). Drop or select more files to append.")
            if self.enhance_thread is None:
                self.file_progress.setMaximum(count)
                self.file_progress.setValue(0)

        self.update_progress_bar_text()
        self.update_action_states()

        if self.enhance_thread is None:
            self.enhance_button.setEnabled(count > 0)
            if count > 0:
                self.enhance_button.setText("Enhance")

    def collect_settings(self) -> EnhanceSettings:
        model_version = self.model_version_combo.currentData()
        model_path = self.model_path_edit.text().strip() or DEFAULT_MODEL_PATH
        device = self.device_combo.currentData()
        cutoff = None if self.cutoff_auto_checkbox.isChecked() else self.cutoff_spin.value()
        suffix_text = self.suffix_edit.text().strip()
        if not suffix_text:
            suffix_text = "_enhanced"
        override_output_folder = self.override_output_folder_edit.text().strip()
        return EnhanceSettings(
            model_version=model_version,
            model_path=model_path,
            device=device,
            input_sr=self.input_sr_spin.value(),
            duration=self.duration_spin.value(),
            cutoff=cutoff,
            enhance=self.enable_enhance_checkbox.isChecked(),
            denoise=self.denoise_checkbox.isChecked(),
            batch=self.batch_checkbox.isChecked(),
            suffix_enabled=self.suffix_enabled_checkbox.isChecked(),
            suffix_text=suffix_text,
            increment_suffix=self.increment_suffix_checkbox.isChecked(),
            output_to_enhanced_folder=self.output_folder_checkbox.isChecked(),
            output_folder_name=sanitize_folder_name(self.output_folder_name_edit.text()),
            use_override_output_folder=self.override_output_folder_checkbox.isChecked(),
            override_output_folder=override_output_folder,
        )

    def on_enhance_button_clicked(self) -> None:
        if self.enhance_thread is not None:
            self.enhance_button.setText("Cancelling...")
            self.enhance_thread.request_cancel()
            self.append_log("Cancel requested. Current file will stop after current stage.")
            return
        self.start_batch_enhance()

    def start_batch_enhance(self) -> None:
        if not self.source_files:
            self.append_log("No input files selected.")
            return

        settings = self.collect_settings()
        self.append_log(f"Starting batch: {len(self.source_files)} file(s)")

        self.enhance_thread = EnhanceThread(self.source_files.copy(), settings)
        self.enhance_thread.status.connect(self.append_log)
        self.enhance_thread.progress.connect(self.on_batch_progress)
        self.enhance_thread.file_success.connect(self.on_file_success)
        self.enhance_thread.file_failure.connect(self.on_file_failure)
        self.enhance_thread.batch_finished.connect(self.on_batch_finished)
        self.enhance_thread.finished.connect(self.on_thread_finished)

        self.set_controls_enabled(False)
        self.enhance_button.setEnabled(True)
        self.set_enhance_running_state(True)
        self.enhance_button.setText(f"Cancel (0/{len(self.source_files)})")
        self.file_progress.setMaximum(len(self.source_files))
        self.file_progress.setValue(0)
        self.update_progress_bar_text(0, len(self.source_files))
        self.update_action_states()
        self.enhance_thread.start()

    def on_batch_progress(self, index: int, total: int) -> None:
        if self.enhance_thread is not None:
            self.enhance_button.setText(f"Cancel ({index}/{total})")
            self.file_progress.setMaximum(total)
            self.file_progress.setValue(index)
            self.update_progress_bar_text(index, total)

    def on_file_success(self, source_path_text: str, output_path: str, elapsed_sec: float, index: int, total: int) -> None:
        source_path = Path(source_path_text)
        output_item = self.find_list_item_by_source(source_path)
        if output_item is None:
            output_item = QListWidgetItem()
            output_item.setData(SOURCE_PATH_ROLE, str(source_path))
            self.output_list.addItem(output_item)

        output_item.setText(f"Done: {Path(output_path).name}")
        output_item.setData(OUTPUT_PATH_ROLE, output_path)
        output_item.setToolTip(f"{output_path}\nSource: {source_path}")
        self.output_list.setCurrentItem(output_item)
        self.append_log(f"[{index}/{total}] Done {source_path.name} in {elapsed_sec:.2f}s -> {output_path}")

    def on_file_failure(self, source_path_text: str, error_text: str, index: int, total: int) -> None:
        source_path = Path(source_path_text)
        item = self.find_list_item_by_source(source_path)
        if item is not None:
            item.setText(f"Failed: {source_path.name}")
            item.setData(OUTPUT_PATH_ROLE, None)
            item.setToolTip(str(source_path))
        self.append_log(f"[{index}/{total}] Failed: {source_path.name}")
        self.append_log(error_text)

    def on_batch_finished(self, elapsed_sec: float, done: int, failed: int, cancelled: bool) -> None:
        total = len(self.source_files)
        completed = min(done + failed, total)
        self.file_progress.setMaximum(max(total, 1))
        self.file_progress.setValue(completed)
        self.update_progress_bar_text(completed, max(total, 1))
        if cancelled:
            self.enhance_button.setText("Cancelled")
            self.append_log(f"Batch cancelled. Completed: {completed}/{total}")
        else:
            self.enhance_button.setText(f"Done in {elapsed_sec:.1f}s")
            self.append_log(f"Batch finished in {elapsed_sec:.1f}s. Success: {done}, Failed: {failed}")

    def on_thread_finished(self) -> None:
        thread = self.enhance_thread
        self.enhance_thread = None
        if thread is not None:
            thread.deleteLater()
        self.set_controls_enabled(True)
        self.set_enhance_running_state(False)
        self.enhance_button.setEnabled(len(self.source_files) > 0)
        self.update_action_states()

    def set_controls_enabled(self, enabled: bool) -> None:
        self.select_button.setEnabled(enabled)
        self.clear_button.setEnabled(enabled)
        self.open_output_folder_button.setEnabled(True)
        self.advanced_group.setEnabled(enabled)
        self.drop_hint.setEnabled(enabled)
        if enabled:
            self.on_output_folder_toggled(self.output_folder_checkbox.isChecked())
            self.on_override_output_folder_toggled(self.override_output_folder_checkbox.isChecked())
        self.update_action_states()

    def load_ui_settings(self) -> None:
        geometry = self.settings_store.value("window/geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        split_state = self.settings_store.value("window/content_split_state")
        if split_state is not None:
            self.content_split.restoreState(split_state)

        model_version = self.settings_store.value("controls/model_version", "v2")
        model_index = self.model_version_combo.findData(model_version)
        if model_index >= 0:
            self.model_version_combo.setCurrentIndex(model_index)

        self.model_path_edit.setText(self.settings_store.value("controls/model_path", DEFAULT_MODEL_PATH))

        device = self.settings_store.value("controls/device", "auto")
        device_index = self.device_combo.findData(device)
        if device_index >= 0:
            self.device_combo.setCurrentIndex(device_index)

        self.input_sr_spin.setValue(self.settings_store.value("controls/input_sr", 16000, type=int))
        self.duration_spin.setValue(self.settings_store.value("controls/duration", 10000, type=int))
        self.cutoff_auto_checkbox.setChecked(self.settings_store.value("controls/cutoff_auto", True, type=bool))
        self.cutoff_spin.setValue(self.settings_store.value("controls/cutoff_value", 4000, type=int))
        self.enable_enhance_checkbox.setChecked(self.settings_store.value("controls/run_enhance", True, type=bool))
        self.denoise_checkbox.setChecked(self.settings_store.value("controls/denoise", False, type=bool))
        self.batch_checkbox.setChecked(self.settings_store.value("controls/batch", False, type=bool))
        self.suffix_enabled_checkbox.setChecked(self.settings_store.value("controls/suffix_enabled", True, type=bool))
        self.suffix_edit.setText(self.settings_store.value("controls/suffix_text", "_enhanced"))
        self.increment_suffix_checkbox.setChecked(self.settings_store.value("controls/increment_suffix", True, type=bool))
        self.output_folder_checkbox.setChecked(
            self.settings_store.value("controls/output_to_enhanced_folder", True, type=bool)
        )
        self.output_folder_name_edit.setText(self.settings_store.value("controls/output_folder_name", "enhanced"))
        self.override_output_folder_checkbox.setChecked(
            self.settings_store.value("controls/use_override_output_folder", False, type=bool)
        )
        self.override_output_folder_edit.setText(
            self.settings_store.value("controls/override_output_folder", "", type=str)
        )
        self.auto_update_checkbox.setChecked(self.settings_store.value("controls/auto_update_on_launch", False, type=bool))
        self.on_cutoff_auto_changed(self.cutoff_auto_checkbox.isChecked())
        self.on_suffix_enabled_changed(self.suffix_enabled_checkbox.isChecked())
        self.on_output_folder_toggled(self.output_folder_checkbox.isChecked())
        self.on_override_output_folder_toggled(self.override_output_folder_checkbox.isChecked())

    def save_ui_settings(self) -> None:
        self.settings_store.setValue("window/geometry", self.saveGeometry())
        self.settings_store.setValue("window/content_split_state", self.content_split.saveState())
        self.settings_store.setValue("controls/model_version", self.model_version_combo.currentData())
        self.settings_store.setValue("controls/model_path", self.model_path_edit.text().strip())
        self.settings_store.setValue("controls/device", self.device_combo.currentData())
        self.settings_store.setValue("controls/input_sr", self.input_sr_spin.value())
        self.settings_store.setValue("controls/duration", self.duration_spin.value())
        self.settings_store.setValue("controls/cutoff_auto", self.cutoff_auto_checkbox.isChecked())
        self.settings_store.setValue("controls/cutoff_value", self.cutoff_spin.value())
        self.settings_store.setValue("controls/run_enhance", self.enable_enhance_checkbox.isChecked())
        self.settings_store.setValue("controls/denoise", self.denoise_checkbox.isChecked())
        self.settings_store.setValue("controls/batch", self.batch_checkbox.isChecked())
        self.settings_store.setValue("controls/suffix_enabled", self.suffix_enabled_checkbox.isChecked())
        self.settings_store.setValue("controls/suffix_text", self.suffix_edit.text())
        self.settings_store.setValue("controls/increment_suffix", self.increment_suffix_checkbox.isChecked())
        self.settings_store.setValue("controls/output_to_enhanced_folder", self.output_folder_checkbox.isChecked())
        self.settings_store.setValue("controls/output_folder_name", self.output_folder_name_edit.text().strip())
        self.settings_store.setValue(
            "controls/use_override_output_folder", self.override_output_folder_checkbox.isChecked()
        )
        self.settings_store.setValue("controls/override_output_folder", self.override_output_folder_edit.text().strip())
        self.settings_store.setValue("controls/auto_update_on_launch", self.auto_update_checkbox.isChecked())
        self.settings_store.sync()

    def closeEvent(self, event) -> None:  # noqa: N802
        self.save_ui_settings()
        if self.enhance_thread is not None:
            self.enhance_thread.request_cancel()
            self.enhance_thread.wait(3000)
        if self.update_check_thread is not None:
            self.update_check_thread.wait(1000)
        if self.update_download_thread is not None:
            self.update_download_thread.wait(1000)
        super().closeEvent(event)


def main() -> None:
    app = QApplication([])
    app.setStyle("Fusion")
    app_icon_path = resolve_resource_path("assets", "toollogo.png")
    if app_icon_path.exists():
        app.setWindowIcon(QIcon(str(app_icon_path)))
    window = MainWindow()
    window.show()
    app.exec()


if __name__ == "__main__":
    main()
