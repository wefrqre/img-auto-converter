#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PySide6 import QtCore, QtGui, QtWidgets

try:
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer

    WATCH_BACKEND = "watchdog"
except ModuleNotFoundError:
    WATCH_BACKEND = "polling"

    class FileSystemEventHandler:
        pass

    class Observer:
        def __init__(self) -> None:
            self._handler: SvgEventHandler | None = None
            self._watch_path: Path | None = None
            self._running = False
            self._thread: threading.Thread | None = None
            self._snapshot: dict[str, float] = {}

        def schedule(
            self,
            handler: "SvgEventHandler",
            path: str,
            recursive: bool = False,  # noqa: ARG002
        ) -> None:
            self._handler = handler
            self._watch_path = Path(path)

        def start(self) -> None:
            if self._running:
                return
            self._snapshot = self._current_snapshot()
            self._running = True
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()

        def stop(self) -> None:
            self._running = False

        def join(self, timeout: float | None = None) -> None:
            if self._thread:
                self._thread.join(timeout=timeout)

        def _loop(self) -> None:
            while self._running:
                self._scan_once()
                time.sleep(1.0)

        def _current_snapshot(self) -> dict[str, float]:
            if not self._watch_path or not self._watch_path.exists():
                return {}

            current: dict[str, float] = {}
            for path in self._watch_path.rglob("*"):
                if not path.is_file() or not is_svg_file(path):
                    continue
                try:
                    current[str(path)] = path.stat().st_mtime
                except OSError:
                    continue
            return current

        def _scan_once(self) -> None:
            if not self._handler or not self._watch_path or not self._watch_path.exists():
                return

            current = self._current_snapshot()
            for path_key, mtime in current.items():
                previous_mtime = self._snapshot.get(path_key)
                if previous_mtime is None or previous_mtime != mtime:
                    self._handler.dispatch_path(Path(path_key))

            self._snapshot = current


APP_NAME = "응용 이미지 자동 변환기"
WINDOW_TITLE = "Img Auto Converter"
APP_VERSION = "1.0.0"
CONFIG_PATH = Path.home() / ".applied_image_auto_converter.json"
UPDATE_URL_CONFIG_PATH = Path.home() / ".applied_image_auto_converter_update_url.txt"
UPDATE_URL_BUNDLED_FILENAME = "update_url.txt"
APP_ICON_FILENAME = "app_icon.png"
INFO_ICON_FILENAMES = ("info.svg", "Info.svg")
ARROW_DOWN_ICON_FILENAMES = ("arrow-down.svg", "arrow_down.svg", "Arrow_down.svg")
ARROW_UP_ICON_FILENAMES = ("arrow-up.svg", "arrow_up.svg", "Arrow_up.svg")
ARROW_DOWN_SMALL_ICON_FILENAMES = (
    "arrow_down_small.svg",
    "arrow-down-small.svg",
    "arrow_donw_small.svg",
)
ARROW_UP_SMALL_ICON_FILENAMES = ("arrow_up_small.svg", "arrow-up-small.svg")
FIGMA_ICON_FILENAMES = ("Figma.svg", "figma.svg")
LOCAL_ICON_FILENAMES = ("Local.svg", "local.svg")
LOADING_ICON_FILENAMES = ("loading.svg", "Loading.svg")
INFO_TOOLTIP_TEXT = "Figma에서 SVG 파일을 폴더에 저장하면\nPNG로 자동 변환됩니다."
DEFAULT_BASE_DIR = Path.home() / "Desktop" / "figma_exports"
DEFAULT_INPUT_DIR = DEFAULT_BASE_DIR / "svg"
DEFAULT_DPI = 96
DPI_OPTIONS = (96, 192)
PATH_HINTS = ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"]
WATCH_EXTENSIONS = {".svg"}
UPDATE_INFO_URL = os.environ.get("APP_UPDATE_INFO_URL", "").strip()
UPDATE_REQUEST_TIMEOUT_SECONDS = 3
DIRECT_TOOL_PATHS = {
    "inkscape": [
        Path("/Applications/Inkscape.app/Contents/MacOS/inkscape"),
        Path.home() / "Applications/Inkscape.app/Contents/MacOS/inkscape",
    ],
    "magick": [],
}


def resource_path(filename: str) -> Path:
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / filename)
        candidates.append(Path(sys.executable).resolve().parent.parent / "Resources" / filename)
    candidates.append(Path(__file__).resolve().parent / filename)

    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[-1]


@dataclass
class ToolPaths:
    inkscape: str | None
    magick: str | None

    @property
    def missing(self) -> list[str]:
        missing_tools: list[str] = []
        if not self.inkscape:
            missing_tools.append("Inkscape")
        return missing_tools


def build_augmented_path() -> str:
    path_parts: list[str] = []
    seen: set[str] = set()

    for candidate in PATH_HINTS:
        if candidate not in seen and Path(candidate).exists():
            path_parts.append(candidate)
            seen.add(candidate)

    for candidate in os.environ.get("PATH", "").split(os.pathsep):
        if candidate and candidate not in seen:
            path_parts.append(candidate)
            seen.add(candidate)

    return os.pathsep.join(path_parts)


def configure_runtime_path() -> str:
    augmented_path = build_augmented_path()
    os.environ["PATH"] = augmented_path
    return augmented_path


def get_resource_root() -> Path:
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        executable_path = Path(sys.executable).resolve()
        return executable_path.parents[1] / "Resources"
    return Path(__file__).resolve().parent


def bundled_tool_candidates(name: str) -> list[Path]:
    resource_root = get_resource_root()
    candidates = [
        resource_root / "bin" / name,
        resource_root / "tools" / "bin" / name,
    ]
    if name == "inkscape":
        candidates.append(
            resource_root / "bin" / "vendor" / "Inkscape.app" / "Contents" / "MacOS" / "inkscape"
        )
        candidates.append(
            resource_root / "vendor" / "Inkscape.app" / "Contents" / "MacOS" / "inkscape"
        )
    candidates.extend(DIRECT_TOOL_PATHS.get(name, []))
    return candidates


def find_executable(name: str, search_path: str) -> str | None:
    for executable in bundled_tool_candidates(name):
        if executable.exists() and os.access(executable, os.X_OK):
            return str(executable)

    for candidate_dir in search_path.split(os.pathsep):
        if not candidate_dir:
            continue

        executable = Path(candidate_dir) / name
        if executable.exists() and os.access(executable, os.X_OK):
            return str(executable)
    return None


def detect_tools() -> ToolPaths:
    search_path = configure_runtime_path()
    return ToolPaths(
        inkscape=find_executable("inkscape", search_path),
        magick=find_executable("magick", search_path),
    )


def first_non_empty_line(text: str) -> str:
    for raw in text.splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            return line
    return ""


