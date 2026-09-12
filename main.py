from __future__ import annotations

import contextlib
import datetime as dt
import logging
import multiprocessing as mp
import os
import queue
import re
import shutil
import sys
import tempfile
import threading
import time
import warnings
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import pystray
import tkinter as tk
from PIL import Image, ImageDraw, ImageGrab
from pystray import Menu, MenuItem
from tkinter import filedialog, messagebox, ttk

from transcriber_worker import run_transcriber_process


APP_NAME = "Local Meeting Recorder"
RECORD_SAMPLE_RATE = 48000
TRANSCRIBE_SAMPLE_RATE = 16000
CHANNELS = 1
CHUNK_SECONDS = 0.5
TRANSCRIBE_SECONDS = 2
REALTIME_MIN_AUDIO_SECONDS = 1.5
REVIEW_SECONDS = 15
REVIEW_OVERLAP_SECONDS = 2
REVIEW_MIN_AUDIO_SECONDS = 3
STOP_FINAL_WAIT_SECONDS = 15
MINUTES_UPDATE_SECONDS = 5
AUTO_SCREENSHOT_SCAN_SECONDS = 0.5
AUTO_SCREENSHOT_STABLE_SECONDS = 0.6
AUTO_SCREENSHOT_COOLDOWN_SECONDS = 2.0
AUTO_SCREENSHOT_CHANGE_THRESHOLD = 0.08
AUTO_SCREENSHOT_STABLE_THRESHOLD = 0.02
SCREENSHOT_PREVIEW_SIZE = (180, 100)
WHISPER_MODEL = "distil-small.en"
LOCAL_MODEL_DIR = Path(__file__).with_name("models") / "faster-distil-whisper-small.en"
FALLBACK_MODEL_DIR = Path(__file__).with_name("models") / "faster-whisper-small"
WHISPER_LANGUAGE: Optional[str] = "en"
WHISPER_DEVICE = "cpu"
WHISPER_COMPUTE_TYPE = "int8"
WHISPER_CPU_THREADS = max(1, min(4, (os.cpu_count() or 4) - 1))
WHISPER_NUM_WORKERS = 2
REALTIME_BEAM_SIZE = 1
FINAL_BEAM_SIZE = 5
VAD_PARAMETERS = {"min_silence_duration_ms": 500}
MIN_TRANSCRIBE_RMS = 0.0015
DEFAULT_LANGUAGE = "en"
AUDIO_SOURCES = ("source_speaker", "source_microphone")
SPEAKER_LABEL_KEYS = {
    "source_microphone": "speaker_me",
    "source_speaker": "speaker_others",
}


TRANSLATIONS = {
    "en": {
        "app_name": "Local Meeting Recorder",
        "record": "Record",
        "text_window": "Text Window",
        "stop": "Stop Recording",
        "screenshot": "Screenshot",
        "auto_screenshot_on": "Auto Screenshot On",
        "auto_screenshot_off": "Auto Screenshot Off",
        "about": "About",
        "exit": "Exit",
        "transcript_title": "Live Transcript",
        "copy_text": "Copy text to clipboard",
        "stopping_wait": "Recording is stopping and the transcript is being finished. Please wait.",
        "already_recording": "Recording is already in progress. Click Stop Recording to stop.",
        "ask_auto_screenshot": "Do you want to turn on Auto Screenshot for this recording?",
        "screenshot_saved": "Screenshot saved:",
        "screenshot_failed": "Screenshot failed:",
        "missing_deps_title": "Missing recording dependencies. Recording cannot start.",
        "missing_deps_body": "Please install the dependencies in requirements.txt, then restart the program.",
        "details": "Details",
        "not_recording": "No recording is currently in progress.",
        "source_speaker": "speaker",
        "source_microphone": "microphone",
        "speaker_me": "Me",
        "speaker_others": "Others",
        "source_unavailable": "{source} recording is unavailable. Other available audio sources will continue to be saved.",
        "all_sources_unavailable": "Speaker and microphone recording are both unavailable.",
        "save_question": "Do you want to save the MP3 file and text file?",
        "save_dialog_title": "Save recording and transcript",
        "mp3_files": "MP3 files",
        "all_files": "All files",
        "overwrite_text": "{filename} already exists. Overwrite it?",
        "save_failed": "Save failed:",
        "saved": "Saved:",
        "about_title": "About",
        "about_body": "This software is open source and follows the principle of free use.",
        "language": "Language",
        "transcription_missing": "faster-whisper is not installed. Recording will be saved, but the text file will be empty.\n\nInstall the dependencies in requirements.txt to enable local live transcription.",
        "transcription_unavailable": "Live transcription is currently unavailable. Recording will continue to be saved.",
        "recording_failed": "Recording failed:",
        "meeting_summary_title": "Meeting Summary",
        "live_minutes_title": "Live Meeting Minutes",
        "main_discussion_title": "Main discussion",
        "action_items_title": "Action items",
        "owner_label": "Owner",
        "owner_unknown": "Unassigned",
        "owner_team": "Team",
        "summary_note": "This summary is generated locally from the transcript.",
        "no_summary": "Not enough transcript text to summarize.",
        "no_action_items": "No clear action items detected.",
    },
    "zh": {
        "app_name": "本地会议录音",
        "record": "录音",
        "text_window": "文本窗口",
        "stop": "停止录音",
        "screenshot": "截屏",
        "auto_screenshot_on": "Auto Screenshot On",
        "auto_screenshot_off": "Auto Screenshot Off",
        "about": "关于",
        "exit": "退出",
        "transcript_title": "实时转录文本",
        "copy_text": "复制文本到剪贴板",
        "stopping_wait": "正在停止录音并补全文本转录，请稍候。",
        "already_recording": "正在录音中，请点击“停止录音”结束录音。",
        "ask_auto_screenshot": "是否为本次录音开启 Auto Screenshot？",
        "screenshot_saved": "截屏已保存：",
        "screenshot_failed": "截屏失败：",
        "missing_deps_title": "缺少录音依赖，无法开始录音。",
        "missing_deps_body": "请先安装 requirements.txt 中的依赖，然后重新运行程序。",
        "details": "详细信息",
        "not_recording": "当前并未录音。",
        "source_speaker": "扬声器",
        "source_microphone": "麦克风",
        "speaker_me": "本人",
        "speaker_others": "其他人",
        "source_unavailable": "{source}录音不可用，将继续保存其他可用声音。",
        "all_sources_unavailable": "扬声器和麦克风录音都不可用。",
        "save_question": "是否保存 MP3 文件和文本文件？",
        "save_dialog_title": "保存录音和转录文本",
        "mp3_files": "MP3 文件",
        "all_files": "所有文件",
        "overwrite_text": "{filename} 已存在，是否覆盖？",
        "save_failed": "保存失败：",
        "saved": "已保存：",
        "about_title": "关于",
        "about_body": "该软件为开源软件，遵循自由使用原则。",
        "language": "语言",
        "transcription_missing": "未安装 faster-whisper，当前只会保存录音，文本文件将为空。\n\n安装 requirements.txt 中的依赖后，可启用本地实时转录。",
        "transcription_unavailable": "实时转录暂时不可用，录音会继续保存。",
        "recording_failed": "录音失败：",
        "meeting_summary_title": "会议总结",
        "live_minutes_title": "实时会议纪要",
        "main_discussion_title": "主要讨论",
        "action_items_title": "Action items",
        "owner_label": "负责人",
        "owner_unknown": "未分配",
        "owner_team": "团队",
        "summary_note": "该总结由本地规则根据转录文本生成。",
        "no_summary": "转录文本不足，无法生成总结。",
        "no_action_items": "未检测到明确的 action items。",
    },
}


def quiet_library_noise() -> None:
    logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
    logging.getLogger("faster_whisper").setLevel(logging.ERROR)
    warnings.filterwarnings(
        "ignore",
        message=".*data discontinuity in recording.*",
        module=r"soundcard\.mediafoundation",
    )


def recording_file_stem() -> str:
    return dt.datetime.now().strftime("%Y%m%d_%H")


def app_directory() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def screenshot_folder_name() -> str:
    return dt.datetime.now().strftime("%Y%m%d_%H")


def screenshot_file_stem() -> str:
    return dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def make_tray_icon() -> Image.Image:
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    draw.rounded_rectangle((10, 6, 54, 58), radius=16, fill=(34, 99, 235, 255))
    draw.rounded_rectangle((25, 14, 39, 37), radius=6, fill=(255, 255, 255, 255))
    draw.rounded_rectangle((22, 24, 42, 46), radius=10, outline=(255, 255, 255, 255), width=4)
    draw.line((32, 46, 32, 53), fill=(255, 255, 255, 255), width=4)
    draw.line((24, 53, 40, 53), fill=(255, 255, 255, 255), width=4)
    return image


def float_audio_to_int16(samples: np.ndarray) -> np.ndarray:
    clipped = np.clip(samples, -1.0, 1.0)
    return (clipped * 32767).astype(np.int16)


def normalize_audio(data: np.ndarray) -> np.ndarray:
    audio = np.asarray(data, dtype=np.float32)
    if audio.ndim == 2:
        audio = audio.mean(axis=1)
    return audio.reshape(-1)


