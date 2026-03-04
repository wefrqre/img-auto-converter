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

import tkinter as tk
from tkinter import messagebox

try:
    from watchdog.events import FileSystemEvent, FileSystemEventHandler
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
                path = Path(path_key)

                previous_mtime = self._snapshot.get(path_key)
                if previous_mtime is None or previous_mtime != mtime:
                    self._handler.dispatch_path(path)

            self._snapshot = current


APP_NAME = "응용이미지자동화 변환기"
CONFIG_PATH = Path.home() / ".applied_image_auto_converter.json"
DEFAULT_BASE_DIR = Path.home() / "Desktop" / "figma_exports"
DEFAULT_INPUT_DIR = DEFAULT_BASE_DIR / "svg"
DEFAULT_DPI = 96
DPI_OPTIONS = (96, 192)
DEFAULT_OUTPUT_DIR = DEFAULT_BASE_DIR / f"png_{DEFAULT_DPI}dpi"
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
        if not self.magick:
            missing_tools.append("ImageMagick (magick)")
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


def save_config(input_dir: Path, output_dir: Path, dpi: int) -> None:
    payload = {
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "dpi": dpi,
    }
    CONFIG_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def ensure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


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


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_NAME)
        self.root.geometry("640x360")
        self.root.minsize(560, 320)

        self.tool_paths = detect_tools()
        self.event_queue: queue.Queue[Path] = queue.Queue()
        self.debounce_jobs: dict[str, str] = {}
        self.observer: Observer | None = None
        self.worker_lock = threading.Lock()
        self.stop_requested = False

        config = load_config()
        self.selected_dpi = normalize_dpi(config.get("dpi"))
        self.input_dir = DEFAULT_INPUT_DIR
        self.output_dir = output_dir_for_dpi(self.selected_dpi)
        self.first_launch_setup()

        self.dpi_value = tk.IntVar(value=self.selected_dpi)
        self.status_text = tk.StringVar(value="대기 중")
        self.status_display_text = tk.StringVar(value="상태: 대기 중")
        self.dependency_text = tk.StringVar(value=self.dependency_summary())
        self.status_entry: tk.Entry | None = None
        self.watch_toggle_button: tk.Button | None = None

        self.build_ui()
        self.set_status(self.status_text.get())
        self.refresh_paths()
        self.root.after(300, self.poll_events)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        if self.tool_paths.missing:
            self.show_dependency_warning()
        else:
            self.root.after(200, self.start_watch)

    def first_launch_setup(self) -> None:
        created: list[Path] = []
        for directory in (DEFAULT_BASE_DIR, self.input_dir, self.output_dir):
            if not directory.exists():
                ensure_directory(directory)
                created.append(directory)

        save_config(self.input_dir, self.output_dir, self.selected_dpi)

        if created:
            created_text = "\n".join(str(path) for path in created)
            self.root.after(
                200,
                lambda: messagebox.showinfo(
                    APP_NAME,
                    "초기 폴더를 만들었습니다.\n\n"
                    f"{created_text}\n\n"
                    "앞으로 SVG는 입력 폴더에 넣으면 자동 변환됩니다.",
                ),
            )

    def dependency_summary(self) -> str:
        if not self.tool_paths.missing:
            return (
                "의존성 상태: Inkscape / ImageMagick 확인됨"
                f" | 리소스 경로: {get_resource_root()}"
            )
        return "의존성 상태: " + ", ".join(self.tool_paths.missing) + " 필요"

    def build_ui(self) -> None:
        background = "#f5f5f7"

        self.root.configure(bg=background)

        outer = tk.Frame(self.root, bg=background, padx=24, pady=24)
        outer.pack(fill="both", expand=True)

        self.status_entry = tk.Entry(
            outer,
            textvariable=self.status_display_text,
            font=("Helvetica Neue", 14, "bold"),
            relief="solid",
            bd=1,
            highlightthickness=0,
            state="readonly",
            readonlybackground="#ffffff",
            fg="#111111",
            justify="left",
        )
        self.status_entry.pack(fill="x", ipady=10)

        card = tk.Frame(
            outer,
            bg="#ffffff",
            relief="solid",
            bd=1,
            padx=18,
            pady=18,
        )
        card.pack(fill="x", pady=(16, 0))

        dpi_row = tk.Frame(card, bg="#ffffff")
        dpi_row.pack(fill="x", pady=(0, 14))

        for index, dpi in enumerate(DPI_OPTIONS):
            dpi_radio = tk.Radiobutton(
                dpi_row,
                text=f"{dpi} DPI",
                variable=self.dpi_value,
                value=dpi,
                command=self.set_dpi,
                font=("Helvetica Neue", 11, "bold"),
                bg="#ffffff",
                fg="#111111",
                activebackground="#ffffff",
                activeforeground="#111111",
                selectcolor="#ffffff",
                padx=4,
                pady=0,
                highlightthickness=0,
                cursor="hand2",
            )
            dpi_radio.grid(row=0, column=index, padx=(0, 18), sticky="w")

        grid_actions = tk.Frame(card, bg="#ffffff")
        grid_actions.pack()
        grid_actions.grid_columnconfigure(0, minsize=176, weight=1)
        grid_actions.grid_columnconfigure(1, minsize=176, weight=1)

        self.watch_toggle_button = self._add_action_tile(
            grid_actions,
            row=0,
            column=0,
            columnspan=2,
            text="감시 시작",
            command=self.toggle_watch,
            primary=True,
        )
        self._add_action_tile(
            grid_actions,
            row=1,
            column=0,
            text="폴더 열기",
            command=self.open_base_dir,
        )
        self._add_action_tile(
            grid_actions,
            row=1,
            column=1,
            text="결과 열기",
            command=self.open_output_dir,
        )
        self._add_action_tile(
            grid_actions,
            row=2,
            column=0,
            text="설정 보기",
            command=self.show_current_settings,
        )
        self._add_action_tile(
            grid_actions,
            row=2,
            column=1,
            text="기본 폴더 다시 만들기",
            command=self.create_default_folders,
        )
        self.update_control_states()

    def _add_action_tile(
        self,
        parent: tk.Misc,
        row: int,
        column: int,
        text: str,
        command: Callable[[], None],
        primary: bool = False,
        columnspan: int = 1,
    ) -> tk.Button:
        tile_width = 176 if columnspan == 1 else 360
        tile = tk.Frame(
            parent,
            bg="#ffffff",
            width=tile_width,
            height=40,
            highlightthickness=0,
            bd=0,
        )
        tile.grid(
            row=row,
            column=column,
            columnspan=columnspan,
            padx=8,
            pady=8,
            sticky="ew",
        )
        tile.grid_propagate(False)

        button = self._build_action_button(
            tile,
            text=text,
            command=command,
            primary=primary,
        )
        button.place(x=0, y=0, relwidth=1, relheight=1)
        return button

    def _build_action_button(
        self,
        parent: tk.Misc,
        text: str,
        command: Callable[[], None],
        primary: bool = False,
    ) -> tk.Button:
        bg = "#007aff" if primary else "#ffffff"
        fg = "white" if primary else "#111111"
        active_bg = "#0062cc" if primary else "#ececf0"
        active_fg = "white" if primary else "#111111"
        border = 0 if primary else 1
        relief = "flat" if primary else "solid"

        return tk.Button(
            parent,
            text=text,
            command=command,
            font=("Helvetica Neue", 12, "bold"),
            bg=bg,
            fg=fg,
            activebackground=active_bg,
            activeforeground=active_fg,
            relief=relief,
            bd=border,
            padx=12,
            pady=0,
            highlightthickness=0,
            cursor="hand2",
        )

    def show_current_settings(self) -> None:
        messagebox.showinfo(
            APP_NAME,
            "현재 설정\n\n"
            f"상태: {self.status_text.get()}\n"
            f"DPI: {self.dpi_value.get()}\n"
            f"{self.dependency_text.get()}\n\n"
            f"기본 폴더:\n{DEFAULT_BASE_DIR}\n\n"
            f"입력 폴더:\n{self.input_dir}\n\n"
            f"출력 폴더:\n{self.output_dir}\n\n"
            "사용 순서:\n"
            "1) 감시 버튼 누르기\n"
            "2) 자동으로 열린 svg 폴더에 SVG를 넣기\n"
            "3) PNG는 폴더 열기에서 확인",
        )

    def open_folder(self, path: Path) -> None:
        ensure_directory(path)
        completed = subprocess.run(
            ["open", str(path)],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode == 0:
            return

        details = completed.stderr.strip() or completed.stdout.strip() or "알 수 없는 오류"
        messagebox.showerror(APP_NAME, f"폴더를 열지 못했습니다.\n\n{path}\n\n{details}")

    def open_base_dir(self) -> None:
        self.open_folder(DEFAULT_BASE_DIR)

    def open_output_dir(self) -> None:
        self.open_folder(self.output_dir)

    def refresh_paths(self) -> None:
        save_config(self.input_dir, self.output_dir, self.dpi_value.get())

    def set_dpi(self, dpi: int | None = None) -> None:
        if dpi is None:
            selected = normalize_dpi(self.dpi_value.get())
        else:
            selected = normalize_dpi(dpi)
        self.selected_dpi = selected
        self.dpi_value.set(selected)
        self.output_dir = output_dir_for_dpi(selected)
        ensure_directory(self.output_dir)
        self.refresh_paths()

    def set_status(self, message: str) -> None:
        self.status_text.set(message)
        self.status_display_text.set(f"상태: {message}")
        self.root.title(f"{APP_NAME} - {message}")
        if not self.status_entry:
            return

        bar_fill = "#ffffff"
        text_fill = "#111111"
        if "자동 변환 활성화" in message:
            bar_fill = "#eaf3ff"
            text_fill = "#0066cc"
        elif "변환 중" in message:
            bar_fill = "#fff6e8"
            text_fill = "#b86a00"
        elif "실패" in message:
            bar_fill = "#ffeceb"
            text_fill = "#c62828"
        elif "완료" in message:
            bar_fill = "#ecf9f0"
            text_fill = "#188038"

        self.status_entry.configure(
            readonlybackground=bar_fill,
            fg=text_fill,
            disabledforeground=text_fill,
            insertbackground=text_fill,
        )
        self.update_control_states()

    def update_control_states(self) -> None:
        watching = self.observer is not None
        if not self.watch_toggle_button:
            return

        if watching:
            self.watch_toggle_button.configure(
                text="감시 중지",
                bg="#ffffff",
                fg="#111111",
                activebackground="#ececf0",
                activeforeground="#111111",
                relief="solid",
                bd=1,
            )
        else:
            self.watch_toggle_button.configure(
                text="감시 시작",
                bg="#007aff",
                fg="white",
                activebackground="#0062cc",
                activeforeground="white",
                relief="flat",
                bd=0,
            )

    def toggle_watch(self) -> None:
        if self.observer:
            self.stop_watch()
        else:
            self.start_watch()

    def create_default_folders(self) -> None:
        ensure_directory(DEFAULT_INPUT_DIR)
        current_output_dir = output_dir_for_dpi(self.dpi_value.get())
        ensure_directory(current_output_dir)
        self.input_dir = DEFAULT_INPUT_DIR
        self.output_dir = current_output_dir
        self.refresh_paths()
        self.set_status("기본 폴더 준비 완료")
        messagebox.showinfo(
            APP_NAME,
            "기본 폴더를 준비했습니다.\n\n"
            f"입력: {DEFAULT_INPUT_DIR}\n"
            f"출력: {current_output_dir}",
        )

    def show_dependency_warning(self) -> None:
        install_text = (
            "필수 도구가 설치되지 않았습니다.\n\n"
            f"누락: {', '.join(self.tool_paths.missing)}\n\n"
            "권장 설치 방법:\n"
            "1) Homebrew 설치\n"
            "2) 터미널에서 아래 실행\n"
            "   brew install --cask inkscape\n"
            "   brew install imagemagick\n\n"
            "앱은 /opt/homebrew/bin, /usr/local/bin, /usr/bin, /bin 을 자동으로 확인합니다."
        )
        self.set_status("의존성 설치 필요")
        self.root.after(250, lambda: messagebox.showwarning(APP_NAME, install_text))

    def refresh_tools(self) -> bool:
        self.tool_paths = detect_tools()
        self.dependency_text.set(self.dependency_summary())
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

        ensure_directory(self.input_dir)
        ensure_directory(self.output_dir)
        while not self.event_queue.empty():
            try:
                self.event_queue.get_nowait()
            except queue.Empty:
                break
        self.debounce_jobs.clear()

        self.observer = Observer()
        handler = SvgEventHandler(self.enqueue_event)
        self.observer.schedule(handler, str(self.input_dir), recursive=False)
        self.observer.start()
        self.set_status("자동 변환 활성화")
        self.open_folder(self.input_dir)

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

        if not self.stop_requested:
            self.root.after(300, self.poll_events)

    def schedule_debounced_convert(self, path: Path) -> None:
        path_key = str(path)
        existing_job = self.debounce_jobs.pop(path_key, None)
        if existing_job:
            self.root.after_cancel(existing_job)

        job_id = self.root.after(700, lambda p=path: self.convert_svg(p))
        self.debounce_jobs[path_key] = job_id

    def convert_all(self) -> None:
        if not self.refresh_tools():
            return

        ensure_directory(self.input_dir)
        ensure_directory(self.output_dir)

        svg_files = sorted(path for path in self.input_dir.iterdir() if is_svg_file(path))
        if not svg_files:
            self.set_status("변환할 SVG 없음")
            return

        converted = 0
        for svg_path in svg_files:
            if self.convert_svg(svg_path, show_message=False):
                converted += 1

        self.set_status(f"전체 변환 완료 ({converted}/{len(svg_files)})")

    def convert_svg(self, svg_path: Path, show_message: bool = False) -> bool:
        path_key = str(svg_path)
        self.debounce_jobs.pop(path_key, None)

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
            if show_message:
                messagebox.showinfo(APP_NAME, f"변환 완료\n\n{output_path}")
            return True
        except Exception as exc:  # noqa: BLE001
            self.set_status("변환 실패")
            messagebox.showerror(
                APP_NAME,
                f"{svg_path.name} 변환에 실패했습니다.\n\n{exc}",
            )
            return False
        finally:
            self.worker_lock.release()

    def run_pipeline(self, svg_path: Path, output_path: Path) -> None:
        if not self.tool_paths.inkscape or not self.tool_paths.magick:
            raise RuntimeError("Inkscape 또는 ImageMagick 경로를 확인할 수 없습니다.")

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

            magick_command = [
                self.tool_paths.magick,
                str(temp_png),
                "-units",
                "PixelsPerInch",
                "-density",
                str(self.dpi_value.get()),
                "-alpha",
                "on",
                "-background",
                "none",
                "-type",
                "TrueColorAlpha",
                "-define",
                "png:color-type=6",
                str(output_path),
            ]
            self.run_command(magick_command, env)
        finally:
            if temp_png.exists():
                temp_png.unlink()

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

    def on_close(self) -> None:
        self.stop_requested = True
        self.stop_watch()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