def read_update_url_from_file(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return first_non_empty_line(path.read_text(encoding="utf-8"))
    except OSError:
        return ""


def is_supported_update_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    return parsed.scheme in {"http", "https", "file"}


def load_config() -> dict[str, object]:
    if not CONFIG_PATH.exists():
        return {}

    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_config(input_dir: Path, output_dir: Path, dpi: int) -> bool:
    existing = load_config()
    payload: dict[str, object] = dict(existing) if isinstance(existing, dict) else {}
    payload.update(
        {
            "input_dir": str(input_dir),
            "output_dir": str(output_dir),
            "dpi": dpi,
        }
    )
    try:
        CONFIG_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return True
    except OSError:
        return False


def resolve_update_info_url(config: dict[str, object]) -> str:
    env_url = UPDATE_INFO_URL.strip()
    if env_url and is_supported_update_url(env_url):
        return env_url

    config_url = str(config.get("update_info_url", "")).strip()
    if config_url and is_supported_update_url(config_url):
        return config_url

    user_file_url = read_update_url_from_file(UPDATE_URL_CONFIG_PATH)
    if user_file_url and is_supported_update_url(user_file_url):
        return user_file_url

    bundled_url = read_update_url_from_file(resource_path(UPDATE_URL_BUNDLED_FILENAME))
    if bundled_url and is_supported_update_url(bundled_url):
        return bundled_url

    return ""


def ensure_directory(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        return True
    except OSError:
        return False


def normalize_dpi(value: object) -> int:
    try:
        numeric = int(value)
    except (TypeError, ValueError):
        return DEFAULT_DPI
    if numeric in DPI_OPTIONS:
        return numeric
    return DEFAULT_DPI


def output_dir_for_dpi(dpi: int) -> Path:
    safe_dpi = normalize_dpi(dpi)
    return DEFAULT_BASE_DIR / f"png_{safe_dpi}dpi"


def parse_version_parts(version: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in version.strip().split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    while parts and parts[-1] == 0:
        parts.pop()
    return tuple(parts or [0])


def compare_versions(left: str, right: str) -> int:
    left_parts = list(parse_version_parts(left))
    right_parts = list(parse_version_parts(right))
    max_len = max(len(left_parts), len(right_parts))
    left_parts.extend([0] * (max_len - len(left_parts)))
    right_parts.extend([0] * (max_len - len(right_parts)))
    if left_parts > right_parts:
        return 1
    if left_parts < right_parts:
        return -1
    return 0


def format_file_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.2f} MB"


def format_display_path(path: Path) -> str:
    expanded = path.expanduser()
    home = Path.home()
    try:
        relative = expanded.relative_to(home)
    except ValueError:
        return str(expanded)
    parts = [part for part in relative.parts if part and part != "."]
    if not parts:
        return "~"
    if len(parts) >= 2:
        return "/".join(parts[-2:])
    return parts[0]


def is_svg_file(path: Path) -> bool:
    return path.suffix.lower() in WATCH_EXTENSIONS


class SvgEventHandler(FileSystemEventHandler):
    def __init__(self, on_change: Callable[[Path], None]) -> None:
        self.on_change = on_change

    def on_created(self, event) -> None:
        self._handle(event)

    def on_modified(self, event) -> None:
        self._handle(event)

    def on_moved(self, event) -> None:
        if event.is_directory:
            return
        destination = getattr(event, "dest_path", None)
        if destination:
            self.dispatch_path(Path(destination))

    def _handle(self, event) -> None:
        if event.is_directory:
            return
        self.dispatch_path(Path(event.src_path))

    def dispatch_path(self, path: Path) -> None:
        self.on_change(path)


class PillButton(QtWidgets.QPushButton):
    def __init__(
        self,
        text: str,
        outlined: bool,
        tone: str = "neutral",
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(text, parent)
        self.outlined = outlined
        self.tone = tone
        self.suffix_icon: QtGui.QPixmap | None = None
        self.suffix_icon_gap = 8
        self.setFixedHeight(38)
        self.setMinimumHeight(38)
        self.setMaximumHeight(38)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setFocusPolicy(QtCore.Qt.NoFocus)
        self.setFlat(True)

    def set_tone(self, tone: str) -> None:
        self.tone = tone
        self.update()

    def set_suffix_icon(self, pixmap: QtGui.QPixmap | None, width: int = 8, gap: int = 8) -> None:
        self.suffix_icon_gap = max(0, int(gap))
        if pixmap is None or pixmap.isNull():
            self.suffix_icon = None
            self.update()
            return
        target_width = max(1, int(width))
        source_size = pixmap.deviceIndependentSize()
        source_width = max(1.0, source_size.width())
        target_height = max(1, int(round(target_width * (source_size.height() / source_width))))
        scaled = pixmap.scaled(
            target_width,
            target_height,
            QtCore.Qt.KeepAspectRatio,
            QtCore.Qt.SmoothTransformation,
        )
        scaled.setDevicePixelRatio(1.0)
        self.suffix_icon = scaled
        self.update()

    def _outlined_palette(self) -> tuple[QtGui.QColor, QtGui.QColor, QtGui.QColor]:
        if self.isDown():
            return (
                QtGui.QColor("#C7D4E0"),
                QtGui.QColor("#E8F1F8"),
                QtGui.QColor("#17324A"),
            )
        if self.underMouse():
            return (
                QtGui.QColor("#C9D9E7"),
                QtGui.QColor("#F7FBFF"),
                QtGui.QColor("#102B41"),
            )
        return (
            QtGui.QColor("#D3DDE7"),
            QtGui.QColor("#FFFFFF"),
            QtGui.QColor("#17324A"),
        )

    def _filled_palette(self) -> tuple[QtGui.QColor, QtGui.QColor, QtGui.QColor]:
        palettes = {
            "active": {
                "default": ("#224D72", "#123954", "#F7FBFF"),
                "hover": ("#285A86", "#16425F", "#F7FBFF"),
                "down": ("#17384F", "#102B3F", "#F7FBFF"),
            },
            "warning": {
                "default": ("#C86B40", "#A84E29", "#FFF9F5"),
                "hover": ("#D47B4F", "#B45A31", "#FFF9F5"),
                "down": ("#B25831", "#904223", "#FFF9F5"),
            },
            "primary": {
                "default": ("#3F89FF", "#225FD6", "#F7FBFF"),
                "hover": ("#5A9AFF", "#3171E2", "#F7FBFF"),
                "down": ("#2F6EDD", "#1C54BF", "#F7FBFF"),
            },
            "neutral": {
                "default": ("#94A4B4", "#708295", "#FFFFFF"),
                "hover": ("#9EADBB", "#7B8C9E", "#FFFFFF"),
                "down": ("#7B8C9E", "#5E7185", "#FFFFFF"),
            },
        }
        tone_palette = palettes.get(self.tone, palettes["primary"])
        if self.isDown():
            start, end, text = tone_palette["down"]
        elif self.underMouse():
            start, end, text = tone_palette["hover"]
        else:
            start, end, text = tone_palette["default"]
        return QtGui.QColor(start), QtGui.QColor(end), QtGui.QColor(text)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # noqa: N802
        del event
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)

        rect = self.rect().adjusted(0, 0, -1, -1)
        radius = rect.height() / 2.0

        if self.outlined:
            border_color, background, text_color = self._outlined_palette()
            painter.setPen(QtGui.QPen(border_color, 1))
            painter.setBrush(background)
        else:
            start_color, end_color, text_color = self._filled_palette()
            gradient = QtGui.QLinearGradient(rect.topLeft(), rect.topRight())
            gradient.setColorAt(0.0, start_color)
            gradient.setColorAt(1.0, end_color)
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(QtGui.QBrush(gradient))
        painter.drawRoundedRect(rect, radius, radius)

        painter.setPen(text_color)
        font = painter.font()
        font.setPointSize(12)
        font.setWeight(QtGui.QFont.Bold)
        painter.setFont(font)
        fm = QtGui.QFontMetrics(font)
        text = self.text()
        text_width = fm.horizontalAdvance(text)

        icon_width = 0
        icon_height = 0
        if self.suffix_icon and not self.suffix_icon.isNull():
            icon_width = self.suffix_icon.width()
            icon_height = self.suffix_icon.height()

        content_width = text_width
        if icon_width > 0:
            content_width += self.suffix_icon_gap + icon_width

        start_x = int(rect.x() + (rect.width() - content_width) / 2)
        text_rect = QtCore.QRect(start_x, rect.y(), text_width, rect.height())
        painter.drawText(text_rect, QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft, text)

        if icon_width > 0 and self.suffix_icon:
            icon_x = start_x + text_width + self.suffix_icon_gap
            icon_y = int(rect.y() + (rect.height() - icon_height) / 2)
            painter.drawPixmap(icon_x, icon_y, self.suffix_icon)


class SolidPillButton(QtWidgets.QPushButton):
    def __init__(
        self,
        text: str,
        background: str,
        hover: str,
        pressed: str,
        text_color: str,
        font_px: int = 10,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(text, parent)
        font_px = max(1, int(font_px))
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setFocusPolicy(QtCore.Qt.NoFocus)
        self.setFlat(True)
        self.setFixedHeight(32)
        self.setMinimumHeight(32)
        self.setMaximumHeight(32)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        font = QtGui.QFont("Inter")
        font.setPixelSize(font_px)
        font.setWeight(QtGui.QFont.DemiBold)
        self.setFont(font)
        self.setStyleSheet(
            f"""
            QPushButton {{
                background: {background};
                border: none;
                border-radius: 16px;
                color: {text_color};
                font-family: "Inter";
                font-size: {font_px}px;
                font-weight: 600;
                padding: 7px 11px;
            }}
            QPushButton:hover {{
                background: {hover};
            }}
            QPushButton:pressed {{
                background: {pressed};
            }}
            """
        )


class ClickableFrame(QtWidgets.QFrame):
    clicked = QtCore.Signal()

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:  # noqa: N802
        if event.button() == QtCore.Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class HoverIconLabel(QtWidgets.QLabel):
    hovered = QtCore.Signal()
    unhovered = QtCore.Signal()

    def enterEvent(self, event: QtCore.QEvent) -> None:  # noqa: N802
        self.hovered.emit()
        super().enterEvent(event)

    def leaveEvent(self, event: QtCore.QEvent) -> None:  # noqa: N802
        self.unhovered.emit()
        super().leaveEvent(event)


class TooltipPointer(QtWidgets.QWidget):
    def __init__(self, color: str = "#353738", parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.color = QtGui.QColor(color)
        self.setFixedSize(12, 7)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # noqa: N802
        del event
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(self.color)

        w = float(self.width())
        h = float(self.height())
        cx = w / 2.0

        path = QtGui.QPainterPath()
        path.moveTo(0.0, h)
        path.lineTo(cx - 1.0, 1.0)
        path.quadTo(cx, 0.0, cx + 1.0, 1.0)
        path.lineTo(w, h)
        path.closeSubpath()
        painter.drawPath(path)


class InfoTooltip(QtWidgets.QWidget):
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent, QtCore.Qt.ToolTip | QtCore.Qt.FramelessWindowHint)
        self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setObjectName("infoTooltip")
        self.setStyleSheet(
            """
            QWidget#infoTooltip { background: transparent; }
            QFrame#tooltipBubble { background: #353738; border-radius: 8px; }
            QLabel#tooltipText { color: #F7F7F8; font-size: 13px; font-weight: 400; background: transparent; }
            """
        )

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        pointer_row = QtWidgets.QHBoxLayout()
        pointer_row.setContentsMargins(0, 0, 0, 0)
        pointer_row.setSpacing(0)
        pointer_row.addStretch(1)
        self.pointer = TooltipPointer("#353738")
        pointer_row.addWidget(self.pointer, 0, QtCore.Qt.AlignHCenter | QtCore.Qt.AlignTop)
        pointer_row.addStretch(1)
        root.addLayout(pointer_row)

        bubble = QtWidgets.QFrame()
        bubble.setObjectName("tooltipBubble")
        bubble_layout = QtWidgets.QVBoxLayout(bubble)
        bubble_layout.setContentsMargins(8, 8, 8, 8)
        bubble_layout.setSpacing(0)

        self.text_label = QtWidgets.QLabel()
        self.text_label.setObjectName("tooltipText")
        self.text_label.setWordWrap(False)
        self.text_label.setTextFormat(QtCore.Qt.RichText)
        self.text_label.setMinimumWidth(0)
        self.text_label.setMaximumWidth(16777215)
        bubble_layout.addWidget(self.text_label)
        root.addWidget(bubble)

        self.set_text(INFO_TOOLTIP_TEXT)

    def set_text(self, text: str) -> None:
        lines = text.splitlines() or [text]
        fm = QtGui.QFontMetrics(QtGui.QFont(self.font().family(), 13))
        target_width = max(1, max(fm.horizontalAdvance(line) for line in lines))
        self.text_label.setFixedWidth(target_width)
        safe_text = (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("\n", "<br/>")
        )
        self.text_label.setText(
            f'<div style="line-height:19px; color:#F7F7F8; font-size:13px;">{safe_text}</div>'
        )


class App(QtWidgets.QWidget):
    update_available = QtCore.Signal(str, str, str)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(280, 640)
        self.setMinimumWidth(280)
        self.setMinimumHeight(0)

        self.tool_paths = detect_tools()
        self.event_queue: queue.Queue[Path] = queue.Queue()
        self.debounce_jobs: dict[str, QtCore.QTimer] = {}
        self.pending_convert_paths: list[Path] = []
        self.pending_convert_keys: set[str] = set()
        self.processing_convert_queue = False
        self.last_processed_svg_mtimes: dict[str, float] = {}
        self.watch_started_at = 0.0
        self.observer: Observer | None = None
        self.worker_lock = threading.Lock()
        self.stop_requested = False

        config = load_config()
        self.selected_dpi = normalize_dpi(config.get("dpi"))
        self.input_dir = DEFAULT_INPUT_DIR
        self.output_dir = output_dir_for_dpi(self.selected_dpi)
        self.status_text = "변환 중지됨"
        self.log_messages: list[str] = []

        self.status_dot: QtWidgets.QFrame | None = None
        self.status_label: QtWidgets.QLabel | None = None
        self.progress_bar: QtWidgets.QProgressBar | None = None
        self.progress_label: QtWidgets.QLabel | None = None
        self.progress_title_label: QtWidgets.QLabel | None = None
        self.info_icon_label: HoverIconLabel | None = None
        self.info_tooltip: InfoTooltip | None = None
        self.folder_button: QtWidgets.QPushButton | None = None
        self.dpi_button: QtWidgets.QPushButton | None = None
        self.input_path_label: QtWidgets.QLabel | None = None
        self.output_path_label: QtWidgets.QLabel | None = None
        self.version_label: QtWidgets.QLabel | None = None
        self.top_loading_label: QtWidgets.QLabel | None = None
        self.dpi_buttons: dict[int, QtWidgets.QRadioButton] = {}
        self.status_animation_timer: QtCore.QTimer | None = None
        self.status_animation_step = 0
        self.status_animation_base = ""
        self.info_labels: dict[str, QtWidgets.QLabel] = {}
        self.bottom_card: QtWidgets.QFrame | None = None
        self.shell_card: QtWidgets.QFrame | None = None
        self.bottom_layout: QtWidgets.QVBoxLayout | None = None
        self.bottom_toggle_row: ClickableFrame | None = None
        self.bottom_content_widget: QtWidgets.QWidget | None = None
        self.bottom_toggle_icon: QtWidgets.QLabel | None = None
        self.bottom_collapsed = False
        self.startup_warning: str | None = None
        self.update_info_url = resolve_update_info_url(config)
        self.notified_update_versions: set[str] = set()
        self.transfer_total_files = 0
        self.transfer_completed_files = 0
        self.current_transfer_fraction = 0.0
        self.current_transfer_percent = 0

        self.first_launch_setup()
        self.build_ui()
        self.set_status("변환 중지됨")
        self.refresh_paths()
        self.update_available.connect(self.show_update_dialog)

        if self.startup_warning:
            warning_text = self.startup_warning
            QtCore.QTimer.singleShot(
                250,
                lambda: QtWidgets.QMessageBox.warning(self, APP_NAME, warning_text),
            )

        self.poll_timer = QtCore.QTimer(self)
        self.poll_timer.timeout.connect(self.poll_events)
        self.poll_timer.start(300)

        self.rescan_timer = QtCore.QTimer(self)
        self.rescan_timer.setSingleShot(True)
        self.rescan_timer.timeout.connect(self.reconcile_recent_svgs)

        if self.tool_paths.missing:
            self.show_dependency_warning()
        else:
            QtCore.QTimer.singleShot(200, self.start_watch)
        if self.update_info_url:
            QtCore.QTimer.singleShot(900, self.check_for_updates_async)

    def first_launch_setup(self) -> None:
        created: list[Path] = []
        failed: list[Path] = []
        for directory in (DEFAULT_BASE_DIR, self.input_dir, self.output_dir):
            if not directory.exists():
                if ensure_directory(directory):
                    created.append(directory)
                else:
                    failed.append(directory)

        config_saved = save_config(self.input_dir, self.output_dir, self.selected_dpi)

        if created:
            QtCore.QTimer.singleShot(
                200,
                self.show_initial_folder_ready_dialog,
            )

        if failed or not config_saved:
            parts: list[str] = []
            if failed:
                failed_text = "\n".join(str(path) for path in failed)
                parts.append(f"폴더를 만들지 못했습니다.\n{failed_text}")
            if not config_saved:
                parts.append(f"설정 파일을 저장하지 못했습니다.\n{CONFIG_PATH}")
            parts.append("macOS 권한 설정을 확인한 뒤 다시 시도하세요.")
            self.startup_warning = "\n\n".join(parts)

    def show_initial_folder_ready_dialog(self) -> None:
        dialog = QtWidgets.QMessageBox(self)
        dialog.setIcon(QtWidgets.QMessageBox.Information)
        dialog.setWindowTitle("작업 폴더가 준비됐어요")
        dialog.setText(
            "SVG 파일을 svg 폴더에 저장하면\n"
            "PNG가 자동으로 생성됩니다.\n\n"
            "폴더 위치 : Desktop > figma_exports"
        )
        confirm_button = dialog.addButton("확인", QtWidgets.QMessageBox.AcceptRole)
        open_button = dialog.addButton("폴더 열기", QtWidgets.QMessageBox.ActionRole)
        confirm_button.setDefault(True)
        dialog.exec()
        if dialog.clickedButton() == open_button:
            self.open_base_dir()

    def dependency_summary(self) -> str:
        if self.tool_paths.inkscape:
            return "Inkscape 확인됨 · SVG 저장 시 PNG 자동 생성"
        return "Inkscape 설치가 필요합니다"

    def build_ui(self) -> None:
        self.setStyleSheet(
            """
            QWidget {
                background: #F5F5F5;
                color: #4C5052;
                font-family: "Inter";
            }
            QLabel {
                background: transparent;
            }
            QFrame#shellCard {
                background: #FCFCFC;
                border: none;
                border-radius: 24px;
            }
            QWidget#transferVisualCell {
                background: transparent;
                border: none;
            }
            QWidget#transferFlowWidget {
                background: transparent;
                border: none;
            }
            QWidget#transferCaptionSpacer {
                background: transparent;
                border: none;
            }
            QLabel#microLabel {
                color: #9E9E9E;
                font-size: 12px;
                font-weight: 500;
            }
            QLabel#appTitle {
                color: #4C5052;
                font-size: 16px;
                font-weight: 700;
            }
            QLabel#microCaption {
                color: #717171;
                font-size: 8px;
                font-weight: 500;
            }
            QLabel#microCaptionMuted {
                color: #C0C0C0;
                font-size: 8px;
                font-weight: 500;
            }
            QLabel#sectionTitle {
                color: #4C5052;
                font-size: 13px;
                font-weight: 600;
            }
            QProgressBar#transferProgressBar {
                background: #E9E9E9;
                border: none;
                border-radius: 2px;
                min-height: 4px;
                max-height: 4px;
            }
            QProgressBar#transferProgressBar::chunk {
                background: #39B95C;
                border-radius: 2px;
            }
            QLabel#progressCaption {
                color: #717171;
                font-size: 9px;
                font-weight: 500;
            }
            QLabel#progressValue {
                color: #39B95C;
                font-size: 9px;
                font-weight: 600;
            }
            QFrame#detailShell {
                background: #EAEAEA;
                border: none;
                border-radius: 12px;
            }
            QFrame#detailToggleRow {
                background: transparent;
                border: none;
            }
            QFrame#detailBody {
                background: #FAFAFA;
                border: none;
                border-radius: 8px;
            }
            QLabel#detailTitle {
                background: transparent;
                color: #4C5052;
                font-size: 10px;
                font-weight: 600;
            }
            QLabel#detailKey {
                color: #8D8D8D;
                font-size: 9px;
                font-weight: 500;
            }
            QLabel#detailValue {
                color: #656565;
                font-size: 9px;
                font-weight: 400;
            }
            QPushButton#actionButton {
                background: #F0F1F3;
                border: none;
                border-radius: 16px;
                color: #6F6F6F;
                font-size: 10px;
                font-weight: 600;
                min-height: 32px;
                padding-left: 11px;
                padding-right: 11px;
            }
            QPushButton#actionButton:hover {
                background: #E6E8EB;
            }
            QPushButton#actionButton:pressed {
                background: #DDE1E5;
            }
            QLabel#versionLabel {
                color: #9A9A9A;
                font-size: 8px;
                font-weight: 400;
            }
            QLabel#hiddenStatus {
                color: transparent;
                font-size: 1px;
            }
            QFrame#dot {
                background: #D9D9D9;
                border-radius: 2px;
            }
            QMenu {
                background: #FAFAFA;
                border: 1px solid #E5E5E5;
                border-radius: 10px;
                padding: 6px;
            }
            QMenu::item {
                color: #4C5052;
                padding: 6px 12px;
                border-radius: 8px;
            }
            QMenu::item:selected {
                background: #F0F1F3;
            }
            """
        )

        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        main_layout.setAlignment(QtCore.Qt.AlignHCenter | QtCore.Qt.AlignTop)
        main_layout.setSizeConstraint(QtWidgets.QLayout.SetFixedSize)

        self.shell_card = QtWidgets.QFrame()
        self.shell_card.setObjectName("shellCard")
        self.shell_card.setFixedWidth(280)
        shell_layout = QtWidgets.QVBoxLayout(self.shell_card)
        shell_layout.setContentsMargins(24, 30, 24, 16)
        shell_layout.setSpacing(20)
        shell_layout.setAlignment(QtCore.Qt.AlignHCenter | QtCore.Qt.AlignTop)

        title_stack = QtWidgets.QVBoxLayout()
        title_stack.setContentsMargins(0, 0, 0, 0)
        title_stack.setSpacing(4)
        title_stack.setAlignment(QtCore.Qt.AlignHCenter)

        title_micro_row = QtWidgets.QHBoxLayout()
        title_micro_row.setContentsMargins(0, 0, 0, 0)
        title_micro_row.setSpacing(2)
        title_micro_row.setAlignment(QtCore.Qt.AlignHCenter)

        micro_label = QtWidgets.QLabel("응용 이미지 자동변환기")
        micro_label.setObjectName("microLabel")
        title_micro_row.addWidget(micro_label, 0, QtCore.Qt.AlignVCenter)

        self.info_icon_label = HoverIconLabel()
        self.info_icon_label.setFixedSize(12, 12)
        self.info_icon_label.setAlignment(QtCore.Qt.AlignCenter)
        self.info_icon_label.setPixmap(self._load_info_icon_pixmap(12))
        self.info_icon_label.setCursor(QtCore.Qt.PointingHandCursor)
        self.info_icon_label.hovered.connect(self.show_info_tooltip)
        self.info_icon_label.unhovered.connect(self.hide_info_tooltip)
        title_micro_row.addWidget(self.info_icon_label, 0, QtCore.Qt.AlignVCenter)
        title_stack.addLayout(title_micro_row)

        title_label = QtWidgets.QLabel("Img Auto Converter")
        title_label.setObjectName("appTitle")
        title_stack.addWidget(title_label, 0, QtCore.Qt.AlignHCenter)
        shell_layout.addLayout(title_stack)

        self.status_label = QtWidgets.QLabel()
        self.status_label.setObjectName("hiddenStatus")
        self.status_label.hide()
        shell_layout.addWidget(self.status_label)

        transfer_block = QtWidgets.QWidget()
        transfer_block.setObjectName("transferFlowWidget")
        transfer_block.setFixedWidth(232)
        transfer_grid = QtWidgets.QGridLayout(transfer_block)
        transfer_grid.setContentsMargins(0, 0, 0, 0)
        transfer_grid.setHorizontalSpacing(0)
        transfer_grid.setVerticalSpacing(8)
        transfer_grid.setColumnMinimumWidth(0, 87)
        transfer_grid.setColumnMinimumWidth(1, 52)
        transfer_grid.setColumnMinimumWidth(2, 87)

        source_visual = QtWidgets.QWidget()
        source_visual.setObjectName("transferVisualCell")
        source_visual.setFixedSize(87, 57)
        source_visual_layout = QtWidgets.QVBoxLayout(source_visual)
        source_visual_layout.setContentsMargins(0, 0, 0, 0)
        source_visual_layout.setSpacing(0)
        source_visual_layout.setAlignment(QtCore.Qt.AlignCenter)
        source_visual_layout.addWidget(
            self._build_svg_label(FIGMA_ICON_FILENAMES, 57, 57),
            0,
            QtCore.Qt.AlignCenter,
        )
        transfer_grid.addWidget(source_visual, 0, 0, QtCore.Qt.AlignCenter)

        transfer_flow_row = QtWidgets.QHBoxLayout()
        transfer_flow_row.setContentsMargins(0, 0, 0, 0)
        transfer_flow_row.setSpacing(7)
        transfer_flow_row.setAlignment(QtCore.Qt.AlignCenter)
        for _ in range(2):
            transfer_flow_row.addWidget(self._build_dot(), 0, QtCore.Qt.AlignVCenter)
        self.top_loading_label = self._build_svg_label(LOADING_ICON_FILENAMES, 12, 10)
        transfer_flow_row.addWidget(self.top_loading_label, 0, QtCore.Qt.AlignVCenter)
        for _ in range(2):
            transfer_flow_row.addWidget(self._build_dot(), 0, QtCore.Qt.AlignVCenter)
        transfer_flow_widget = QtWidgets.QWidget()
        transfer_flow_widget.setObjectName("transferFlowWidget")
        transfer_flow_widget.setFixedSize(52, 57)
        transfer_flow_widget_layout = QtWidgets.QVBoxLayout(transfer_flow_widget)
        transfer_flow_widget_layout.setContentsMargins(0, 0, 0, 0)
        transfer_flow_widget_layout.setSpacing(0)
        transfer_flow_widget_layout.setAlignment(QtCore.Qt.AlignCenter)
        transfer_flow_widget_layout.addLayout(transfer_flow_row)
        transfer_grid.addWidget(transfer_flow_widget, 0, 1, QtCore.Qt.AlignCenter)

        destination_visual = QtWidgets.QWidget()
        destination_visual.setObjectName("transferVisualCell")
        destination_visual.setFixedSize(87, 57)
        destination_visual_layout = QtWidgets.QVBoxLayout(destination_visual)
        destination_visual_layout.setContentsMargins(0, 0, 0, 0)
        destination_visual_layout.setSpacing(0)
        destination_visual_layout.setAlignment(QtCore.Qt.AlignCenter)
        destination_visual_layout.addWidget(
            self._build_svg_label(LOCAL_ICON_FILENAMES, 87, 53),
            0,
            QtCore.Qt.AlignCenter,
        )
        transfer_grid.addWidget(destination_visual, 0, 2, QtCore.Qt.AlignCenter)

        self.input_path_label = QtWidgets.QLabel()
        self.input_path_label.setFixedWidth(87)
        self.input_path_label.setAlignment(QtCore.Qt.AlignCenter)
        self.input_path_label.setText(
            "<span style='color:#C0C0C0;'>Exported from</span><br/>"
            "<span style='color:#717171;'>Figma SVG</span>"
        )
        self.input_path_label.setObjectName("microCaption")
        transfer_grid.addWidget(self.input_path_label, 1, 0, QtCore.Qt.AlignHCenter | QtCore.Qt.AlignTop)

        transfer_caption_spacer = QtWidgets.QWidget()
        transfer_caption_spacer.setObjectName("transferCaptionSpacer")
        transfer_caption_spacer.setFixedSize(52, 1)
        transfer_grid.addWidget(transfer_caption_spacer, 1, 1, QtCore.Qt.AlignTop)

        self.output_path_label = QtWidgets.QLabel()
        self.output_path_label.setFixedWidth(87)
        self.output_path_label.setAlignment(QtCore.Qt.AlignCenter)
        self.output_path_label.setText(
            "<span style='color:#C0C0C0;'>Saved to</span><br/>"
            "<span style='color:#717171;'>Local PNG Folder</span>"
        )
        self.output_path_label.setObjectName("microCaption")
        transfer_grid.addWidget(self.output_path_label, 1, 2, QtCore.Qt.AlignHCenter | QtCore.Qt.AlignTop)
        shell_layout.addWidget(transfer_block, 0, QtCore.Qt.AlignHCenter)

        progress_section = QtWidgets.QVBoxLayout()
        progress_section.setContentsMargins(0, 0, 0, 0)
        progress_section.setSpacing(12)
        progress_section.setAlignment(QtCore.Qt.AlignHCenter)

        self.progress_title_label = QtWidgets.QLabel("Transfer progress")
        self.progress_title_label.setObjectName("sectionTitle")
        progress_section.addWidget(self.progress_title_label, 0, QtCore.Qt.AlignHCenter)

        progress_stack = QtWidgets.QVBoxLayout()
        progress_stack.setContentsMargins(0, 0, 0, 0)
        progress_stack.setSpacing(6)
        progress_stack.setAlignment(QtCore.Qt.AlignHCenter)

        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setObjectName("transferProgressBar")
        self.progress_bar.setFixedWidth(189)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        progress_stack.addWidget(self.progress_bar, 0, QtCore.Qt.AlignHCenter)

        self.progress_label = QtWidgets.QLabel()
        self.progress_label.setAlignment(QtCore.Qt.AlignCenter)
        progress_stack.addWidget(self.progress_label, 0, QtCore.Qt.AlignHCenter)
        progress_section.addLayout(progress_stack)
        shell_layout.addLayout(progress_section)

        self.bottom_card = QtWidgets.QFrame()
        self.bottom_card.setObjectName("detailShell")
        self.bottom_layout = QtWidgets.QVBoxLayout(self.bottom_card)
        self.bottom_layout.setContentsMargins(4, 8, 4, 4)
        self.bottom_layout.setSpacing(6)

        self.bottom_toggle_row = ClickableFrame()
        self.bottom_toggle_row.setObjectName("detailToggleRow")
        self.bottom_toggle_row.setCursor(QtCore.Qt.PointingHandCursor)
        toggle_layout = QtWidgets.QHBoxLayout(self.bottom_toggle_row)
        toggle_layout.setContentsMargins(0, 0, 0, 0)
        toggle_layout.setSpacing(6)
        toggle_layout.setAlignment(QtCore.Qt.AlignHCenter)

        title_group = QtWidgets.QWidget()
        title_group.setStyleSheet("background: transparent;")
        title_group_layout = QtWidgets.QHBoxLayout(title_group)
        title_group_layout.setContentsMargins(0, 0, 0, 0)
        title_group_layout.setSpacing(2)
        title_group_layout.setAlignment(QtCore.Qt.AlignCenter)

        header_icon = QtWidgets.QLabel()
        header_icon.setFixedSize(12, 12)
        header_icon.setAlignment(QtCore.Qt.AlignCenter)
        header_icon.setPixmap(self._load_detail_info_icon_pixmap(12))
        header_icon.setStyleSheet("background: transparent;")
        title_group_layout.addWidget(header_icon, 0, QtCore.Qt.AlignVCenter)

        toggle_label = QtWidgets.QLabel("Transfer Details")
        toggle_label.setObjectName("detailTitle")
        title_group_layout.addWidget(toggle_label, 0, QtCore.Qt.AlignVCenter)
        toggle_layout.addWidget(title_group, 0, QtCore.Qt.AlignVCenter)

        self.bottom_toggle_icon = QtWidgets.QLabel()
        detail_arrow_size = self._svg_intrinsic_size(ARROW_UP_SMALL_ICON_FILENAMES, 7, 4)
        self.bottom_toggle_icon.setFixedSize(detail_arrow_size)
        self.bottom_toggle_icon.setAlignment(QtCore.Qt.AlignCenter)
        self.bottom_toggle_icon.setStyleSheet("background: transparent;")
        toggle_layout.addWidget(self.bottom_toggle_icon, 0, QtCore.Qt.AlignVCenter)
        self.bottom_toggle_row.clicked.connect(self.toggle_bottom_section)
        self.bottom_layout.addWidget(self.bottom_toggle_row, 0, QtCore.Qt.AlignHCenter)

        self.bottom_content_widget = QtWidgets.QWidget()
        bottom_content_layout = QtWidgets.QVBoxLayout(self.bottom_content_widget)
        bottom_content_layout.setContentsMargins(0, 0, 0, 0)
        bottom_content_layout.setSpacing(0)

        detail_body = QtWidgets.QFrame()
        detail_body.setObjectName("detailBody")
        detail_body_layout = QtWidgets.QGridLayout(detail_body)
        detail_body_layout.setContentsMargins(12, 8, 12, 8)
        detail_body_layout.setHorizontalSpacing(12)
        detail_body_layout.setVerticalSpacing(7)

        fields = [
            "파일명",
            "파일 크기",
            "이미지 크기",
            "DPI",
            "색상 모드",
            "비트 깊이",
            "PNG 타입",
            "알파 채널",
            "색상 프로파일",
        ]
        for row, field in enumerate(fields):
            key_label = QtWidgets.QLabel(field)
            key_label.setObjectName("detailKey")
            key_label.setAlignment(QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft)
            value_label = QtWidgets.QLabel("-")
            value_label.setObjectName("detailValue")
            value_label.setAlignment(QtCore.Qt.AlignVCenter | QtCore.Qt.AlignRight)
            value_label.setWordWrap(False)
            value_label.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Fixed)
            value_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            detail_body_layout.addWidget(key_label, row, 0, QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft)
            detail_body_layout.addWidget(value_label, row, 1, QtCore.Qt.AlignVCenter | QtCore.Qt.AlignRight)
            self.info_labels[field] = value_label
        detail_body_layout.setColumnStretch(0, 1)
        detail_body_layout.setColumnStretch(1, 1)
        bottom_content_layout.addWidget(detail_body)
        self.bottom_layout.addWidget(self.bottom_content_widget)
        shell_layout.addWidget(self.bottom_card)

        button_row = QtWidgets.QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.setSpacing(10)

        self.dpi_button = QtWidgets.QPushButton("DPI 변경")
        self.dpi_button.setObjectName("actionButton")
        self.dpi_button.setCursor(QtCore.Qt.PointingHandCursor)
        dpi_arrow_size = self._svg_intrinsic_size(ARROW_UP_SMALL_ICON_FILENAMES, 7, 4)
        self.dpi_button.setIcon(
            QtGui.QIcon(self._load_arrow_down_small_icon_pixmap(dpi_arrow_size.width(), dpi_arrow_size.height()))
        )
        self.dpi_button.setIconSize(dpi_arrow_size)
        self.dpi_button.setLayoutDirection(QtCore.Qt.RightToLeft)
        self.dpi_button.clicked.connect(self.show_dpi_menu)
        button_row.addWidget(self.dpi_button, 1)

        self.folder_button = SolidPillButton(
            "폴더 열기",
            background="#2F2F2F",
            hover="#242424",
            pressed="#1D1D1D",
            text_color="#FEFEFE",
            font_px=10,
        )
        self.folder_button.setObjectName("folderActionButton")
        self.folder_button.clicked.connect(self.open_base_dir)
        button_row.addWidget(self.folder_button, 1)
        shell_layout.addLayout(button_row)

        self.version_label = QtWidgets.QLabel(f"V {APP_VERSION}")
        self.version_label.setObjectName("versionLabel")
        self.version_label.setAlignment(QtCore.Qt.AlignCenter)
        shell_layout.addWidget(self.version_label, 0, QtCore.Qt.AlignHCenter)

        main_layout.addWidget(self.shell_card, 0, QtCore.Qt.AlignHCenter)

        self.status_animation_timer = QtCore.QTimer(self)
        self.status_animation_timer.setInterval(420)
        self.status_animation_timer.timeout.connect(self._tick_status_animation)
        self.update_runtime_summary()
        self.update_transfer_progress(0)
        self.set_bottom_collapsed(True)

    @staticmethod
    def _build_dot() -> QtWidgets.QFrame:
        dot = QtWidgets.QFrame()
        dot.setObjectName("dot")
        dot.setFixedSize(4, 4)
        return dot

    @staticmethod
    def _build_svg_label(filenames: tuple[str, ...], width: int, height: int) -> QtWidgets.QLabel:
        label = QtWidgets.QLabel()
        label.setFixedSize(width, height)
        label.setAlignment(QtCore.Qt.AlignCenter)
        pixmap = App._load_svg_pixmap(filenames, width, height)
        if not pixmap.isNull():
            label.setPixmap(pixmap)
        return label

    @staticmethod
    def _svg_intrinsic_size(
        filenames: tuple[str, ...],
        fallback_width: int,
        fallback_height: int,
    ) -> QtCore.QSize:
        for filename in filenames:
            icon_path = resource_path(filename)
            if not icon_path.exists():
                continue
            try:
                svg_text = icon_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            match = re.search(r'width="([0-9.]+)".*height="([0-9.]+)"', svg_text, re.S)
            if not match:
                continue
            width = max(1, int(round(float(match.group(1)))))
            height = max(1, int(round(float(match.group(2)))))
            return QtCore.QSize(width, height)
        return QtCore.QSize(fallback_width, fallback_height)

    def update_runtime_summary(self) -> None:
        if self.input_path_label:
            self.input_path_label.setToolTip(str(self.input_dir))
        if self.output_path_label:
            self.output_path_label.setToolTip(str(self.output_dir))

    def position_version_label(self) -> None:
        return

    def check_for_updates_async(self) -> None:
        if not self.update_info_url:
            return
        threading.Thread(target=self._check_for_updates_worker, daemon=True).start()

    def _check_for_updates_worker(self) -> None:
        if not self.update_info_url:
            return
        try:
            request = urllib.request.Request(
                self.update_info_url,
                headers={"Cache-Control": "no-cache", "Pragma": "no-cache"},
            )
            with urllib.request.urlopen(request, timeout=UPDATE_REQUEST_TIMEOUT_SECONDS) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, ValueError, OSError):
            return

        latest_version = str(payload.get("version", "")).strip()
        download_url = str(payload.get("download_url", "")).strip()
        notes = str(payload.get("notes", "")).strip()
        if not latest_version or not download_url:
            return
        if compare_versions(latest_version, APP_VERSION) <= 0:
            return
        self.update_available.emit(latest_version, download_url, notes)

    def show_update_dialog(self, latest_version: str, download_url: str, notes: str) -> None:
        if latest_version in self.notified_update_versions:
            return
        self.notified_update_versions.add(latest_version)

        dialog = QtWidgets.QMessageBox(self)
        dialog.setIcon(QtWidgets.QMessageBox.Information)
        dialog.setWindowTitle(APP_NAME)
        message = f"새 버전 {latest_version}이 있습니다.\n현재 버전 {APP_VERSION}"
        if notes:
            message = f"{message}\n\n{notes}"
        dialog.setText(message)
        download_button = dialog.addButton("다운로드", QtWidgets.QMessageBox.AcceptRole)
        dialog.addButton("나중에", QtWidgets.QMessageBox.RejectRole)
        dialog.exec()
        if dialog.clickedButton() == download_button:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl(download_url))

    def show_dpi_menu(self) -> None:
        if not self.dpi_button:
            return
        dpi_arrow_size = self._svg_intrinsic_size(ARROW_UP_SMALL_ICON_FILENAMES, 7, 4)
        self.dpi_button.setIcon(
            QtGui.QIcon(
                self._load_arrow_up_small_icon_pixmap(
                    dpi_arrow_size.width(),
                    dpi_arrow_size.height(),
                )
            )
        )
        self.dpi_button.setIconSize(dpi_arrow_size)
        try:
            menu = QtWidgets.QMenu(self)
            for value in DPI_OPTIONS:
                action = menu.addAction(f"{value} DPI")
                action.setCheckable(True)
                action.setChecked(self.selected_dpi == value)
                action.triggered.connect(lambda _checked=False, v=value: self.set_dpi(v))
            menu.exec(self.dpi_button.mapToGlobal(QtCore.QPoint(0, self.dpi_button.height())))
        finally:
            self.dpi_button.setIcon(
                QtGui.QIcon(
                    self._load_arrow_down_small_icon_pixmap(
                        dpi_arrow_size.width(),
                        dpi_arrow_size.height(),
                    )
                )
            )
            self.dpi_button.setIconSize(dpi_arrow_size)

    def update_dpi_display(self) -> None:
        self.update_runtime_summary()

    def show_info_tooltip(self) -> None:
        if not self.info_icon_label:
            return
        if self.info_tooltip is None:
            self.info_tooltip = InfoTooltip(self)

        self.info_tooltip.set_text(INFO_TOOLTIP_TEXT)
        self.info_tooltip.adjustSize()

        icon_center_global = self.info_icon_label.mapToGlobal(
            QtCore.QPoint(self.info_icon_label.width() // 2, self.info_icon_label.height())
        )
        tooltip_width = self.info_tooltip.width()
        tooltip_height = self.info_tooltip.height()
        x = icon_center_global.x() - (tooltip_width // 2)
        y = icon_center_global.y() + 8

        screen = QtGui.QGuiApplication.screenAt(icon_center_global)
        if screen is None:
            screen = QtGui.QGuiApplication.primaryScreen()
        if screen is not None:
            bounds = screen.availableGeometry()
            x = max(bounds.left() + 8, min(x, bounds.right() - tooltip_width - 8))
            y = max(bounds.top() + 8, min(y, bounds.bottom() - tooltip_height - 8))

        self.info_tooltip.move(x, y)
        self.info_tooltip.show()
        self.info_tooltip.raise_()

    def hide_info_tooltip(self) -> None:
        if self.info_tooltip:
            self.info_tooltip.hide()

    def toggle_bottom_section(self) -> None:
        self.set_bottom_collapsed(not self.bottom_collapsed)

    def set_bottom_collapsed(self, collapsed: bool) -> None:
        self.bottom_collapsed = collapsed
        if not self.bottom_card or not self.bottom_content_widget or not self.bottom_layout:
            return

        if self.bottom_toggle_icon:
            detail_arrow_size = self._svg_intrinsic_size(ARROW_UP_SMALL_ICON_FILENAMES, 7, 4)
            arrow = (
                self._load_arrow_down_small_icon_pixmap(
                    detail_arrow_size.width(),
                    detail_arrow_size.height(),
                )
                if collapsed
                else self._load_arrow_up_small_icon_pixmap(
                    detail_arrow_size.width(),
                    detail_arrow_size.height(),
                )
            )
            self.bottom_toggle_icon.setPixmap(arrow)

        self.bottom_content_widget.setVisible(not collapsed)
        if collapsed:
            self.bottom_layout.setContentsMargins(4, 8, 4, 8)
            self.bottom_layout.setSpacing(0)
        else:
            self.bottom_layout.setContentsMargins(4, 8, 4, 4)
            self.bottom_layout.setSpacing(6)

        if self.folder_button:
            self.folder_button.update()

        self.adjustSize()
        layout = self.layout()
        if layout:
            layout.activate()
        self.setFixedSize(self.sizeHint())

    def _tick_status_animation(self) -> None:
        if not self.status_label:
            return
        self.status_animation_step = (self.status_animation_step % 3) + 1
        dots = "." * self.status_animation_step
        self.status_label.setText(f"{self.status_animation_base}{dots}")

    @staticmethod
    def _load_svg_pixmap(
        filenames: tuple[str, ...],
        width: int,
        height: int,
    ) -> QtGui.QPixmap:
        for filename in filenames:
            icon_path = resource_path(filename)
            if not icon_path.exists():
                continue
            icon = QtGui.QIcon(str(icon_path))
            if not icon.isNull():
                return icon.pixmap(width, height)
        return QtGui.QPixmap()

    @staticmethod
    def _tint_pixmap(pixmap: QtGui.QPixmap, color: str) -> QtGui.QPixmap:
        if pixmap.isNull():
            return pixmap
        tinted = QtGui.QPixmap(pixmap.size())
        tinted.fill(QtCore.Qt.transparent)
        painter = QtGui.QPainter(tinted)
        painter.drawPixmap(0, 0, pixmap)
        painter.setCompositionMode(QtGui.QPainter.CompositionMode_SourceIn)
        painter.fillRect(tinted.rect(), QtGui.QColor(color))
        painter.end()
        tinted.setDevicePixelRatio(pixmap.devicePixelRatio())
        return tinted

    @staticmethod
    def _normalize_pixmap_size(pixmap: QtGui.QPixmap, width: int, height: int) -> QtGui.QPixmap:
        if pixmap.isNull():
            return pixmap
        normalized = QtGui.QPixmap(width, height)
        normalized.fill(QtCore.Qt.transparent)
        painter = QtGui.QPainter(normalized)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
        painter.drawPixmap(0, 0, width, height, pixmap)
        painter.end()
        normalized.setDevicePixelRatio(1.0)
        return normalized

    @staticmethod
    def _load_info_icon_pixmap(size: int) -> QtGui.QPixmap:
        pixmap = App._load_svg_pixmap(INFO_ICON_FILENAMES, size, size)
        if not pixmap.isNull():
            return pixmap
        return QtWidgets.QApplication.style().standardIcon(
            QtWidgets.QStyle.SP_MessageBoxInformation
        ).pixmap(size, size)

    @staticmethod
    def _load_detail_info_icon_pixmap(size: int) -> QtGui.QPixmap:
        pixmap = App._load_info_icon_pixmap(size)
        if not pixmap.isNull():
            normalized = App._normalize_pixmap_size(pixmap, size, size)
            return App._tint_pixmap(normalized, "#4C5052")
        fallback = QtWidgets.QApplication.style().standardIcon(
            QtWidgets.QStyle.SP_MessageBoxInformation
        ).pixmap(size, size)
        normalized = App._normalize_pixmap_size(fallback, size, size)
        return App._tint_pixmap(normalized, "#4C5052")

    @staticmethod
    def _load_arrow_down_icon_pixmap(size: int) -> QtGui.QPixmap:
        pixmap = App._load_svg_pixmap(ARROW_DOWN_ICON_FILENAMES, size, size)
        if not pixmap.isNull():
            return pixmap
        return QtWidgets.QApplication.style().standardIcon(
            QtWidgets.QStyle.SP_ArrowDown
        ).pixmap(size, size)

    @staticmethod
    def _load_arrow_up_icon_pixmap(size: int) -> QtGui.QPixmap:
        pixmap = App._load_svg_pixmap(ARROW_UP_ICON_FILENAMES, size, size)
        if not pixmap.isNull():
            return pixmap
        return App._load_arrow_down_icon_pixmap(size).transformed(
            QtGui.QTransform().rotate(180),
            QtCore.Qt.SmoothTransformation,
        )

    @staticmethod
    def _load_arrow_down_small_icon_pixmap(width: int, height: int) -> QtGui.QPixmap:
        pixmap = App._load_svg_pixmap(ARROW_DOWN_SMALL_ICON_FILENAMES, width, height)
        if not pixmap.isNull():
            return pixmap
        return App._load_arrow_down_icon_pixmap(max(width, height))

    @staticmethod
    def _load_arrow_up_small_icon_pixmap(width: int, height: int) -> QtGui.QPixmap:
        pixmap = App._load_svg_pixmap(ARROW_UP_SMALL_ICON_FILENAMES, width, height)
        if not pixmap.isNull():
            return pixmap
        return App._load_arrow_up_icon_pixmap(max(width, height))

    def open_folder(self, path: Path) -> None:
        if not ensure_directory(path):
            QtWidgets.QMessageBox.critical(
                self,
                APP_NAME,
                f"폴더를 만들거나 열 수 없습니다.\n\n{path}\n\nmacOS 권한 설정을 확인하세요.",
            )
            return
        completed = subprocess.run(
            ["open", str(path)],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode == 0:
            return

        details = completed.stderr.strip() or completed.stdout.strip() or "알 수 없는 오류"
        QtWidgets.QMessageBox.critical(
            self,
            APP_NAME,
            f"폴더를 열지 못했습니다.\n\n{path}\n\n{details}",
        )

    def open_base_dir(self) -> None:
        self.open_folder(DEFAULT_BASE_DIR)

    def open_input_dir(self) -> None:
        self.open_folder(self.input_dir)

    def open_output_dir(self) -> None:
        self.open_folder(self.output_dir)

    def refresh_paths(self) -> None:
        if not save_config(self.input_dir, self.output_dir, self.selected_dpi):
            self.append_log_entry("설정 저장 실패")
        self.update_runtime_summary()

    def append_log(self, message: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        self.log_messages.append(f"[{timestamp}] {message}")
        self.log_messages = self.log_messages[-200:]

    def append_log_entry(self, message: str, file_path: Path | None = None) -> None:
        self.append_log(message)
        if file_path:
            self.show_file_info(file_path)
        self.flush_ui()

    def handle_log_selection(self) -> None:
        return

    @staticmethod
    def flush_ui() -> None:
        app = QtWidgets.QApplication.instance()
        if app:
            app.processEvents()

    def update_history_count(self) -> None:
        return

    def update_transfer_progress(self, percent: int) -> None:
        clamped = max(0, min(100, int(percent)))
        self.current_transfer_percent = clamped
        if self.progress_bar:
            self.progress_bar.setValue(clamped)
        if self.progress_label:
            if clamped >= 100:
                self.progress_label.setText(
                    "<span style='color:#39B95C; font-family:Inter; font-size:9px; font-weight:600;'>"
                    "PNG file successfully created 🎉"
                    "</span>"
                )
            elif (
                clamped <= 0
                and self.status_text == "변환 대기 중"
                and not self.processing_convert_queue
                and not self.pending_convert_paths
            ):
                self.progress_label.setText(
                    "<span style='color:#717171; font-family:Inter; font-size:9px; font-weight:500;'>"
                    "Waiting for SVG file... 👀"
                    "</span>"
                )
            else:
                self.progress_label.setText(
                    "<span style='color:#717171; font-family:Inter; font-size:9px; font-weight:500;'>"
                    "Your file transfer is "
                    "</span>"
                    f"<span style='color:#39B95C; font-family:Inter; font-size:9px; font-weight:600;'>{clamped}%</span>"
                    "<span style='color:#717171; font-family:Inter; font-size:9px; font-weight:500;'>"
                    " completed"
                    "</span>"
                )

    def update_transfer_phase(self, phase_fraction: float) -> None:
        if self.transfer_total_files <= 0:
            self.update_transfer_progress(0)
            return
        total = max(1, self.transfer_total_files)
        completed = min(self.transfer_completed_files, total)
        overall = ((completed + max(0.0, min(1.0, phase_fraction))) / total) * 100.0
        self.current_transfer_fraction = phase_fraction
        self.update_transfer_progress(int(round(overall)))

    def finish_transfer_step(self, success: bool) -> None:
        if self.transfer_total_files <= 0:
            self.update_transfer_progress(100 if success else 0)
            return

        self.transfer_completed_files = min(
            self.transfer_total_files,
            self.transfer_completed_files + 1,
        )
        self.current_transfer_fraction = 0.0
        if self.transfer_completed_files >= self.transfer_total_files:
            self.update_transfer_progress(100 if success else max(self.current_transfer_percent, 100))
            self.transfer_total_files = 0
            self.transfer_completed_files = 0
            return
        self.update_transfer_phase(0.0)

    def set_info_value(self, key: str, value: str) -> None:
        label = self.info_labels.get(key)
        if label:
            label.setText(value)

    def show_file_info(self, file_path: Path) -> None:
        info = self.read_png_info(file_path)
        for key, value in info.items():
            self.set_info_value(key, value)

    def read_png_info(self, file_path: Path) -> dict[str, str]:
        default_info = {
            "파일명": file_path.name,
            "파일 크기": "-",
            "이미지 크기": "-",
            "DPI": "-",
            "색상 모드": "RGB",
            "비트 깊이": "-",
            "PNG 타입": "-",
            "알파 채널": "-",
            "색상 프로파일": "-",
        }
        if not file_path.exists():
            return default_info

        try:
            raw = file_path.read_bytes()
        except OSError:
            return default_info

        if len(raw) < 33 or not raw.startswith(b"\x89PNG\r\n\x1a\n"):
            return default_info

        width = int.from_bytes(raw[16:20], "big")
        height = int.from_bytes(raw[20:24], "big")
        bit_depth = raw[24]
        color_type = raw[25]

        png_type = {
            2: "TrueColor",
            3: "Indexed",
            6: "TrueColorAlpha",
        }.get(color_type, f"타입 {color_type}")
        channels = {
            0: 1,
            2: 3,
            3: 1,
            4: 2,
            6: 4,
        }.get(color_type, 1)

        dpi_text = "-"
        profile_name = "-"
        offset = 8
        while offset + 8 <= len(raw):
            chunk_length = int.from_bytes(raw[offset : offset + 4], "big")
            chunk_type = raw[offset + 4 : offset + 8]
            data_start = offset + 8
            data_end = data_start + chunk_length
            if data_end + 4 > len(raw):
                break

            chunk_data = raw[data_start:data_end]
            if chunk_type == b"pHYs" and chunk_length >= 9 and chunk_data[8] == 1:
                dots_per_meter_x = int.from_bytes(chunk_data[0:4], "big")
                dpi_text = f"{int(round(dots_per_meter_x * 0.0254))} DPI"
            elif chunk_type == b"sRGB":
                profile_name = "sRGB"
            elif chunk_type == b"iCCP" and profile_name == "-":
                profile_name = "ICC 프로파일"

            offset = data_end + 4
            if chunk_type == b"IEND":
                break

        return {
            "파일명": file_path.name,
            "파일 크기": format_file_size(len(raw)),
            "이미지 크기": f"{width} x {height} (pixel)",
            "DPI": dpi_text,
            "색상 모드": "RGB",
            "비트 깊이": f"{bit_depth * channels}bit",
            "PNG 타입": png_type,
            "알파 채널": "있음" if color_type in (4, 6) else "없음",
            "색상 프로파일": profile_name,
        }

    def set_dpi(self, dpi: int | None = None) -> None:
        if dpi is None:
            for candidate, radio in self.dpi_buttons.items():
                if radio.isChecked():
                    dpi = candidate
                    break
        if dpi is None:
            dpi = self.selected_dpi

        self.selected_dpi = normalize_dpi(dpi)
        self.output_dir = output_dir_for_dpi(self.selected_dpi)
        if not ensure_directory(self.output_dir):
            QtWidgets.QMessageBox.warning(
                self,
                APP_NAME,
                f"출력 폴더를 만들지 못했습니다.\n\n{self.output_dir}\n\nmacOS 권한 설정을 확인하세요.",
            )
        self.update_dpi_display()
        self.refresh_paths()

    def set_status(self, message: str) -> None:
        self.status_text = message
        self.setWindowTitle(f"{WINDOW_TITLE} - {message}")
        animated_base: str | None = None
        if message == "변환 진행 중":
            animated_base = "processing"

        if animated_base:
            self.status_animation_base = animated_base
            self.status_animation_step = 0
            if self.status_animation_timer and not self.status_animation_timer.isActive():
                self.status_animation_timer.start()
            self._tick_status_animation()
            return

        if self.status_animation_timer and self.status_animation_timer.isActive():
            self.status_animation_timer.stop()

        if self.status_label:
            self.status_label.setText(message)

        if (
            message == "변환 대기 중"
            and self.current_transfer_percent <= 0
            and not self.processing_convert_queue
            and not self.pending_convert_paths
        ):
            self.update_transfer_progress(0)

    def show_dependency_warning(self) -> None:
        install_text = (
            "필수 도구가 설치되지 않았습니다.\n\n"
            f"누락: {', '.join(self.tool_paths.missing)}\n\n"
            "권장 설치 방법:\n"
            "1) Homebrew 설치\n"
            "2) 터미널에서 아래 실행\n"
            "   brew install --cask inkscape\n\n"
            "앱은 /opt/homebrew/bin, /usr/local/bin, /usr/bin, /bin 과 앱 내부 번들을 자동으로 확인합니다."
        )
        self.set_status("의존성 설치 필요")
        QtCore.QTimer.singleShot(
            250,
            lambda: QtWidgets.QMessageBox.warning(self, APP_NAME, install_text),
        )

    def refresh_tools(self) -> bool:
        self.tool_paths = detect_tools()
        self.update_runtime_summary()
        if self.tool_paths.missing:
            self.show_dependency_warning()
            return False
        return True

    def start_watch(self) -> None:
        if self.observer:
            self.set_status("변환 대기 중")
            return

        if not self.refresh_tools():
            return

        if not ensure_directory(self.input_dir) or not ensure_directory(self.output_dir):
            self.set_status("폴더 권한 필요")
            QtWidgets.QMessageBox.warning(
                self,
                APP_NAME,
                "입력 또는 출력 폴더를 준비하지 못했습니다.\n\nmacOS 권한 설정을 확인하세요.",
            )
            return

        while not self.event_queue.empty():
            try:
                self.event_queue.get_nowait()
            except queue.Empty:
                break

        for timer in self.debounce_jobs.values():
            timer.stop()
            timer.deleteLater()
        self.debounce_jobs.clear()
        self.pending_convert_paths.clear()
        self.pending_convert_keys.clear()
        self.processing_convert_queue = False
        self.last_processed_svg_mtimes.clear()
        self.watch_started_at = time.time()
        self.rescan_timer.stop()

        self.observer = Observer()
        handler = SvgEventHandler(self.enqueue_event)
        self.observer.schedule(handler, str(self.input_dir), recursive=True)
        self.observer.start()
        self.set_status("변환 대기 중")

    def stop_watch(self) -> None:
        observer = self.observer
        if not observer:
            self.set_status("변환 중지됨")
            return

        self.observer = None
        observer.stop()
        observer.join(timeout=3)
        self.pending_convert_paths.clear()
        self.pending_convert_keys.clear()
        self.processing_convert_queue = False
        self.last_processed_svg_mtimes.clear()
        self.rescan_timer.stop()
        self.set_status("변환 중지됨")

    def enqueue_event(self, path: Path) -> None:
        if is_svg_file(path):
            self.event_queue.put(path)

    def poll_events(self) -> None:
        saw_events = False
        while True:
            try:
                path = self.event_queue.get_nowait()
            except queue.Empty:
                break
            saw_events = True
            self.schedule_debounced_convert(path)
        if saw_events:
            self.rescan_timer.start(1200)

    def reconcile_recent_svgs(self) -> None:
        if not self.input_dir.exists():
            return

        threshold = self.watch_started_at - 0.5 if self.watch_started_at else 0.0
        for path in self.input_dir.rglob("*"):
            if not path.is_file() or not is_svg_file(path):
                continue

            try:
                svg_mtime = path.stat().st_mtime
            except OSError:
                continue

            if svg_mtime < threshold:
                continue

            output_path = self.output_dir / f"{path.stem}.png"
            try:
                output_mtime = output_path.stat().st_mtime if output_path.exists() else 0.0
            except OSError:
                output_mtime = 0.0

            if not output_path.exists() or output_mtime < svg_mtime:
                self.schedule_debounced_convert(path)

    def schedule_debounced_convert(self, path: Path) -> None:
        path_key = str(path)
        try:
            current_mtime = path.stat().st_mtime
        except OSError:
            return

        if self.last_processed_svg_mtimes.get(path_key) == current_mtime:
            return

        existing_timer = self.debounce_jobs.pop(path_key, None)
        if existing_timer:
            existing_timer.stop()
            existing_timer.deleteLater()

        timer = QtCore.QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda p=path: self._run_debounced_convert(p))
        self.debounce_jobs[path_key] = timer
        timer.start(700)

    def _run_debounced_convert(self, path: Path) -> None:
        self.debounce_jobs.pop(str(path), None)
        self.enqueue_conversion_request(path)

    def enqueue_conversion_request(self, path: Path) -> None:
        path_key = str(path)
        if path_key in self.pending_convert_keys:
            return

        if not self.processing_convert_queue and not self.pending_convert_paths:
            self.transfer_total_files = 0
            self.transfer_completed_files = 0
            self.current_transfer_fraction = 0.0
            self.update_transfer_progress(0)

        self.pending_convert_paths.append(path)
        self.pending_convert_keys.add(path_key)
        self.transfer_total_files += 1
        self.update_transfer_phase(0.0)

        if not self.processing_convert_queue:
            QtCore.QTimer.singleShot(0, self.process_next_conversion)

    def process_next_conversion(self) -> None:
        if self.processing_convert_queue:
            return
        if not self.pending_convert_paths:
            return

        path = self.pending_convert_paths.pop(0)
        self.pending_convert_keys.discard(str(path))
        self.processing_convert_queue = True
        try:
            self.convert_svg(path)
        finally:
            self.processing_convert_queue = False
            if self.pending_convert_paths:
                QtCore.QTimer.singleShot(0, self.process_next_conversion)

    def bring_to_front_if_needed(self) -> None:
        app = QtWidgets.QApplication.instance()
        is_minimized = bool(self.windowState() & QtCore.Qt.WindowMinimized)
        if not is_minimized and self.isActiveWindow():
            return

        if is_minimized:
            self.setWindowState(self.windowState() & ~QtCore.Qt.WindowMinimized)
            self.showNormal()
        elif not self.isVisible():
            self.show()

        self.raise_()
        self.activateWindow()
        if app:
            app.setActiveWindow(self)
            app.processEvents()

    def convert_svg(self, svg_path: Path, show_message: bool = False) -> bool:
        if not svg_path.exists():
            return False
        if not is_svg_file(svg_path):
            return False
        if not self.refresh_tools():
            return False

        if not self.worker_lock.acquire(blocking=False):
            self.set_status("변환 진행 중")
            return False

        try:
            self.bring_to_front_if_needed()
            current_mtime = svg_path.stat().st_mtime
            ensure_directory(self.output_dir)
            output_path = self.output_dir / f"{svg_path.stem}.png"
            self.set_status("변환 진행 중")
            self.update_transfer_phase(0.08)
            self.flush_ui()
            self.run_pipeline(svg_path, output_path)
            self.last_processed_svg_mtimes[str(svg_path)] = current_mtime
            self.finish_transfer_step(True)
            self.append_log_entry(f"변환 완료 {svg_path.name}", output_path)
            self.show_file_info(output_path)
            self.flush_ui()
            if self.observer and not self.pending_convert_paths:
                self.set_status("변환 대기 중")
            elif not self.observer:
                self.set_status("변환 중지됨")
            if show_message:
                QtWidgets.QMessageBox.information(
                    self,
                    APP_NAME,
                    f"변환 완료\n\n{output_path}",
                )
            return True
        except Exception as exc:  # noqa: BLE001
            self.set_status("변환 실패")
            self.finish_transfer_step(False)
            self.append_log_entry(f"변환 실패 {svg_path.name}")
            self.flush_ui()
            QtWidgets.QMessageBox.critical(
                self,
                APP_NAME,
                f"{svg_path.name} 변환에 실패했습니다.\n\n{exc}",
            )
            if self.observer and not self.pending_convert_paths:
                QtCore.QTimer.singleShot(450, lambda: self.set_status("변환 대기 중"))
            return False
        finally:
            self.worker_lock.release()

    def run_pipeline(self, svg_path: Path, output_path: Path) -> None:
        if not self.tool_paths.inkscape:
            raise RuntimeError("Inkscape 경로를 확인할 수 없습니다.")

        env = os.environ.copy()
        env["PATH"] = build_augmented_path()
        self.update_transfer_phase(0.2)

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp_file:
            temp_png = Path(tmp_file.name)

        try:
            inkscape_command = [
                self.tool_paths.inkscape,
                str(svg_path),
                "--export-type=png",
                f"--export-filename={temp_png}",
                "--export-background-opacity=0",
            ]
            self.run_command(inkscape_command, env)
            self.update_transfer_phase(0.72)

            self.finalize_png(temp_png, output_path)
            self.update_transfer_phase(0.96)
        finally:
            if temp_png.exists():
                temp_png.unlink()

    def finalize_png(self, temp_png: Path, output_path: Path) -> None:
        image = QtGui.QImage(str(temp_png))
        if image.isNull():
            raise RuntimeError("임시 PNG를 불러오지 못했습니다.")
        self.update_transfer_phase(0.8)

        rgba_image = image.convertToFormat(QtGui.QImage.Format_RGBA8888)
        rgba_image.setColorSpace(QtGui.QColorSpace(QtGui.QColorSpace.NamedColorSpace.SRgb))
        dots_per_meter = int(round(self.selected_dpi / 0.0254))
        rgba_image.setDotsPerMeterX(dots_per_meter)
        rgba_image.setDotsPerMeterY(dots_per_meter)
        self.update_transfer_phase(0.9)

        if not rgba_image.save(str(output_path), "PNG"):
            raise RuntimeError("최종 PNG 저장에 실패했습니다.")

    @staticmethod
    def run_command(command: list[str], env: dict[str, str]) -> None:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )
        if completed.returncode == 0:
            return

        stderr = completed.stderr.strip()
        stdout = completed.stdout.strip()
        details = stderr or stdout or "알 수 없는 오류"
        raise RuntimeError(f"{command[0]} 실행 실패\n\n{details}")

    def closeEvent(self, event) -> None:  # noqa: N802
        self.stop_requested = True
        self.hide_info_tooltip()
        if self.observer:
            self.stop_watch()
        event.accept()

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.position_version_label()


def main() -> None:
    application = QtWidgets.QApplication(sys.argv)
    application.setApplicationName(APP_NAME)
    application.setApplicationVersion(APP_VERSION)
    window = App()
    window.show()
    sys.exit(application.exec())


if __name__ == "__main__":
    main()
