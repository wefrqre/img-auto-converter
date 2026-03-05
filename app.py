#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
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
CONFIG_PATH = Path.home() / ".applied_image_auto_converter.json"
APP_ICON_FILENAME = "app_icon.png"
HERO_ICON_BOX_SIZE = 42
HERO_ICON_WIDTH = 35
HERO_ICON_HEIGHT = 34
INFO_ICON_FILENAMES = ("info.svg", "Info.svg")
ARROW_DOWN_ICON_FILENAMES = ("arrow_down.svg", "Arrow_down.svg")
FOLDER_ICON_FILENAMES = ("vector.svg", "Vector.svg")
INFO_TOOLTIP_TEXT = "Figma에서 SVG 파일을 폴더에 저장하면\nPNG로 자동 변환됩니다."
DEFAULT_BASE_DIR = Path.home() / "Desktop" / "figma_exports"
DEFAULT_INPUT_DIR = DEFAULT_BASE_DIR / "svg"
DEFAULT_DPI = 96
DPI_OPTIONS = (96, 192)
PATH_HINTS = ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"]
WATCH_EXTENSIONS = {".svg"}
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


def load_config() -> dict[str, object]:
    if not CONFIG_PATH.exists():
        return {}

    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_config(input_dir: Path, output_dir: Path, dpi: int) -> bool:
    payload = {
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "dpi": dpi,
    }
    try:
        CONFIG_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return True
    except OSError:
        return False


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


def format_file_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.2f} MB"


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
    def __init__(self, text: str, outlined: bool, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.outlined = outlined
        self.suffix_icon: QtGui.QPixmap | None = None
        self.suffix_icon_gap = 8
        self.setFixedHeight(30)
        self.setMinimumHeight(30)
        self.setMaximumHeight(30)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setFocusPolicy(QtCore.Qt.NoFocus)
        self.setFlat(True)

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

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # noqa: N802
        del event
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)

        rect = self.rect().adjusted(0, 0, -1, -1)
        radius = rect.height() / 2.0

        if self.outlined:
            if self.isDown():
                background = QtGui.QColor("#EFF1F5")
            elif self.underMouse():
                background = QtGui.QColor("#F8F9FB")
            else:
                background = QtGui.QColor("#FFFFFF")
            border_color = QtGui.QColor("#E6E7ED")
            painter.setPen(QtGui.QPen(border_color, 1))
        else:
            if self.isDown():
                background = QtGui.QColor("#E1E3E8")
            elif self.underMouse():
                background = QtGui.QColor("#E9EAEE")
            else:
                background = QtGui.QColor("#F0F1F3")
            painter.setPen(QtCore.Qt.NoPen)

        painter.setBrush(background)
        painter.drawRoundedRect(rect, radius, radius)

        painter.setPen(QtGui.QColor("#6F6F6F"))
        font = painter.font()
        font.setPointSize(12)
        font.setWeight(QtGui.QFont.DemiBold)
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
            QLabel#tooltipText { color: #F7F7F8; font-size: 12px; font-weight: 400; background: transparent; }
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
        fm = QtGui.QFontMetrics(QtGui.QFont(self.font().family(), 12))
        target_width = max(1, max(fm.horizontalAdvance(line) for line in lines))
        self.text_label.setFixedWidth(target_width)
        safe_text = (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("\n", "<br/>")
        )
        self.text_label.setText(
            f'<div style="line-height:18px; color:#F7F7F8; font-size:12px;">{safe_text}</div>'
        )


