from __future__ import annotations

import contextlib
import datetime as dt
import io
import logging
import multiprocessing as mp
import os
import queue
import re
import sys
import threading
import time
import warnings
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
APP_VERSION = "1.0.6"
RECORD_SAMPLE_RATE = 48000
TRANSCRIBE_SAMPLE_RATE = 16000
CHANNELS = 1
CHUNK_SECONDS = 0.5
CAPTURE_READ_SECONDS = 0.05
CAPTURE_BLOCK_SECONDS = 0.2
TRANSCRIBE_SECONDS = 3
REALTIME_MIN_AUDIO_SECONDS = 1.5
REALTIME_BEAM_SIZE = 3
REVIEW_SECONDS = 15
REVIEW_MIN_AUDIO_SECONDS = 3
MINUTES_UPDATE_SECONDS = 5
AUTO_SCREENSHOT_INTERVAL_SECONDS = 30
ENGLISH_WHISPER_MODEL = "distil-small.en"
ENGLISH_MODEL_DIR = Path(__file__).with_name("models") / "faster-distil-whisper-small.en"
CHINESE_WHISPER_MODEL = "small"
CHINESE_MODEL_DIR = Path(__file__).with_name("models") / "faster-whisper-small"
DEFAULT_TRANSCRIPTION_LANGUAGE = "en"
TRANSCRIPTION_MODEL_CONFIGS = {
    "en": {
        "model_name": ENGLISH_WHISPER_MODEL,
        "model_dir": ENGLISH_MODEL_DIR,
        "language": "en",
        "transcribe_seconds": 5,
        "beam_size": 5,
        "condition_on_previous_text": True,
        "initial_prompt": None,
        "simplify_chinese": False,
    },
    "zh": {
        "model_name": CHINESE_WHISPER_MODEL,
        "model_dir": CHINESE_MODEL_DIR,
        "language": "zh",
        "transcribe_seconds": 10,
        "beam_size": 5,
        "condition_on_previous_text": True,
        "initial_prompt": (
            "以下是简体中文会议转录。请使用简体中文输出，并尽量添加正确的中文标点。"
            "常见词包括：会议、讨论、方案、问题、行动项、负责人、时间节点、"
        ),
        "simplify_chinese": True,
    },
}
WHISPER_MODEL = ENGLISH_WHISPER_MODEL
LOCAL_MODEL_DIR = ENGLISH_MODEL_DIR
FALLBACK_WHISPER_MODEL = CHINESE_WHISPER_MODEL
FALLBACK_MODEL_DIR = CHINESE_MODEL_DIR
WHISPER_LANGUAGE: Optional[str] = DEFAULT_TRANSCRIPTION_LANGUAGE
WHISPER_DEVICE = "cpu"
WHISPER_COMPUTE_TYPE = "int8"
WHISPER_CPU_THREADS = 2
WHISPER_NUM_WORKERS = 1
FINAL_BEAM_SIZE = 5
VAD_PARAMETERS = {"min_silence_duration_ms": 500}
MIN_TRANSCRIBE_RMS = 0.0015
DEFAULT_LANGUAGE = "en"
AUDIO_ONLY_DIAGNOSTIC = False
TRANSCRIPTION_SOURCE_QUEUE_SECONDS = 120
TRANSCRIPTION_SOURCE_QUEUE_MAXSIZE = max(1, int(TRANSCRIPTION_SOURCE_QUEUE_SECONDS / CHUNK_SECONDS))
TRANSCRIBER_INPUT_QUEUE_MAXSIZE = 2
TRANSCRIBER_OUTPUT_QUEUE_MAXSIZE = 20
ENABLE_BACKGROUND_REVIEW = False
RECORD_MICROPHONE = True
MIX_MICROPHONE_IN_MP3 = True
MIC_MIN_RMS = 0.002
MIC_LEAK_ANALYSIS_RATE = 8000
MIC_LEAK_MAX_LAG_SECONDS = 0.08
MIC_LEAK_LAG_STEP_SECONDS = 0.002
MIC_LEAK_CORRELATION_THRESHOLD = 0.82
AUDIO_SOURCES = ("source_speaker", "source_microphone")
SPEAKER_LABEL_KEYS = {
    "source_speaker": "speaker_others",
    "source_microphone": "speaker_me",
}


