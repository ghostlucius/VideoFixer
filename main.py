import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile
from pathlib import Path
from tkinter import filedialog, messagebox
import tkinter as tk
from tkinter import ttk


APP_NAME = "VideoFixer"
APP_VERSION = "1.1.0"
AUTHOR = "Luciano Villani"
FFMPEG_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
VIDEO_TYPES = (
    ("Video files", "*.mp4 *.mov *.mkv *.avi *.m4v *.webm"),
    ("MP4 files", "*.mp4"),
    ("All files", "*.*"),
)


def app_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "VideoFixer"


def bundled_ffmpeg_dir() -> Path:
    return app_data_dir() / "ffmpeg"


def legacy_ffmpeg_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "FastVideoFixer" / "ffmpeg"


def find_on_path(name: str) -> str | None:
    found = shutil.which(name)
    return found if found and Path(found).exists() else None


def find_local_tool(name: str) -> str | None:
    for root in (bundled_ffmpeg_dir(), legacy_ffmpeg_dir()):
        if not root.exists():
            continue
        for tool in root.rglob(name):
            if tool.is_file():
                return str(tool)
    return None


def find_ffmpeg() -> str | None:
    return find_local_tool("ffmpeg.exe") or find_on_path("ffmpeg")


def find_ffprobe(ffmpeg_path: str | None) -> str | None:
    local = find_local_tool("ffprobe.exe")
    if local:
        return local
    if ffmpeg_path:
        sibling = Path(ffmpeg_path).with_name("ffprobe.exe")
        if sibling.exists():
            return str(sibling)
    return find_on_path("ffprobe")


def download_ffmpeg(progress_callback) -> str:
    target_root = bundled_ffmpeg_dir()
    target_root.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        zip_path = Path(tmp) / "ffmpeg.zip"

        def report(block_count, block_size, total_size):
            if total_size > 0:
                downloaded = min(block_count * block_size, total_size)
                progress_callback(downloaded / total_size * 100)

        urllib.request.urlretrieve(FFMPEG_URL, zip_path, reporthook=report)

        extract_dir = Path(tmp) / "extract"
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(extract_dir)

        ffmpeg_exe = next(extract_dir.rglob("ffmpeg.exe"), None)
        ffprobe_exe = next(extract_dir.rglob("ffprobe.exe"), None)
        if not ffmpeg_exe:
            raise RuntimeError("The downloaded package did not contain ffmpeg.exe.")

        bin_dir = target_root / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ffmpeg_exe, bin_dir / "ffmpeg.exe")
        if ffprobe_exe:
            shutil.copy2(ffprobe_exe, bin_dir / "ffprobe.exe")

    return str(target_root / "bin" / "ffmpeg.exe")


def output_path_for(input_file: str, output_folder: str | None) -> str:
    source = Path(input_file)
    folder = Path(output_folder) if output_folder else source.parent
    return str(folder / f"{source.stem}-fixed{source.suffix}")


def parse_time(value: str) -> float:
    match = re.match(r"(\d+):(\d+):(\d+(?:\.\d+)?)", value.strip())
    if not match:
        return 0.0
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def media_duration(ffprobe: str | None, input_file: str) -> float:
    if not ffprobe:
        return 0.0
    command = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        input_file,
    ]
    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        return float(result.stdout.strip())
    except Exception:
        return 0.0


def detected_gpu_names() -> list[str]:
    if os.name != "nt":
        return []

    commands = [
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name",
        ],
        ["wmic", "path", "win32_VideoController", "get", "name"],
    ]

    for command in commands:
        try:
            result = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            names = [
                line.strip()
                for line in result.stdout.splitlines()
                if line.strip() and line.strip().lower() != "name"
            ]
            if names:
                return names
        except Exception:
            continue
    return []


