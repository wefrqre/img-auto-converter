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
            for path in self._watch_path.iterdir():
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


APP_NAME = "응용이미지자동화 변환기"
CONFIG_PATH = Path.home() / ".applied_image_auto_converter.json"
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


class App(QtWidgets.QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(550, 500)
        self.setMinimumSize(550, 500)

        self.tool_paths = detect_tools()
        self.event_queue: queue.Queue[Path] = queue.Queue()
        self.debounce_jobs: dict[str, QtCore.QTimer] = {}
        self.observer: Observer | None = None
        self.worker_lock = threading.Lock()
        self.stop_requested = False

        config = load_config()
        self.selected_dpi = normalize_dpi(config.get("dpi"))
        self.input_dir = DEFAULT_INPUT_DIR
        self.output_dir = output_dir_for_dpi(self.selected_dpi)
        self.status_text = "대기 중"
        self.log_messages: list[str] = []

        self.status_label: QtWidgets.QLabel | None = None
        self.folder_button: QtWidgets.QPushButton | None = None
        self.dpi_buttons: dict[int, QtWidgets.QRadioButton] = {}
        self.log_list: QtWidgets.QListWidget | None = None
        self.info_labels: dict[str, QtWidgets.QLabel] = {}
        self.startup_warning: str | None = None

        self.first_launch_setup()
        self.build_ui()
        self.set_status("대기 중")
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
                background: #f5f5f7;
                color: #3b3b3f;
                font-family: "Helvetica Neue";
            }
            QLabel {
                background: transparent;
            }
            QLabel#heroTitle {
                color: #373737;
                font-size: 16px;
                font-weight: 700;
            }
            QLabel#heroSubtitle {
                color: #6d6d72;
                font-size: 12px;
                font-weight: 500;
            }
            QLabel#rowLabel {
                color: #6d6d72;
                font-size: 12px;
                font-weight: 600;
            }
            QLabel#panelTitle {
                color: #6d6d72;
                font-size: 12px;
                font-weight: 600;
            }
            QLabel#statusValue {
                background: transparent;
                font-size: 13px;
                font-weight: 600;
            }
            QFrame#card {
                background: #ffffff;
                border: 1px solid #efeff4;
                border-radius: 8px;
            }
            QPushButton#primaryButton {
                background: #1569d8;
                color: #ffffff;
                border: none;
                border-radius: 8px;
                padding: 10px 16px;
                font-size: 12px;
                font-weight: 600;
            }
            QPushButton#primaryButton:hover {
                background: #105cc0;
            }
            QRadioButton {
                background: transparent;
                color: #111111;
                font-size: 12px;
                font-weight: 500;
                spacing: 8px;
            }
            QListWidget#selectionList {
                background: transparent;
                border: none;
                font-family: Menlo;
                font-size: 11px;
            }
            QListWidget#selectionList::item {
                padding: 8px 10px;
            }
            QListWidget#selectionList::item:selected {
                background: #eaf3ff;
                color: #111111;
                border-radius: 8px;
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
        main_layout.setContentsMargins(24, 22, 24, 22)
        main_layout.setSpacing(0)

        header_row = QtWidgets.QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(16)

        header_row.addWidget(self._build_hero_icon(), 0, QtCore.Qt.AlignVCenter)

        header_text_col = QtWidgets.QVBoxLayout()
        header_text_col.setContentsMargins(0, 0, 0, 0)
        header_text_col.setSpacing(4)
        header_text_col.setAlignment(QtCore.Qt.AlignVCenter)

        hero_title = QtWidgets.QLabel("응용 이미지 자동 변환기")
        hero_title.setObjectName("heroTitle")
        header_text_col.addWidget(hero_title)

        hero_subtitle = QtWidgets.QLabel("Figma에서 SVG 파일을 Input 폴더에 저장하면 PNG로 자동 변환됩니다.")
        hero_subtitle.setObjectName("heroSubtitle")
        hero_subtitle.setWordWrap(True)
        header_text_col.addWidget(hero_subtitle)

        header_row.addLayout(header_text_col, 1)
        main_layout.addLayout(header_row)

        main_layout.addSpacing(28)

        status_row = QtWidgets.QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.setSpacing(16)

        status_title = QtWidgets.QLabel("상태")
        status_title.setObjectName("rowLabel")
        status_title.setFixedWidth(100)
        status_row.addWidget(status_title)

        self.status_label = QtWidgets.QLabel()
        self.status_label.setObjectName("statusValue")
        self.status_label.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        status_row.addWidget(self.status_label)
        status_row.addStretch(1)
        main_layout.addLayout(status_row)

        main_layout.addSpacing(20)

        settings_layout = QtWidgets.QHBoxLayout()
        settings_layout.setContentsMargins(0, 0, 0, 0)
        settings_layout.setSpacing(16)

        dpi_label = QtWidgets.QLabel("DPI 설정")
        dpi_label.setObjectName("rowLabel")
        dpi_label.setFixedWidth(100)
        settings_layout.addWidget(dpi_label)

        dpi_row = QtWidgets.QHBoxLayout()
        dpi_row.setContentsMargins(0, 0, 0, 0)
        dpi_row.setSpacing(18)

        for dpi in DPI_OPTIONS:
            label_text = "96 DPI (RC)" if dpi == 96 else "192 DPI (RV)"
            radio = QtWidgets.QRadioButton(label_text)
            radio.setChecked(dpi == self.selected_dpi)
            radio.toggled.connect(
                lambda checked, value=dpi: checked and self.set_dpi(value)
            )
            self.dpi_buttons[dpi] = radio
            dpi_row.addWidget(radio)
        settings_layout.addLayout(dpi_row)
        settings_layout.addStretch(1)
        main_layout.addLayout(settings_layout)

        main_layout.addSpacing(20)

        content_row = QtWidgets.QHBoxLayout()
        content_row.setContentsMargins(0, 0, 0, 0)
        content_row.setSpacing(18)

        left_col = QtWidgets.QVBoxLayout()
        left_col.setContentsMargins(0, 0, 0, 0)
        left_col.setSpacing(8)

        history_title = QtWidgets.QLabel("변환내역")
        history_title.setObjectName("panelTitle")
        left_col.addWidget(history_title)

        log_card = self._build_card()
        log_layout = QtWidgets.QVBoxLayout(log_card)
        log_layout.setContentsMargins(8, 8, 8, 8)
        log_layout.setSpacing(0)

        self.log_list = QtWidgets.QListWidget()
        self.log_list.setObjectName("selectionList")
        self.log_list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.log_list.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.log_list.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.log_list.itemSelectionChanged.connect(self.handle_log_selection)
        log_layout.addWidget(self.log_list)
        left_col.addWidget(log_card, 1)
        content_row.addLayout(left_col, 1)

        right_col = QtWidgets.QVBoxLayout()
        right_col.setContentsMargins(0, 0, 0, 0)
        right_col.setSpacing(8)

        info_title = QtWidgets.QLabel("파일 정보")
        info_title.setObjectName("panelTitle")
        right_col.addWidget(info_title)

        info_card = self._build_card()
        info_layout = QtWidgets.QGridLayout(info_card)
        info_layout.setContentsMargins(12, 12, 12, 12)
        info_layout.setHorizontalSpacing(12)
        info_layout.setVerticalSpacing(6)

        fields = [
            "파일명",
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
            value_label = QtWidgets.QLabel("-")
            value_label.setObjectName("infoValue")
            value_label.setWordWrap(True)
            value_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            info_layout.addWidget(key_label, row, 0, QtCore.Qt.AlignTop)
            info_layout.addWidget(value_label, row, 1)
            self.info_labels[field] = value_label
        info_layout.setColumnStretch(1, 1)
        right_col.addWidget(info_card, 1)
        content_row.addLayout(right_col, 1)

        main_layout.addLayout(content_row, 1)

        main_layout.addSpacing(22)

        self.folder_button = QtWidgets.QPushButton("폴더열기")
        self.folder_button.setObjectName("primaryButton")
        self.folder_button.setFixedHeight(40)
        self.folder_button.clicked.connect(self.open_base_dir)
        main_layout.addWidget(self.folder_button)

    def _build_hero_icon(self) -> QtWidgets.QFrame:
        frame = QtWidgets.QFrame()
        frame.setFixedSize(52, 52)
        frame.setStyleSheet(
            "QFrame { background: #ffffff; border: 1px solid #efeff4; border-radius: 8px; }"
        )

        layout = QtWidgets.QVBoxLayout(frame)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(0)

        label = QtWidgets.QLabel()
        label.setAlignment(QtCore.Qt.AlignCenter)
        label.setPixmap(self._create_logo_pixmap(34))
        layout.addWidget(label)
        return frame

    @staticmethod
    def _create_logo_pixmap(size: int) -> QtGui.QPixmap:
        pixmap = QtGui.QPixmap(size, size)
        pixmap.fill(QtCore.Qt.transparent)

        painter = QtGui.QPainter(pixmap)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)

        path = QtGui.QPainterPath()
        c = size / 2
        outer = size * 0.42
        inner = size * 0.17
        points = [
            QtCore.QPointF(c, c - outer),
            QtCore.QPointF(c + inner, c - inner),
            QtCore.QPointF(c + outer, c),
            QtCore.QPointF(c + inner, c + inner),
            QtCore.QPointF(c, c + outer),
            QtCore.QPointF(c - inner, c + inner),
            QtCore.QPointF(c - outer, c),
            QtCore.QPointF(c - inner, c - inner),
        ]
        path.moveTo(points[0])
        for point in points[1:]:
            path.lineTo(point)
        path.closeSubpath()

        gradient = QtGui.QLinearGradient(0, 0, size, size)
        gradient.setColorAt(0.0, QtGui.QColor("#55a1ff"))
        gradient.setColorAt(0.45, QtGui.QColor("#1d74e8"))
        gradient.setColorAt(1.0, QtGui.QColor("#0f58cc"))
        painter.setBrush(QtGui.QBrush(gradient))
        painter.setPen(QtGui.QPen(QtGui.QColor("#2d78df"), 3))
        painter.drawPath(path)

        highlight = QtGui.QPainterPath()
        highlight.moveTo(c - inner * 1.2, c - inner * 1.5)
        highlight.lineTo(c, c - outer * 0.72)
        highlight.lineTo(c + inner * 1.65, c - inner * 0.2)
        painter.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 120), 4))
        painter.drawPath(highlight)

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
        item = QtWidgets.QListWidgetItem(f"[{timestamp}] {message}")
        if file_path:
            item.setData(QtCore.Qt.UserRole, str(file_path))
        self.log_list.insertItem(0, item)
        while self.log_list.count() > 200:
            self.log_list.takeItem(self.log_list.count() - 1)

    def handle_log_selection(self) -> None:
        if not self.log_list or not self.log_list.selectedItems():
            return
        path_text = self.log_list.selectedItems()[0].data(QtCore.Qt.UserRole)
        if path_text:
            self.show_file_info(Path(path_text))

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

        self.selected_dpi = normalize_dpi(dpi)
        self.output_dir = output_dir_for_dpi(self.selected_dpi)
        if not ensure_directory(self.output_dir):
            QtWidgets.QMessageBox.warning(
                self,
                APP_NAME,
                f"출력 폴더를 만들지 못했습니다.\n\n{self.output_dir}\n\nmacOS 권한 설정을 확인하세요.",
            )
        self.refresh_paths()

    def set_status(self, message: str) -> None:
        self.status_text = message
        self.setWindowTitle(f"{APP_NAME} - {message}")

        if message == "대기 중":
            text = "● 대기중"
            style = "color: #23914a;"
        elif "자동 변환 활성화" in message:
            text = "● 자동 변환 활성화"
            style = "color: #23914a;"
        elif "변환 중" in message:
            text = f"● {message}"
            style = "color: #b86a00;"
        elif "실패" in message:
            text = "● 변환 실패"
            style = "color: #c62828;"
        elif "완료" in message:
            text = f"● {message}"
            style = "color: #188038;"
        else:
            text = f"● {message}"
            style = "color: #111111;"

        if self.status_label:
            self.status_label.setText(text)
            self.status_label.setStyleSheet(style)

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
            self.set_status("자동 변환 활성화")
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

        self.observer = Observer()
        handler = SvgEventHandler(self.enqueue_event)
        self.observer.schedule(handler, str(self.input_dir), recursive=False)
        self.observer.start()
        self.set_status("자동 변환 활성화")

    def stop_watch(self) -> None:
        observer = self.observer
        if not observer:
            self.set_status("대기 중")
            return

        self.observer = None
        observer.stop()
        observer.join(timeout=3)
        self.set_status("대기 중")

    def enqueue_event(self, path: Path) -> None:
        if is_svg_file(path):
            self.event_queue.put(path)

    def poll_events(self) -> None:
        while True:
            try:
                path = self.event_queue.get_nowait()
            except queue.Empty:
                break
            self.schedule_debounced_convert(path)

    def schedule_debounced_convert(self, path: Path) -> None:
        path_key = str(path)
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
        self.convert_svg(path)

    def convert_svg(self, svg_path: Path, show_message: bool = False) -> bool:
        if not svg_path.exists():
            return False
        if not is_svg_file(svg_path):
            return False
        if not self.refresh_tools():
            return False

        if not self.worker_lock.acquire(blocking=False):
            self.set_status("다른 파일 변환 중")
            return False

        try:
            ensure_directory(self.output_dir)
            output_path = self.output_dir / f"{svg_path.stem}.png"
            self.set_status(f"변환 중: {svg_path.name}")
            self.run_pipeline(svg_path, output_path)
            self.set_status(f"완료: {output_path.name}")
            self.append_log_entry(f"변환 완료 {svg_path.name}", output_path)
            self.show_file_info(output_path)
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
            QtWidgets.QMessageBox.critical(
                self,
                APP_NAME,
                f"{svg_path.name} 변환에 실패했습니다.\n\n{exc}",
            )
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