TRANSLATIONS = {
    "en": {
        "app_name": "Local Meeting Recorder",
        "record": "🔴 Start Recording",
        "record_stop": "◼ Stop Recording",
        "text_window": "Text Window",
        "stop": "Stop Recording",
        "screenshot": "Take a screenshot",
        "auto_screenshot_on": "Auto Screenshot: On",
        "auto_screenshot_off": "Auto Screenshot: Off",
        "about": "About",
        "exit": "Exit",
        "transcript_title": "Live Transcript",
        "copy_text": "Copy text to clipboard",
        "stopping_wait": "Recording is stopping and the transcript is being finished. Please wait.",
        "already_recording": "Recording is already in progress. Click Stop Recording to stop.",
        "ask_auto_screenshot": "Do you want to turn on Auto Screenshot for this recording?",
        "recording_options_title": "Start Recording",
        "transcription_language": "Transcription language",
        "transcription_language_english": "English",
        "transcription_language_chinese": "Chinese",
        "auto_screenshot_option": "Auto Screenshot",
        "start": "Start",
        "cancel": "Cancel",
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
        "all_sources_unavailable": "No available audio source could be recorded.",
        "save_question": "Do you want to save the MP3 file and text file?",
        "save_dialog_title": "Save recording and transcript",
        "mp3_files": "MP3 files",
        "all_files": "All files",
        "overwrite_text": "{filename} already exists. Overwrite it?",
        "save_failed": "Save failed:",
        "saved": "Saved:",
        "about_title": "About",
        "about_body": "This software is open source and follows the principle of free use.",
        "about_version": "Version: {version}",
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
        "record": "🔴 开始录音",
        "record_stop": "◼ 停止录音",
        "text_window": "文本窗口",
        "stop": "停止录音",
        "screenshot": "截屏",
        "auto_screenshot_on": "Auto Screenshot: On",
        "auto_screenshot_off": "Auto Screenshot: Off",
        "about": "关于",
        "exit": "退出",
        "transcript_title": "实时转录文本",
        "copy_text": "复制文本到剪贴板",
        "stopping_wait": "正在停止录音并补全文本转录，请稍候。",
        "already_recording": "正在录音中，请点击“停止录音”结束录音。",
        "ask_auto_screenshot": "是否为本次录音开启 Auto Screenshot？",
        "recording_options_title": "开始录音",
        "transcription_language": "转录语言",
        "transcription_language_english": "英文",
        "transcription_language_chinese": "中文",
        "auto_screenshot_option": "自动截屏",
        "start": "开始",
        "cancel": "取消",
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
        "all_sources_unavailable": "没有可用声音源可录制。",
        "save_question": "是否保存 MP3 文件和文本文件？",
        "save_dialog_title": "保存录音和转录文本",
        "mp3_files": "MP3 文件",
        "all_files": "所有文件",
        "overwrite_text": "{filename} 已存在，是否覆盖？",
        "save_failed": "保存失败：",
        "saved": "已保存：",
        "about_title": "关于",
        "about_body": "该软件为开源软件，遵循自由使用原则。",
        "about_version": "版本号：{version}",
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


def lower_current_thread_priority() -> None:
    if sys.platform != "win32":
        return
    with contextlib.suppress(Exception):
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        get_current_thread = kernel32.GetCurrentThread
        set_thread_priority = kernel32.SetThreadPriority
        thread = get_current_thread()
        thread_mode_background_begin = 0x00010000
        thread_priority_below_normal = -1
        if not set_thread_priority(thread, thread_mode_background_begin):
            set_thread_priority(thread, thread_priority_below_normal)


def raise_current_thread_priority() -> None:
    if sys.platform != "win32":
        return
    with contextlib.suppress(Exception):
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        get_current_thread = kernel32.GetCurrentThread
        set_thread_priority = kernel32.SetThreadPriority
        thread = get_current_thread()
        thread_priority_highest = 2
        thread_priority_above_normal = 1
        if not set_thread_priority(thread, thread_priority_highest):
            set_thread_priority(thread, thread_priority_above_normal)


def is_microphone_in_use_by_other_app() -> bool:
    if sys.platform != "win32":
        return False

    try:
        import comtypes
        from pycaw.constants import AudioSessionState
        from pycaw.pycaw import AudioSession, AudioUtilities, IAudioSessionControl2
    except Exception:
        return False

    com_initialized = False
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            comtypes.CoInitialize()
            com_initialized = True

            microphone = AudioUtilities.CreateDevice(AudioUtilities.GetMicrophone())
            if microphone is None:
                return False

            with contextlib.suppress(Exception):
                if microphone.EndpointVolume.GetMute():
                    return False

            session_enumerator = microphone.AudioSessionManager.GetSessionEnumerator()
            active_value = getattr(AudioSessionState.Active, "value", AudioSessionState.Active)
            active_state = int(active_value)
            current_pid = os.getpid()
            for index in range(session_enumerator.GetCount()):
                control = session_enumerator.GetSession(index)
                if control is None:
                    continue
                session = AudioSession(control.QueryInterface(IAudioSessionControl2))
                process_id = int(session.ProcessId)
                if int(session.State) == active_state and process_id not in (0, current_pid):
                    return True
    except Exception:
        return False
    finally:
        if com_initialized:
            with contextlib.suppress(Exception):
                comtypes.CoUninitialize()

    return False


def recording_file_stem() -> str:
    return dt.datetime.now().strftime("%Y%m%d_%H%M")


def app_directory() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def screenshot_folder_name() -> str:
    return dt.datetime.now().strftime("%Y%m%d_%H%M")


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


def normalized_abs_correlation(first: np.ndarray, second: np.ndarray) -> float:
    if first.size == 0 or second.size == 0:
        return 0.0
    size = min(first.size, second.size)
    first = first[:size] - float(np.mean(first[:size]))
    second = second[:size] - float(np.mean(second[:size]))
    denominator = float(np.sqrt(np.sum(first * first) * np.sum(second * second)))
    if denominator <= 1e-9:
        return 0.0
    return abs(float(np.sum(first * second) / denominator))


def max_lagged_correlation(reference: np.ndarray, candidate: np.ndarray, sample_rate: int) -> float:
    max_lag = max(1, int(MIC_LEAK_MAX_LAG_SECONDS * sample_rate))
    lag_step = max(1, int(MIC_LEAK_LAG_STEP_SECONDS * sample_rate))
    minimum_size = max(1, int(0.15 * sample_rate))
    best = 0.0

    for lag in range(-max_lag, max_lag + 1, lag_step):
        if lag < 0:
            ref = reference[-lag:]
            cand = candidate[: candidate.size + lag]
        elif lag > 0:
            ref = reference[:-lag]
            cand = candidate[lag:]
        else:
            ref = reference
            cand = candidate

        size = min(ref.size, cand.size)
        if size < minimum_size:
            continue
        best = max(best, normalized_abs_correlation(ref[:size], cand[:size]))

    return best


def is_microphone_bleed_from_speaker(speaker_audio: Optional[np.ndarray], microphone_audio: np.ndarray) -> bool:
    microphone_audio = normalize_audio(microphone_audio)
    if microphone_audio.size == 0 or audio_rms(microphone_audio) < MIC_MIN_RMS:
        return True

    if speaker_audio is None:
        return False

    speaker_audio = normalize_audio(speaker_audio)
    if speaker_audio.size == 0 or audio_rms(speaker_audio) < MIN_TRANSCRIBE_RMS:
        return False

    speaker = resample_audio(speaker_audio, RECORD_SAMPLE_RATE, MIC_LEAK_ANALYSIS_RATE)
    microphone = resample_audio(microphone_audio, RECORD_SAMPLE_RATE, MIC_LEAK_ANALYSIS_RATE)
    size = min(speaker.size, microphone.size)
    if size <= 0:
        return False

    correlation = max_lagged_correlation(speaker[:size], microphone[:size], MIC_LEAK_ANALYSIS_RATE)
    return correlation >= MIC_LEAK_CORRELATION_THRESHOLD


def filter_microphone_bleed(chunks: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    microphone_audio = chunks.get("source_microphone")
    if microphone_audio is None:
        return chunks

    if is_microphone_bleed_from_speaker(chunks.get("source_speaker"), microphone_audio):
        filtered = dict(chunks)
        filtered.pop("source_microphone", None)
        return filtered

    return chunks


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


def usable_model_dir(path: Path) -> bool:
    return path.exists() and (path / "config.json").exists() and (path / "model.bin").exists()


def transcription_model_config(language: str) -> dict[str, object]:
    return TRANSCRIPTION_MODEL_CONFIGS.get(language, TRANSCRIPTION_MODEL_CONFIGS[DEFAULT_TRANSCRIPTION_LANGUAGE])


def whisper_model_path(language: str = DEFAULT_TRANSCRIPTION_LANGUAGE) -> str:
    config = transcription_model_config(language)
    model_dir = config.get("model_dir")
    if isinstance(model_dir, Path) and usable_model_dir(model_dir):
        return str(model_dir)
    return str(config["model_name"])


def whisper_language_code(language: str = DEFAULT_TRANSCRIPTION_LANGUAGE) -> Optional[str]:
    value = transcription_model_config(language).get("language")
    return None if value is None else str(value)


def realtime_transcribe_seconds(language: str = DEFAULT_TRANSCRIPTION_LANGUAGE) -> float:
    return float(transcription_model_config(language).get("transcribe_seconds", TRANSCRIBE_SECONDS))


def realtime_beam_size(language: str = DEFAULT_TRANSCRIPTION_LANGUAGE) -> int:
    return int(transcription_model_config(language).get("beam_size", REALTIME_BEAM_SIZE))


def condition_on_previous_text(language: str = DEFAULT_TRANSCRIPTION_LANGUAGE) -> bool:
    return bool(transcription_model_config(language).get("condition_on_previous_text", False))


def transcription_initial_prompt(language: str = DEFAULT_TRANSCRIPTION_LANGUAGE) -> Optional[str]:
    value = transcription_model_config(language).get("initial_prompt")
    return str(value) if value else None


def simplify_chinese_text(language: str = DEFAULT_TRANSCRIPTION_LANGUAGE) -> bool:
    return bool(transcription_model_config(language).get("simplify_chinese", False))


@dataclass
class AudioFrameSet:
    sources: dict[str, np.ndarray]


@dataclass
class TranscriptJob:
    source_name: str
    audio: np.ndarray


@dataclass
class ReviewJob:
    source_name: str
    start: float
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


@dataclass
class RecordingSession:
    file_stem: str
    transcription_language: str = DEFAULT_TRANSCRIPTION_LANGUAGE
    stop_event: threading.Event = field(default_factory=threading.Event)
    active_event: threading.Event = field(default_factory=threading.Event)
    audio_queue: "queue.Queue[Optional[TranscriptJob]]" = field(default_factory=queue.Queue)
    review_queue: "queue.Queue[Optional[ReviewJob]]" = field(default_factory=queue.Queue)
    minutes_queue: "queue.Queue[Optional[str]]" = field(default_factory=queue.Queue)
    mp3_queue: "queue.Queue[Optional[AudioFrameSet]]" = field(default_factory=queue.Queue)
    mp3_buffer: io.BytesIO = field(default_factory=io.BytesIO)
    transcription_source_queue: "queue.Queue[Optional[AudioFrameSet]]" = field(
        default_factory=lambda: queue.Queue(maxsize=TRANSCRIPTION_SOURCE_QUEUE_MAXSIZE)
    )
    recorder_thread: Optional[threading.Thread] = None
    writer_thread: Optional[threading.Thread] = None
    transcription_preparer_thread: Optional[threading.Thread] = None
    transcription_thread: Optional[threading.Thread] = None
    transcription_result_thread: Optional[threading.Thread] = None
    review_thread: Optional[threading.Thread] = None
    minutes_thread: Optional[threading.Thread] = None
    transcriber_input_queue: Optional[object] = None
    transcriber_output_queue: Optional[object] = None
    transcriber_process: Optional[object] = None
    final_entries: list[TranscriptEntry] = field(default_factory=list)
    final_lock: threading.Lock = field(default_factory=threading.Lock)

    def cleanup(self) -> None:
        with contextlib.suppress(Exception):
            self.mp3_buffer.close()


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
        self.recording_options_window: Optional[tk.Toplevel] = None
        self.recording_options_language_var: Optional[tk.StringVar] = None
        self.recording_options_auto_screenshot_var: Optional[tk.BooleanVar] = None
        self.about_window: Optional[tk.Toplevel] = None
        self.about_language_var: Optional[tk.StringVar] = None
        self.about_body_label: Optional[ttk.Label] = None
        self.about_version_label: Optional[ttk.Label] = None
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
        self.whisper_model = None
        self.whisper_model_language: Optional[str] = None
        self.whisper_model_lock = threading.Lock()
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
        with self.state_lock:
            state = self.state
        if state in {"recording", "stopping"}:
            return self.t("record_stop")
        return self.t("record")

    @property
    def auto_screenshot_menu_text(self) -> str:
        if self.auto_screenshot_enabled:
            return self.t("auto_screenshot_on")
        return self.t("auto_screenshot_off")

    def build_menu(self) -> Menu:
        return Menu(
            MenuItem(lambda item: self.record_menu_text, self.tray_record_clicked),
            MenuItem(lambda item: self.t("text_window"), self.tray_text_window_clicked),
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

    def begin_screenshot_session(self, folder_name: Optional[str] = None) -> Path:
        directory = self.screenshot_root() / (folder_name or screenshot_folder_name())
        with self.screenshot_lock:
            directory.mkdir(parents=True, exist_ok=True)
            self.screenshot_dir = directory
        return directory

    def reset_screenshot_session(self) -> None:
        with self.screenshot_lock:
            self.screenshot_dir = None

    def ensure_screenshot_dir(self) -> Path:
        with self.screenshot_lock:
            if self.screenshot_dir is not None:
                self.screenshot_dir.mkdir(parents=True, exist_ok=True)
                return self.screenshot_dir
            directory = self.screenshot_root() / screenshot_folder_name()
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
        self.save_screenshot(notify=False)

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
        lower_current_thread_priority()
        try:
            while not self.screenshot_stop_event.is_set():
                self.save_screenshot(notify=False)
                if self.screenshot_stop_event.wait(AUTO_SCREENSHOT_INTERVAL_SECONDS):
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
            self.stop_transcription_source_queue(session)
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
            if session.transcription_preparer_thread is not None:
                session.transcription_preparer_thread.join(timeout=2)
            if session.transcription_thread is not None:
                session.transcription_thread.join(timeout=1)
            if session.review_thread is not None:
                session.review_thread.join(timeout=4)
            self.stop_transcriber_process(session, timeout=1, terminate=True)
            if session.minutes_thread is not None:
                session.minutes_thread.join(timeout=1)
            session.cleanup()
        self.root.after(0, self.finalize_exit)

    def finalize_exit(self) -> None:
        self.session = None
        self.clear_transcript()
        self.reset_screenshot_session()
        with contextlib.suppress(Exception):
            if self.transcript_window is not None:
                self.transcript_window.window.destroy()
        self.transcript_window = None
        with contextlib.suppress(Exception):
            if self.recording_options_window is not None:
                self.recording_options_window.destroy()
        self.recording_options_window = None
        self.recording_options_language_var = None
        self.recording_options_auto_screenshot_var = None
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
        if self.about_version_label is not None:
            self.about_version_label.configure(text=self.t("about_version", version=APP_VERSION))
        if self.about_language_label is not None:
            self.about_language_label.configure(text=self.t("language"))
        if self.about_english_check is not None:
            self.about_english_check.configure(text="English")
        if self.about_chinese_check is not None:
            self.about_chinese_check.configure(text="中文")
        if self.about_language_var is not None:
            self.about_language_var.set(self.language)

    def show_recording_options_window(self) -> None:
        if self.recording_options_window is not None and self.recording_options_window.winfo_exists():
            self.recording_options_window.deiconify()
            self.recording_options_window.lift()
            self.recording_options_window.focus_force()
            return

        window = tk.Toplevel(self.root)
        self.recording_options_window = window
        window.title(self.t("recording_options_title"))
        window.geometry("360x220")
        window.resizable(False, False)
        window.protocol("WM_DELETE_WINDOW", self.close_recording_options_window)

        frame = ttk.Frame(window, padding=16)
        frame.pack(fill=tk.BOTH, expand=True)

        self.recording_options_language_var = tk.StringVar(value=DEFAULT_TRANSCRIPTION_LANGUAGE)
        self.recording_options_auto_screenshot_var = tk.BooleanVar(value=self.auto_screenshot_enabled)

        ttk.Label(frame, text=self.t("transcription_language")).pack(anchor=tk.W)
        ttk.Radiobutton(
            frame,
            text=self.t("transcription_language_english"),
            variable=self.recording_options_language_var,
            value="en",
        ).pack(anchor=tk.W, pady=(6, 0))
        ttk.Radiobutton(
            frame,
            text=self.t("transcription_language_chinese"),
            variable=self.recording_options_language_var,
            value="zh",
        ).pack(anchor=tk.W, pady=(4, 0))
        ttk.Checkbutton(
            frame,
            text=self.t("auto_screenshot_option"),
            variable=self.recording_options_auto_screenshot_var,
        ).pack(anchor=tk.W, pady=(14, 0))

        button_frame = ttk.Frame(frame)
        button_frame.pack(anchor=tk.E, fill=tk.X, pady=(18, 0))
        ttk.Button(button_frame, text=self.t("cancel"), command=self.close_recording_options_window).pack(
            side=tk.RIGHT
        )
        ttk.Button(button_frame, text=self.t("start"), command=self.accept_recording_options).pack(
            side=tk.RIGHT, padx=(0, 8)
        )

        window.bind("<Return>", lambda _event: self.accept_recording_options())
        window.bind("<Escape>", lambda _event: self.close_recording_options_window())
        window.update_idletasks()
        x = max(0, (window.winfo_screenwidth() - window.winfo_width()) // 2)
        y = max(0, (window.winfo_screenheight() - window.winfo_height()) // 2)
        window.geometry(f"360x220+{x}+{y}")
        with contextlib.suppress(Exception):
            window.attributes("-topmost", True)
        with contextlib.suppress(Exception):
            window.lift()
            window.focus_force()

    def close_recording_options_window(self) -> None:
        if self.recording_options_window is not None:
            with contextlib.suppress(Exception):
                self.recording_options_window.destroy()
        self.recording_options_window = None
        self.recording_options_language_var = None
        self.recording_options_auto_screenshot_var = None

    def accept_recording_options(self) -> None:
        language_var = self.recording_options_language_var
        auto_screenshot_var = self.recording_options_auto_screenshot_var
        language = language_var.get() if language_var is not None else DEFAULT_TRANSCRIPTION_LANGUAGE
        if language not in TRANSCRIPTION_MODEL_CONFIGS:
            language = DEFAULT_TRANSCRIPTION_LANGUAGE
        auto_screenshot = bool(auto_screenshot_var.get()) if auto_screenshot_var is not None else False
        self.close_recording_options_window()
        self.begin_recording(language, auto_screenshot)

    def toggle_recording(self) -> None:
        with self.state_lock:
            state = self.state

        if state == "idle":
            self.start_recording()
        elif state == "recording":
            self.stop_recording()
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

        if AUDIO_ONLY_DIAGNOSTIC:
            self.begin_recording(DEFAULT_TRANSCRIPTION_LANGUAGE, False)
            return

        self.show_recording_options_window()

    def begin_recording(self, transcription_language: str, auto_screenshot_requested: bool) -> None:
        with self.state_lock:
            state = self.state
        if state == "recording":
            messagebox.showinfo(self.title(), self.t("already_recording"))
            return
        if state == "stopping":
            messagebox.showinfo(self.title(), self.t("stopping_wait"))
            return
        if transcription_language not in TRANSCRIPTION_MODEL_CONFIGS:
            transcription_language = DEFAULT_TRANSCRIPTION_LANGUAGE

        file_stem = recording_file_stem()
        self.begin_screenshot_session(file_stem)
        self.set_auto_screenshot_enabled(auto_screenshot_requested)

        session = RecordingSession(file_stem=file_stem, transcription_language=transcription_language)
        session.active_event.set()

        self.clear_transcript()
        if self.transcript_window is not None:
            self.transcript_window.set_text("")

        self.session = session
        with self.state_lock:
            self.state = "recording"
        self.update_tray_menu()

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
        if not AUDIO_ONLY_DIAGNOSTIC:
            self.start_transcriber_process(session)
            session.transcription_preparer_thread = threading.Thread(
                target=self.transcription_preparer_worker,
                args=(session,),
                name="transcription-preparer",
                daemon=True,
            )
            session.transcription_thread = threading.Thread(
                target=self.transcription_worker,
                args=(session,),
                name="transcription-relay",
                daemon=True,
            )
            if ENABLE_BACKGROUND_REVIEW:
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
        if session.transcription_preparer_thread is not None:
            session.transcription_preparer_thread.start()
        if session.transcription_result_thread is not None:
            session.transcription_result_thread.start()
        if session.transcription_thread is not None:
            session.transcription_thread.start()
        if session.review_thread is not None:
            session.review_thread.start()
        if session.minutes_thread is not None:
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
        self.stop_transcription_source_queue(session)
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

        try:
            import soundcard as sc

            speaker = sc.default_speaker()
            loopback = sc.get_microphone(speaker.name, include_loopback=True)
            capture_sources: list[tuple[str, object]] = [("source_speaker", loopback)]
            if RECORD_MICROPHONE and is_microphone_in_use_by_other_app():
                with contextlib.suppress(Exception):
                    capture_sources.append(("source_microphone", sc.default_microphone()))

            chunk_frames = max(1, int(RECORD_SAMPLE_RATE * CHUNK_SECONDS))

            source_queues: dict[str, "queue.Queue[Optional[np.ndarray]]"] = {
                source_name: queue.Queue() for source_name, _source in capture_sources
            }
            source_done = {name: False for name in source_queues}
            source_errors: "queue.Queue[tuple[str, Exception]]" = queue.Queue()

            for source_name, source in capture_sources:
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

                chunks = filter_microphone_bleed(chunks)
                if not chunks:
                    continue

                mp3_chunks = chunks
                if not MIX_MICROPHONE_IN_MP3:
                    mp3_chunks = {name: audio for name, audio in chunks.items() if name != "source_microphone"}
                if mp3_chunks:
                    session.mp3_queue.put(AudioFrameSet(mp3_chunks))
                if not AUDIO_ONLY_DIAGNOSTIC:
                    self.offer_transcription_source_frame(session, AudioFrameSet(chunks))

            if all(source_done.values()) and not session.stop_event.is_set():
                raise RuntimeError(self.t("all_sources_unavailable"))
        except Exception as exc:
            self.root.after(0, lambda error=exc: self.handle_recording_error(session, error))
        finally:
            session.stop_event.set()
            for thread in source_threads:
                thread.join(timeout=2)
            session.mp3_queue.put(None)
            if not AUDIO_ONLY_DIAGNOSTIC:
                self.stop_transcription_source_queue(session)

    def offer_transcription_source_frame(self, session: RecordingSession, frame_set: AudioFrameSet) -> None:
        try:
            session.transcription_source_queue.put_nowait(frame_set)
            return
        except queue.Full:
            pass

        with contextlib.suppress(queue.Empty):
            session.transcription_source_queue.get_nowait()
        with contextlib.suppress(queue.Full):
            session.transcription_source_queue.put_nowait(frame_set)

    def stop_transcription_source_queue(self, session: RecordingSession) -> None:
        with contextlib.suppress(queue.Full):
            session.transcription_source_queue.put_nowait(None)

    def transcription_preparer_worker(self, session: RecordingSession) -> None:
        lower_current_thread_priority()
        transcription_buffers: dict[str, list[np.ndarray]] = {source: [] for source in AUDIO_SOURCES}
        buffered_frames: dict[str, int] = {source: 0 for source in AUDIO_SOURCES}
        review_buffers: dict[str, list[np.ndarray]] = {source: [] for source in AUDIO_SOURCES}
        review_buffered_frames: dict[str, int] = {source: 0 for source in AUDIO_SOURCES}
        review_start_frames: dict[str, int] = {source: 0 for source in AUDIO_SOURCES}
        transcribe_frames = int(TRANSCRIBE_SAMPLE_RATE * realtime_transcribe_seconds(session.transcription_language))
        review_frames = int(TRANSCRIBE_SAMPLE_RATE * REVIEW_SECONDS)
        review_min_frames = int(TRANSCRIBE_SAMPLE_RATE * REVIEW_MIN_AUDIO_SECONDS)
        min_frames = int(TRANSCRIBE_SAMPLE_RATE * REALTIME_MIN_AUDIO_SECONDS)

        try:
            while True:
                try:
                    frame_set = session.transcription_source_queue.get(timeout=0.1)
                except queue.Empty:
                    if session.stop_event.is_set():
                        break
                    continue

                if frame_set is None or session.stop_event.is_set():
                    break
                if not session.active_event.wait(0.1):
                    continue

                for source_name in AUDIO_SOURCES:
                    source_audio = frame_set.sources.get(source_name)
                    if source_audio is None or source_audio.size == 0:
                        continue

                    transcript_audio = resample_audio(source_audio, RECORD_SAMPLE_RATE, TRANSCRIBE_SAMPLE_RATE)
                    if ENABLE_BACKGROUND_REVIEW:
                        self.buffer_review_audio(
                            session,
                            source_name,
                            transcript_audio,
                            review_buffers,
                            review_buffered_frames,
                            review_start_frames,
                            review_frames,
                        )

                    if audio_rms(transcript_audio) < MIN_TRANSCRIBE_RMS:
                        continue
                    transcription_buffers[source_name].append(transcript_audio)
                    buffered_frames[source_name] += transcript_audio.size
                    if buffered_frames[source_name] >= transcribe_frames:
                        session.audio_queue.put(TranscriptJob(source_name, np.concatenate(transcription_buffers[source_name])))
                        transcription_buffers[source_name].clear()
                        buffered_frames[source_name] = 0

            if not session.stop_event.is_set():
                for source_name, source_buffer in transcription_buffers.items():
                    if buffered_frames[source_name] >= min_frames and source_buffer:
                        session.audio_queue.put(TranscriptJob(source_name, np.concatenate(source_buffer)))
        finally:
            if ENABLE_BACKGROUND_REVIEW and not session.stop_event.is_set():
                self.flush_review_buffers(
                    session,
                    review_buffers,
                    review_buffered_frames,
                    review_start_frames,
                    review_min_frames,
                )
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
    ) -> None:
        if audio.size == 0:
            return
        review_buffers[source_name].append(audio)
        review_buffered_frames[source_name] += audio.size
        if review_buffered_frames[source_name] >= target_frames:
            self.flush_review_buffer(
                session,
                source_name,
                review_buffers,
                review_buffered_frames,
                review_start_frames,
                target_frames,
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
            )

    def flush_review_buffer(
        self,
        session: RecordingSession,
        source_name: str,
        review_buffers: dict[str, list[np.ndarray]],
        review_buffered_frames: dict[str, int],
        review_start_frames: dict[str, int],
        min_frames: int,
    ) -> None:
        source_buffer = review_buffers[source_name]
        if not source_buffer:
            return

        audio = np.concatenate(source_buffer)
        start = review_start_frames[source_name] / TRANSCRIBE_SAMPLE_RATE
        review_start_frames[source_name] += audio.size
        review_buffers[source_name] = []
        review_buffered_frames[source_name] = 0

        if audio.size >= min_frames and audio_rms(audio) >= MIN_TRANSCRIBE_RMS:
            session.review_queue.put(ReviewJob(source_name, start, audio))

    def collect_source_chunks(
        self,
        source_queues: dict[str, "queue.Queue[Optional[np.ndarray]]"],
        source_done: dict[str, bool],
        chunk_frames: int,
    ) -> dict[str, np.ndarray]:
        chunks: dict[str, np.ndarray] = {}
        pending = {name for name, done in source_done.items() if not done}
        deadline = time.monotonic() + CHUNK_SECONDS + CAPTURE_BLOCK_SECONDS + 0.25

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
                    chunks[source_name] = normalize_audio(item)

            if pending and not received:
                time.sleep(0.01)

        return chunks

    def mp3_writer_worker(self, session: RecordingSession) -> None:
        encoder = None

        try:
            import lameenc

            encoder = lameenc.Encoder()
            encoder.set_bit_rate(128)
            encoder.set_in_sample_rate(RECORD_SAMPLE_RATE)
            encoder.set_channels(CHANNELS)
            encoder.set_quality(2)

            while True:
                frame_set = session.mp3_queue.get()
                if frame_set is None:
                    break
                if not frame_set.sources:
                    continue

                audio = mix_audio_sources(list(frame_set.sources.values()))
                if audio.size == 0:
                    continue
                pcm_bytes = float_audio_to_int16(audio).tobytes()

                mp3_chunk = encoder.encode(pcm_bytes)
                if mp3_chunk:
                    session.mp3_buffer.write(mp3_chunk)

            final_chunk = encoder.flush()
            if final_chunk:
                session.mp3_buffer.write(final_chunk)
        except Exception as exc:
            session.stop_event.set()
            self.root.after(0, lambda error=exc: self.handle_recording_error(session, error))

    def audio_capture_worker(
        self,
        session: RecordingSession,
        source_name: str,
        source: object,
        target_queue: "queue.Queue[Optional[np.ndarray]]",
        error_queue: "queue.Queue[tuple[str, Exception]]",
    ) -> None:
        buffers: list[np.ndarray] = []
        buffered_frames = 0
        try:
            raise_current_thread_priority()
            output_frames = max(1, int(RECORD_SAMPLE_RATE * CHUNK_SECONDS))
            read_frames = max(1, int(RECORD_SAMPLE_RATE * CAPTURE_READ_SECONDS))
            block_frames = max(read_frames * 4, int(RECORD_SAMPLE_RATE * CAPTURE_BLOCK_SECONDS))
            with source.recorder(
                samplerate=RECORD_SAMPLE_RATE,
                channels=CHANNELS,
                blocksize=block_frames,
            ) as recorder:
                while not session.stop_event.is_set():
                    if not session.active_event.wait(0.02):
                        continue

                    data = recorder.record(numframes=read_frames)
                    audio = normalize_audio(data)
                    if not audio.size:
                        continue

                    buffers.append(audio)
                    buffered_frames += audio.size
                    while buffered_frames >= output_frames:
                        combined = np.concatenate(buffers)
                        target_queue.put(combined[:output_frames].copy())
                        remainder = combined[output_frames:]
                        buffers = [remainder] if remainder.size else []
                        buffered_frames = remainder.size

            if buffered_frames:
                target_queue.put(np.concatenate(buffers))
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

    def start_transcriber_process(self, session: RecordingSession) -> None:
        try:
            context = mp.get_context("spawn")
            session.transcriber_input_queue = context.Queue(maxsize=TRANSCRIBER_INPUT_QUEUE_MAXSIZE)
            session.transcriber_output_queue = context.Queue(maxsize=TRANSCRIBER_OUTPUT_QUEUE_MAXSIZE)
            config = {
                "model_path": whisper_model_path(session.transcription_language),
                "device": WHISPER_DEVICE,
                "compute_type": WHISPER_COMPUTE_TYPE,
                "cpu_threads": WHISPER_CPU_THREADS,
                "num_workers": WHISPER_NUM_WORKERS,
                "language": whisper_language_code(session.transcription_language),
                "condition_on_previous_text": condition_on_previous_text(session.transcription_language),
                "initial_prompt": transcription_initial_prompt(session.transcription_language),
                "simplify_chinese": simplify_chinese_text(session.transcription_language),
                "vad_parameters": VAD_PARAMETERS,
            }
            session.transcriber_process = context.Process(
                target=run_transcriber_process,
                args=(session.transcriber_input_queue, session.transcriber_output_queue, config),
                name="whisper-transcriber",
                daemon=True,
            )
            session.transcriber_process.start()
            session.transcription_result_thread = threading.Thread(
                target=self.transcription_result_worker,
                args=(session,),
                name="transcription-results",
                daemon=True,
            )
        except Exception as exc:
            self.close_transcriber_queues(session)
            self.root.after(0, lambda error=exc: self.show_transcription_runtime_warning(error))

    def submit_transcriber_job(self, session: RecordingSession, job: dict[str, object]) -> None:
        input_queue = session.transcriber_input_queue
        if input_queue is None:
            return
        try:
            input_queue.put_nowait(job)
            return
        except queue.Full:
            pass

        with contextlib.suppress(queue.Empty):
            input_queue.get_nowait()
        with contextlib.suppress(queue.Full):
            input_queue.put_nowait(job)

    def signal_transcriber_stop(self, session: RecordingSession) -> None:
        input_queue = session.transcriber_input_queue
        if input_queue is None:
            return
        try:
            input_queue.put_nowait(None)
            return
        except queue.Full:
            pass

        with contextlib.suppress(queue.Empty):
            input_queue.get_nowait()
        with contextlib.suppress(queue.Full):
            input_queue.put_nowait(None)

    def stop_transcriber_process(self, session: RecordingSession, timeout: float = 2.0, terminate: bool = False) -> None:
        self.signal_transcriber_stop(session)

        process = session.transcriber_process
        if process is not None:
            process.join(timeout=timeout)
            if terminate and process.is_alive():
                with contextlib.suppress(Exception):
                    process.terminate()
                process.join(timeout=1)
            with contextlib.suppress(Exception):
                process.close()
            session.transcriber_process = None

        result_thread = session.transcription_result_thread
        if result_thread is not None and result_thread is not threading.current_thread():
            result_thread.join(timeout=timeout)
        session.transcription_result_thread = None
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
        lower_current_thread_priority()
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
                if text and not session.stop_event.is_set():
                    self.append_transcript(session, source_name, text)

    def get_whisper_model(self, transcription_language: str) -> object:
        language = transcription_language
        if language not in TRANSCRIPTION_MODEL_CONFIGS:
            language = DEFAULT_TRANSCRIPTION_LANGUAGE
        with self.whisper_model_lock:
            if self.whisper_model is None or self.whisper_model_language != language:
                from faster_whisper import WhisperModel

                self.whisper_model = WhisperModel(
                    whisper_model_path(language),
                    device=WHISPER_DEVICE,
                    compute_type=WHISPER_COMPUTE_TYPE,
                    cpu_threads=WHISPER_CPU_THREADS,
                    num_workers=WHISPER_NUM_WORKERS,
                )
                self.whisper_model_language = language
            return self.whisper_model

    def transcription_worker(self, session: RecordingSession) -> None:
        lower_current_thread_priority()
        if session.transcriber_input_queue is None:
            self.drain_audio_queue(session.audio_queue)
            return

        try:
            while not session.stop_event.is_set():
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
                        "source_name": job.source_name,
                        "audio": audio,
                        "beam_size": realtime_beam_size(session.transcription_language),
                    },
                )
        finally:
            self.signal_transcriber_stop(session)

    def review_transcription_worker(self, session: RecordingSession) -> None:
        lower_current_thread_priority()
        try:
            model = self.get_whisper_model(session.transcription_language)
        except ImportError:
            self.root.after(0, self.show_transcription_dependency_warning)
            self.drain_audio_queue(session.review_queue)
            return
        except Exception as exc:
            self.root.after(0, lambda error=exc: self.show_transcription_runtime_warning(error))
            self.drain_audio_queue(session.review_queue)
            return

        while True:
            job = session.review_queue.get()
            if job is None:
                break

            audio = job.audio
            if audio.size == 0 or audio_rms(audio) < MIN_TRANSCRIBE_RMS:
                continue

            try:
                segments, _info = model.transcribe(
                    audio,
                    beam_size=FINAL_BEAM_SIZE,
                    language=whisper_language_code(session.transcription_language),
                    vad_filter=True,
                    vad_parameters=VAD_PARAMETERS,
                    condition_on_previous_text=True,
                )
                entries = [
                    TranscriptEntry(job.start + float(segment.start), job.source_name, value)
                    for segment in segments
                    if (value := segment.text.strip())
                ]
            except Exception as exc:
                self.root.after(0, lambda error=exc: self.show_transcription_runtime_warning(error))
                continue

            if entries:
                with session.final_lock:
                    session.final_entries.extend(entries)

    def meeting_minutes_worker(self, session: RecordingSession) -> None:
        lower_current_thread_priority()
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

        self.root.after(0, lambda active_session=session, current_text=document: self.refresh_transcript_text(active_session, current_text))

    def cancel_pending_transcription(self, session: RecordingSession) -> None:
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
        if session.transcription_preparer_thread is not None:
            session.transcription_preparer_thread.join(timeout=2)
        if session.transcription_thread is not None:
            session.transcription_thread.join(timeout=2)
        if session.review_thread is not None:
            session.review_thread.join(timeout=2)
        self.stop_transcriber_process(session, timeout=2, terminate=True)
        self.cancel_pending_minutes(session)
        if session.minutes_thread is not None:
            session.minutes_thread.join(timeout=1)
        self.finalize_transcript_from_review(session)
        self.root.after(0, lambda: self.complete_stop(session))

    def finalize_transcript_from_review(self, session: RecordingSession) -> None:
        with session.final_lock:
            entries = list(session.final_entries)

        transcript = self.format_transcript_entries(entries)
        if not transcript:
            with self.transcript_lock:
                transcript = self.transcript_body_text or self.transcript_without_existing_minutes(self.transcript_text)

        summary = self.build_meeting_summary(transcript)
        document = transcript
        if summary:
            document = f"{transcript}\n\n{summary}" if transcript else summary
        self.replace_transcript_document(session, document)

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
            return

        with self.state_lock:
            self.state = "idle"
        self.update_tray_menu()

        if self.shutting_down:
            session.cleanup()
            self.session = None
            self.clear_transcript()
            self.reset_screenshot_session()
            return

        should_save = messagebox.askyesno(
            self.title(),
            self.t("save_question"),
        )
        if should_save:
            self.ask_and_save_files(session)

        session.cleanup()
        self.session = None
        self.clear_transcript()
        self.reset_screenshot_session()
        if self.transcript_window is not None:
            self.transcript_window.set_text("")

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
            with self.transcript_lock:
                transcript = self.transcript_text

            if text_target.exists():
                overwrite_text = messagebox.askyesno(
                    self.title(),
                    self.t("overwrite_text", filename=text_target.name),
                )
                if not overwrite_text:
                    continue

            try:
                mp3_target.write_bytes(session.mp3_buffer.getvalue())
                text_target.write_text(transcript, encoding="utf-8")
            except Exception as exc:
                messagebox.showerror(self.title(), f"{self.t('save_failed')}\n{exc}")
                continue

            messagebox.showinfo(
                self.title(),
                f"{self.t('saved')}\n{mp3_target}\n{text_target}",
            )
            return

    def append_transcript(self, session: RecordingSession, source_name: str, text: str) -> None:
        if self.session is not session:
            return

        sentences = split_sentences(text)
        if not sentences:
            return

        with self.transcript_lock:
            if self.transcript_blocks and self.transcript_blocks[-1].source_name == source_name:
                self.transcript_blocks[-1].sentences.extend(sentences)
            else:
                self.transcript_blocks.append(TranscriptBlock(source_name, sentences))
            transcript = self.format_transcript_blocks(self.transcript_blocks)
            self.transcript_body_text = transcript
            document = self.compose_transcript_document(self.transcript_body_text, self.meeting_minutes_text)
            self.transcript_text = document

        self.queue_minutes_update(session, transcript)
        self.root.after(0, lambda active_session=session, current_text=document: self.refresh_transcript_text(active_session, current_text))

    def replace_transcript_document(self, session: RecordingSession, text: str) -> None:
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

        self.about_version_label = ttk.Label(frame)
        self.about_version_label.pack(anchor=tk.W, pady=(8, 0))

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
        self.about_version_label = None
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

        session.stop_event.set()
        session.active_event.set()
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