class VideoFixer(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} {APP_VERSION}")
        self._size_to_screen()
        self.minsize(760, 560)
        self.resizable(True, True)
        self.configure(bg="#f5f7fb")

        self.events = queue.Queue()
        self.input_file = tk.StringVar()
        self.output_folder = tk.StringVar()
        self.encoder = tk.StringVar(value="")
        self.status = tk.StringVar(value="Select a video to begin.")
        self.recommendation = tk.StringVar(value="")
        self.progress_text = tk.StringVar(value="0%")
        self.is_busy = False
        self.encoder_buttons = []
        self.gpu_names = detected_gpu_names()
        self.recommended_encoder = ""

        self._style()
        self._build()
        self._refresh_encoder_recommendation()
        self._wire_validation()
        self._update_form_state()
        self.after(100, self._process_events)

    def _style(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background="#f5f7fb")
        style.configure("Panel.TFrame", background="#ffffff", relief="flat")
        style.configure("TLabel", background="#f5f7fb", foreground="#152238", font=("Segoe UI", 10))
        style.configure("Panel.TLabel", background="#ffffff", foreground="#152238", font=("Segoe UI", 10))
        style.configure("Title.TLabel", background="#f5f7fb", foreground="#111827", font=("Segoe UI Semibold", 22))
        style.configure("Sub.TLabel", background="#f5f7fb", foreground="#64748b", font=("Segoe UI", 10))
        style.configure("Status.TLabel", background="#ffffff", foreground="#475569", font=("Segoe UI", 10))
        style.configure("TButton", font=("Segoe UI Semibold", 10), padding=(14, 9), borderwidth=0)
        style.map("TButton", background=[("active", "#dbeafe")])
        style.configure("Accent.TButton", background="#2563eb", foreground="#ffffff")
        style.map("Accent.TButton", background=[("active", "#1d4ed8"), ("disabled", "#93c5fd")])
        style.configure("TRadiobutton", background="#ffffff", foreground="#1f2937", font=("Segoe UI", 10))
        style.configure("Horizontal.TProgressbar", troughcolor="#e5e7eb", background="#2563eb", thickness=12)

    def _build(self):
        wrapper = ttk.Frame(self, padding=24)
        wrapper.pack(fill="both", expand=True)

        ttk.Label(wrapper, text=APP_NAME, style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            wrapper,
            text=f"Repair damaged videos with ffmpeg. Version {APP_VERSION}. Author: {AUTHOR}",
            style="Sub.TLabel",
        ).pack(anchor="w", pady=(2, 16))

        panel = ttk.Frame(wrapper, style="Panel.TFrame", padding=20)
        panel.pack(fill="both", expand=True)

        self._file_row(panel, "Video file", self.input_file, self._choose_video)
        self._file_row(panel, "Output folder", self.output_folder, self._choose_output_folder, optional=True)

        ttk.Label(panel, text="Fix encoder", style="Panel.TLabel").pack(anchor="w", pady=(10, 8))
        encoders = ttk.Frame(panel, style="Panel.TFrame")
        encoders.pack(fill="x")
        encoder_options = (
            ("Software", "software"),
            ("Auto GPU", "gpu_auto"),
            ("NVIDIA", "gpu_nvidia"),
            ("Intel", "gpu_intel"),
            ("AMD", "gpu_amd"),
        )
        for label, value in encoder_options:
            button = ttk.Radiobutton(
                encoders,
                text=label,
                variable=self.encoder,
                value=value,
                style="TRadiobutton",
            )
            button.pack(side="left", padx=(0, 18))
            self.encoder_buttons.append(button)

        ttk.Label(panel, textvariable=self.recommendation, style="Status.TLabel").pack(anchor="w", pady=(10, 0))
        ttk.Label(panel, textvariable=self.status, style="Status.TLabel").pack(anchor="w", pady=(10, 8))
        progress_line = ttk.Frame(panel, style="Panel.TFrame")
        progress_line.pack(fill="x")
        self.progress = ttk.Progressbar(progress_line, mode="determinate", maximum=100)
        self.progress.pack(side="left", fill="x", expand=True, padx=(0, 12))
        ttk.Label(progress_line, textvariable=self.progress_text, style="Panel.TLabel", width=6).pack(side="right")

        actions = ttk.Frame(panel, style="Panel.TFrame")
        actions.pack(fill="x", pady=(16, 0))
        self.start_button = ttk.Button(actions, text="Start", style="Accent.TButton", command=self._start)
        self.start_button.pack(side="right")
        ttk.Button(actions, text="Clear", command=self._clear).pack(side="right", padx=(0, 10))
        ttk.Button(actions, text="Fit screen", command=self._fit_screen).pack(side="right", padx=(0, 10))

        footer = ttk.Label(
            wrapper,
            text="If ffmpeg is missing, the app asks before downloading it for this user only.",
            style="Sub.TLabel",
        )
        footer.pack(anchor="w", pady=(12, 0))

    def _file_row(self, parent, label, variable, command, optional=False):
        ttk.Label(parent, text=label, style="Panel.TLabel").pack(anchor="w", pady=(0, 8))
        row = ttk.Frame(parent, style="Panel.TFrame")
        row.pack(fill="x", pady=(0, 12))
        entry = ttk.Entry(row, textvariable=variable, font=("Segoe UI", 10))
        entry.pack(side="left", fill="x", expand=True, ipady=7)
        button_text = "Choose"
        ttk.Button(row, text=button_text, command=command).pack(side="left", padx=(10, 0))
        if optional:
            ttk.Button(row, text="Same folder", command=self._use_same_folder).pack(side="left", padx=(8, 0))

    def _use_same_folder(self):
        input_file = self.input_file.get().strip()
        if input_file:
            self.output_folder.set(str(Path(input_file).parent))
        else:
            self.output_folder.set("")

    def _size_to_screen(self):
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        width = min(1040, max(900, screen_width - 120))
        height = min(760, max(660, screen_height - 140))
        left = max(0, (screen_width - width) // 2)
        top = max(0, (screen_height - height) // 2)
        self.geometry(f"{width}x{height}+{left}+{top}")

    def _fit_screen(self):
        try:
            self.state("zoomed")
        except tk.TclError:
            screen_width = self.winfo_screenwidth()
            screen_height = self.winfo_screenheight()
            self.geometry(f"{screen_width}x{screen_height}+0+0")

    def _wire_validation(self):
        for variable in (self.input_file, self.output_folder, self.encoder):
            variable.trace_add("write", lambda *_: self._update_form_state())

    def _update_form_state(self):
        encoder_state = "normal" if not self.is_busy else "disabled"
        for button in self.encoder_buttons:
            button.configure(state=encoder_state)

        if not self.encoder.get() and self.recommended_encoder:
            self.encoder.set(self.recommended_encoder)
            return

        can_start = self._form_is_ready()
        if hasattr(self, "start_button"):
            self.start_button.configure(state="normal" if can_start else "disabled")

    def _refresh_encoder_recommendation(self):
        value, label, reason = self._recommended_encoder_choice()
        self.recommended_encoder = value
        self.recommendation.set(f"Recommended for Fix: {label}. {reason}")

    def _recommended_encoder_choice(self):
        names = " ".join(self.gpu_names).lower()
        ffmpeg_path = find_ffmpeg()
        encoders = self._available_video_encoders(ffmpeg_path) if ffmpeg_path else set()

        candidates = []
        if "nvidia" in names or "geforce" in names or "rtx" in names or "gtx" in names:
            candidates.append(("gpu_nvidia", "NVIDIA GPU", "h264_nvenc"))
        if "intel" in names or "uhd graphics" in names or "iris" in names:
            candidates.append(("gpu_intel", "Intel GPU", "h264_qsv"))
        if "amd" in names or "radeon" in names:
            candidates.append(("gpu_amd", "AMD GPU", "h264_amf"))

        if not candidates:
            return "software", "Software", "No compatible GPU encoder was detected."

        if not ffmpeg_path:
            if len(candidates) == 1:
                value, label, _encoder = candidates[0]
                return value, label, "Detected from your graphics hardware. ffmpeg will be checked when installed."
            return "gpu_auto", "Auto GPU", "Multiple graphics options were detected."

        usable = [(value, label) for value, label, encoder in candidates if encoder in encoders]
        if len(usable) == 1:
            value, label = usable[0]
            return value, label, "Detected from your graphics hardware and supported by ffmpeg."
        if len(usable) > 1:
            return "gpu_auto", "Auto GPU", "Multiple GPU encoders are available."

        return "software", "Software", "Your detected GPU encoder is not available in ffmpeg."

    def _form_is_ready(self):
        if self.is_busy:
            return False
        input_file = self.input_file.get().strip()
        if not input_file or not Path(input_file).is_file():
            return False
        output_dir = self.output_folder.get().strip()
        if output_dir and not Path(output_dir).is_dir():
            return False
        if self.encoder.get() not in {
            "software",
            "gpu_auto",
            "gpu_nvidia",
            "gpu_intel",
            "gpu_amd",
        }:
            return False
        return True

    def _choose_video(self):
        path = filedialog.askopenfilename(title="Select video", filetypes=VIDEO_TYPES)
        if path:
            self.input_file.set(path)
            self.output_folder.set(str(Path(path).parent))
            self.status.set("Ready.")

    def _choose_output_folder(self):
        path = filedialog.askdirectory(title="Select output folder")
        if path:
            self.output_folder.set(path)

    def _clear(self):
        if self.is_busy:
            return
        self.input_file.set("")
        self.output_folder.set("")
        self.encoder.set("")
        self.progress["value"] = 0
        self.progress_text.set("0%")
        self.status.set("Select a video to begin.")
        self._update_form_state()

    def _start(self):
        if self.is_busy:
            return
        if not self._form_is_ready():
            messagebox.showwarning(APP_NAME, "Please select a valid video and all required choices before starting.")
            self._update_form_state()
            return
        input_file = self.input_file.get().strip()
        if not input_file or not Path(input_file).is_file():
            messagebox.showwarning(APP_NAME, "Please select a valid video file.")
            return

        output_dir = self.output_folder.get().strip()
        if output_dir and not Path(output_dir).is_dir():
            messagebox.showwarning(APP_NAME, "Please select a valid output folder.")
            return

        ffmpeg_path = find_ffmpeg()
        if not ffmpeg_path:
            answer = messagebox.askyesno(
                APP_NAME,
                "ffmpeg is required to repair videos, but it was not found on this system.\n\n"
                "The program can download and install ffmpeg automatically for this Windows user. "
                "An internet connection is required.\n\n"
                "Do you want to download ffmpeg now?",
            )
            if not answer:
                self.status.set("ffmpeg is required before conversion can start.")
                return

        self._set_busy(True)
        self.progress["value"] = 0
        self.progress_text.set("0%")
        thread = threading.Thread(target=self._worker, args=(input_file, output_dir or None), daemon=True)
        thread.start()

    def _set_busy(self, busy):
        self.is_busy = busy
        self._update_form_state()

    def _worker(self, input_file, output_dir):
        try:
            ffmpeg_path = find_ffmpeg()
            if not ffmpeg_path:
                self.events.put(("status", "Downloading ffmpeg..."))
                ffmpeg_path = download_ffmpeg(lambda pct: self.events.put(("progress", pct)))
                self.events.put(("status", "ffmpeg installed. Preparing video..."))
                self.events.put(("recommendation", None))

            ffprobe_path = find_ffprobe(ffmpeg_path)
            duration = media_duration(ffprobe_path, input_file)
            output_file = output_path_for(input_file, output_dir)
            Path(output_file).parent.mkdir(parents=True, exist_ok=True)

            command = self._ffmpeg_command(ffmpeg_path, input_file, output_file)
            self.events.put(("progress", 0))

            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )

            last_progress = 0.0
            log_tail = []
            assert process.stdout is not None
            for line in process.stdout:
                line = line.strip()
                if line:
                    log_tail.append(line)
                    log_tail = log_tail[-12:]
                percent = self._progress_from_line(line, duration)
                if percent is not None and percent >= last_progress:
                    last_progress = min(percent, 99.0)
                    self.events.put(("progress", last_progress))

            code = process.wait()
            if code != 0:
                detail = "\n".join(log_tail[-6:]) or "ffmpeg exited with an error."
                raise RuntimeError(detail)

            self.events.put(("progress", 100))
            self.events.put(("done", output_file))
        except Exception as exc:
            self.events.put(("error", str(exc)))

    def _ffmpeg_command(self, ffmpeg_path, input_file, output_file):
        encoder_name, encoder_args = self._encoder_args(ffmpeg_path)
        self.events.put(("status", f"Repairing video with {encoder_name}..."))
        return [
            ffmpeg_path,
            "-y",
            "-fflags",
            "+genpts+discardcorrupt",
            "-err_detect",
            "ignore_err",
            "-analyzeduration",
            "100M",
            "-probesize",
            "100M",
            "-i",
            input_file,
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-sn",
            "-dn",
            *encoder_args,
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-ac",
            "2",
            "-ar",
            "48000",
            "-max_muxing_queue_size",
            "4096",
            "-tag:v",
            "avc1",
            "-movflags",
            "+faststart",
            "-progress",
            "pipe:1",
            "-nostats",
            output_file,
        ]

    def _encoder_args(self, ffmpeg_path):
        selected = self.encoder.get()
        encoders = self._available_video_encoders(ffmpeg_path)

        gpu_choices = [
            ("NVIDIA GPU", "h264_nvenc", ["-c:v", "h264_nvenc", "-preset", "fast", "-cq", "26", "-pix_fmt", "yuv420p"]),
            ("Intel GPU", "h264_qsv", ["-c:v", "h264_qsv", "-preset", "fast", "-global_quality", "26", "-pix_fmt", "nv12"]),
            ("AMD GPU", "h264_amf", ["-c:v", "h264_amf", "-quality", "speed", "-qp_i", "26", "-qp_p", "26", "-pix_fmt", "yuv420p"]),
        ]

        if selected == "gpu_auto":
            for label, encoder, args in gpu_choices:
                if encoder in encoders:
                    return label, args
            return "Software", ["-c:v", "libx264", "-preset", "medium", "-crf", "26", "-pix_fmt", "yuv420p"]

        explicit = {
            "gpu_nvidia": gpu_choices[0],
            "gpu_intel": gpu_choices[1],
            "gpu_amd": gpu_choices[2],
        }
        if selected in explicit:
            label, encoder, args = explicit[selected]
            if encoder not in encoders:
                raise RuntimeError(
                    f"{label} encoding is not available in this ffmpeg build or on this PC.\n\n"
                    "Choose Auto GPU or Software and try again."
                )
            return label, args

        return "Software", ["-c:v", "libx264", "-preset", "medium", "-crf", "26", "-pix_fmt", "yuv420p"]

    def _available_video_encoders(self, ffmpeg_path):
        try:
            result = subprocess.run(
                [ffmpeg_path, "-hide_banner", "-encoders"],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            return set(re.findall(r"\b(h264_\w+|libx264)\b", result.stdout))
        except Exception:
            return {"libx264"}

    def _progress_from_line(self, line, duration):
        if duration <= 0:
            return None
        if line.startswith("out_time_ms=") or line.startswith("out_time_us="):
            try:
                value = float(line.split("=", 1)[1]) / 1_000_000
                return value / duration * 100
            except ValueError:
                return None
        if line.startswith("out_time="):
            value = parse_time(line.split("=", 1)[1])
            return value / duration * 100
        return None

    def _process_events(self):
        try:
            while True:
                event, payload = self.events.get_nowait()
                if event == "status":
                    self.status.set(payload)
                elif event == "recommendation":
                    previous = self.encoder.get()
                    self._refresh_encoder_recommendation()
                    if previous in {"", "gpu_auto", self.recommended_encoder}:
                        self.encoder.set(self.recommended_encoder)
                    self._update_form_state()
                elif event == "progress":
                    value = max(0, min(100, float(payload)))
                    self.progress["value"] = value
                    self.progress_text.set(f"{int(value)}%")
                elif event == "done":
                    self._set_busy(False)
                    self.status.set(f"Finished: {payload}")
                    messagebox.showinfo(APP_NAME, f"Video fixed successfully:\n{payload}")
                elif event == "error":
                    self._set_busy(False)
                    self.status.set("Conversion failed.")
                    messagebox.showerror(APP_NAME, f"Conversion failed:\n\n{payload}")
        except queue.Empty:
            pass
        self.after(100, self._process_events)


if __name__ == "__main__":
    if sys.platform != "win32":
        print("This application is designed for Windows 10 and Windows 11.")
    app = VideoFixer()
    app.mainloop()