def mix_audio_sources(chunks: list[np.ndarray]) -> np.ndarray:
    tracks = [normalize_audio(chunk) for chunk in chunks if chunk is not None and chunk.size > 0]
    if not tracks:
        return np.empty(0, dtype=np.float32)

    max_length = max(track.size for track in tracks)
    mixed = np.zeros(max_length, dtype=np.float32)
    for track in tracks:
        mixed[: track.size] += track

    peak = float(np.max(np.abs(mixed))) if mixed.size else 0.0
    if peak > 1.0:
        mixed /= peak
    return mixed


def fit_audio_length(audio: np.ndarray, target_length: int) -> np.ndarray:
    audio = normalize_audio(audio)
    if audio.size == target_length:
        return audio.astype(np.float32, copy=False)
    if audio.size > target_length:
        return audio[:target_length].astype(np.float32, copy=False)

    padded = np.zeros(target_length, dtype=np.float32)
    padded[: audio.size] = audio
    return padded


def resample_audio(audio: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    audio = normalize_audio(audio)
    if audio.size == 0 or source_rate == target_rate:
        return audio.astype(np.float32, copy=False)

    target_length = max(1, int(round(audio.size * target_rate / source_rate)))
    source_positions = np.arange(audio.size, dtype=np.float32)
    target_positions = np.linspace(0, audio.size - 1, target_length, dtype=np.float32)
    return np.interp(target_positions, source_positions, audio).astype(np.float32)


def audio_rms(audio: np.ndarray) -> float:
    audio = normalize_audio(audio)
    if audio.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(audio * audio)))


def screenshot_signature(image: Image.Image) -> np.ndarray:
    preview = image.resize(SCREENSHOT_PREVIEW_SIZE).convert("L")
    return np.asarray(preview, dtype=np.int16)


def screenshot_change_score(before: np.ndarray, after: np.ndarray) -> float:
    if before.shape != after.shape:
        return 1.0
    return float(np.mean(np.abs(after - before)) / 255.0)


def split_sentences(text: str) -> list[str]:
    cleaned = re.sub(r"\s+", " ", text.strip())
    if not cleaned:
        return []
    sentences = re.findall(r"[^.!?。！？；;]+[.!?。！？；;]?", cleaned)
    return [sentence.strip() for sentence in sentences if sentence.strip()]


def summarize_sentences(sentences: list[str], limit: int = 3) -> list[str]:
    if not sentences:
        return []
    words = [
        word.lower()
        for sentence in sentences
        for word in re.findall(r"[A-Za-z][A-Za-z'-]+|[\u4e00-\u9fff]", sentence)
    ]
    stop_words = {
        "the", "and", "that", "this", "with", "for", "you", "are", "was", "were",
        "have", "has", "had", "from", "not", "but", "they", "our", "your", "can",
        "will", "would", "could", "should", "there", "here", "about", "just",
    }
    frequencies: dict[str, int] = {}
    for word in words:
        if word in stop_words or len(word) <= 2:
            continue
        frequencies[word] = frequencies.get(word, 0) + 1
    if not frequencies:
        return sentences[:limit]

    scored: list[tuple[float, int, str]] = []
    for index, sentence in enumerate(sentences):
        tokens = re.findall(r"[A-Za-z][A-Za-z'-]+|[\u4e00-\u9fff]", sentence.lower())
        score = sum(frequencies.get(token, 0) for token in tokens)
        if score:
            scored.append((score / max(1, len(tokens)), index, sentence))

    best = sorted(scored, key=lambda item: item[0], reverse=True)[:limit]
    return [sentence for _score, _index, sentence in sorted(best, key=lambda item: item[1])]


def extract_action_items(sentences: list[str], limit: int = 8) -> list[str]:
    patterns = (
        r"\b(action item|todo|to do|follow up|next step|need to|needs to|we should|we need|"
        r"i will|we will|you will|can you|please|assign|owner|deadline|by tomorrow|by next|"
        r"before next|responsible|assigned to|take care|send|share|prepare|schedule|confirm|review|update|fix|check)\b",
        r"(需要|待办|行动项|下一步|跟进|确认|安排|发送|准备|更新|修复|检查|评审|负责|截止)",
    )
    action_items: list[str] = []
    for sentence in sentences:
        normalized = sentence.lower()
        if any(re.search(pattern, normalized) for pattern in patterns):
            action_items.append(sentence)
        if len(action_items) >= limit:
            break
    return action_items


def wav_has_audio(path: Path, threshold: float = MIN_TRANSCRIBE_RMS) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    with contextlib.suppress(Exception):
        with wave.open(str(path), "rb") as wav_file:
            frame_count = wav_file.getnframes()
            if frame_count <= 0:
                return False
            frames_per_read = max(1, wav_file.getframerate() * 5)
            total_square = 0.0
            total_count = 0
            while total_count < frame_count:
                frames = wav_file.readframes(frames_per_read)
                if not frames:
                    break
                audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
                total_square += float(np.sum(audio * audio))
                total_count += audio.size
        if total_count <= 0:
            return False
        return float(np.sqrt(total_square / total_count)) >= threshold
    return False


@dataclass
class AudioFrameSet:
    sources: dict[str, np.ndarray]


@dataclass
class TranscriptJob:
    source_name: str
    start: float
    end: float
    audio: np.ndarray


@dataclass
class ReviewJob:
    source_name: str
    start: float
    end: float
    audio: np.ndarray


@dataclass
class TranscriptBlock:
    source_name: str
    sentences: list[str] = field(default_factory=list)


@dataclass
class TranscriptEntry:
    start: float
    source_name: str
    text: str
    end: float = 0.0


@dataclass
class RecordingSession:
    file_stem: str
    temp_dir: tempfile.TemporaryDirectory[str]
    mp3_path: Path
    wav_path: Path
    source_wav_paths: dict[str, Path]
    text_path: Path
    stop_event: threading.Event = field(default_factory=threading.Event)
    active_event: threading.Event = field(default_factory=threading.Event)
    audio_queue: "queue.Queue[Optional[TranscriptJob]]" = field(default_factory=queue.Queue)
    review_queue: "queue.Queue[Optional[ReviewJob]]" = field(default_factory=queue.Queue)
    minutes_queue: "queue.Queue[Optional[str]]" = field(default_factory=queue.Queue)
    mp3_queue: "queue.Queue[Optional[AudioFrameSet]]" = field(default_factory=queue.Queue)
    recorder_thread: Optional[threading.Thread] = None
    writer_thread: Optional[threading.Thread] = None
    transcription_thread: Optional[threading.Thread] = None
    review_thread: Optional[threading.Thread] = None
    transcription_result_thread: Optional[threading.Thread] = None
    minutes_thread: Optional[threading.Thread] = None
    transcriber_input_queue: Optional[object] = None
    transcriber_output_queue: Optional[object] = None
    transcriber_cancel_realtime_event: Optional[object] = None
    transcriber_process: Optional[object] = None
    transcriber_stop_sent: bool = False
    transcriber_lock: threading.Lock = field(default_factory=threading.Lock)
    draft_entries: list[TranscriptEntry] = field(default_factory=list)
    final_entries: list[TranscriptEntry] = field(default_factory=list)
    finalized_until: dict[str, float] = field(default_factory=lambda: {source: 0.0 for source in AUDIO_SOURCES})
    final_lock: threading.Lock = field(default_factory=threading.Lock)
    saved_text_path: Optional[Path] = None
    save_decision_event: threading.Event = field(default_factory=threading.Event)

    def cleanup(self) -> None:
        with contextlib.suppress(Exception):
            self.temp_dir.cleanup()