class App(QtWidgets.QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(400, 440)
        self.setMinimumWidth(400)
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
        self.info_icon_label: HoverIconLabel | None = None
        self.info_tooltip: InfoTooltip | None = None
        self.folder_button: QtWidgets.QPushButton | None = None
        self.stop_button: QtWidgets.QPushButton | None = None
        self.dpi_button: QtWidgets.QPushButton | None = None
        self.dpi_value_label: QtWidgets.QLabel | None = None
        self.dpi_buttons: dict[int, QtWidgets.QRadioButton] = {}
        self.status_animation_timer: QtCore.QTimer | None = None
        self.status_animation_step = 0
        self.status_animation_base = ""
        self.log_list: QtWidgets.QListWidget | None = None
        self.log_stack: QtWidgets.QStackedLayout | None = None
        self.log_empty_label: QtWidgets.QLabel | None = None
        self.log_items_by_file: dict[str, QtWidgets.QListWidgetItem] = {}
        self.history_count_label: QtWidgets.QLabel | None = None
        self.info_labels: dict[str, QtWidgets.QLabel] = {}
        self.bottom_card: QtWidgets.QFrame | None = None
        self.bottom_layout: QtWidgets.QVBoxLayout | None = None
        self.bottom_toggle_row: ClickableFrame | None = None
        self.bottom_content_widget: QtWidgets.QWidget | None = None
        self.bottom_toggle_icon: QtWidgets.QLabel | None = None
        self.inline_toggle_button: QtWidgets.QToolButton | None = None
        self.bottom_collapsed = False
        self.expanded_window_height = 440
        self.startup_warning: str | None = None

        self.first_launch_setup()
        self.build_ui()
        self.set_status("변환 중지됨")
        self.refresh_paths()

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
            created_text = "\n".join(str(path) for path in created)
            QtCore.QTimer.singleShot(
                200,
                lambda: QtWidgets.QMessageBox.information(
                    self,
                    APP_NAME,
                    "초기 폴더를 만들었습니다.\n\n"
                    f"{created_text}\n\n"
                    "앞으로 SVG는 입력 폴더에 넣으면 자동 변환됩니다.",
                ),
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

    def dependency_summary(self) -> str:
        if self.tool_paths.inkscape:
            return "의존성 상태: Inkscape 확인됨 / PNG 후처리 내장"
        return "의존성 상태: Inkscape 필요"

    def build_ui(self) -> None:
        self.setStyleSheet(
            """
            QWidget {
                background: #f3f4f7;
                color: #3b3b3f;
                font-family: "Helvetica Neue";
            }
            QLabel {
                background: transparent;
            }
            QLabel#heroTitle {
                color: #9E9E9E;
                font-size: 12px;
                font-weight: 600;
            }
            QLabel#statusValue {
                color: #585E60;
                font-size: 17px;
                font-weight: 700;
            }
            QLabel#dpiValue {
                color: #9E9E9E;
                font-size: 12px;
                font-weight: 400;
            }
            QLabel#panelTitle {
                color: #6d6d72;
                font-size: 12px;
                font-weight: 600;
            }
            QFrame#collapseRow {
                background: transparent;
                border: none;
                border-radius: 0px;
            }
            QLabel#collapseTitle {
                color: #585E60;
                font-size: 12px;
                font-weight: 600;
                background: transparent;
            }
            QLabel#logEmpty {
                color: #9E9E9E;
                font-size: 10px;
                font-weight: 400;
                background: transparent;
            }
            QFrame#panelCard {
                background: #ffffff;
                border: none;
                border-radius: 12px;
            }
            QFrame#card {
                background: transparent;
                border: none;
                border-radius: 0px;
            }
            QWidget#historyHeader {
                background: transparent;
            }
            QWidget#infoHeader {
                background: transparent;
            }
            QWidget#contentColumn {
                background: transparent;
            }
            QWidget#bottomContent {
                background: transparent;
            }
            QWidget#logStackHost {
                background: transparent;
            }
            QRadioButton {
                background: transparent;
                color: #6F6F6F;
                font-size: 12px;
                font-weight: 400;
            }
            QListWidget#selectionList {
                background: transparent;
                border: none;
                font-family: Menlo;
                font-size: 11px;
            }
            QListWidget#selectionList::item {
                padding: 6px 0px;
                margin: 0px 0px;
            }
            QListWidget#selectionList::item:selected {
                background: #eaf3ff;
                color: #111111;
                border-radius: 8px;
                margin: 0px 0px;
            }
            QScrollBar:vertical {
                background: transparent;
                width: 3px;
                margin: 2px 0px;
            }
            QScrollBar::handle:vertical {
                background: #c7c7cc;
                min-height: 24px;
                border-radius: 20px;
            }
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {
                height: 0px;
                background: transparent;
                border: none;
            }
            QScrollBar::add-page:vertical,
            QScrollBar::sub-page:vertical {
                background: transparent;
            }
            QLabel#infoKey {
                color: #6b7280;
                font-size: 11px;
                font-weight: 600;
            }
            QLabel#infoValue {
                color: #111111;
                font-size: 11px;
            }
            """
        )

        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(14, 12, 14, 12)
        main_layout.setSpacing(12)

        top_card = QtWidgets.QFrame()
        top_card.setObjectName("panelCard")
        top_layout = QtWidgets.QVBoxLayout(top_card)
        top_layout.setContentsMargins(16, 18, 16, 14)
        top_layout.setSpacing(0)

        hero_row = QtWidgets.QHBoxLayout()
        hero_row.setContentsMargins(0, 0, 0, 0)
        hero_row.setSpacing(12)
        hero_row.addWidget(self._build_hero_icon(), 0, QtCore.Qt.AlignTop)

        hero_text = QtWidgets.QVBoxLayout()
        hero_text.setContentsMargins(0, 0, 0, 0)
        hero_text.setSpacing(2)

        title_row = QtWidgets.QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(4)

        hero_title = QtWidgets.QLabel("응용 이미지 변환기")
        hero_title.setObjectName("heroTitle")
        title_row.addWidget(hero_title, 0, QtCore.Qt.AlignVCenter)

        self.info_icon_label = HoverIconLabel()
        self.info_icon_label.setFixedSize(16, 16)
        self.info_icon_label.setAlignment(QtCore.Qt.AlignCenter)
        self.info_icon_label.setPixmap(self._load_info_icon_pixmap(16))
        self.info_icon_label.setCursor(QtCore.Qt.PointingHandCursor)
        self.info_icon_label.hovered.connect(self.show_info_tooltip)
        self.info_icon_label.unhovered.connect(self.hide_info_tooltip)
        title_row.addWidget(self.info_icon_label, 0, QtCore.Qt.AlignVCenter)
        title_row.addStretch(1)
        hero_text.addLayout(title_row)

        status_row = QtWidgets.QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.setSpacing(8)

        self.status_label = QtWidgets.QLabel()
        self.status_label.setObjectName("statusValue")
        self.status_label.setAlignment(QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft)
        status_row.addWidget(self.status_label, 0, QtCore.Qt.AlignVCenter)

        status_row.addStretch(1)

        self.dpi_value_label = QtWidgets.QLabel()
        self.dpi_value_label.setObjectName("dpiValue")
        self.dpi_value_label.setAlignment(QtCore.Qt.AlignVCenter | QtCore.Qt.AlignRight)
        status_row.addWidget(self.dpi_value_label, 0, QtCore.Qt.AlignVCenter)
        hero_text.addLayout(status_row)
        hero_text.addStretch(1)

        hero_row.addLayout(hero_text, 1)
        top_layout.addLayout(hero_row)
        top_layout.addSpacing(26)

        button_row = QtWidgets.QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.setSpacing(8)
        self.dpi_button = PillButton("DPI 설정", outlined=True)
        self.dpi_button.set_suffix_icon(self._load_arrow_down_icon_pixmap(8), width=8, gap=8)
        self.dpi_button.clicked.connect(self.show_dpi_menu)
        button_row.addWidget(self.dpi_button, 1)

        self.folder_button = PillButton("폴더 열기", outlined=True)
        self.folder_button.clicked.connect(self.open_base_dir)
        button_row.addWidget(self.folder_button, 1)

        self.stop_button = PillButton("변환 시작", outlined=False)
        self.stop_button.clicked.connect(self.toggle_watch)
        button_row.addWidget(self.stop_button, 1)
        top_layout.addLayout(button_row)

        main_layout.addWidget(top_card, 0)

        self.bottom_card = QtWidgets.QFrame()
        self.bottom_card.setObjectName("panelCard")
        self.bottom_layout = QtWidgets.QVBoxLayout(self.bottom_card)
        self.bottom_layout.setContentsMargins(16, 12, 16, 10)
        self.bottom_layout.setSpacing(8)

        self.bottom_toggle_row = ClickableFrame()
        self.bottom_toggle_row.setObjectName("collapseRow")
        self.bottom_toggle_row.setFixedHeight(46)
        self.bottom_toggle_row.setCursor(QtCore.Qt.PointingHandCursor)
        toggle_layout = QtWidgets.QHBoxLayout(self.bottom_toggle_row)
        toggle_layout.setContentsMargins(16, 0, 16, 0)
        toggle_layout.setSpacing(8)

        toggle_title = QtWidgets.QLabel("변환 내역 / 파일 정보")
        toggle_title.setObjectName("collapseTitle")
        toggle_layout.addWidget(toggle_title, 0, QtCore.Qt.AlignVCenter)
        toggle_layout.addStretch(1)

        self.bottom_toggle_icon = QtWidgets.QLabel()
        self.bottom_toggle_icon.setFixedSize(18, 18)
        self.bottom_toggle_icon.setAlignment(QtCore.Qt.AlignCenter)
        toggle_layout.addWidget(self.bottom_toggle_icon, 0, QtCore.Qt.AlignVCenter)

        self.bottom_toggle_row.clicked.connect(self.toggle_bottom_section)
        self.bottom_layout.addWidget(self.bottom_toggle_row, 0)

        self.bottom_content_widget = QtWidgets.QWidget()
        self.bottom_content_widget.setObjectName("bottomContent")
        bottom_content_layout = QtWidgets.QVBoxLayout(self.bottom_content_widget)
        bottom_content_layout.setContentsMargins(0, 0, 0, 0)
        bottom_content_layout.setSpacing(0)

        content_row = QtWidgets.QHBoxLayout()
        content_row.setContentsMargins(0, 0, 0, 0)
        content_row.setSpacing(4)

        left_col = QtWidgets.QVBoxLayout()
        left_col.setContentsMargins(0, 0, 0, 0)
        left_col.setSpacing(0)

        history_header_widget = ClickableFrame()
        history_header_widget.setObjectName("historyHeader")
        history_header_widget.setFixedHeight(20)
        history_header_widget.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed
        )
        history_header_widget.setCursor(QtCore.Qt.PointingHandCursor)
        history_header_widget.clicked.connect(self.toggle_bottom_section)
        history_header = QtWidgets.QHBoxLayout(history_header_widget)
        history_header.setContentsMargins(0, 0, 0, 0)
        history_header.setSpacing(0)

        history_title = QtWidgets.QLabel("변환 내역")
        history_title.setObjectName("panelTitle")
        history_title.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        history_header.addWidget(history_title, 0, QtCore.Qt.AlignVCenter)
        history_header.addSpacing(4)

        self.history_count_label = QtWidgets.QLabel()
        self.history_count_label.setStyleSheet(
            "color: #9D9D9D; font-size: 12px; font-weight: 600;"
        )
        self.history_count_label.setSizePolicy(
            QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed
        )
        self.history_count_label.setContentsMargins(0, 0, 0, 0)
        self.history_count_label.hide()
        history_header.addWidget(self.history_count_label, 0, QtCore.Qt.AlignVCenter)
        history_header.addStretch(1)
        left_col.addWidget(history_header_widget, 0)
        left_col.addSpacing(4)

        log_card = self._build_card()
        log_card.setFixedHeight(236)
        log_layout = QtWidgets.QVBoxLayout(log_card)
        log_layout.setContentsMargins(0, 8, 16, 14)
        log_layout.setSpacing(0)

        log_stack_host = QtWidgets.QWidget()
        log_stack_host.setObjectName("logStackHost")
        log_stack_host.setStyleSheet("background: transparent;")
        log_stack_host.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        self.log_stack = QtWidgets.QStackedLayout(log_stack_host)
        self.log_stack.setContentsMargins(0, 0, 0, 0)
        self.log_stack.setSpacing(0)

        self.log_empty_label = QtWidgets.QLabel("변환 내역이 없습니다.")
        self.log_empty_label.setObjectName("logEmpty")
        self.log_empty_label.setAlignment(QtCore.Qt.AlignCenter)
        self.log_empty_label.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding
        )
        self.log_empty_label.setStyleSheet(
            "QLabel#logEmpty { background: transparent; color: #9E9E9E; font-size: 10px; font-weight: 400; }"
        )
        self.log_stack.addWidget(self.log_empty_label)

        self.log_list = QtWidgets.QListWidget()
        self.log_list.setObjectName("selectionList")
        self.log_list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.log_list.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.log_list.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.log_list.itemSelectionChanged.connect(self.handle_log_selection)
        self.log_stack.addWidget(self.log_list)
        log_layout.addWidget(log_stack_host)
        left_col.addWidget(log_card, 0)
        left_col.addStretch(1)
        left_panel = QtWidgets.QWidget()
        left_panel.setObjectName("contentColumn")
        left_panel.setMinimumWidth(0)
        left_panel.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        left_panel.setLayout(left_col)
        content_row.addWidget(left_panel, 1)

        right_col = QtWidgets.QVBoxLayout()
        right_col.setContentsMargins(0, 0, 0, 0)
        right_col.setSpacing(0)

        info_header_widget = ClickableFrame()
        info_header_widget.setObjectName("infoHeader")
        info_header_widget.setFixedHeight(20)
        info_header_widget.setCursor(QtCore.Qt.PointingHandCursor)
        info_header_widget.clicked.connect(self.toggle_bottom_section)
        info_header = QtWidgets.QHBoxLayout(info_header_widget)
        info_header.setContentsMargins(0, 0, 0, 0)
        info_header.setSpacing(0)

        info_title = QtWidgets.QLabel("파일 정보")
        info_title.setObjectName("panelTitle")
        info_header.addWidget(info_title, 0, QtCore.Qt.AlignVCenter)
        info_header.addStretch(1)
        self.inline_toggle_button = QtWidgets.QToolButton()
        self.inline_toggle_button.setAutoRaise(True)
        self.inline_toggle_button.setCursor(QtCore.Qt.PointingHandCursor)
        self.inline_toggle_button.setFixedSize(18, 18)
        self.inline_toggle_button.setIconSize(QtCore.QSize(18, 18))
        self.inline_toggle_button.setStyleSheet(
            "QToolButton { background: transparent; border: none; padding: 0px; margin: 0px; }"
            "QToolButton:hover { background: transparent; }"
            "QToolButton:pressed { background: transparent; }"
        )
        self.inline_toggle_button.clicked.connect(self.toggle_bottom_section)
        info_header.addWidget(self.inline_toggle_button, 0, QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        right_col.addWidget(info_header_widget, 0)
        right_col.addSpacing(4)

        info_card = self._build_card()
        info_card.setFixedHeight(236)
        info_layout = QtWidgets.QGridLayout(info_card)
        info_layout.setContentsMargins(0, 12, 12, 14)
        info_layout.setHorizontalSpacing(12)
        info_layout.setVerticalSpacing(6)

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
            key_label.setObjectName("infoKey")
            key_label.setFixedHeight(22)
            key_label.setAlignment(QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft)
            key_label.setContentsMargins(0, 0, 0, 0)
            value_label = QtWidgets.QLabel("-")
            value_label.setObjectName("infoValue")
            value_label.setFixedHeight(22)
            value_label.setAlignment(QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft)
            value_label.setWordWrap(False)
            value_label.setContentsMargins(0, 0, 0, 0)
            value_label.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Fixed)
            value_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            info_layout.addWidget(key_label, row, 0, QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft)
            info_layout.addWidget(value_label, row, 1, QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft)
            info_layout.setRowMinimumHeight(row, 22)
            self.info_labels[field] = value_label
        info_layout.setColumnStretch(1, 1)
        right_col.addWidget(info_card, 0)
        right_col.addStretch(1)
        right_panel = QtWidgets.QWidget()
        right_panel.setObjectName("contentColumn")
        right_panel.setMinimumWidth(0)
        right_panel.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        right_panel.setLayout(right_col)
        content_row.addWidget(right_panel, 1)
        content_row.setStretch(0, 1)
        content_row.setStretch(1, 1)

        bottom_content_layout.addLayout(content_row)
        self.bottom_layout.addWidget(self.bottom_content_widget, 1)
        main_layout.addWidget(self.bottom_card, 1)

        self.status_animation_timer = QtCore.QTimer(self)
        self.status_animation_timer.setInterval(420)
        self.status_animation_timer.timeout.connect(self._tick_status_animation)
        self.update_dpi_display()
        self.update_history_count()
        self.set_bottom_collapsed(True)

    def show_dpi_menu(self) -> None:
        if not self.dpi_button:
            return
        menu = QtWidgets.QMenu(self)
        for value in DPI_OPTIONS:
            action = menu.addAction(f"{value} DPI")
            action.setCheckable(True)
            action.setChecked(self.selected_dpi == value)
            action.triggered.connect(lambda _checked=False, v=value: self.set_dpi(v))
        menu.exec(self.dpi_button.mapToGlobal(QtCore.QPoint(0, self.dpi_button.height())))

    def update_dpi_display(self) -> None:
        if self.dpi_value_label:
            self.dpi_value_label.setText(f"{self.selected_dpi} DPI")

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
        previous_state = self.bottom_collapsed
        self.bottom_collapsed = collapsed
        if not self.bottom_card or not self.bottom_layout:
            return

        base_pixmap = self._load_arrow_down_icon_pixmap(18)
        up_pixmap = base_pixmap.transformed(
            QtGui.QTransform().rotate(180), QtCore.Qt.SmoothTransformation
        )

        if self.bottom_toggle_icon:
            self.bottom_toggle_icon.setPixmap(base_pixmap)

        if self.inline_toggle_button:
            self.inline_toggle_button.setIcon(QtGui.QIcon(up_pixmap))

        if collapsed:
            if not previous_state:
                self.expanded_window_height = max(self.height(), self.expanded_window_height)

            if self.inline_toggle_button:
                self.inline_toggle_button.hide()
            if self.bottom_toggle_row:
                self.bottom_toggle_row.show()
            if self.bottom_content_widget:
                self.bottom_content_widget.hide()

            self.bottom_layout.setContentsMargins(0, 0, 0, 0)
            self.bottom_layout.setSpacing(0)
            self.bottom_card.setFixedHeight(46)
            layout = self.layout()
            if layout:
                layout.activate()
                collapsed_height = max(layout.sizeHint().height(), self.minimumSizeHint().height())
                self.setMinimumHeight(collapsed_height)
                self.resize(self.width(), collapsed_height)
            return

        if self.bottom_content_widget:
            self.bottom_content_widget.show()
        if self.bottom_toggle_row:
            self.bottom_toggle_row.hide()
        if self.inline_toggle_button:
            self.inline_toggle_button.show()
        self.bottom_layout.setContentsMargins(16, 12, 16, 10)
        self.bottom_layout.setSpacing(8)
        self.bottom_card.setMinimumHeight(0)
        self.bottom_card.setMaximumHeight(16777215)
        layout = self.layout()
        if layout:
            layout.activate()
            expanded_height = max(
                self.expanded_window_height,
                layout.sizeHint().height(),
                self.minimumSizeHint().height(),
                440,
            )
            self.setMinimumHeight(expanded_height)
            self.resize(self.width(), expanded_height)

    def toggle_watch(self) -> None:
        if self.observer:
            self.stop_watch()
            return
        self.start_watch()

    def _tick_status_animation(self) -> None:
        if not self.status_label:
            return
        self.status_animation_step = (self.status_animation_step % 3) + 1
        dots = "." * self.status_animation_step
        self.status_label.setText(f"{self.status_animation_base}{dots}")

    def _build_hero_icon(self) -> QtWidgets.QFrame:
        frame = QtWidgets.QFrame()
        frame.setFixedSize(HERO_ICON_BOX_SIZE, HERO_ICON_BOX_SIZE)
        frame.setStyleSheet(
            "QFrame { background: #E4EFFF; border: none; border-radius: 13px; }"
        )

        layout = QtWidgets.QVBoxLayout(frame)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        label = QtWidgets.QLabel()
        label.setFixedSize(HERO_ICON_WIDTH, HERO_ICON_HEIGHT)
        label.setAlignment(QtCore.Qt.AlignCenter)
        label.setPixmap(self._load_logo_pixmap(HERO_ICON_WIDTH, HERO_ICON_HEIGHT))
        layout.addWidget(label, 0, QtCore.Qt.AlignCenter)
        return frame

    @staticmethod
    def _load_logo_pixmap(width: int = HERO_ICON_WIDTH, height: int = HERO_ICON_HEIGHT) -> QtGui.QPixmap:
        icon_path = resource_path(APP_ICON_FILENAME)
        if icon_path.exists():
            pixmap = QtGui.QPixmap(str(icon_path))
            if not pixmap.isNull():
                return pixmap.scaled(
                    width,
                    height,
                    QtCore.Qt.KeepAspectRatio,
                    QtCore.Qt.SmoothTransformation,
                )
        fallback = App._create_logo_pixmap(max(width, height))
        return fallback.scaled(
            width,
            height,
            QtCore.Qt.KeepAspectRatio,
            QtCore.Qt.SmoothTransformation,
        )

    @staticmethod
    def _load_info_icon_pixmap(size: int) -> QtGui.QPixmap:
        for filename in INFO_ICON_FILENAMES:
            icon_path = resource_path(filename)
            if icon_path.exists():
                icon = QtGui.QIcon(str(icon_path))
                if not icon.isNull():
                    return icon.pixmap(size, size)
        return QtWidgets.QApplication.style().standardIcon(
            QtWidgets.QStyle.SP_MessageBoxInformation
        ).pixmap(size, size)

    @staticmethod
    def _load_arrow_down_icon_pixmap(size: int) -> QtGui.QPixmap:
        for filename in ARROW_DOWN_ICON_FILENAMES:
            icon_path = resource_path(filename)
            if icon_path.exists():
                icon = QtGui.QIcon(str(icon_path))
                if not icon.isNull():
                    return icon.pixmap(size, size)
        return QtWidgets.QApplication.style().standardIcon(
            QtWidgets.QStyle.SP_ArrowDown
        ).pixmap(size, size)

    @staticmethod
    def _create_logo_pixmap(size: int) -> QtGui.QPixmap:
        pixmap = QtGui.QPixmap(size, size)
        pixmap.fill(QtCore.Qt.transparent)

        painter = QtGui.QPainter(pixmap)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)

        shadow_path = QtGui.QPainterPath()
        shadow_path.moveTo(size * 0.18, size * 0.33)
        shadow_path.lineTo(size * 0.44, size * 0.08)
        shadow_path.lineTo(size * 0.83, size * 0.44)
        shadow_path.lineTo(size * 0.54, size * 0.82)
        shadow_path.lineTo(size * 0.14, size * 0.46)
        shadow_path.closeSubpath()
        painter.fillPath(shadow_path.translated(0, size * 0.03), QtGui.QColor(0, 0, 0, 24))

        main_path = QtGui.QPainterPath()
        main_path.moveTo(size * 0.16, size * 0.34)
        main_path.lineTo(size * 0.44, size * 0.10)
        main_path.lineTo(size * 0.84, size * 0.44)
        main_path.lineTo(size * 0.56, size * 0.80)
        main_path.lineTo(size * 0.14, size * 0.47)
        main_path.closeSubpath()

        gradient = QtGui.QLinearGradient(size * 0.15, size * 0.1, size * 0.84, size * 0.8)
        gradient.setColorAt(0.0, QtGui.QColor("#6fb6ff"))
        gradient.setColorAt(0.35, QtGui.QColor("#2b82ee"))
        gradient.setColorAt(1.0, QtGui.QColor("#1a62d8"))
        painter.setBrush(QtGui.QBrush(gradient))
        painter.setPen(QtGui.QPen(QtGui.QColor("#2c78dd"), 1.8))
        painter.drawPath(main_path)

        facet = QtGui.QPainterPath()
        facet.moveTo(size * 0.38, size * 0.18)
        facet.lineTo(size * 0.50, size * 0.08)
        facet.lineTo(size * 0.74, size * 0.28)
        facet.lineTo(size * 0.63, size * 0.39)
        facet.closeSubpath()
        facet_gradient = QtGui.QLinearGradient(size * 0.38, size * 0.08, size * 0.74, size * 0.39)
        facet_gradient.setColorAt(0.0, QtGui.QColor(255, 255, 255, 180))
        facet_gradient.setColorAt(1.0, QtGui.QColor(255, 255, 255, 60))
        painter.fillPath(facet, facet_gradient)

        inner_line = QtGui.QPainterPath()
        inner_line.moveTo(size * 0.26, size * 0.26)
        inner_line.lineTo(size * 0.43, size * 0.14)
        inner_line.lineTo(size * 0.67, size * 0.36)
        painter.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 130), 2.2, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap, QtCore.Qt.RoundJoin))
        painter.drawPath(inner_line)

        painter.end()
        return pixmap

    def _build_section_title(self, title: str) -> QtWidgets.QLabel:
        label = QtWidgets.QLabel(title)
        label.setObjectName("sectionTitle")
        label.setAlignment(QtCore.Qt.AlignCenter)
        return label

    @staticmethod
    def _build_card() -> QtWidgets.QFrame:
        card = QtWidgets.QFrame()
        card.setObjectName("card")
        return card

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

    def open_output_dir(self) -> None:
        self.open_folder(self.output_dir)

    def refresh_paths(self) -> None:
        if not save_config(self.input_dir, self.output_dir, self.selected_dpi):
            self.append_log_entry("설정 저장 실패")

    def append_log(self, message: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        self.log_messages.append(f"[{timestamp}] {message}")
        self.log_messages = self.log_messages[-200:]

    def append_log_entry(self, message: str, file_path: Path | None = None) -> None:
        self.append_log(message)
        if not self.log_list:
            return

        timestamp = time.strftime("%H:%M:%S")
        item_text = f"[{timestamp}] {message}"
        item: QtWidgets.QListWidgetItem | None = None
        file_key: str | None = None

        if file_path:
            file_key = str(file_path)
            item = self.log_items_by_file.get(file_key)

        if item is None:
            item = QtWidgets.QListWidgetItem(item_text)
            if file_key:
                item.setData(QtCore.Qt.UserRole, file_key)
                self.log_items_by_file[file_key] = item
            self.log_list.insertItem(0, item)
        else:
            item.setText(item_text)
            row = self.log_list.row(item)
            if row >= 0:
                self.log_list.takeItem(row)
            self.log_list.insertItem(0, item)

        while self.log_list.count() > 200:
            removed_item = self.log_list.takeItem(self.log_list.count() - 1)
            if removed_item is not None:
                removed_key = removed_item.data(QtCore.Qt.UserRole)
                if removed_key and self.log_items_by_file.get(removed_key) is removed_item:
                    self.log_items_by_file.pop(removed_key, None)
        self.update_history_count()
        self.flush_ui()

    def handle_log_selection(self) -> None:
        if not self.log_list or not self.log_list.selectedItems():
            return
        path_text = self.log_list.selectedItems()[0].data(QtCore.Qt.UserRole)
        if path_text:
            self.show_file_info(Path(path_text))

    @staticmethod
    def flush_ui() -> None:
        app = QtWidgets.QApplication.instance()
        if app:
            app.processEvents()

    def update_history_count(self) -> None:
        if not self.history_count_label or not self.log_list:
            return

        if self.log_stack:
            self.log_stack.setCurrentIndex(1 if self.log_list.count() > 0 else 0)

        unique_files: set[str] = set()
        for index in range(self.log_list.count()):
            item = self.log_list.item(index)
            if not item:
                continue
            file_key = item.data(QtCore.Qt.UserRole)
            if file_key:
                unique_files.add(str(file_key))

        count = len(unique_files)
        if count <= 0:
            self.history_count_label.hide()
            self.history_count_label.clear()
            return

        if self.bottom_collapsed:
            self.set_bottom_collapsed(False)

        self.history_count_label.setText(f"{count}건")
        self.history_count_label.show()

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
        self.setWindowTitle(f"{APP_NAME} - {message}")
        animated_base: str | None = None
        if message == "변환 진행 중":
            animated_base = "변환중"

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
            if message == "변환 중지됨":
                self.status_label.setText("변환 중지됨")
            elif message == "변환 실패":
                self.status_label.setText("변환 실패")
            else:
                self.status_label.setText(message)

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
        if self.tool_paths.missing:
            self.show_dependency_warning()
            return False
        return True

    def start_watch(self) -> None:
        if self.observer:
            self.set_status("변환 대기 중")
            if self.stop_button:
                self.stop_button.setText("변환 중지")
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
        if self.stop_button:
            self.stop_button.setText("변환 중지")

    def stop_watch(self) -> None:
        observer = self.observer
        if not observer:
            self.set_status("변환 중지됨")
            if self.stop_button:
                self.stop_button.setText("변환 시작")
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
        if self.stop_button:
            self.stop_button.setText("변환 시작")

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

        self.pending_convert_paths.append(path)
        self.pending_convert_keys.add(path_key)

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
            self.flush_ui()
            self.run_pipeline(svg_path, output_path)
            self.last_processed_svg_mtimes[str(svg_path)] = current_mtime
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

            self.finalize_png(temp_png, output_path)
        finally:
            if temp_png.exists():
                temp_png.unlink()

    def finalize_png(self, temp_png: Path, output_path: Path) -> None:
        image = QtGui.QImage(str(temp_png))
        if image.isNull():
            raise RuntimeError("임시 PNG를 불러오지 못했습니다.")

        rgba_image = image.convertToFormat(QtGui.QImage.Format_RGBA8888)
        rgba_image.setColorSpace(QtGui.QColorSpace(QtGui.QColorSpace.NamedColorSpace.SRgb))
        dots_per_meter = int(round(self.selected_dpi / 0.0254))
        rgba_image.setDotsPerMeterX(dots_per_meter)
        rgba_image.setDotsPerMeterY(dots_per_meter)

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


def main() -> None:
    application = QtWidgets.QApplication(sys.argv)
    application.setApplicationName(APP_NAME)
    window = App()
    window.show()
    sys.exit(application.exec())


if __name__ == "__main__":
    main()