class TranscriptWindow:
    def __init__(self, root: tk.Tk, app: "MeetingRecorderApp") -> None:
        self.root = root
        self.app = app
        self.window = tk.Toplevel(root)
        self.window.geometry("720x460")
        self.window.minsize(420, 260)
        self.window.protocol("WM_DELETE_WINDOW", self.close)

        frame = ttk.Frame(self.window, padding=12)
        frame.pack(fill=tk.BOTH, expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        text_frame = ttk.Frame(frame)
        text_frame.grid(row=0, column=0, sticky="nsew")
        text_frame.columnconfigure(0, weight=1)
        text_frame.rowconfigure(0, weight=1)

        self.text = tk.Text(
            text_frame,
            wrap=tk.WORD,
            undo=False,
            font=("Microsoft YaHei UI", 11),
            padx=10,
            pady=10,
        )
        self.text.configure(state=tk.DISABLED)

        scrollbar = ttk.Scrollbar(text_frame, orient=tk.VERTICAL, command=self.text.yview)
        self.text.configure(yscrollcommand=scrollbar.set)
        self.text.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        button_row = ttk.Frame(frame)
        button_row.grid(row=1, column=0, sticky="ew", pady=(10, 0))

        self.copy_button = ttk.Button(button_row, command=self.copy_text)
        self.copy_button.pack(side=tk.RIGHT, ipadx=16, ipady=4)
        self.refresh_language()

    def refresh_language(self) -> None:
        self.window.title(self.app.t("transcript_title"))
        self.copy_button.configure(text=self.app.t("copy_text"))

    def close(self) -> None:
        self.app.transcript_window = None
        self.window.destroy()

    def set_text(self, value: str) -> None:
        self.text.configure(state=tk.NORMAL)
        self.text.delete("1.0", tk.END)
        if value:
            self.text.insert(tk.END, value)
        self.text.configure(state=tk.DISABLED)
        self.text.see(tk.END)

    def copy_text(self) -> None:
        value = self.text.get("1.0", tk.END).strip()
        self.root.clipboard_clear()
        self.root.clipboard_append(value)
        self.root.update_idletasks()


class MeetingRecorderApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.withdraw()

        self.state = "idle"
        self.state_lock = threading.Lock()
        self.session: Optional[RecordingSession] = None
        self.transcript_window: Optional[TranscriptWindow] = None
        self.about_window: Optional[tk.Toplevel] = None
        self.about_language_var: Optional[tk.StringVar] = None
        self.about_body_label: Optional[ttk.Label] = None
        self.about_language_label: Optional[ttk.Label] = None
        self.about_english_check: Optional[ttk.Checkbutton] = None
        self.about_chinese_check: Optional[ttk.Checkbutton] = None
        self.language = DEFAULT_LANGUAGE
        self.shutting_down = False
        self.transcript_blocks: list[TranscriptBlock] = []
        self.transcript_body_text = ""
        self.meeting_minutes_text = ""
        self.transcript_text = ""
        self.transcript_lock = threading.Lock()
        self.transcription_warning_shown = False
        self.auto_screenshot_enabled = False
        self.screenshot_dir: Optional[Path] = None
        self.screenshot_lock = threading.Lock()
        self.screenshot_stop_event = threading.Event()
        self.screenshot_thread: Optional[threading.Thread] = None
        self.screenshot_warning_shown = False

        self.icon = pystray.Icon(self.t("app_name"), make_tray_icon(), self.t("app_name"), self.build_menu())

    def t(self, key: str, **kwargs: object) -> str:
        value = TRANSLATIONS[self.language].get(key, TRANSLATIONS["en"].get(key, key))
        if kwargs:
            return value.format(**kwargs)
        return value

    def title(self) -> str:
        return self.t("app_name")

    def speaker_label(self, source_name: str) -> str:
        return self.t(SPEAKER_LABEL_KEYS.get(source_name, "speaker_others"))

    def format_transcript_blocks(self, blocks: list[TranscriptBlock]) -> str:
        paragraphs: list[str] = []
        for block in blocks:
            if not block.sentences:
                continue
            paragraphs.append(f"{self.speaker_label(block.source_name)}:\n" + "\n".join(block.sentences))
        return "\n\n".join(paragraphs)

    def compose_transcript_document(self, transcript: str, minutes: str) -> str:
        transcript = transcript.strip()
        minutes = minutes.strip()
        if transcript and minutes:
            return f"{transcript}\n\n{minutes}"
        return transcript or minutes

    def clear_transcript(self) -> None:
        with self.transcript_lock:
            self.transcript_blocks = []
            self.transcript_body_text = ""
            self.meeting_minutes_text = ""
            self.transcript_text = ""

    @property
    def record_menu_text(self) -> str:
        return self.t("record")

    @property
    def auto_screenshot_menu_text(self) -> str:
        if self.auto_screenshot_enabled:
            return self.t("auto_screenshot_on")
        return self.t("auto_screenshot_off")

    def build_menu(self) -> Menu:
        return Menu(
            MenuItem(lambda item: self.record_menu_text, self.tray_record_clicked, default=True),
            MenuItem(lambda item: self.t("text_window"), self.tray_text_window_clicked),
            MenuItem(lambda item: self.t("stop"), self.tray_stop_clicked),
            MenuItem(lambda item: self.t("screenshot"), self.tray_screenshot_clicked),
            MenuItem(lambda item: self.auto_screenshot_menu_text, self.tray_auto_screenshot_clicked),
            MenuItem(lambda item: self.t("about"), self.tray_about_clicked),
            MenuItem(lambda item: self.t("exit"), self.tray_exit_clicked),
        )

    def run(self) -> None:
        tray_thread = threading.Thread(target=self.icon.run, name="tray", daemon=True)
        tray_thread.start()
        try:
            self.root.mainloop()
        finally:
            with contextlib.suppress(Exception):
                self.icon.stop()

    def tray_record_clicked(self, icon: pystray.Icon, item: MenuItem) -> None:
        self.root.after(0, self.toggle_recording)

    def tray_text_window_clicked(self, icon: pystray.Icon, item: MenuItem) -> None:
        self.root.after(0, self.open_transcript_window)

    def tray_stop_clicked(self, icon: pystray.Icon, item: MenuItem) -> None:
        self.root.after(0, self.stop_recording)

    def tray_screenshot_clicked(self, icon: pystray.Icon, item: MenuItem) -> None:
        self.root.after(0, self.take_manual_screenshot)

    def tray_auto_screenshot_clicked(self, icon: pystray.Icon, item: MenuItem) -> None:
        self.root.after(0, self.toggle_auto_screenshot)

    def tray_about_clicked(self, icon: pystray.Icon, item: MenuItem) -> None:
        self.root.after(0, self.show_about)

    def tray_exit_clicked(self, icon: pystray.Icon, item: MenuItem) -> None:
        self.root.after(0, self.exit_app)

    def update_tray_menu(self) -> None:
        with contextlib.suppress(Exception):
            self.icon.title = self.title()
        with contextlib.suppress(Exception):
            self.icon.update_menu()

    def refresh_language(self) -> None:
        self.root.title(self.title())
        if self.transcript_window is not None:
            self.transcript_window.refresh_language()
        self.refresh_about_window()
        self.update_tray_menu()

    def set_language(self, language: str) -> None:
        if language not in TRANSLATIONS:
            return
        if self.language == language:
            self.refresh_about_window()
            return
        self.language = language
        self.refresh_language()

    def screenshot_root(self) -> Path:
        return app_directory() / "screen"

    def ensure_screenshot_dir(self) -> Path:
        directory = self.screenshot_root() / screenshot_folder_name()
        with self.screenshot_lock:
            directory.mkdir(parents=True, exist_ok=True)
            self.screenshot_dir = directory
        return directory

    def next_screenshot_path(self) -> Path:
        directory = self.ensure_screenshot_dir()
        stem = screenshot_file_stem()
        path = directory / f"{stem}.jpg"
        index = 1
        while path.exists():
            path = directory / f"{stem}_{index}.jpg"
            index += 1
        return path

    def capture_screen_image(self) -> Image.Image:
        try:
            image = ImageGrab.grab(all_screens=True)
        except TypeError:
            image = ImageGrab.grab()
        if image.mode != "RGB":
            image = image.convert("RGB")
        return image

    def save_screenshot(self, image: Optional[Image.Image] = None, notify: bool = False) -> Optional[Path]:
        try:
            screenshot = image or self.capture_screen_image()
            path = self.next_screenshot_path()
            screenshot.save(path, "JPEG", quality=92)
            if notify:
                messagebox.showinfo(self.title(), f"{self.t('screenshot_saved')}\n{path}")
            return path
        except Exception as exc:
            if notify:
                messagebox.showerror(self.title(), f"{self.t('screenshot_failed')}\n{exc}")
            elif not self.screenshot_warning_shown:
                self.screenshot_warning_shown = True
                self.root.after(
                    0,
                    lambda error=exc: messagebox.showwarning(
                        self.title(),
                        f"{self.t('screenshot_failed')}\n{error}",
                    ),
                )
            return None

    def take_manual_screenshot(self) -> None:
        self.save_screenshot(notify=True)

    def toggle_auto_screenshot(self) -> None:
        self.set_auto_screenshot_enabled(not self.auto_screenshot_enabled)

    def set_auto_screenshot_enabled(self, enabled: bool) -> None:
        if enabled:
            self.auto_screenshot_enabled = True
            self.start_auto_screenshot()
        else:
            self.auto_screenshot_enabled = False
            self.stop_auto_screenshot()
        self.update_tray_menu()

    def start_auto_screenshot(self) -> None:
        if self.screenshot_thread is not None and self.screenshot_thread.is_alive():
            return
        self.ensure_screenshot_dir()
        self.screenshot_stop_event.clear()
        self.screenshot_warning_shown = False
        self.screenshot_thread = threading.Thread(
            target=self.auto_screenshot_worker,
            name="auto-screenshot",
            daemon=True,
        )
        self.screenshot_thread.start()

    def stop_auto_screenshot(self) -> None:
        self.screenshot_stop_event.set()
        thread = self.screenshot_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)
        if thread is None or not thread.is_alive():
            self.screenshot_thread = None

    def auto_screenshot_worker(self) -> None:
        last_signature: Optional[np.ndarray] = None
        last_saved_at = 0.0
        try:
            while not self.screenshot_stop_event.is_set():
                image = self.capture_screen_image()
                signature = screenshot_signature(image)

                if last_signature is not None:
                    change_score = screenshot_change_score(last_signature, signature)
                    now = time.monotonic()
                    if (
                        change_score >= AUTO_SCREENSHOT_CHANGE_THRESHOLD
                        and now - last_saved_at >= AUTO_SCREENSHOT_COOLDOWN_SECONDS
                    ):
                        if self.screenshot_stop_event.wait(AUTO_SCREENSHOT_STABLE_SECONDS):
                            break
                        stable_image = self.capture_screen_image()
                        stable_signature = screenshot_signature(stable_image)
                        stable_score = screenshot_change_score(signature, stable_signature)
                        if stable_score <= AUTO_SCREENSHOT_STABLE_THRESHOLD:
                            self.save_screenshot(stable_image, notify=False)
                            last_saved_at = time.monotonic()
                            last_signature = stable_signature
                            if self.screenshot_stop_event.wait(AUTO_SCREENSHOT_SCAN_SECONDS):
                                break
                            continue
                        signature = stable_signature

                last_signature = signature
                if self.screenshot_stop_event.wait(AUTO_SCREENSHOT_SCAN_SECONDS):
                    break
        except Exception as exc:
            self.auto_screenshot_enabled = False
            self.root.after(0, self.update_tray_menu)
            if not self.screenshot_warning_shown:
                self.screenshot_warning_shown = True
                self.root.after(
                    0,
                    lambda error=exc: messagebox.showwarning(
                        self.title(),
                        f"{self.t('screenshot_failed')}\n{error}",
                    ),
                )

    def exit_app(self) -> None:
        if self.shutting_down:
            return
        self.shutting_down = True
        self.set_auto_screenshot_enabled(False)
        with contextlib.suppress(Exception):
            self.icon.stop()

        session = self.session
        if session is not None:
            session.stop_event.set()
            session.active_event.set()
            self.cancel_pending_transcription(session)
            session.review_queue.put(None)
            self.cancel_pending_minutes(session)

        threading.Thread(
            target=self.finish_exit_worker,
            args=(session,),
            name="exit-cleanup",
            daemon=True,
        ).start()

    def finish_exit_worker(self, session: Optional[RecordingSession]) -> None:
        if session is not None:
            if session.recorder_thread is not None:
                session.recorder_thread.join(timeout=4)
            if session.writer_thread is not None:
                session.writer_thread.join(timeout=4)
            if session.transcription_thread is not None:
                session.transcription_thread.join(timeout=1)
            if session.review_thread is not None:
                session.review_thread.join(timeout=4)
            self.stop_transcriber_process(session, timeout=2, terminate=True)
            if session.minutes_thread is not None:
                session.minutes_thread.join(timeout=1)
            session.cleanup()
        self.root.after(0, self.finalize_exit)

    def finalize_exit(self) -> None:
        self.session = None
        self.clear_transcript()
        with contextlib.suppress(Exception):
            if self.transcript_window is not None:
                self.transcript_window.window.destroy()
        self.transcript_window = None
        with contextlib.suppress(Exception):
            if self.about_window is not None:
                self.about_window.destroy()
        self.about_window = None
        with contextlib.suppress(Exception):
            self.root.quit()
        with contextlib.suppress(Exception):
            self.root.destroy()

    def refresh_about_window(self) -> None:
        if self.about_window is None or not self.about_window.winfo_exists():
            return
        self.about_window.title(self.t("about_title"))
        if self.about_body_label is not None:
            self.about_body_label.configure(text=self.t("about_body"))
        if self.about_language_label is not None:
            self.about_language_label.configure(text=self.t("language"))
        if self.about_english_check is not None:
            self.about_english_check.configure(text="English")
        if self.about_chinese_check is not None:
            self.about_chinese_check.configure(text="中文")
        if self.about_language_var is not None:
            self.about_language_var.set(self.language)

    def toggle_recording(self) -> None:
        with self.state_lock:
            state = self.state

        if state == "idle":
            self.start_recording()
        elif state == "recording":
            messagebox.showinfo(self.title(), self.t("already_recording"))
        elif state == "stopping":
            messagebox.showinfo(self.title(), self.t("stopping_wait"))

    def start_recording(self) -> None:
        try:
            import lameenc  # noqa: F401
            import soundcard as sc  # noqa: F401
        except ImportError as exc:
            messagebox.showerror(
                self.title(),
                f"{self.t('missing_deps_title')}\n\n"
                f"{self.t('missing_deps_body')}\n\n"
                f"{self.t('details')}: {exc}",
            )
            return

        auto_screenshot_requested = messagebox.askyesno(
            self.title(),
            self.t("ask_auto_screenshot"),
        )
        self.set_auto_screenshot_enabled(auto_screenshot_requested)

        file_stem = recording_file_stem()
        temp_dir = tempfile.TemporaryDirectory(prefix="local_meeting_recorder_")
        temp_root = Path(temp_dir.name)
        session = RecordingSession(
            file_stem=file_stem,
            temp_dir=temp_dir,
            mp3_path=temp_root / f"{file_stem}.mp3",
            wav_path=temp_root / f"{file_stem}.wav",
            source_wav_paths={
                "source_speaker": temp_root / f"{file_stem}.speaker.wav",
                "source_microphone": temp_root / f"{file_stem}.microphone.wav",
            },
            text_path=temp_root / f"{file_stem}.txt",
        )
        session.active_event.set()
        session.text_path.write_text("", encoding="utf-8")

        self.clear_transcript()
        if self.transcript_window is not None:
            self.transcript_window.set_text("")

        self.session = session
        with self.state_lock:
            self.state = "recording"
        self.update_tray_menu()
        self.start_transcriber_process(session)

        session.recorder_thread = threading.Thread(
            target=self.recording_worker,
            args=(session,),
            name="audio-recorder",
            daemon=True,
        )
        session.writer_thread = threading.Thread(
            target=self.mp3_writer_worker,
            args=(session,),
            name="mp3-writer",
            daemon=True,
        )
        session.transcription_thread = threading.Thread(
            target=self.transcription_worker,
            args=(session,),
            name="transcriber",
            daemon=True,
        )
        session.review_thread = threading.Thread(
            target=self.review_transcription_worker,
            args=(session,),
            name="review-transcriber",
            daemon=True,
        )
        session.minutes_thread = threading.Thread(
            target=self.meeting_minutes_worker,
            args=(session,),
            name="meeting-minutes",
            daemon=True,
        )
        session.recorder_thread.start()
        session.writer_thread.start()
        session.transcription_thread.start()
        session.review_thread.start()
        session.minutes_thread.start()

    def stop_recording(self) -> None:
        session = self.session
        with self.state_lock:
            state = self.state

        if session is None or state == "idle":
            messagebox.showinfo(self.title(), self.t("not_recording"))
            return

        if state == "stopping":
            return

        self.set_auto_screenshot_enabled(False)
        session.stop_event.set()
        session.active_event.set()
        self.cancel_pending_transcription(session)
        with self.state_lock:
            self.state = "stopping"
        self.update_tray_menu()

        threading.Thread(
            target=self.finish_stop_worker,
            args=(session,),
            name="finish-stop",
            daemon=True,
        ).start()

    def recording_worker(self, session: RecordingSession) -> None:
        source_threads: list[threading.Thread] = []
        transcription_buffers: dict[str, list[np.ndarray]] = {source: [] for source in AUDIO_SOURCES}
        buffered_frames: dict[str, int] = {source: 0 for source in AUDIO_SOURCES}
        transcription_start_frames: dict[str, int] = {source: 0 for source in AUDIO_SOURCES}
        source_stream_frames: dict[str, int] = {source: 0 for source in AUDIO_SOURCES}
        review_buffers: dict[str, list[np.ndarray]] = {source: [] for source in AUDIO_SOURCES}
        review_buffered_frames: dict[str, int] = {source: 0 for source in AUDIO_SOURCES}
        review_start_frames: dict[str, int] = {source: 0 for source in AUDIO_SOURCES}
        review_frames = int(TRANSCRIBE_SAMPLE_RATE * REVIEW_SECONDS)
        review_overlap_frames = int(TRANSCRIBE_SAMPLE_RATE * REVIEW_OVERLAP_SECONDS)
        review_min_frames = int(TRANSCRIBE_SAMPLE_RATE * REVIEW_MIN_AUDIO_SECONDS)

        try:
            import soundcard as sc

            speaker = sc.default_speaker()
            microphone = sc.default_microphone()
            loopback = sc.get_microphone(speaker.name, include_loopback=True)

            chunk_frames = max(1, int(RECORD_SAMPLE_RATE * CHUNK_SECONDS))
            transcribe_frames = int(TRANSCRIBE_SAMPLE_RATE * TRANSCRIBE_SECONDS)
            zero_chunk = np.zeros(chunk_frames, dtype=np.float32)

            source_queues: dict[str, "queue.Queue[Optional[np.ndarray]]"] = {source: queue.Queue() for source in AUDIO_SOURCES}
            source_done = {name: False for name in source_queues}
            source_errors: "queue.Queue[tuple[str, Exception]]" = queue.Queue()

            for source_name, source in (("source_speaker", loopback), ("source_microphone", microphone)):
                thread = threading.Thread(
                    target=self.audio_capture_worker,
                    args=(session, source_name, source, source_queues[source_name], source_errors),
                    name=f"capture-{source_name}",
                    daemon=True,
                )
                source_threads.append(thread)
                thread.start()

            while not session.stop_event.is_set():
                if not session.active_event.wait(0.1):
                    continue

                self.show_capture_warnings(session, source_errors)
                chunks = self.collect_source_chunks(source_queues, source_done, chunk_frames)
                if not chunks and all(source_done.values()):
                    break
                if not chunks:
                    if all(source_done.values()):
                        break
                    continue

                audio = mix_audio_sources(list(chunks.values()))
                if audio.size == 0:
                    continue

                session.mp3_queue.put(AudioFrameSet(chunks))

                for source_name in AUDIO_SOURCES:
                    source_audio = chunks.get(source_name, zero_chunk)
                    transcript_audio = resample_audio(source_audio, RECORD_SAMPLE_RATE, TRANSCRIBE_SAMPLE_RATE)
                    chunk_start_frame = source_stream_frames[source_name]
                    source_stream_frames[source_name] += transcript_audio.size
                    self.buffer_review_audio(
                        session,
                        source_name,
                        transcript_audio,
                        review_buffers,
                        review_buffered_frames,
                        review_start_frames,
                        review_frames,
                        review_overlap_frames,
                    )

                    if session.stop_event.is_set() or audio_rms(transcript_audio) < MIN_TRANSCRIBE_RMS:
                        continue
                    if not transcription_buffers[source_name]:
                        transcription_start_frames[source_name] = chunk_start_frame
                    transcription_buffers[source_name].append(transcript_audio)
                    buffered_frames[source_name] += transcript_audio.size
                    if buffered_frames[source_name] >= transcribe_frames:
                        job_audio = np.concatenate(transcription_buffers[source_name])
                        job_start_frame = transcription_start_frames[source_name]
                        session.audio_queue.put(
                            TranscriptJob(
                                source_name,
                                job_start_frame / TRANSCRIBE_SAMPLE_RATE,
                                (job_start_frame + job_audio.size) / TRANSCRIBE_SAMPLE_RATE,
                                job_audio,
                            )
                        )
                        transcription_buffers[source_name].clear()
                        buffered_frames[source_name] = 0

            if all(source_done.values()) and not session.stop_event.is_set():
                raise RuntimeError(self.t("all_sources_unavailable"))

            if not session.stop_event.is_set():
                min_frames = int(TRANSCRIBE_SAMPLE_RATE * REALTIME_MIN_AUDIO_SECONDS)
                for source_name, source_buffer in transcription_buffers.items():
                    if buffered_frames[source_name] >= min_frames and source_buffer:
                        job_audio = np.concatenate(source_buffer)
                        job_start_frame = transcription_start_frames[source_name]
                        session.audio_queue.put(
                            TranscriptJob(
                                source_name,
                                job_start_frame / TRANSCRIBE_SAMPLE_RATE,
                                (job_start_frame + job_audio.size) / TRANSCRIBE_SAMPLE_RATE,
                                job_audio,
                            )
                        )
        except Exception as exc:
            self.root.after(0, lambda error=exc: self.handle_recording_error(session, error))
        finally:
            session.stop_event.set()
            for thread in source_threads:
                thread.join(timeout=2)
            self.flush_review_buffers(
                session,
                review_buffers,
                review_buffered_frames,
                review_start_frames,
                review_min_frames,
            )
            session.mp3_queue.put(None)
            session.audio_queue.put(None)
            session.review_queue.put(None)

    def buffer_review_audio(
        self,
        session: RecordingSession,
        source_name: str,
        audio: np.ndarray,
        review_buffers: dict[str, list[np.ndarray]],
        review_buffered_frames: dict[str, int],
        review_start_frames: dict[str, int],
        target_frames: int,
        overlap_frames: int,
    ) -> None:
        if audio.size == 0:
            return
        review_buffers[source_name].append(audio)
        review_buffered_frames[source_name] += audio.size
        while review_buffered_frames[source_name] >= target_frames:
            self.flush_review_buffer(
                session,
                source_name,
                review_buffers,
                review_buffered_frames,
                review_start_frames,
                target_frames,
                overlap_frames=overlap_frames,
            )

    def flush_review_buffers(
        self,
        session: RecordingSession,
        review_buffers: dict[str, list[np.ndarray]],
        review_buffered_frames: dict[str, int],
        review_start_frames: dict[str, int],
        min_frames: int,
    ) -> None:
        for source_name in AUDIO_SOURCES:
            self.flush_review_buffer(
                session,
                source_name,
                review_buffers,
                review_buffered_frames,
                review_start_frames,
                min_frames,
                final=True,
            )

    def flush_review_buffer(
        self,
        session: RecordingSession,
        source_name: str,
        review_buffers: dict[str, list[np.ndarray]],
        review_buffered_frames: dict[str, int],
        review_start_frames: dict[str, int],
        min_frames: int,
        overlap_frames: int = 0,
        final: bool = False,
    ) -> None:
        source_buffer = review_buffers[source_name]
        if not source_buffer:
            return

        audio = np.concatenate(source_buffer)
        if final:
            review_start_frames[source_name] += audio.size
            review_buffers[source_name] = []
            review_buffered_frames[source_name] = 0
            if audio.size < min_frames or audio_rms(audio) < MIN_TRANSCRIBE_RMS:
                return
            start = (review_start_frames[source_name] - audio.size) / TRANSCRIBE_SAMPLE_RATE
            end = review_start_frames[source_name] / TRANSCRIBE_SAMPLE_RATE
            session.review_queue.put(ReviewJob(source_name, start, end, audio))
            return

        if audio.size < min_frames:
            return

        job_audio = audio[:min_frames]
        start_frame = review_start_frames[source_name]
        advance_frames = max(1, min(job_audio.size, job_audio.size - overlap_frames))
        review_start_frames[source_name] += advance_frames
        remaining = audio[advance_frames:]
        review_buffers[source_name] = [remaining] if remaining.size else []
        review_buffered_frames[source_name] = int(remaining.size)

        if audio_rms(job_audio) >= MIN_TRANSCRIBE_RMS:
            start = start_frame / TRANSCRIBE_SAMPLE_RATE
            end = (start_frame + job_audio.size) / TRANSCRIBE_SAMPLE_RATE
            session.review_queue.put(ReviewJob(source_name, start, end, job_audio))

    def collect_source_chunks(
        self,
        source_queues: dict[str, "queue.Queue[Optional[np.ndarray]]"],
        source_done: dict[str, bool],
        chunk_frames: int,
    ) -> dict[str, np.ndarray]:
        chunks: dict[str, np.ndarray] = {}
        pending = {name for name, done in source_done.items() if not done}
        deadline = time.monotonic() + CHUNK_SECONDS + 0.25

        while pending and time.monotonic() < deadline:
            received = False
            for source_name in list(pending):
                try:
                    item = source_queues[source_name].get_nowait()
                except queue.Empty:
                    continue

                received = True
                pending.remove(source_name)
                if item is None:
                    source_done[source_name] = True
                elif item.size:
                    chunks[source_name] = fit_audio_length(item, chunk_frames)

            if pending and not received:
                time.sleep(0.01)

        return chunks

    def mp3_writer_worker(self, session: RecordingSession) -> None:
        encoder = None
        mp3_file = None
        wav_file = None
        source_wav_files: dict[str, wave.Wave_write] = {}
        chunk_frames = max(1, int(RECORD_SAMPLE_RATE * CHUNK_SECONDS))

        try:
            import lameenc

            encoder = lameenc.Encoder()
            encoder.set_bit_rate(128)
            encoder.set_in_sample_rate(RECORD_SAMPLE_RATE)
            encoder.set_channels(CHANNELS)
            encoder.set_quality(2)
            mp3_file = session.mp3_path.open("wb")

            wav_file = wave.open(str(session.wav_path), "wb")
            wav_file.setnchannels(CHANNELS)
            wav_file.setsampwidth(2)
            wav_file.setframerate(RECORD_SAMPLE_RATE)
            for source_name, source_path in session.source_wav_paths.items():
                source_file = wave.open(str(source_path), "wb")
                source_file.setnchannels(CHANNELS)
                source_file.setsampwidth(2)
                source_file.setframerate(RECORD_SAMPLE_RATE)
                source_wav_files[source_name] = source_file

            while True:
                frame_set = session.mp3_queue.get()
                if frame_set is None:
                    break
                if not frame_set.sources:
                    continue

                for source_name, source_file in source_wav_files.items():
                    source_audio = frame_set.sources.get(source_name)
                    if source_audio is None:
                        source_audio = np.zeros(chunk_frames, dtype=np.float32)
                    source_file.writeframes(float_audio_to_int16(source_audio).tobytes())

                audio = mix_audio_sources(list(frame_set.sources.values()))
                if audio.size == 0:
                    continue
                pcm_bytes = float_audio_to_int16(audio).tobytes()
                wav_file.writeframes(pcm_bytes)

                mp3_chunk = encoder.encode(pcm_bytes)
                if mp3_chunk:
                    mp3_file.write(mp3_chunk)

            final_chunk = encoder.flush()
            if final_chunk:
                mp3_file.write(final_chunk)
        except Exception as exc:
            session.stop_event.set()
            self.root.after(0, lambda error=exc: self.handle_recording_error(session, error))
        finally:
            for source_file in source_wav_files.values():
                with contextlib.suppress(Exception):
                    source_file.close()
            with contextlib.suppress(Exception):
                if wav_file is not None:
                    wav_file.close()
            with contextlib.suppress(Exception):
                if mp3_file is not None:
                    mp3_file.close()

    def audio_capture_worker(
        self,
        session: RecordingSession,
        source_name: str,
        source: object,
        target_queue: "queue.Queue[Optional[np.ndarray]]",
        error_queue: "queue.Queue[tuple[str, Exception]]",
    ) -> None:
        try:
            chunk_frames = max(1, int(RECORD_SAMPLE_RATE * CHUNK_SECONDS))
            with source.recorder(samplerate=RECORD_SAMPLE_RATE, channels=CHANNELS) as recorder:
                while not session.stop_event.is_set():
                    if not session.active_event.wait(0.1):
                        continue

                    data = recorder.record(numframes=chunk_frames)
                    audio = normalize_audio(data)
                    if audio.size:
                        target_queue.put(fit_audio_length(audio, chunk_frames))
        except Exception as exc:
            error_queue.put((source_name, exc))
        finally:
            target_queue.put(None)

    def show_capture_warnings(
        self,
        session: RecordingSession,
        source_errors: "queue.Queue[tuple[str, Exception]]",
    ) -> None:
        while True:
            try:
                source_name, exc = source_errors.get_nowait()
            except queue.Empty:
                return

            if self.session is session and not session.stop_event.is_set():
                self.root.after(
                    0,
                    lambda name=source_name, error=exc: messagebox.showwarning(
                        self.title(),
                        f"{self.t('source_unavailable', source=self.t(name))}\n\n"
                        f"{self.t('details')}: {error}",
                    ),
                )

    def transcriber_config(self) -> dict[str, object]:
        model_root = app_directory() / "models"
        return {
            "model": WHISPER_MODEL,
            "local_model_dir": str(model_root / LOCAL_MODEL_DIR.name),
            "fallback_model_dir": str(model_root / FALLBACK_MODEL_DIR.name),
            "language": WHISPER_LANGUAGE,
            "device": WHISPER_DEVICE,
            "compute_type": WHISPER_COMPUTE_TYPE,
            "cpu_threads": WHISPER_CPU_THREADS,
            "num_workers": WHISPER_NUM_WORKERS,
            "vad_parameters": VAD_PARAMETERS,
        }

    def start_transcriber_process(self, session: RecordingSession) -> None:
        try:
            context = mp.get_context("spawn")
            input_queue = context.Queue(maxsize=8)
            output_queue = context.Queue()
            cancel_realtime_event = context.Event()
            process = context.Process(
                target=run_transcriber_process,
                args=(input_queue, output_queue, self.transcriber_config(), cancel_realtime_event),
                name="whisper-transcriber",
                daemon=True,
            )
            process.start()
        except Exception as exc:
            self.root.after(0, lambda error=exc: self.show_transcription_runtime_warning(error))
            return

        session.transcriber_input_queue = input_queue
        session.transcriber_output_queue = output_queue
        session.transcriber_cancel_realtime_event = cancel_realtime_event
        session.transcriber_process = process
        session.transcription_result_thread = threading.Thread(
            target=self.transcription_result_worker,
            args=(session,),
            name="transcriber-results",
            daemon=True,
        )
        session.transcription_result_thread.start()

    def submit_transcriber_job(self, session: RecordingSession, payload: dict[str, object]) -> bool:
        input_queue = session.transcriber_input_queue
        process = session.transcriber_process
        if input_queue is None or process is None:
            return False

        while True:
            if getattr(process, "exitcode", None) is not None:
                return False
            try:
                input_queue.put(payload, timeout=0.2)
                return True
            except queue.Full:
                if self.shutting_down:
                    return False

    def signal_transcriber_stop(self, session: RecordingSession, timeout: Optional[float] = None) -> bool:
        input_queue = session.transcriber_input_queue
        if input_queue is None:
            return False
        with session.transcriber_lock:
            if session.transcriber_stop_sent:
                return True
            deadline = None if timeout is None else time.monotonic() + timeout
            while True:
                try:
                    input_queue.put(None, timeout=0.2)
                    session.transcriber_stop_sent = True
                    return True
                except queue.Full:
                    if deadline is not None and time.monotonic() >= deadline:
                        return False
                except Exception:
                    return False

    def stop_transcriber_process(
        self,
        session: RecordingSession,
        timeout: Optional[float] = None,
        terminate: bool = False,
    ) -> None:
        self.signal_transcriber_stop(session, timeout=2 if terminate else None)
        process = session.transcriber_process
        if process is not None:
            process.join(timeout=timeout)
            if terminate and process.is_alive():
                with contextlib.suppress(Exception):
                    process.terminate()
                process.join(timeout=2)

        result_thread = session.transcription_result_thread
        if result_thread is not None and result_thread is not threading.current_thread():
            result_thread.join(timeout=timeout)
        if process is not None:
            with contextlib.suppress(Exception):
                process.close()
            session.transcriber_process = None
        session.transcription_result_thread = None
        session.transcriber_cancel_realtime_event = None

        self.close_transcriber_queues(session)

    def close_transcriber_queues(self, session: RecordingSession) -> None:
        for queue_name in ("transcriber_input_queue", "transcriber_output_queue"):
            process_queue = getattr(session, queue_name)
            if process_queue is None:
                continue
            with contextlib.suppress(Exception):
                process_queue.close()
            with contextlib.suppress(Exception):
                process_queue.join_thread()
            setattr(session, queue_name, None)

    def transcription_result_worker(self, session: RecordingSession) -> None:
        output_queue = session.transcriber_output_queue
        if output_queue is None:
            return

        empty_after_exit_count = 0
        while True:
            try:
                result = output_queue.get(timeout=0.2)
            except queue.Empty:
                process = session.transcriber_process
                if process is not None and getattr(process, "exitcode", None) is not None:
                    empty_after_exit_count += 1
                    if empty_after_exit_count >= 5:
                        break
                else:
                    empty_after_exit_count = 0
                continue

            empty_after_exit_count = 0
            kind = result.get("kind")
            if kind == "done":
                break
            if kind == "missing":
                self.root.after(0, self.show_transcription_dependency_warning)
                continue
            if kind == "error":
                message = str(result.get("message", ""))
                self.root.after(0, lambda error=RuntimeError(message): self.show_transcription_runtime_warning(error))
                continue
            if kind == "realtime":
                text = str(result.get("text", "")).strip()
                source_name = str(result.get("source_name", "source_speaker"))
                start = float(result.get("start", 0.0))
                end = float(result.get("end", start))
                if text and not session.stop_event.is_set():
                    self.append_transcript(session, source_name, start, end, text)
                continue
            if kind == "review":
                source_name = str(result.get("source_name", "source_speaker"))
                start = float(result.get("start", 0.0))
                end = float(result.get("end", start))
                entries: list[TranscriptEntry] = []
                for item in result.get("entries", []):
                    if len(item) == 3:
                        entry_start, entry_end, text = item
                    else:
                        entry_start, text = item
                        entry_end = entry_start
                    if str(text).strip():
                        entries.append(TranscriptEntry(float(entry_start), source_name, str(text), float(entry_end)))
                if end > start:
                    self.apply_final_transcript_entries(session, source_name, start, end, entries)

    def transcription_worker(self, session: RecordingSession) -> None:
        if session.transcriber_input_queue is None:
            self.drain_audio_queue(session.audio_queue)
            return

        while True:
            if not session.active_event.wait(0.1):
                continue

            try:
                job = session.audio_queue.get(timeout=0.1)
            except queue.Empty:
                continue

            if job is None:
                break
            audio = job.audio
            if audio.size == 0:
                continue

            self.submit_transcriber_job(
                session,
                {
                    "kind": "realtime",
                    "source_name": job.source_name,
                    "start": job.start,
                    "end": job.end,
                    "audio": audio,
                    "beam_size": REALTIME_BEAM_SIZE,
                    "condition_on_previous_text": False,
                },
            )

    def review_transcription_worker(self, session: RecordingSession) -> None:
        if session.transcriber_input_queue is None:
            self.drain_audio_queue(session.review_queue)
            return

        while True:
            job = session.review_queue.get()
            if job is None:
                break

            audio = job.audio
            if audio.size == 0 or audio_rms(audio) < MIN_TRANSCRIBE_RMS:
                continue

            self.submit_transcriber_job(
                session,
                {
                    "kind": "review",
                    "source_name": job.source_name,
                    "start": job.start,
                    "end": job.end,
                    "audio": audio,
                    "beam_size": FINAL_BEAM_SIZE,
                    "condition_on_previous_text": True,
                },
            )

    def meeting_minutes_worker(self, session: RecordingSession) -> None:
        while True:
            transcript = session.minutes_queue.get()
            if transcript is None:
                break

            deadline = time.monotonic() + MINUTES_UPDATE_SECONDS
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    latest = session.minutes_queue.get(timeout=remaining)
                except queue.Empty:
                    break
                if latest is None:
                    return
                transcript = latest

            while not session.active_event.is_set() and not session.stop_event.is_set():
                session.active_event.wait(0.2)
            if session.stop_event.is_set() or self.session is not session:
                continue

            minutes = self.build_meeting_summary(transcript, live=True)
            self.apply_live_minutes(session, minutes)

    def queue_minutes_update(self, session: RecordingSession, transcript: str) -> None:
        if session.stop_event.is_set():
            return
        while True:
            try:
                item = session.minutes_queue.get_nowait()
            except queue.Empty:
                break
            if item is None:
                session.minutes_queue.put(None)
                return
        session.minutes_queue.put(transcript)

    def cancel_pending_minutes(self, session: RecordingSession) -> None:
        while True:
            try:
                session.minutes_queue.get_nowait()
            except queue.Empty:
                break
        session.minutes_queue.put(None)

    def apply_live_minutes(self, session: RecordingSession, minutes: str) -> None:
        if self.session is not session or session.stop_event.is_set():
            return
        with self.transcript_lock:
            self.meeting_minutes_text = minutes
            document = self.compose_transcript_document(self.transcript_body_text, self.meeting_minutes_text)
            self.transcript_text = document

        session.text_path.write_text(document, encoding="utf-8")
        self.root.after(0, lambda active_session=session, current_text=document: self.refresh_transcript_text(active_session, current_text))

    def cancel_pending_transcription(self, session: RecordingSession) -> None:
        cancel_event = session.transcriber_cancel_realtime_event
        if cancel_event is not None:
            with contextlib.suppress(Exception):
                cancel_event.set()
        while True:
            try:
                session.audio_queue.get_nowait()
            except queue.Empty:
                break
        session.audio_queue.put(None)

    def drain_audio_queue(self, audio_queue: "queue.Queue[object]") -> None:
        while True:
            audio = audio_queue.get()
            if audio is None:
                break

    def finish_stop_worker(self, session: RecordingSession) -> None:
        if session.recorder_thread is not None:
            session.recorder_thread.join()
        if session.writer_thread is not None:
            session.writer_thread.join()

        finalizer_thread = threading.Thread(
            target=self.finish_final_transcript_worker,
            args=(session,),
            name="finish-final-transcript",
            daemon=True,
        )
        finalizer_thread.start()
        finalizer_thread.join(timeout=STOP_FINAL_WAIT_SECONDS)

        self.root.after(0, lambda: self.complete_stop(session))
        try:
            finalizer_thread.join()
            self.update_saved_text_file(session)
        finally:
            session.save_decision_event.wait()
            session.cleanup()

    def finish_final_transcript_worker(self, session: RecordingSession) -> None:
        self.cancel_pending_minutes(session)
        if session.minutes_thread is not None:
            session.minutes_thread.join()

        if session.transcription_thread is not None:
            session.transcription_thread.join()
        if session.review_thread is not None:
            session.review_thread.join()
        self.stop_transcriber_process(session)
        self.finalize_transcript_from_review(session)

    def finalize_transcript_from_review(self, session: RecordingSession) -> None:
        transcript = self.format_final_transcript(session)
        if not transcript:
            transcript = self.format_layered_transcript(session)

        summary_source = self.strip_layer_headers(transcript)
        summary = self.build_meeting_summary(summary_source)
        document = transcript
        if summary:
            document = f"{transcript}\n\n{summary}" if transcript else summary
        self.replace_transcript_document(session, document)
        self.sync_saved_text_file_if_available(session)

    def update_saved_text_file(self, session: RecordingSession) -> None:
        session.save_decision_event.wait()
        text_target = session.saved_text_path
        if text_target is None or not session.text_path.exists():
            return
        with contextlib.suppress(Exception):
            shutil.copy2(session.text_path, text_target)

    def sync_saved_text_file_if_available(self, session: RecordingSession) -> None:
        text_target = session.saved_text_path
        if text_target is None or not session.text_path.exists():
            return
        with contextlib.suppress(Exception):
            shutil.copy2(session.text_path, text_target)

    def strip_layer_headers(self, transcript: str) -> str:
        return "\n".join(
            line
            for line in transcript.splitlines()
            if line.strip() not in {"[Draft]", "[Final]"}
        ).strip()

    def format_final_transcript(self, session: RecordingSession) -> str:
        with session.final_lock:
            entries = list(session.final_entries)
        return self.format_transcript_entries(entries)

    def format_layered_transcript(self, session: RecordingSession, final_only: bool = False) -> str:
        with session.final_lock:
            final_entries = list(session.final_entries)
            finalized_until = dict(session.finalized_until)
            draft_entries = list(session.draft_entries)

        final_text = self.format_transcript_entries(final_entries)
        if final_only:
            return final_text

        draft_tail = [
            entry
            for entry in draft_entries
            if entry.start >= finalized_until.get(entry.source_name, 0.0) - 0.05
        ]
        draft_text = self.format_transcript_entries(draft_tail)

        sections: list[str] = []
        if final_text:
            sections.append(f"[Final]\n{final_text}")
        if draft_text:
            sections.append(f"[Draft]\n{draft_text}")
        return "\n\n".join(sections)

    def apply_final_transcript_entries(
        self,
        session: RecordingSession,
        source_name: str,
        start: float,
        end: float,
        entries: list[TranscriptEntry],
    ) -> None:
        with session.final_lock:
            session.final_entries = [
                entry
                for entry in session.final_entries
                if not (
                    entry.source_name == source_name
                    and start - 0.05 <= entry.start < end + 0.05
                )
            ]
            session.final_entries.extend(entries)
            session.finalized_until[source_name] = max(
                session.finalized_until.get(source_name, 0.0),
                end,
            )
            session.draft_entries = [
                entry
                for entry in session.draft_entries
                if entry.start >= session.finalized_until.get(entry.source_name, 0.0) - 0.05
            ]

        transcript = self.format_layered_transcript(session)
        document = transcript
        if self.session is session:
            with self.transcript_lock:
                if self.session is session:
                    self.transcript_body_text = transcript
                    document = self.compose_transcript_document(self.transcript_body_text, self.meeting_minutes_text)
                    self.transcript_text = document

        session.text_path.write_text(document, encoding="utf-8")
        self.sync_saved_text_file_if_available(session)
        if self.session is session:
            self.queue_minutes_update(session, self.strip_layer_headers(transcript))
            self.root.after(0, lambda active_session=session, current_text=document: self.refresh_transcript_text(active_session, current_text))

    def format_transcript_entries(self, entries: list[TranscriptEntry]) -> str:
        blocks: list[TranscriptBlock] = []
        for entry in sorted(entries, key=lambda item: item.start):
            sentences = split_sentences(entry.text)
            if not sentences:
                continue
            if blocks and blocks[-1].source_name == entry.source_name:
                blocks[-1].sentences.extend(sentences)
            else:
                blocks.append(TranscriptBlock(entry.source_name, sentences))
        return self.format_transcript_blocks(blocks)

    def build_meeting_summary(self, transcript: str, live: bool = False) -> str:
        sentence_entries = self.transcript_sentences_with_speakers(transcript)
        sentences = [sentence for sentence, _speaker in sentence_entries]

        main_points = summarize_sentences(sentences)
        action_items = self.extract_action_items_with_owners(sentence_entries)

        lines = [
            f"{self.t('live_minutes_title' if live else 'meeting_summary_title')}:",
            self.t("summary_note"),
            "",
            f"{self.t('main_discussion_title')}:",
        ]
        if main_points:
            lines.extend(f"- {sentence}" for sentence in main_points)
        else:
            lines.append(f"- {self.t('no_summary')}")

        lines.extend(["", f"{self.t('action_items_title')}:"])
        if action_items:
            lines.extend(
                f"- {self.t('owner_label')}: {owner} | {item}"
                for owner, item in action_items
            )
        else:
            lines.append(f"- {self.t('no_action_items')}")
        return "\n".join(lines)

    def transcript_without_existing_minutes(self, transcript: str) -> str:
        section_titles = {
            "Meeting Summary",
            "Live Meeting Minutes",
            "会议总结",
            "实时会议纪要",
            self.t("meeting_summary_title"),
            self.t("live_minutes_title"),
        }
        kept_lines: list[str] = []
        for line in transcript.splitlines():
            title = line.strip().rstrip(":：")
            if title in section_titles:
                break
            kept_lines.append(line)
        return "\n".join(kept_lines).strip()

    def transcript_sentences_with_speakers(self, transcript: str) -> list[tuple[str, str]]:
        speaker_labels = {
            "Me": self.t("speaker_me"),
            "Others": self.t("speaker_others"),
            "本人": self.t("speaker_me"),
            "其他人": self.t("speaker_others"),
            self.t("speaker_me"): self.t("speaker_me"),
            self.t("speaker_others"): self.t("speaker_others"),
        }
        current_speaker = self.t("owner_unknown")
        sentence_entries: list[tuple[str, str]] = []

        for raw_line in self.transcript_without_existing_minutes(transcript).splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line in {"[Draft]", "[Final]"}:
                continue
            label = line.rstrip(":：").strip()
            if label in speaker_labels:
                current_speaker = speaker_labels[label]
                continue
            for sentence in split_sentences(line):
                sentence_entries.append((sentence, current_speaker))
        return sentence_entries

    def extract_action_items_with_owners(
        self,
        sentence_entries: list[tuple[str, str]],
        limit: int = 8,
    ) -> list[tuple[str, str]]:
        action_items: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for sentence, speaker in sentence_entries:
            if not extract_action_items([sentence], limit=1):
                continue
            owner = self.infer_action_owner(sentence, speaker)
            key = (owner, sentence)
            if key in seen:
                continue
            seen.add(key)
            action_items.append(key)
            if len(action_items) >= limit:
                break
        return action_items

    def infer_action_owner(self, sentence: str, speaker: str) -> str:
        unknown = self.t("owner_unknown")
        team = self.t("owner_team")
        fallback_owner = speaker if speaker and speaker != unknown else unknown
        lower = sentence.lower()

        owner_patterns = (
            r"\bassigned to\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})\b",
            r"\bowner\s*(?:is|:|-)?\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})\b",
            r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})\s+(?:is\s+)?responsible\b",
            r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})\s+(?:will|needs to|need to|should)\b",
        )
        ignored = {"i", "we", "you", "me", "team", "action", "owner", "next", "the", "this", "that"}
        for pattern in owner_patterns:
            match = re.search(pattern, sentence)
            if not match:
                continue
            candidate = match.group(1).strip()
            if candidate.lower() not in ignored:
                return candidate

        chinese_patterns = (
            r"(?:负责人|由)\s*[:：]?\s*([\u4e00-\u9fff]{2,4})",
            r"([\u4e00-\u9fff]{2,4})\s*(?:负责|跟进|确认|安排|发送|准备|更新|修复|检查)",
        )
        for pattern in chinese_patterns:
            match = re.search(pattern, sentence)
            if match:
                candidate = match.group(1).strip()
                if candidate not in {"我们", "大家", "需要", "确认", "安排", "准备"}:
                    return candidate

        if re.search(r"\bwe\s+(?:will|need|should|can|are going to)\b", lower) or "我们" in sentence or "大家" in sentence:
            return team
        if re.search(r"\bi\s+(?:will|need|should|can|am going to)\b", lower) or "我" in sentence:
            return fallback_owner
        return fallback_owner

    def complete_stop(self, session: RecordingSession) -> None:
        if self.session is not session:
            session.save_decision_event.set()
            return

        with self.state_lock:
            self.state = "idle"
        self.update_tray_menu()

        try:
            if not self.shutting_down:
                should_save = messagebox.askyesno(
                    self.title(),
                    self.t("save_question"),
                )
                if should_save:
                    self.ask_and_save_files(session)
        finally:
            self.session = None
            self.clear_transcript()
            if self.transcript_window is not None:
                self.transcript_window.set_text("")
            session.save_decision_event.set()

    def ask_and_save_files(self, session: RecordingSession) -> None:
        while True:
            selected = filedialog.asksaveasfilename(
                title=self.t("save_dialog_title"),
                initialfile=f"{session.file_stem}.mp3",
                defaultextension=".mp3",
                filetypes=((self.t("mp3_files"), "*.mp3"), (self.t("all_files"), "*.*")),
            )
            if not selected:
                return

            selected_path = Path(selected)
            if selected_path.suffix.lower() == ".mp3":
                mp3_target = selected_path
            else:
                mp3_target = selected_path.with_suffix(".mp3")
            text_target = mp3_target.with_suffix(".txt")

            if text_target.exists():
                overwrite_text = messagebox.askyesno(
                    self.title(),
                    self.t("overwrite_text", filename=text_target.name),
                )
                if not overwrite_text:
                    continue

            try:
                shutil.copy2(session.mp3_path, mp3_target)
                if session.text_path.exists():
                    shutil.copy2(session.text_path, text_target)
                else:
                    text_target.write_text("", encoding="utf-8")
                session.saved_text_path = text_target
            except Exception as exc:
                messagebox.showerror(self.title(), f"{self.t('save_failed')}\n{exc}")
                continue

            messagebox.showinfo(
                self.title(),
                f"{self.t('saved')}\n{mp3_target}\n{text_target}",
            )
            return

    def append_transcript(
        self,
        session: RecordingSession,
        source_name: str,
        start: float,
        end: float,
        text: str,
    ) -> None:
        if self.session is not session:
            return

        sentences = split_sentences(text)
        if not sentences:
            return

        with session.final_lock:
            session.draft_entries.append(TranscriptEntry(start, source_name, text, end))

        transcript = self.format_layered_transcript(session)
        with self.transcript_lock:
            self.transcript_body_text = transcript
            document = self.compose_transcript_document(self.transcript_body_text, self.meeting_minutes_text)
            self.transcript_text = document

        session.text_path.write_text(document, encoding="utf-8")
        self.queue_minutes_update(session, self.strip_layer_headers(transcript))
        self.root.after(0, lambda active_session=session, current_text=document: self.refresh_transcript_text(active_session, current_text))

    def replace_transcript_document(self, session: RecordingSession, text: str) -> None:
        session.text_path.write_text(text, encoding="utf-8")
        self.sync_saved_text_file_if_available(session)
        if self.session is not session:
            return
        with self.transcript_lock:
            self.transcript_blocks = []
            self.transcript_body_text = text
            self.meeting_minutes_text = ""
            self.transcript_text = text
        self.root.after(0, lambda active_session=session, current_text=text: self.refresh_transcript_text(active_session, current_text))

    def refresh_transcript_text(self, session: RecordingSession, text: str) -> None:
        if self.session is not session:
            return
        if self.transcript_window is not None:
            self.transcript_window.set_text(text)

    def open_transcript_window(self) -> None:
        if self.transcript_window is None:
            self.transcript_window = TranscriptWindow(self.root, self)
        else:
            self.transcript_window.window.deiconify()
            self.transcript_window.window.lift()

        with self.state_lock:
            state = self.state
        if state == "idle":
            self.transcript_window.set_text("")
        else:
            with self.transcript_lock:
                transcript = self.transcript_text
            self.transcript_window.set_text(transcript)

    def show_about(self) -> None:
        if self.about_window is not None and self.about_window.winfo_exists():
            self.about_window.deiconify()
            self.about_window.lift()
            return

        self.about_window = tk.Toplevel(self.root)
        self.about_window.geometry("380x220")
        self.about_window.resizable(False, False)
        self.about_window.protocol("WM_DELETE_WINDOW", self.close_about_window)

        frame = ttk.Frame(self.about_window, padding=16)
        frame.pack(fill=tk.BOTH, expand=True)

        self.about_body_label = ttk.Label(frame, wraplength=330, justify=tk.LEFT)
        self.about_body_label.pack(anchor=tk.W, fill=tk.X)

        self.about_language_label = ttk.Label(frame)
        self.about_language_label.pack(anchor=tk.W, pady=(18, 6))

        self.about_language_var = tk.StringVar(value=self.language)
        self.about_english_check = ttk.Checkbutton(
            frame,
            variable=self.about_language_var,
            onvalue="en",
            offvalue="zh",
            command=lambda: self.set_language("en"),
        )
        self.about_english_check.pack(anchor=tk.W)

        self.about_chinese_check = ttk.Checkbutton(
            frame,
            variable=self.about_language_var,
            onvalue="zh",
            offvalue="en",
            command=lambda: self.set_language("zh"),
        )
        self.about_chinese_check.pack(anchor=tk.W, pady=(4, 0))
        self.refresh_about_window()

    def close_about_window(self) -> None:
        if self.about_window is not None:
            self.about_window.destroy()
        self.about_window = None
        self.about_language_var = None
        self.about_body_label = None
        self.about_language_label = None
        self.about_english_check = None
        self.about_chinese_check = None

    def show_transcription_dependency_warning(self) -> None:
        if self.transcription_warning_shown:
            return
        self.transcription_warning_shown = True
        messagebox.showwarning(
            self.title(),
            self.t("transcription_missing"),
        )

    def show_transcription_runtime_warning(self, exc: Exception) -> None:
        if self.transcription_warning_shown:
            return
        self.transcription_warning_shown = True
        messagebox.showwarning(
            self.title(),
            f"{self.t('transcription_unavailable')}\n\n{self.t('details')}: {exc}",
        )

    def handle_recording_error(self, session: RecordingSession, exc: Exception) -> None:
        if self.session is not session:
            return

        self.set_auto_screenshot_enabled(False)
        session.stop_event.set()
        session.active_event.set()
        self.cancel_pending_transcription(session)
        session.review_queue.put(None)
        self.cancel_pending_minutes(session)
        self.stop_transcriber_process(session, timeout=1, terminate=True)
        with self.state_lock:
            self.state = "idle"
        self.update_tray_menu()
        if self.shutting_down:
            session.cleanup()
            self.session = None
            self.clear_transcript()
            return
        messagebox.showerror(self.title(), f"{self.t('recording_failed')}\n{exc}")

        session.cleanup()
        self.session = None
        self.clear_transcript()
        if self.transcript_window is not None:
            self.transcript_window.set_text("")


def main() -> None:
    mp.freeze_support()
    quiet_library_noise()
    root = tk.Tk()
    root.title(APP_NAME)
    app = MeetingRecorderApp(root)
    app.run()


if __name__ == "__main__":
    main()
