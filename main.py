from __future__ import annotations

import contextlib
import copy
import datetime as dt
import io
import json
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
from typing import Any, Optional

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
        "offline_transcript": "Offline Transcript",
        "system_settings": "System Settings",
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
        "offline_live_busy": "Please stop live recording before using Offline Transcript.",
        "offline_busy": "Offline transcript is currently running. Please stop it before starting live recording.",
        "ask_auto_screenshot": "Do you want to turn on Auto Screenshot for this recording?",
        "recording_options_title": "Start Recording",
        "transcription_language": "Transcription language",
        "recognition_prompt": "Recognition prompt",
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
        "audio_files": "Audio files",
        "text_files": "Text files",
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
        "summary_note_rules": "This summary is generated locally from the transcript.",
        "summary_note_llm": "This summary is generated by the configured local LLM.",
        "no_summary": "Not enough transcript text to summarize.",
        "no_action_items": "No clear action items detected.",
    },
    "zh": {
        "app_name": "本地会议录音",
        "record": "🔴 开始录音",
        "record_stop": "◼ 停止录音",
        "text_window": "文本窗口",
        "offline_transcript": "离线转写",
        "system_settings": "系统设置",
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
        "offline_live_busy": "请先停止实时录音，然后再使用离线转写。",
        "offline_busy": "离线转写正在运行，请先停止它再开始实时录音。",
        "ask_auto_screenshot": "是否为本次录音开启 Auto Screenshot？",
        "recording_options_title": "开始录音",
        "transcription_language": "转录语言",
        "recognition_prompt": "辅助识别 Prompt",
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
        "audio_files": "音频文件",
        "text_files": "文本文件",
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
        "summary_note_rules": "该总结由本地规则根据转录文本生成。",
        "summary_note_llm": "该总结由配置的本地大模型生成。",
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


def format_duration(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def format_wall_time(timestamp: Optional[float]) -> str:
    if timestamp is None:
        return "-"
    return dt.datetime.fromtimestamp(timestamp).strftime("%H:%M:%S")


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


def default_realtime_settings() -> dict[str, Any]:
    return {
        "sample_rate": TRANSCRIBE_SAMPLE_RATE,
        "whisper_device": WHISPER_DEVICE,
        "compute_type": WHISPER_COMPUTE_TYPE,
        "cpu_threads": WHISPER_CPU_THREADS,
        "num_workers": WHISPER_NUM_WORKERS,
        "vad_filter": True,
        "vad_min_silence_duration_ms": int(VAD_PARAMETERS.get("min_silence_duration_ms", 500)),
        "min_transcribe_rms": MIN_TRANSCRIBE_RMS,
        "transcriber_input_queue_maxsize": TRANSCRIBER_INPUT_QUEUE_MAXSIZE,
        "chunk_seconds": CHUNK_SECONDS,
        "capture_read_seconds": CAPTURE_READ_SECONDS,
        "capture_block_seconds": CAPTURE_BLOCK_SECONDS,
        "auto_screenshot_interval_seconds": AUTO_SCREENSHOT_INTERVAL_SECONDS,
        "auto_stop_meeting_end": True,
        "meeting_end_idle_seconds": 120.0,
        "meeting_end_rms_threshold": MIN_TRANSCRIBE_RMS,
        "llm_summary_enabled": False,
        "llm_summary_base_url": "http://127.0.0.1:8080/v1/chat/completions",
        "llm_summary_model": "Qwen/Qwen3-4B-GGUF:Q4_K_M",
        "llm_summary_timeout_seconds": 90.0,
        "llm_summary_max_input_chars": 12000,
        "llm_summary_temperature": 0.2,
        "languages": {
            "en": {
                "model_name": ENGLISH_WHISPER_MODEL,
                "model_dir": str(ENGLISH_MODEL_DIR),
                "language": "en",
                "transcribe_seconds": realtime_transcribe_seconds("en"),
                "overlap_seconds": 0.0,
                "beam_size": realtime_beam_size("en"),
                "condition_on_previous_text": condition_on_previous_text("en"),
                "initial_prompt": transcription_initial_prompt("en") or "",
                "simplify_chinese": simplify_chinese_text("en"),
            },
            "zh": {
                "model_name": CHINESE_WHISPER_MODEL,
                "model_dir": str(CHINESE_MODEL_DIR),
                "language": "zh",
                "transcribe_seconds": realtime_transcribe_seconds("zh"),
                "overlap_seconds": 0.0,
                "beam_size": realtime_beam_size("zh"),
                "condition_on_previous_text": condition_on_previous_text("zh"),
                "initial_prompt": transcription_initial_prompt("zh") or "",
                "simplify_chinese": simplify_chinese_text("zh"),
            },
        },
    }


def merge_dict(base: dict[str, Any], updates: object) -> dict[str, Any]:
    if not isinstance(updates, dict):
        return base
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            merge_dict(base[key], value)
        else:
            base[key] = value
    return base


def user_settings_path() -> Path:
    return app_directory() / "local_meeting_recorder_settings.json"


def output_directory() -> Path:
    return app_directory() / "output"


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
    realtime_settings: dict[str, Any] = field(default_factory=default_realtime_settings)
    recognition_prompt: str = ""
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
    meeting_idle_started_at: Optional[float] = None
    meeting_last_microphone_check_at: float = 0.0
    meeting_last_microphone_active: bool = False
    auto_stop_requested: bool = False
    stop_auto_save: bool = False
    stop_reason: str = ""

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


class AudioMciPlayer:
    def __init__(self) -> None:
        self.alias = f"offline_audio_{id(self)}"
        self.path: Optional[Path] = None
        self.length_ms = 0
        self.lock = threading.RLock()

    def _send(self, command: str, buffer_length: int = 0, check: bool = True) -> str:
        if sys.platform != "win32":
            raise RuntimeError("Audio playback is only available on Windows.")

        import ctypes

        buffer = ctypes.create_unicode_buffer(buffer_length) if buffer_length else None
        result = ctypes.windll.winmm.mciSendStringW(command, buffer, buffer_length, None)
        if result and check:
            error_buffer = ctypes.create_unicode_buffer(256)
            ctypes.windll.winmm.mciGetErrorStringW(result, error_buffer, 256)
            raise RuntimeError(error_buffer.value or f"MCI error {result}")
        return buffer.value if buffer is not None else ""

    def open(self, path: Path) -> float:
        with self.lock:
            self.close()
            self.path = path
            quoted_path = str(path).replace('"', "")
            self._send(f'open "{quoted_path}" alias {self.alias}')
            self._send(f"set {self.alias} time format milliseconds")
            length = self._send(f"status {self.alias} length", buffer_length=64)
            self.length_ms = max(0, int(float(length.strip() or 0)))
            return self.length_ms / 1000

    def close(self) -> None:
        with self.lock:
            with contextlib.suppress(Exception):
                self._send(f"stop {self.alias}", check=False)
            with contextlib.suppress(Exception):
                self._send(f"close {self.alias}", check=False)
            self.path = None
            self.length_ms = 0

    def play(self) -> None:
        with self.lock:
            if self.path is None:
                return
            self._send(f"play {self.alias}")

    def play_range(self, start_seconds: float, end_seconds: float, wait: bool = True) -> None:
        with self.lock:
            if self.path is None:
                return
            start_ms = max(0, int(start_seconds * 1000))
            end_ms = max(start_ms, int(end_seconds * 1000))
            if self.length_ms:
                end_ms = min(end_ms, self.length_ms)
            suffix = " wait" if wait else ""
            command = f"play {self.alias} from {start_ms} to {end_ms}{suffix}"
        self._send(command)

    def pause(self) -> None:
        with self.lock:
            if self.path is None:
                return
            self._send(f"pause {self.alias}", check=False)

    def stop(self, reset: bool = False) -> None:
        with self.lock:
            if self.path is None:
                return
            self._send(f"stop {self.alias}", check=False)
            if reset:
                self._send(f"seek {self.alias} to 0", check=False)

    def mode(self) -> str:
        with self.lock:
            if self.path is None:
                return "closed"
            return self._send(f"status {self.alias} mode", buffer_length=64, check=False).strip().lower()

    def position_seconds(self) -> float:
        with self.lock:
            if self.path is None:
                return 0.0
            value = self._send(f"status {self.alias} position", buffer_length=64, check=False)
            with contextlib.suppress(ValueError):
                return max(0.0, float(value.strip() or 0) / 1000)
            return 0.0

    def seek_relative(self, delta_seconds: float) -> None:
        with self.lock:
            if self.path is None:
                return
            current_ms = int(self.position_seconds() * 1000)
            target_ms = max(0, current_ms + int(delta_seconds * 1000))
            if self.length_ms:
                target_ms = min(target_ms, self.length_ms)
            self._send(f"seek {self.alias} to {target_ms}", check=False)

    def length_seconds(self) -> float:
        with self.lock:
            return self.length_ms / 1000 if self.length_ms else 0.0


class OfflineTranscriptWindow:
    def __init__(self, root: tk.Tk, app: "MeetingRecorderApp") -> None:
        self.root = root
        self.app = app
        self.window = tk.Toplevel(root)
        self.window.title(app.t("offline_transcript"))
        self.window.geometry("1080x700")
        self.window.minsize(900, 560)
        self.window.protocol("WM_DELETE_WINDOW", self.close)

        self.audio_path: Optional[Path] = None
        self.duration_seconds = 0.0
        self.state = "idle"
        self.audio_state = "stopped"
        self.decoded_audio: Optional[np.ndarray] = None
        self.decoded_sample_rate = TRANSCRIBE_SAMPLE_RATE
        self.processed_audio_seconds = 0.0
        self.decoded_audio_lock = threading.Lock()
        self.closed = False
        self.started_at: Optional[float] = None
        self.finished_at: Optional[float] = None
        self.offline_file_stem = recording_file_stem()
        self.worker_thread: Optional[threading.Thread] = None
        self.summary_thread: Optional[threading.Thread] = None
        self.summary_running = False
        self.stop_event = threading.Event()
        self.active_event = threading.Event()
        self.transcriber_input_queue: Optional[Any] = None
        self.transcriber_output_queue: Optional[Any] = None
        self.transcriber_process: Optional[Any] = None
        self.active_settings: Optional[dict[str, Any]] = None
        self.player = AudioMciPlayer()

        self.language_var = tk.StringVar(value=DEFAULT_TRANSCRIPTION_LANGUAGE)
        self.slice_seconds_var = tk.DoubleVar(value=realtime_transcribe_seconds(DEFAULT_TRANSCRIPTION_LANGUAGE))
        self.overlap_seconds_var = tk.DoubleVar(value=0.0)
        self.beam_size_var = tk.IntVar(value=realtime_beam_size(DEFAULT_TRANSCRIPTION_LANGUAGE))
        self.device_var = tk.StringVar(value=WHISPER_DEVICE)
        self.compute_type_var = tk.StringVar(value=WHISPER_COMPUTE_TYPE)
        self.cpu_threads_var = tk.IntVar(value=WHISPER_CPU_THREADS)
        self.num_workers_var = tk.IntVar(value=WHISPER_NUM_WORKERS)
        self.vad_var = tk.BooleanVar(value=True)
        self.vad_silence_ms_var = tk.IntVar(value=int(VAD_PARAMETERS.get("min_silence_duration_ms", 500)))
        self.condition_var = tk.BooleanVar(value=condition_on_previous_text(DEFAULT_TRANSCRIPTION_LANGUAGE))
        self.min_rms_var = tk.DoubleVar(value=MIN_TRANSCRIBE_RMS)
        self.simplify_var = tk.BooleanVar(value=simplify_chinese_text(DEFAULT_TRANSCRIPTION_LANGUAGE))
        self.play_chunks_var = tk.BooleanVar(value=False)
        self.progress_var = tk.DoubleVar(value=0.0)
        self.audio_progress_var = tk.DoubleVar(value=0.0)
        self.sample_rate_var = tk.IntVar(value=TRANSCRIBE_SAMPLE_RATE)

        self.status_var = tk.StringVar(value="Import an audio file to begin.")
        self.file_var = tk.StringVar(value="File: -")
        self.audio_position_var = tk.StringVar(value="Audio: 00:00 / 00:00")
        self.duration_var = tk.StringVar(value="Duration: -")
        self.processed_var = tk.StringVar(value="Processed: 00:00 / 00:00")
        self.started_var = tk.StringVar(value="Started: -")
        self.finished_var = tk.StringVar(value="Finished: -")
        self.elapsed_var = tk.StringVar(value="Elapsed: -")

        self._build_ui()
        self.apply_language_defaults()
        self.poll_audio_progress()

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.window, padding=12)
        outer.pack(fill=tk.BOTH, expand=True)
        outer.columnconfigure(0, weight=0, minsize=300)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(0, weight=1)

        settings = ttk.Frame(outer, width=300)
        settings.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        settings.columnconfigure(0, weight=1)

        basic = ttk.LabelFrame(settings, text="Basic", padding=10)
        basic.grid(row=0, column=0, sticky="ew")
        basic.columnconfigure(1, weight=1)

        ttk.Label(basic, text="Language").grid(row=0, column=0, sticky="w", columnspan=2)
        ttk.Radiobutton(
            basic,
            text="English",
            variable=self.language_var,
            value="en",
            command=self.apply_language_defaults,
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Radiobutton(
            basic,
            text="Chinese",
            variable=self.language_var,
            value="zh",
            command=self.apply_language_defaults,
        ).grid(row=1, column=1, sticky="w", pady=(4, 0))

        self._add_entry(basic, "Slice sec", self.slice_seconds_var, 2)
        self._add_entry(basic, "Overlap sec", self.overlap_seconds_var, 3)
        self._add_entry(basic, "Beam size", self.beam_size_var, 4)
        self._add_entry(basic, "Min RMS", self.min_rms_var, 5)

        advanced = ttk.LabelFrame(settings, text="Advanced", padding=10)
        advanced.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        advanced.columnconfigure(1, weight=1)
        ttk.Checkbutton(advanced, text="VAD filter", variable=self.vad_var).grid(
            row=0, column=0, columnspan=2, sticky="w"
        )
        self._add_entry(advanced, "VAD silence ms", self.vad_silence_ms_var, 1)
        ttk.Checkbutton(advanced, text="Previous text", variable=self.condition_var).grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(8, 0)
        )
        ttk.Checkbutton(advanced, text="Simplified CN", variable=self.simplify_var).grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(4, 0)
        )
        ttk.Checkbutton(
            advanced,
            text="Play chunks",
            variable=self.play_chunks_var,
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(4, 0))
        ttk.Label(advanced, text="Initial prompt").grid(row=5, column=0, columnspan=2, sticky="w", pady=(12, 4))
        self.initial_prompt_text = tk.Text(advanced, height=6, width=28, wrap=tk.WORD, font=("Microsoft YaHei UI", 9))
        self.initial_prompt_text.grid(row=6, column=0, columnspan=2, sticky="ew")

        engine = ttk.LabelFrame(settings, text="Engine", padding=10)
        engine.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        engine.columnconfigure(1, weight=1)
        self._add_readonly_entry(engine, "Sample rate", self.sample_rate_var, 0)
        self._add_entry(engine, "Device", self.device_var, 1)
        self._add_entry(engine, "Compute", self.compute_type_var, 2)
        self._add_entry(engine, "CPU threads", self.cpu_threads_var, 3)
        self._add_entry(engine, "Workers", self.num_workers_var, 4)

        right = ttk.Frame(outer)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(3, weight=1)

        controls = ttk.Frame(right)
        controls.grid(row=0, column=0, sticky="ew")
        controls.columnconfigure(1, weight=1)

        audio_row = ttk.Frame(controls)
        audio_row.grid(row=0, column=0, sticky="w")
        ttk.Label(audio_row, text="Audio:").pack(side=tk.LEFT, padx=(0, 8))
        self.audio_import_button = ttk.Button(audio_row, text="Import", command=self.import_audio)
        self.audio_import_button.pack(side=tk.LEFT, padx=(0, 4))
        self.audio_play_button = ttk.Button(audio_row, text="Play", command=self.play_audio)
        self.audio_play_button.pack(side=tk.LEFT, padx=(0, 4))
        self.audio_pause_button = ttk.Button(audio_row, text="Pause", command=self.pause_audio)
        self.audio_pause_button.pack(side=tk.LEFT, padx=(0, 4))
        self.audio_stop_button = ttk.Button(audio_row, text="Stop", command=self.stop_audio)
        self.audio_stop_button.pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(audio_row, text="-15s", command=lambda: self.seek_audio(-15)).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(audio_row, text="+15s", command=lambda: self.seek_audio(15)).pack(side=tk.LEFT)

        transcript_row = ttk.Frame(controls)
        transcript_row.grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Label(transcript_row, text="Transcript:").pack(side=tk.LEFT, padx=(0, 8))
        self.start_button = ttk.Button(transcript_row, text="Start", command=self.start_transcript)
        self.start_button.pack(side=tk.LEFT, padx=(0, 4))
        self.pause_button = ttk.Button(transcript_row, text="Pause", command=self.pause_transcript)
        self.pause_button.pack(side=tk.LEFT, padx=(0, 4))
        self.stop_button = ttk.Button(transcript_row, text="Stop", command=self.stop_transcript)
        self.stop_button.pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(transcript_row, text="Save As", command=self.save_as).pack(side=tk.LEFT, padx=(0, 4))
        self.minutes_button = ttk.Button(transcript_row, text="Generate Minutes", command=self.generate_minutes)
        self.minutes_button.pack(side=tk.LEFT)

        status = ttk.Frame(right)
        status.grid(row=1, column=0, sticky="ew", pady=(12, 6))
        status.columnconfigure(0, weight=1)
        ttk.Label(status, textvariable=self.file_var).grid(row=0, column=0, sticky="w")
        ttk.Label(status, textvariable=self.duration_var).grid(row=0, column=1, sticky="e", padx=(12, 0))
        ttk.Progressbar(status, maximum=1000, variable=self.audio_progress_var).grid(
            row=1, column=0, columnspan=2, sticky="ew", pady=(6, 2)
        )
        ttk.Label(status, textvariable=self.audio_position_var).grid(row=2, column=0, sticky="w")
        ttk.Progressbar(status, maximum=1000, variable=self.progress_var).grid(
            row=3, column=0, columnspan=2, sticky="ew", pady=(8, 2)
        )
        ttk.Label(status, textvariable=self.processed_var).grid(row=4, column=0, sticky="w")
        ttk.Label(status, textvariable=self.status_var).grid(row=4, column=1, sticky="e", padx=(12, 0))

        timing = ttk.Frame(right)
        timing.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        timing.columnconfigure((0, 1, 2), weight=1)
        ttk.Label(timing, textvariable=self.started_var).grid(row=0, column=0, sticky="w")
        ttk.Label(timing, textvariable=self.finished_var).grid(row=0, column=1, sticky="w")
        ttk.Label(timing, textvariable=self.elapsed_var).grid(row=0, column=2, sticky="w")

        text_frame = ttk.Frame(right)
        text_frame.grid(row=3, column=0, sticky="nsew")
        text_frame.columnconfigure(0, weight=1)
        text_frame.rowconfigure(0, weight=1)
        self.text = tk.Text(text_frame, wrap=tk.WORD, undo=False, font=("Microsoft YaHei UI", 11), padx=10, pady=10)
        scrollbar = ttk.Scrollbar(text_frame, orient=tk.VERTICAL, command=self.text.yview)
        self.text.configure(yscrollcommand=scrollbar.set)
        self.text.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        scroll_buttons = ttk.Frame(text_frame)
        scroll_buttons.grid(row=0, column=2, sticky="ns", padx=(6, 0))
        ttk.Button(scroll_buttons, text="Up", width=6, command=lambda: self.text.yview_scroll(-8, "units")).pack(
            pady=(0, 6)
        )
        ttk.Button(scroll_buttons, text="Down", width=6, command=lambda: self.text.yview_scroll(8, "units")).pack()
        self.refresh_buttons()

    def _add_entry(self, parent: ttk.Frame, label: str, variable: tk.Variable, row: int) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(parent, textvariable=variable, width=8).grid(row=row, column=1, sticky="ew", pady=(8, 0))

    def _add_readonly_entry(self, parent: ttk.Frame, label: str, variable: tk.Variable, row: int) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(parent, textvariable=variable, width=8, state="readonly").grid(
            row=row,
            column=1,
            sticky="ew",
            pady=(8, 0),
        )

    def set_button_pressed(self, button: ttk.Button, pressed: bool) -> None:
        try:
            button.state(["pressed"] if pressed else ["!pressed"])
        except Exception:
            pass

    def apply_language_defaults(self) -> None:
        language = self.language_var.get()
        if language not in TRANSCRIPTION_MODEL_CONFIGS:
            language = DEFAULT_TRANSCRIPTION_LANGUAGE
            self.language_var.set(language)
        self.slice_seconds_var.set(realtime_transcribe_seconds(language))
        self.beam_size_var.set(realtime_beam_size(language))
        self.condition_var.set(condition_on_previous_text(language))
        self.simplify_var.set(simplify_chinese_text(language))
        self.initial_prompt_text.delete("1.0", tk.END)
        prompt = transcription_initial_prompt(language)
        if prompt:
            self.initial_prompt_text.insert(tk.END, prompt)

    def is_transcribing(self) -> bool:
        return self.state in {"running", "paused", "stopping"}

    def set_status(self, value: str) -> None:
        if self.closed:
            return
        self.status_var.set(value)
        self.refresh_buttons()

    def refresh_buttons(self) -> None:
        self.start_button.configure(state=tk.DISABLED if self.state == "stopping" else tk.NORMAL)
        self.pause_button.configure(state=tk.NORMAL)
        self.stop_button.configure(state=tk.NORMAL)
        minutes_disabled = self.summary_running or self.state in {"running", "paused", "stopping"}
        self.minutes_button.configure(state=tk.DISABLED if minutes_disabled else tk.NORMAL)
        self.set_button_pressed(self.start_button, self.state == "running")
        self.set_button_pressed(self.pause_button, self.state == "paused")
        self.set_button_pressed(self.stop_button, self.state in {"idle", "done", "stopping"})
        self.refresh_audio_buttons()

    def refresh_audio_buttons(self) -> None:
        self.set_button_pressed(self.audio_play_button, self.audio_state == "playing")
        self.set_button_pressed(self.audio_pause_button, self.audio_state == "paused")
        self.set_button_pressed(self.audio_stop_button, self.audio_state == "stopped")

    def poll_audio_progress(self) -> None:
        if self.closed:
            return
        duration = self.player.length_seconds() or self.duration_seconds
        position = 0.0
        if self.audio_path is not None:
            position = self.player.position_seconds()
            mode = self.player.mode()
            if self.audio_state == "playing" and mode not in {"playing", "seeking"}:
                self.audio_state = "stopped"
                self.refresh_audio_buttons()

        progress = 0.0 if duration <= 0 else min(1000.0, max(0.0, position / duration * 1000))
        self.audio_progress_var.set(progress)
        self.audio_position_var.set(f"Audio: {format_duration(position)} / {format_duration(duration)}")
        self.root.after(250, self.poll_audio_progress)

    def import_audio(self) -> None:
        if self.is_transcribing():
            messagebox.showinfo(self.window.title(), "Please stop the current offline transcript first.")
            return

        selected = filedialog.askopenfilename(
            title="Import audio",
            filetypes=(
                (self.app.t("audio_files"), "*.mp3 *.wav *.m4a *.aac *.flac *.ogg"),
                (self.app.t("all_files"), "*.*"),
            ),
        )
        if not selected:
            return

        path = Path(selected)
        self.audio_path = path
        self.duration_seconds = 0.0
        self.audio_state = "stopped"
        self.offline_file_stem = recording_file_stem()
        with self.decoded_audio_lock:
            self.decoded_audio = None
            self.decoded_sample_rate = TRANSCRIBE_SAMPLE_RATE
            self.processed_audio_seconds = 0.0
        self.text.delete("1.0", tk.END)
        self.progress_var.set(0.0)
        self.audio_progress_var.set(0.0)
        self.started_at = None
        self.finished_at = None
        self.update_time_labels()
        self.file_var.set(f"File: {path.name}")
        try:
            self.duration_seconds = self.player.open(path)
            self.duration_var.set(f"Duration: {format_duration(self.duration_seconds)}")
            self.audio_position_var.set(f"Audio: 00:00 / {format_duration(self.duration_seconds)}")
            self.processed_var.set(f"Processed: 00:00 / {format_duration(self.duration_seconds)}")
            self.set_status("Ready.")
        except Exception as exc:
            self.duration_var.set("Duration: -")
            self.audio_position_var.set("Audio: 00:00 / 00:00")
            self.processed_var.set("Processed: 00:00 / 00:00")
            self.set_status("Ready for transcript. Playback is unavailable for this file.")
            messagebox.showwarning(self.window.title(), f"Audio playback is unavailable:\n{exc}")
        self.refresh_audio_buttons()

    def play_audio(self) -> None:
        try:
            if self.audio_path is None:
                return
            duration = self.player.length_seconds()
            position = self.player.position_seconds()
            if self.audio_state == "stopped" and duration > 0 and position >= max(0.0, duration - 0.25):
                self.player.stop(reset=True)
            self.player.play()
            self.audio_state = "playing"
            self.refresh_audio_buttons()
        except Exception as exc:
            messagebox.showwarning(self.window.title(), f"Audio playback failed:\n{exc}")

    def pause_audio(self) -> None:
        self.player.pause()
        if self.audio_path is not None:
            self.audio_state = "paused"
            self.refresh_audio_buttons()

    def stop_audio(self) -> None:
        self.player.stop(reset=True)
        self.audio_state = "stopped"
        self.audio_progress_var.set(0.0)
        self.audio_position_var.set(f"Audio: 00:00 / {format_duration(self.player.length_seconds() or self.duration_seconds)}")
        self.refresh_audio_buttons()

    def seek_audio(self, delta_seconds: float) -> None:
        self.player.seek_relative(delta_seconds)

    def start_transcript(self) -> None:
        if self.state == "paused":
            self.state = "running"
            self.active_event.set()
            self.set_status("Running...")
            return
        if self.state in {"running", "stopping"}:
            return

        with self.app.state_lock:
            app_state = self.app.state
        if app_state != "idle":
            messagebox.showinfo(self.window.title(), self.app.t("offline_live_busy"))
            return

        if self.audio_path is None:
            messagebox.showinfo(self.window.title(), "Please import an audio file first.")
            return

        try:
            settings = self.current_config()
        except Exception as exc:
            self.state = "idle"
            messagebox.showerror(self.window.title(), f"Invalid transcript settings:\n{exc}")
            return

        self.stop_event.clear()
        self.active_event.set()
        self.state = "running"
        self.started_at = time.time()
        self.finished_at = None
        self.offline_file_stem = dt.datetime.fromtimestamp(self.started_at).strftime("%Y%m%d_%H%M")
        self.active_settings = settings
        self.progress_var.set(0.0)
        self.text.delete("1.0", tk.END)
        self.update_time_labels()
        self.set_status("Starting...")
        try:
            self.start_transcriber_process(settings)
        except Exception as exc:
            self.state = "idle"
            self.active_settings = None
            self.set_status("Failed to start.")
            messagebox.showerror(self.window.title(), f"Offline transcript failed to start:\n{exc}")
            return

        self.worker_thread = threading.Thread(
            target=self.transcription_worker,
            args=(settings,),
            name="offline-transcript",
            daemon=True,
        )
        self.worker_thread.start()

    def pause_transcript(self) -> None:
        if self.state == "running":
            self.state = "paused"
            self.active_event.clear()
            self.set_status("Paused.")

    def stop_transcript(self) -> None:
        if not self.is_transcribing():
            return
        self.state = "stopping"
        self.stop_event.set()
        self.active_event.set()
        self.terminate_transcriber_process()
        self.set_status("Stopping...")

    def save_as(self) -> None:
        if self.audio_path is None:
            messagebox.showinfo(self.window.title(), "Please import an audio file first.")
            return

        segment = self.current_transcribed_audio_segment()
        if segment is None:
            messagebox.showinfo(self.window.title(), "No transcribed audio segment is available yet.")
            return

        segment_audio, segment_rate, segment_seconds = segment
        initial = f"{self.offline_file_stem}.mp3"
        selected = filedialog.asksaveasfilename(
            title="Save transcribed audio and transcript",
            initialfile=initial,
            defaultextension=".mp3",
            filetypes=((self.app.t("mp3_files"), "*.mp3"), (self.app.t("all_files"), "*.*")),
        )
        if not selected:
            return

        audio_target = Path(selected)
        if not audio_target.suffix:
            audio_target = audio_target.with_suffix(".mp3")
        text_target = audio_target.with_suffix(".txt")
        if audio_target.exists() or text_target.exists():
            overwrite = messagebox.askyesno(
                self.window.title(),
                self.app.t("overwrite_text", filename=f"{audio_target.name} / {text_target.name}"),
            )
            if not overwrite:
                return

        try:
            audio_target.write_bytes(self.encode_mp3_segment(segment_audio, segment_rate))
            text_target.write_text(self.text.get("1.0", tk.END).strip(), encoding="utf-8")
            messagebox.showinfo(
                self.window.title(),
                f"{self.app.t('saved')}\n{audio_target}\n{text_target}\n\nAudio segment: 00:00-{format_duration(segment_seconds)}",
            )
        except Exception as exc:
            messagebox.showerror(self.window.title(), f"{self.app.t('save_failed')}\n{exc}")

    def generate_minutes(self) -> None:
        if self.summary_running:
            return
        if self.is_transcribing():
            messagebox.showinfo(self.window.title(), "Please stop or finish the transcript first.")
            return

        transcript = self.text.get("1.0", tk.END).strip()
        transcript = self.app.transcript_without_existing_minutes(transcript).strip()
        if not transcript:
            messagebox.showinfo(self.window.title(), "No transcript text is available yet.")
            return

        self.summary_running = True
        self.set_status("Generating minutes...")
        self.refresh_buttons()
        self.summary_thread = threading.Thread(
            target=self.generate_minutes_worker,
            args=(transcript,),
            name="offline-minutes",
            daemon=True,
        )
        self.summary_thread.start()

    def generate_minutes_worker(self, transcript: str) -> None:
        lower_current_thread_priority()
        try:
            minutes = self.app.build_meeting_summary(transcript)
        except Exception as exc:
            with contextlib.suppress(Exception):
                self.root.after(0, lambda error=exc: self.finish_generate_minutes("", error))
            return

        with contextlib.suppress(Exception):
            self.root.after(0, lambda result=minutes: self.finish_generate_minutes(result, None))

    def finish_generate_minutes(self, minutes: str, error: Optional[Exception]) -> None:
        if self.closed:
            return
        self.summary_running = False
        self.summary_thread = None
        self.refresh_buttons()
        if error is not None:
            self.set_status("Minutes failed.")
            messagebox.showerror(self.window.title(), f"Meeting minutes failed:\n{error}")
            return
        if not minutes.strip():
            self.set_status("No minutes generated.")
            messagebox.showinfo(self.window.title(), "No meeting minutes were generated.")
            return

        transcript = self.app.transcript_without_existing_minutes(self.text.get("1.0", tk.END)).rstrip()
        document = f"{transcript}\n\n{minutes.strip()}" if transcript else minutes.strip()
        self.text.delete("1.0", tk.END)
        self.text.insert(tk.END, document)
        self.text.see(tk.END)
        self.set_status("Minutes generated.")

    def current_transcribed_audio_segment(self) -> Optional[tuple[np.ndarray, int, float]]:
        with self.decoded_audio_lock:
            audio = self.decoded_audio
            sample_rate = self.decoded_sample_rate
            seconds = min(self.processed_audio_seconds, self.duration_seconds)
            if audio is None or audio.size == 0 or seconds <= 0:
                return None
            frame_count = min(audio.size, max(1, int(seconds * sample_rate)))
            return audio[:frame_count].copy(), sample_rate, frame_count / sample_rate

    def encode_mp3_segment(self, audio: np.ndarray, sample_rate: int) -> bytes:
        import lameenc

        encoder = lameenc.Encoder()
        encoder.set_bit_rate(128)
        encoder.set_in_sample_rate(sample_rate)
        encoder.set_channels(1)
        encoder.set_quality(2)

        payload = bytearray()
        encoded = encoder.encode(float_audio_to_int16(audio).tobytes())
        if encoded:
            payload.extend(encoded)
        final_chunk = encoder.flush()
        if final_chunk:
            payload.extend(final_chunk)
        return bytes(payload)

    def current_config(self) -> dict[str, Any]:
        language = self.language_var.get()
        if language not in TRANSCRIPTION_MODEL_CONFIGS:
            language = DEFAULT_TRANSCRIPTION_LANGUAGE
        prompt = self.initial_prompt_text.get("1.0", tk.END).strip() or None
        return {
            "language": language,
            "model_path": whisper_model_path(language),
            "device": self.device_var.get().strip() or WHISPER_DEVICE,
            "compute_type": self.compute_type_var.get().strip() or WHISPER_COMPUTE_TYPE,
            "cpu_threads": max(1, int(self.cpu_threads_var.get())),
            "num_workers": max(1, int(self.num_workers_var.get())),
            "whisper_language": whisper_language_code(language),
            "condition_on_previous_text": bool(self.condition_var.get()),
            "initial_prompt": prompt,
            "simplify_chinese": bool(self.simplify_var.get()),
            "vad_filter": bool(self.vad_var.get()),
            "vad_parameters": {"min_silence_duration_ms": max(0, int(self.vad_silence_ms_var.get()))},
            "beam_size": max(1, int(self.beam_size_var.get())),
            "slice_seconds": max(1.0, float(self.slice_seconds_var.get())),
            "overlap_seconds": max(0.0, float(self.overlap_seconds_var.get())),
            "min_rms": max(0.0, float(self.min_rms_var.get())),
            "play_chunks": bool(self.play_chunks_var.get()),
            "sample_rate": max(8000, int(self.sample_rate_var.get())),
        }

    def start_transcriber_process(self, settings: dict[str, Any]) -> None:
        context = mp.get_context("spawn")
        self.transcriber_input_queue = context.Queue(maxsize=1)
        self.transcriber_output_queue = context.Queue(maxsize=4)
        config = {
            "model_path": settings["model_path"],
            "device": settings["device"],
            "compute_type": settings["compute_type"],
            "cpu_threads": settings["cpu_threads"],
            "num_workers": settings["num_workers"],
            "language": settings["whisper_language"],
            "condition_on_previous_text": settings["condition_on_previous_text"],
            "initial_prompt": settings["initial_prompt"],
            "simplify_chinese": settings["simplify_chinese"],
            "vad_filter": settings["vad_filter"],
            "vad_parameters": settings["vad_parameters"],
        }
        self.transcriber_process = context.Process(
            target=run_transcriber_process,
            args=(self.transcriber_input_queue, self.transcriber_output_queue, config),
            name="offline-whisper-transcriber",
            daemon=True,
        )
        self.transcriber_process.start()

    def terminate_transcriber_process(self) -> None:
        process = self.transcriber_process
        if process is not None and process.is_alive():
            with contextlib.suppress(Exception):
                process.terminate()

    def close_transcriber_process(self) -> None:
        input_queue = self.transcriber_input_queue
        if input_queue is not None:
            with contextlib.suppress(Exception):
                input_queue.put_nowait(None)

        process = self.transcriber_process
        if process is not None:
            process.join(timeout=1)
            if process.is_alive():
                with contextlib.suppress(Exception):
                    process.terminate()
                process.join(timeout=1)
            with contextlib.suppress(Exception):
                process.close()
        self.transcriber_process = None

        for process_queue in (self.transcriber_input_queue, self.transcriber_output_queue):
            if process_queue is None:
                continue
            with contextlib.suppress(Exception):
                process_queue.close()
            with contextlib.suppress(Exception):
                process_queue.join_thread()
        self.transcriber_input_queue = None
        self.transcriber_output_queue = None

    def transcription_worker(self, settings: dict[str, Any]) -> None:
        lower_current_thread_priority()
        try:
            try:
                from faster_whisper.audio import decode_audio
            except ImportError:
                self.root.after(0, self.app.show_transcription_dependency_warning)
                return

            if self.audio_path is None:
                return
            sample_rate = int(settings["sample_rate"])
            audio = decode_audio(str(self.audio_path), sampling_rate=sample_rate)
            audio = normalize_audio(audio)
            duration = audio.size / sample_rate if audio.size else 0.0
            if duration <= 0:
                self.root.after(0, lambda: self.set_status("No audio was found."))
                return

            self.duration_seconds = duration
            with self.decoded_audio_lock:
                self.decoded_audio = audio
                self.decoded_sample_rate = sample_rate
                self.processed_audio_seconds = 0.0
            self.root.after(
                0,
                lambda: (
                    self.duration_var.set(f"Duration: {format_duration(duration)}"),
                    self.processed_var.set(f"Processed: 00:00 / {format_duration(duration)}"),
                ),
            )
            self.root.after(0, lambda: self.set_status("Running..."))

            slice_seconds = float(settings["slice_seconds"])
            overlap_seconds = min(float(settings["overlap_seconds"]), max(0.0, slice_seconds - 0.25))
            step_seconds = max(0.25, slice_seconds - overlap_seconds)
            min_rms = float(settings["min_rms"])
            start_seconds = 0.0
            job_id = 0

            while start_seconds < duration and not self.stop_event.is_set():
                while not self.active_event.wait(0.1):
                    if self.stop_event.is_set():
                        break
                if self.stop_event.is_set():
                    break

                end_seconds = min(duration, start_seconds + slice_seconds)
                start_frame = int(start_seconds * sample_rate)
                end_frame = int(end_seconds * sample_rate)
                chunk = audio[start_frame:end_frame]
                if chunk.size and audio_rms(chunk) >= min_rms:
                    job_id += 1
                    if not self.submit_chunk(job_id, chunk, start_seconds, end_seconds, int(settings["beam_size"])):
                        break
                    playback_thread = self.start_chunk_playback(chunk, sample_rate, bool(settings["play_chunks"]))
                    result = self.wait_for_chunk_result(job_id)
                    if result is not None:
                        text = str(result.get("text", "")).strip()
                        if text:
                            self.append_transcript(start_seconds, end_seconds, text)
                    elif self.transcriber_process is not None and getattr(self.transcriber_process, "exitcode", None) is not None:
                        break
                    if playback_thread is not None:
                        playback_thread.join(timeout=max(0.1, end_seconds - start_seconds + 1.0))

                self.update_progress(end_seconds, duration)
                if end_seconds >= duration:
                    break
                start_seconds += step_seconds

            self.finished_at = time.time()
            self.root.after(0, self.update_time_labels)
            if self.stop_event.is_set():
                self.root.after(0, lambda: self.set_status("Stopped."))
            else:
                self.root.after(0, lambda: self.set_status("Completed."))
        except Exception as exc:
            self.root.after(0, lambda error=exc: messagebox.showerror(self.window.title(), f"Offline transcript failed:\n{error}"))
            self.root.after(0, lambda: self.set_status("Failed."))
        finally:
            self.close_transcriber_process()
            self.active_settings = None
            self.root.after(0, self.finish_transcription)

    def submit_chunk(self, job_id: int, audio: np.ndarray, start_seconds: float, end_seconds: float, beam_size: int) -> bool:
        input_queue = self.transcriber_input_queue
        if input_queue is None:
            return False
        job = {
            "job_id": job_id,
            "source_name": "source_speaker",
            "start": start_seconds,
            "end": end_seconds,
            "audio": audio,
            "beam_size": beam_size,
        }
        while not self.stop_event.is_set():
            process = self.transcriber_process
            if process is not None and getattr(process, "exitcode", None) is not None:
                return False
            try:
                input_queue.put(job, timeout=0.1)
                return True
            except queue.Full:
                continue
        return False

    def start_chunk_playback(
        self,
        audio: np.ndarray,
        sample_rate: int,
        enabled: bool,
    ) -> Optional[threading.Thread]:
        if not enabled:
            return None

        def play_chunk() -> None:
            with contextlib.suppress(Exception):
                self.play_chunk_audio(audio, sample_rate)

        thread = threading.Thread(
            target=play_chunk,
            name="offline-chunk-playback",
            daemon=True,
        )
        thread.start()
        return thread

    def play_chunk_audio(self, audio: np.ndarray, sample_rate: int) -> None:
        chunk = normalize_audio(audio)
        if chunk.size == 0:
            return

        try:
            import soundcard as sc

            block_size = max(1, int(sample_rate * 0.1))
            speaker = sc.default_speaker()
            with speaker.player(samplerate=sample_rate, channels=1, blocksize=block_size) as player:
                for offset in range(0, chunk.size, block_size):
                    if self.stop_event.is_set():
                        break
                    while not self.active_event.wait(0.1):
                        if self.stop_event.is_set():
                            return
                    block = chunk[offset : offset + block_size].reshape(-1, 1)
                    player.play(block)
            return
        except Exception:
            pass

        if sys.platform != "win32":
            return
        import wave
        import winsound

        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(float_audio_to_int16(chunk).tobytes())
        winsound.PlaySound(buffer.getvalue(), winsound.SND_MEMORY)

    def wait_for_chunk_result(self, job_id: int) -> Optional[dict[str, Any]]:
        output_queue = self.transcriber_output_queue
        if output_queue is None:
            return None

        while not self.stop_event.is_set():
            try:
                result = output_queue.get(timeout=0.1)
            except queue.Empty:
                process = self.transcriber_process
                if process is not None and getattr(process, "exitcode", None) is not None:
                    return None
                continue

            kind = result.get("kind")
            if kind == "done":
                return None
            if kind == "missing":
                self.root.after(0, self.app.show_transcription_dependency_warning)
                return None
            if kind == "error":
                message = str(result.get("message", ""))
                self.root.after(0, lambda error=message: self.set_status(f"Whisper error: {error}"))
                continue
            if kind == "realtime" and int(result.get("job_id") or 0) == job_id:
                return result
        return None

    def append_transcript(self, start_seconds: float, end_seconds: float, text: str) -> None:
        def update() -> None:
            if self.closed:
                return
            prefix = f"[{format_duration(start_seconds)}-{format_duration(end_seconds)}] "
            if self.text.index("end-1c") != "1.0":
                self.text.insert(tk.END, "\n\n")
            self.text.insert(tk.END, prefix + text)
            self.text.see(tk.END)

        self.root.after(0, update)

    def update_progress(self, processed_seconds: float, duration: float) -> None:
        with self.decoded_audio_lock:
            self.processed_audio_seconds = max(self.processed_audio_seconds, min(processed_seconds, duration))

        def update() -> None:
            if self.closed:
                return
            progress = 0.0 if duration <= 0 else min(1000.0, max(0.0, processed_seconds / duration * 1000))
            self.progress_var.set(progress)
            self.processed_var.set(
                f"Processed: {format_duration(processed_seconds)} / {format_duration(duration)}"
            )

        self.root.after(0, update)

    def update_time_labels(self) -> None:
        self.started_var.set(f"Started: {format_wall_time(self.started_at)}")
        self.finished_var.set(f"Finished: {format_wall_time(self.finished_at)}")
        if self.started_at is not None:
            end = self.finished_at or time.time()
            self.elapsed_var.set(f"Elapsed: {format_duration(end - self.started_at)}")
        else:
            self.elapsed_var.set("Elapsed: -")

    def finish_transcription(self) -> None:
        if self.closed:
            return
        if self.state == "stopping":
            self.state = "idle"
        elif self.state in {"running", "paused"}:
            self.state = "done"
        self.active_event.clear()
        self.refresh_buttons()

    def close(self) -> None:
        self.closed = True
        self.summary_running = False
        self.stop_event.set()
        self.active_event.set()
        self.player.close()
        self.terminate_transcriber_process()
        self.app.offline_window = None
        self.window.destroy()


class SystemSettingsWindow:
    def __init__(self, root: tk.Tk, app: "MeetingRecorderApp") -> None:
        self.root = root
        self.app = app
        self.window = tk.Toplevel(root)
        self.window.title(app.t("system_settings"))
        self.window.geometry("640x720")
        self.window.minsize(560, 620)
        self.window.protocol("WM_DELETE_WINDOW", self.close)

        self.common_vars: dict[str, tk.Variable] = {}
        self.summary_vars: dict[str, tk.Variable] = {}
        self.language_vars: dict[str, dict[str, tk.Variable]] = {}
        self.prompt_widgets: dict[str, tk.Text] = {}

        self._build_ui()
        self.load_values(app.realtime_settings)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.window, padding=12)
        outer.pack(fill=tk.BOTH, expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(0, weight=1)

        notebook = ttk.Notebook(outer)
        notebook.grid(row=0, column=0, sticky="nsew")

        common = ttk.Frame(notebook, padding=12)
        common.columnconfigure(1, weight=1)
        notebook.add(common, text="Common")
        self._add_common_controls(common)

        summary = ttk.Frame(notebook, padding=12)
        summary.columnconfigure(1, weight=1)
        notebook.add(summary, text="Summary")
        self._add_summary_controls(summary)

        for language, label in (("en", "English"), ("zh", "Chinese")):
            frame = ttk.Frame(notebook, padding=12)
            frame.columnconfigure(1, weight=1)
            notebook.add(frame, text=label)
            self._add_language_controls(frame, language)

        button_row = ttk.Frame(outer)
        button_row.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        ttk.Button(button_row, text="Defaults", command=self.reset_defaults).pack(side=tk.LEFT)
        ttk.Button(button_row, text="Close", command=self.close).pack(side=tk.RIGHT)
        ttk.Button(button_row, text="Save", command=self.save).pack(side=tk.RIGHT, padx=(0, 8))

    def _add_common_controls(self, parent: ttk.Frame) -> None:
        self.common_vars = {
            "sample_rate": tk.IntVar(),
            "whisper_device": tk.StringVar(),
            "compute_type": tk.StringVar(),
            "cpu_threads": tk.IntVar(),
            "num_workers": tk.IntVar(),
            "vad_filter": tk.BooleanVar(),
            "vad_min_silence_duration_ms": tk.IntVar(),
            "min_transcribe_rms": tk.DoubleVar(),
            "transcriber_input_queue_maxsize": tk.IntVar(),
            "chunk_seconds": tk.DoubleVar(),
            "capture_read_seconds": tk.DoubleVar(),
            "capture_block_seconds": tk.DoubleVar(),
            "auto_screenshot_interval_seconds": tk.DoubleVar(),
            "auto_stop_meeting_end": tk.BooleanVar(),
            "meeting_end_idle_seconds": tk.DoubleVar(),
            "meeting_end_rms_threshold": tk.DoubleVar(),
        }
        row = 0
        for key, label in (
            ("sample_rate", "Sample rate"),
            ("whisper_device", "Whisper device"),
            ("compute_type", "Compute type"),
            ("cpu_threads", "CPU threads"),
            ("num_workers", "Whisper workers"),
        ):
            self._entry(parent, label, self.common_vars[key], row)
            row += 1

        ttk.Checkbutton(parent, text="VAD", variable=self.common_vars["vad_filter"]).grid(
            row=row,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(10, 0),
        )
        row += 1
        ttk.Checkbutton(parent, text="Auto stop when meeting ends", variable=self.common_vars["auto_stop_meeting_end"]).grid(
            row=row,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(8, 0),
        )
        row += 1
        for key, label in (
            ("vad_min_silence_duration_ms", "VAD min silence ms"),
            ("min_transcribe_rms", "Min transcript RMS"),
            ("transcriber_input_queue_maxsize", "Realtime input queue max"),
            ("chunk_seconds", "Audio chunk seconds"),
            ("capture_read_seconds", "Capture read seconds"),
            ("capture_block_seconds", "Capture block seconds"),
            ("auto_screenshot_interval_seconds", "Auto screenshot interval sec"),
            ("meeting_end_idle_seconds", "Meeting end idle sec"),
            ("meeting_end_rms_threshold", "Meeting end RMS threshold"),
        ):
            self._entry(parent, label, self.common_vars[key], row)
            row += 1

    def _add_summary_controls(self, parent: ttk.Frame) -> None:
        self.summary_vars = {
            "llm_summary_enabled": tk.BooleanVar(),
            "llm_summary_base_url": tk.StringVar(),
            "llm_summary_model": tk.StringVar(),
            "llm_summary_timeout_seconds": tk.DoubleVar(),
            "llm_summary_max_input_chars": tk.IntVar(),
            "llm_summary_temperature": tk.DoubleVar(),
        }
        row = 0
        ttk.Checkbutton(
            parent,
            text="Use local LLM for final meeting summary",
            variable=self.summary_vars["llm_summary_enabled"],
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(0, 8))
        row += 1
        for key, label in (
            ("llm_summary_base_url", "OpenAI-compatible URL"),
            ("llm_summary_model", "Model"),
            ("llm_summary_timeout_seconds", "Timeout seconds"),
            ("llm_summary_max_input_chars", "Max transcript chars"),
            ("llm_summary_temperature", "Temperature"),
        ):
            self._entry(parent, label, self.summary_vars[key], row)
            row += 1

        ttk.Label(
            parent,
            text=(
                "Start llama.cpp or Ollama first. Live rolling minutes still use the lightweight "
                "local rules so recording stays stable."
            ),
            wraplength=460,
            justify=tk.LEFT,
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(16, 0))

    def _add_language_controls(self, parent: ttk.Frame, language: str) -> None:
        vars_for_language: dict[str, tk.Variable] = {
            "model_name": tk.StringVar(),
            "model_dir": tk.StringVar(),
            "language": tk.StringVar(),
            "transcribe_seconds": tk.DoubleVar(),
            "overlap_seconds": tk.DoubleVar(),
            "beam_size": tk.IntVar(),
            "condition_on_previous_text": tk.BooleanVar(),
            "simplify_chinese": tk.BooleanVar(),
        }
        self.language_vars[language] = vars_for_language
        row = 0
        for key, label in (
            ("model_name", "Model"),
            ("model_dir", "Local model dir"),
            ("language", "Whisper language"),
            ("transcribe_seconds", "Slice seconds"),
            ("overlap_seconds", "Overlap sec"),
            ("beam_size", "Beam size"),
        ):
            self._entry(parent, label, vars_for_language[key], row)
            row += 1

        ttk.Checkbutton(
            parent,
            text="Condition on previous text",
            variable=vars_for_language["condition_on_previous_text"],
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(10, 0))
        row += 1
        ttk.Checkbutton(
            parent,
            text="Simplified Chinese",
            variable=vars_for_language["simplify_chinese"],
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(4, 0))
        row += 1

        ttk.Label(parent, text="Base initial prompt").grid(row=row, column=0, columnspan=2, sticky="w", pady=(14, 4))
        row += 1
        prompt = tk.Text(parent, height=12, wrap=tk.WORD, font=("Microsoft YaHei UI", 10))
        prompt.grid(row=row, column=0, columnspan=2, sticky="nsew")
        parent.rowconfigure(row, weight=1)
        self.prompt_widgets[language] = prompt

    def _entry(self, parent: ttk.Frame, label: str, variable: tk.Variable, row: int) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=(8, 0), padx=(0, 10))
        ttk.Entry(parent, textvariable=variable, width=28).grid(row=row, column=1, sticky="w", pady=(8, 0))

    def load_values(self, settings: dict[str, Any]) -> None:
        for key, variable in self.common_vars.items():
            if key in settings:
                variable.set(settings[key])
        for key, variable in self.summary_vars.items():
            if key in settings:
                variable.set(settings[key])

        languages = settings.get("languages", {})
        if not isinstance(languages, dict):
            languages = {}
        for language, vars_for_language in self.language_vars.items():
            config = languages.get(language, {})
            if not isinstance(config, dict):
                config = {}
            for key, variable in vars_for_language.items():
                if key in config:
                    variable.set(config[key])
            prompt = self.prompt_widgets[language]
            prompt.delete("1.0", tk.END)
            prompt.insert(tk.END, str(config.get("initial_prompt", "") or ""))

    def collect_values(self) -> dict[str, Any]:
        settings = default_realtime_settings()
        for key, variable in self.common_vars.items():
            settings[key] = variable.get()
        for key, variable in self.summary_vars.items():
            settings[key] = variable.get()
        settings["languages"] = {}
        for language, vars_for_language in self.language_vars.items():
            config: dict[str, Any] = {}
            for key, variable in vars_for_language.items():
                config[key] = variable.get()
            config["initial_prompt"] = self.prompt_widgets[language].get("1.0", tk.END).strip()
            settings["languages"][language] = config
        return settings

    def reset_defaults(self) -> None:
        self.load_values(default_realtime_settings())

    def save(self) -> None:
        try:
            self.app.set_realtime_settings(self.collect_values())
            messagebox.showinfo(self.window.title(), self.app.t("saved"))
        except Exception as exc:
            messagebox.showerror(self.window.title(), f"{self.app.t('save_failed')}\n{exc}")

    def close(self) -> None:
        self.app.system_settings_window = None
        self.window.destroy()


class MeetingRecorderApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.withdraw()

        self.state = "idle"
        self.state_lock = threading.Lock()
        self.session: Optional[RecordingSession] = None
        self.transcript_window: Optional[TranscriptWindow] = None
        self.offline_window: Optional[OfflineTranscriptWindow] = None
        self.system_settings_window: Optional[SystemSettingsWindow] = None
        self.recording_options_window: Optional[tk.Toplevel] = None
        self.recording_options_language_var: Optional[tk.StringVar] = None
        self.recording_options_auto_screenshot_var: Optional[tk.BooleanVar] = None
        self.recording_options_prompt_text: Optional[tk.Text] = None
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
        self.realtime_settings = default_realtime_settings()
        self.recording_prompts: dict[str, str] = {"en": "", "zh": ""}
        self.emergency_stop_lock = threading.Lock()
        self.emergency_stop_started = False
        self._windows_wndproc = None
        self._windows_old_wndproc = None
        self.load_user_settings()

        self.icon = pystray.Icon(self.t("app_name"), make_tray_icon(), self.t("app_name"), self.build_menu())
        self.install_windows_power_shutdown_hooks()

    def install_windows_power_shutdown_hooks(self) -> None:
        if sys.platform != "win32":
            return

        try:
            import ctypes
            from ctypes import wintypes

            self.root.update_idletasks()
            hwnd = wintypes.HWND(self.root.winfo_id())
            if not hwnd:
                return

            user32 = ctypes.WinDLL("user32", use_last_error=True)
            lresult = ctypes.c_ssize_t
            wndproc_type = ctypes.WINFUNCTYPE(
                lresult,
                wintypes.HWND,
                wintypes.UINT,
                wintypes.WPARAM,
                wintypes.LPARAM,
            )

            gwlp_wndproc = -4
            wm_query_end_session = 0x0011
            wm_end_session = 0x0016
            wm_power_broadcast = 0x0218
            pbt_apm_query_suspend = 0x0000
            pbt_apm_suspend = 0x0004

            user32.CallWindowProcW.argtypes = (
                ctypes.c_void_p,
                wintypes.HWND,
                wintypes.UINT,
                wintypes.WPARAM,
                wintypes.LPARAM,
            )
            user32.CallWindowProcW.restype = lresult
            user32.DefWindowProcW.argtypes = (
                wintypes.HWND,
                wintypes.UINT,
                wintypes.WPARAM,
                wintypes.LPARAM,
            )
            user32.DefWindowProcW.restype = lresult

            def call_default(
                window_handle: wintypes.HWND,
                message: int,
                wparam: wintypes.WPARAM,
                lparam: wintypes.LPARAM,
            ) -> int:
                old_proc = self._windows_old_wndproc
                if old_proc:
                    return int(user32.CallWindowProcW(old_proc, window_handle, message, wparam, lparam))
                return int(user32.DefWindowProcW(window_handle, message, wparam, lparam))

            def window_proc(
                window_handle: wintypes.HWND,
                message: int,
                wparam: wintypes.WPARAM,
                lparam: wintypes.LPARAM,
            ) -> int:
                try:
                    if message == wm_query_end_session:
                        self.emergency_stop_and_save("windows_shutdown")
                        return 1
                    if message == wm_end_session and int(wparam):
                        self.emergency_stop_and_save("windows_shutdown")
                    elif message == wm_power_broadcast and int(wparam) in {
                        pbt_apm_query_suspend,
                        pbt_apm_suspend,
                    }:
                        self.emergency_stop_and_save("windows_sleep")
                        return 1
                except Exception:
                    pass
                return call_default(window_handle, message, wparam, lparam)

            self._windows_wndproc = wndproc_type(window_proc)
            callback_pointer = ctypes.cast(self._windows_wndproc, ctypes.c_void_p)

            if ctypes.sizeof(ctypes.c_void_p) == ctypes.sizeof(ctypes.c_longlong):
                set_window_long = user32.SetWindowLongPtrW
                set_window_long.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_void_p)
                set_window_long.restype = ctypes.c_void_p
                self._windows_old_wndproc = set_window_long(hwnd, gwlp_wndproc, callback_pointer)
            else:
                set_window_long = user32.SetWindowLongW
                set_window_long.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_long)
                set_window_long.restype = ctypes.c_long
                self._windows_old_wndproc = set_window_long(
                    hwnd,
                    gwlp_wndproc,
                    ctypes.c_long(callback_pointer.value or 0),
                )

            if not self._windows_old_wndproc and ctypes.get_last_error():
                self._windows_wndproc = None
                self._windows_old_wndproc = None
        except Exception:
            self._windows_wndproc = None
            self._windows_old_wndproc = None

    def load_user_settings(self) -> None:
        settings = default_realtime_settings()
        prompts = {"en": "", "zh": ""}
        path = user_settings_path()
        try:
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                merge_dict(settings, data.get("realtime_settings"))
                if isinstance(data.get("recording_prompts"), dict):
                    prompts.update({key: str(value) for key, value in data["recording_prompts"].items()})
        except Exception:
            settings = default_realtime_settings()
            prompts = {"en": "", "zh": ""}
        self.realtime_settings = settings
        self.recording_prompts = prompts

    def save_user_settings(self) -> None:
        payload = {
            "realtime_settings": self.realtime_settings,
            "recording_prompts": self.recording_prompts,
        }
        path = user_settings_path()
        with contextlib.suppress(Exception):
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def set_realtime_settings(self, settings: dict[str, Any]) -> None:
        self.realtime_settings = merge_dict(default_realtime_settings(), settings)
        self.save_user_settings()

    def realtime_language_settings(self, language: str) -> dict[str, Any]:
        languages = self.realtime_settings.get("languages", {})
        if not isinstance(languages, dict):
            languages = {}
        config = languages.get(language)
        if not isinstance(config, dict):
            config = default_realtime_settings()["languages"][DEFAULT_TRANSCRIPTION_LANGUAGE]
        return config

    def realtime_model_path(self, language: str) -> str:
        config = self.realtime_language_settings(language)
        model_dir = Path(str(config.get("model_dir", "")))
        if usable_model_dir(model_dir):
            return str(model_dir)
        return str(config.get("model_name", whisper_model_path(language)))

    def realtime_transcribe_seconds(self, language: str) -> float:
        return max(0.5, float(self.realtime_language_settings(language).get("transcribe_seconds", realtime_transcribe_seconds(language))))

    def realtime_beam_size(self, language: str) -> int:
        return max(1, int(self.realtime_language_settings(language).get("beam_size", realtime_beam_size(language))))

    def realtime_whisper_language(self, language: str) -> Optional[str]:
        value = self.realtime_language_settings(language).get("language")
        return None if value in (None, "") else str(value)

    def realtime_condition_on_previous_text(self, language: str) -> bool:
        return bool(self.realtime_language_settings(language).get("condition_on_previous_text", True))

    def realtime_simplify_chinese(self, language: str) -> bool:
        return bool(self.realtime_language_settings(language).get("simplify_chinese", simplify_chinese_text(language)))

    def realtime_initial_prompt(self, language: str, recognition_prompt: str = "") -> Optional[str]:
        config = self.realtime_language_settings(language)
        base_prompt = str(config.get("initial_prompt", "") or "").strip()
        user_prompt = recognition_prompt.strip()
        parts = [part for part in (base_prompt, user_prompt) if part]
        return "\n".join(parts) if parts else None

    def realtime_vad_parameters(self) -> dict[str, int]:
        return {
            "min_silence_duration_ms": max(
                0,
                int(self.realtime_settings.get("vad_min_silence_duration_ms", 500)),
            )
        }

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
            MenuItem(lambda item: self.t("offline_transcript"), self.tray_offline_transcript_clicked),
            MenuItem(lambda item: self.t("system_settings"), self.tray_system_settings_clicked),
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

    def tray_offline_transcript_clicked(self, icon: pystray.Icon, item: MenuItem) -> None:
        self.root.after(0, self.open_offline_transcript_window)

    def tray_system_settings_clicked(self, icon: pystray.Icon, item: MenuItem) -> None:
        self.root.after(0, self.open_system_settings_window)

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
        if self.offline_window is not None:
            self.offline_window.window.title(self.t("offline_transcript"))
        if self.system_settings_window is not None:
            self.system_settings_window.window.title(self.t("system_settings"))
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
                interval_seconds = max(
                    1.0,
                    float(
                        self.realtime_settings.get(
                            "auto_screenshot_interval_seconds",
                            AUTO_SCREENSHOT_INTERVAL_SECONDS,
                        )
                    ),
                )
                if self.screenshot_stop_event.wait(interval_seconds):
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

    def emergency_stop_and_save(self, reason: str) -> None:
        with self.emergency_stop_lock:
            if self.emergency_stop_started:
                return
            self.emergency_stop_started = True

        try:
            self.request_stop_recording(auto_save=True, emergency=True, reason=reason)
        finally:
            with self.emergency_stop_lock:
                self.emergency_stop_started = False

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
            if self.offline_window is not None:
                self.offline_window.close()
        self.offline_window = None
        with contextlib.suppress(Exception):
            if self.system_settings_window is not None:
                self.system_settings_window.close()
        self.system_settings_window = None
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
        window.geometry("480x420")
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
            command=self.refresh_recording_prompt_text,
        ).pack(anchor=tk.W, pady=(6, 0))
        ttk.Radiobutton(
            frame,
            text=self.t("transcription_language_chinese"),
            variable=self.recording_options_language_var,
            value="zh",
            command=self.refresh_recording_prompt_text,
        ).pack(anchor=tk.W, pady=(4, 0))
        ttk.Checkbutton(
            frame,
            text=self.t("auto_screenshot_option"),
            variable=self.recording_options_auto_screenshot_var,
        ).pack(anchor=tk.W, pady=(14, 0))

        ttk.Label(frame, text=self.t("recognition_prompt")).pack(anchor=tk.W, pady=(14, 4))
        self.recording_options_prompt_text = tk.Text(frame, height=8, wrap=tk.WORD, font=("Microsoft YaHei UI", 10))
        self.recording_options_prompt_text.pack(fill=tk.BOTH, expand=True)
        self.refresh_recording_prompt_text()

        button_frame = ttk.Frame(frame)
        button_frame.pack(anchor=tk.E, fill=tk.X, pady=(18, 0))
        ttk.Button(button_frame, text=self.t("cancel"), command=self.close_recording_options_window).pack(
            side=tk.RIGHT
        )
        ttk.Button(button_frame, text=self.t("start"), command=self.accept_recording_options).pack(
            side=tk.RIGHT, padx=(0, 8)
        )

        window.bind("<Control-Return>", lambda _event: self.accept_recording_options())
        window.bind("<Escape>", lambda _event: self.close_recording_options_window())
        window.update_idletasks()
        x = max(0, (window.winfo_screenwidth() - window.winfo_width()) // 2)
        y = max(0, (window.winfo_screenheight() - window.winfo_height()) // 2)
        window.geometry(f"480x420+{x}+{y}")
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
        self.recording_options_prompt_text = None

    def refresh_recording_prompt_text(self) -> None:
        prompt_text = self.recording_options_prompt_text
        language_var = self.recording_options_language_var
        if prompt_text is None or language_var is None:
            return
        language = language_var.get()
        prompt_text.delete("1.0", tk.END)
        prompt_text.insert(tk.END, self.recording_prompts.get(language, ""))

    def accept_recording_options(self) -> None:
        language_var = self.recording_options_language_var
        auto_screenshot_var = self.recording_options_auto_screenshot_var
        prompt_text = self.recording_options_prompt_text
        language = language_var.get() if language_var is not None else DEFAULT_TRANSCRIPTION_LANGUAGE
        if language not in self.realtime_settings.get("languages", {}):
            language = DEFAULT_TRANSCRIPTION_LANGUAGE
        auto_screenshot = bool(auto_screenshot_var.get()) if auto_screenshot_var is not None else False
        recognition_prompt = prompt_text.get("1.0", tk.END).strip() if prompt_text is not None else ""
        self.recording_prompts[language] = recognition_prompt
        self.save_user_settings()
        self.close_recording_options_window()
        self.begin_recording(language, auto_screenshot, recognition_prompt)

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
        if self.offline_window is not None and self.offline_window.is_transcribing():
            messagebox.showinfo(self.title(), self.t("offline_busy"))
            return

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
            self.begin_recording(DEFAULT_TRANSCRIPTION_LANGUAGE, False, self.recording_prompts.get(DEFAULT_TRANSCRIPTION_LANGUAGE, ""))
            return

        self.show_recording_options_window()

    def begin_recording(
        self,
        transcription_language: str,
        auto_screenshot_requested: bool,
        recognition_prompt: str = "",
    ) -> None:
        with self.state_lock:
            state = self.state
        if state == "recording":
            messagebox.showinfo(self.title(), self.t("already_recording"))
            return
        if state == "stopping":
            messagebox.showinfo(self.title(), self.t("stopping_wait"))
            return
        if transcription_language not in self.realtime_settings.get("languages", {}):
            transcription_language = DEFAULT_TRANSCRIPTION_LANGUAGE

        file_stem = recording_file_stem()
        self.begin_screenshot_session(file_stem)
        self.set_auto_screenshot_enabled(auto_screenshot_requested)

        settings_snapshot = copy.deepcopy(self.realtime_settings)
        session = RecordingSession(
            file_stem=file_stem,
            transcription_language=transcription_language,
            realtime_settings=settings_snapshot,
            recognition_prompt=recognition_prompt,
        )
        chunk_seconds = max(0.05, float(settings_snapshot.get("chunk_seconds", CHUNK_SECONDS)))
        session.transcription_source_queue = queue.Queue(
            maxsize=max(1, int(TRANSCRIPTION_SOURCE_QUEUE_SECONDS / chunk_seconds))
        )
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
        self.request_stop_recording(auto_save=False, emergency=False, reason="")

    def request_stop_recording(
        self,
        auto_save: bool,
        emergency: bool,
        reason: str,
    ) -> None:
        session = self.session
        with self.state_lock:
            state = self.state

        if session is None or state == "idle":
            if not auto_save and not emergency:
                messagebox.showinfo(self.title(), self.t("not_recording"))
            return

        if state == "stopping":
            return

        session.stop_auto_save = auto_save
        session.stop_reason = reason
        if emergency:
            self.auto_screenshot_enabled = False
            self.screenshot_stop_event.set()
        else:
            self.set_auto_screenshot_enabled(False)
        session.stop_event.set()
        session.active_event.set()
        self.stop_transcription_source_queue(session)
        self.cancel_pending_transcription(session)
        session.review_queue.put(None)
        self.cancel_pending_minutes(session)
        with self.state_lock:
            self.state = "stopping"
        self.update_tray_menu()

        if emergency:
            self.finish_stop_worker(session, emergency=True, direct_complete=True)
            return

        threading.Thread(
            target=self.finish_stop_worker,
            args=(session, emergency),
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

            settings = session.realtime_settings
            chunk_seconds = max(0.05, float(settings.get("chunk_seconds", CHUNK_SECONDS)))
            capture_read_seconds = max(0.01, float(settings.get("capture_read_seconds", CAPTURE_READ_SECONDS)))
            capture_block_seconds = max(
                capture_read_seconds,
                float(settings.get("capture_block_seconds", CAPTURE_BLOCK_SECONDS)),
            )
            chunk_frames = max(1, int(RECORD_SAMPLE_RATE * chunk_seconds))

            source_queues: dict[str, "queue.Queue[Optional[np.ndarray]]"] = {
                source_name: queue.Queue() for source_name, _source in capture_sources
            }
            source_done = {name: False for name in source_queues}
            source_errors: "queue.Queue[tuple[str, Exception]]" = queue.Queue()

            for source_name, source in capture_sources:
                thread = threading.Thread(
                    target=self.audio_capture_worker,
                    args=(
                        session,
                        source_name,
                        source,
                        source_queues[source_name],
                        source_errors,
                        chunk_seconds,
                        capture_read_seconds,
                        capture_block_seconds,
                    ),
                    name=f"capture-{source_name}",
                    daemon=True,
                )
                source_threads.append(thread)
                thread.start()

            while not session.stop_event.is_set():
                if not session.active_event.wait(0.1):
                    continue

                self.show_capture_warnings(session, source_errors)
                chunks = self.collect_source_chunks(
                    source_queues,
                    source_done,
                    chunk_frames,
                    chunk_seconds,
                    capture_block_seconds,
                )
                if not chunks and all(source_done.values()):
                    break
                if not chunks:
                    if all(source_done.values()):
                        break
                    continue

                chunks = filter_microphone_bleed(chunks)
                if not chunks:
                    continue
                self.update_meeting_end_detection(session, chunks)

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

    def update_meeting_end_detection(self, session: RecordingSession, chunks: dict[str, np.ndarray]) -> None:
        settings = session.realtime_settings
        if not bool(settings.get("auto_stop_meeting_end", True)) or session.auto_stop_requested:
            return

        now = time.monotonic()
        threshold = max(0.0, float(settings.get("meeting_end_rms_threshold", MIN_TRANSCRIBE_RMS)))
        idle_seconds = max(5.0, float(settings.get("meeting_end_idle_seconds", 120.0)))
        speaker_audio = chunks.get("source_speaker")
        speaker_quiet = speaker_audio is None or speaker_audio.size == 0 or audio_rms(speaker_audio) < threshold
        if now - session.meeting_last_microphone_check_at >= 5.0:
            session.meeting_last_microphone_active = is_microphone_in_use_by_other_app()
            session.meeting_last_microphone_check_at = now
        microphone_active = session.meeting_last_microphone_active

        if speaker_quiet and not microphone_active:
            if session.meeting_idle_started_at is None:
                session.meeting_idle_started_at = now
            elif now - session.meeting_idle_started_at >= idle_seconds:
                session.auto_stop_requested = True
                self.root.after(
                    0,
                    lambda active_session=session: self.auto_stop_recording(active_session, "meeting_end"),
                )
        else:
            session.meeting_idle_started_at = None

    def auto_stop_recording(self, session: RecordingSession, reason: str) -> None:
        if self.session is not session:
            return
        self.request_stop_recording(auto_save=True, emergency=False, reason=reason)

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
        settings = session.realtime_settings
        sample_rate = max(8000, int(settings.get("sample_rate", TRANSCRIBE_SAMPLE_RATE)))
        min_rms = max(0.0, float(settings.get("min_transcribe_rms", MIN_TRANSCRIBE_RMS)))
        language_config = settings.get("languages", {}).get(session.transcription_language, {})
        if not isinstance(language_config, dict):
            language_config = {}
        transcribe_seconds = max(
            0.5,
            float(language_config.get("transcribe_seconds", realtime_transcribe_seconds(session.transcription_language))),
        )
        overlap_seconds = min(
            max(0.0, float(language_config.get("overlap_seconds", 0.0))),
            max(0.0, transcribe_seconds - 0.25),
        )
        overlap_frames = int(sample_rate * overlap_seconds)
        transcription_buffers: dict[str, list[np.ndarray]] = {source: [] for source in AUDIO_SOURCES}
        buffered_frames: dict[str, int] = {source: 0 for source in AUDIO_SOURCES}
        carried_overlap_frames: dict[str, int] = {source: 0 for source in AUDIO_SOURCES}
        review_buffers: dict[str, list[np.ndarray]] = {source: [] for source in AUDIO_SOURCES}
        review_buffered_frames: dict[str, int] = {source: 0 for source in AUDIO_SOURCES}
        review_start_frames: dict[str, int] = {source: 0 for source in AUDIO_SOURCES}
        transcribe_frames = int(sample_rate * transcribe_seconds)
        review_frames = int(sample_rate * REVIEW_SECONDS)
        review_min_frames = int(sample_rate * REVIEW_MIN_AUDIO_SECONDS)
        min_frames = int(sample_rate * REALTIME_MIN_AUDIO_SECONDS)

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

                    transcript_audio = resample_audio(source_audio, RECORD_SAMPLE_RATE, sample_rate)
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

                    if audio_rms(transcript_audio) < min_rms:
                        if carried_overlap_frames[source_name] and buffered_frames[source_name] <= carried_overlap_frames[source_name]:
                            transcription_buffers[source_name].clear()
                            buffered_frames[source_name] = 0
                            carried_overlap_frames[source_name] = 0
                        continue
                    transcription_buffers[source_name].append(transcript_audio)
                    buffered_frames[source_name] += transcript_audio.size
                    if buffered_frames[source_name] >= transcribe_frames:
                        combined = np.concatenate(transcription_buffers[source_name])
                        session.audio_queue.put(TranscriptJob(source_name, combined))
                        if overlap_frames:
                            overlap = combined[-overlap_frames:].copy()
                            transcription_buffers[source_name] = [overlap]
                            buffered_frames[source_name] = overlap.size
                            carried_overlap_frames[source_name] = overlap.size
                        else:
                            transcription_buffers[source_name].clear()
                            buffered_frames[source_name] = 0
                            carried_overlap_frames[source_name] = 0

            if not session.stop_event.is_set():
                for source_name, source_buffer in transcription_buffers.items():
                    new_frames = buffered_frames[source_name] - carried_overlap_frames[source_name]
                    if new_frames >= min_frames and source_buffer:
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
        sample_rate = max(8000, int(session.realtime_settings.get("sample_rate", TRANSCRIBE_SAMPLE_RATE)))
        start = review_start_frames[source_name] / sample_rate
        review_start_frames[source_name] += audio.size
        review_buffers[source_name] = []
        review_buffered_frames[source_name] = 0

        min_rms = max(0.0, float(session.realtime_settings.get("min_transcribe_rms", MIN_TRANSCRIBE_RMS)))
        if audio.size >= min_frames and audio_rms(audio) >= min_rms:
            session.review_queue.put(ReviewJob(source_name, start, audio))

    def collect_source_chunks(
        self,
        source_queues: dict[str, "queue.Queue[Optional[np.ndarray]]"],
        source_done: dict[str, bool],
        chunk_frames: int,
        chunk_seconds: float,
        capture_block_seconds: float,
    ) -> dict[str, np.ndarray]:
        chunks: dict[str, np.ndarray] = {}
        pending = {name for name, done in source_done.items() if not done}
        deadline = time.monotonic() + chunk_seconds + capture_block_seconds + 0.25

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
        chunk_seconds: float,
        capture_read_seconds: float,
        capture_block_seconds: float,
    ) -> None:
        buffers: list[np.ndarray] = []
        buffered_frames = 0
        try:
            raise_current_thread_priority()
            output_frames = max(1, int(RECORD_SAMPLE_RATE * chunk_seconds))
            read_frames = max(1, int(RECORD_SAMPLE_RATE * capture_read_seconds))
            block_frames = max(read_frames * 4, int(RECORD_SAMPLE_RATE * capture_block_seconds))
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
            settings = session.realtime_settings
            language_config = settings.get("languages", {}).get(session.transcription_language, {})
            if not isinstance(language_config, dict):
                language_config = {}
            model_dir = Path(str(language_config.get("model_dir", "")))
            if usable_model_dir(model_dir):
                model_path = str(model_dir)
            else:
                model_path = str(language_config.get("model_name", whisper_model_path(session.transcription_language)))
            base_prompt = str(language_config.get("initial_prompt", "") or "").strip()
            recognition_prompt = session.recognition_prompt.strip()
            prompt_parts = [part for part in (base_prompt, recognition_prompt) if part]
            input_queue_maxsize = max(
                1,
                int(settings.get("transcriber_input_queue_maxsize", TRANSCRIBER_INPUT_QUEUE_MAXSIZE)),
            )
            session.transcriber_input_queue = context.Queue(maxsize=input_queue_maxsize)
            session.transcriber_output_queue = context.Queue(maxsize=TRANSCRIBER_OUTPUT_QUEUE_MAXSIZE)
            language_value = language_config.get("language", whisper_language_code(session.transcription_language))
            if language_value == "":
                language_value = None
            config = {
                "model_path": model_path,
                "device": str(settings.get("whisper_device", WHISPER_DEVICE)),
                "compute_type": str(settings.get("compute_type", WHISPER_COMPUTE_TYPE)),
                "cpu_threads": max(1, int(settings.get("cpu_threads", WHISPER_CPU_THREADS))),
                "num_workers": max(1, int(settings.get("num_workers", WHISPER_NUM_WORKERS))),
                "language": None if language_value is None else str(language_value),
                "condition_on_previous_text": bool(
                    language_config.get(
                        "condition_on_previous_text",
                        condition_on_previous_text(session.transcription_language),
                    )
                ),
                "initial_prompt": "\n".join(prompt_parts) if prompt_parts else None,
                "simplify_chinese": bool(
                    language_config.get("simplify_chinese", simplify_chinese_text(session.transcription_language))
                ),
                "vad_filter": bool(settings.get("vad_filter", True)),
                "vad_parameters": {
                    "min_silence_duration_ms": max(
                        0,
                        int(settings.get("vad_min_silence_duration_ms", 500)),
                    )
                },
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

                language_config = session.realtime_settings.get("languages", {}).get(session.transcription_language, {})
                if not isinstance(language_config, dict):
                    language_config = {}
                self.submit_transcriber_job(
                    session,
                    {
                        "source_name": job.source_name,
                        "audio": audio,
                        "beam_size": max(
                            1,
                            int(language_config.get("beam_size", realtime_beam_size(session.transcription_language))),
                        ),
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

    def finish_stop_worker(
        self,
        session: RecordingSession,
        emergency: bool = False,
        direct_complete: bool = False,
    ) -> None:
        recorder_timeout = 3 if emergency else None
        writer_timeout = 3 if emergency else None
        worker_timeout = 0.5 if emergency else 2
        transcriber_timeout = 0.5 if emergency else 2
        if session.recorder_thread is not None:
            session.recorder_thread.join(timeout=recorder_timeout)
        if session.writer_thread is not None:
            session.writer_thread.join(timeout=writer_timeout)
        if session.transcription_preparer_thread is not None:
            session.transcription_preparer_thread.join(timeout=worker_timeout)
        if session.transcription_thread is not None:
            session.transcription_thread.join(timeout=worker_timeout)
        if session.review_thread is not None:
            session.review_thread.join(timeout=worker_timeout)
        self.stop_transcriber_process(session, timeout=transcriber_timeout, terminate=True)
        self.cancel_pending_minutes(session)
        if session.minutes_thread is not None:
            session.minutes_thread.join(timeout=0.5 if emergency else 1)
        if not emergency:
            self.finalize_transcript_from_review(session)
        if direct_complete:
            self.complete_stop(session)
        else:
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
        if not live:
            llm_summary = self.build_local_llm_meeting_summary(transcript)
            if llm_summary:
                return llm_summary
        return self.build_rule_meeting_summary(transcript, live=live)

    def build_rule_meeting_summary(self, transcript: str, live: bool = False) -> str:
        sentence_entries = self.transcript_sentences_with_speakers(transcript)
        sentences = [sentence for sentence, _speaker in sentence_entries]

        main_points = summarize_sentences(sentences)
        action_items = self.extract_action_items_with_owners(sentence_entries)

        lines = [
            f"{self.t('live_minutes_title' if live else 'meeting_summary_title')}:",
            self.t("summary_note_rules"),
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

    def build_local_llm_meeting_summary(self, transcript: str) -> str:
        settings = self.realtime_settings
        if not bool(settings.get("llm_summary_enabled", False)):
            return ""

        cleaned_transcript = self.transcript_without_existing_minutes(transcript).strip()
        if not cleaned_transcript:
            return ""

        url = str(settings.get("llm_summary_base_url", "") or "").strip()
        model = str(settings.get("llm_summary_model", "") or "").strip()
        if not url or not model:
            return ""

        timeout = max(5.0, float(settings.get("llm_summary_timeout_seconds", 90.0)))
        max_chars = max(1000, int(settings.get("llm_summary_max_input_chars", 12000)))
        temperature = max(0.0, float(settings.get("llm_summary_temperature", 0.2)))
        prompt_transcript = self.trim_transcript_for_summary(cleaned_transcript, max_chars)
        prompt = self.local_llm_summary_prompt(prompt_transcript)

        try:
            import urllib.request

            payload = {
                "model": model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You create accurate meeting minutes from transcripts. "
                            "Do not invent facts, owners, dates, or decisions."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                "temperature": temperature,
                "stream": False,
            }
            request = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except Exception:
            return ""

        text = self.extract_local_llm_text(data)
        if not text:
            return ""
        text = self.strip_llm_thinking(text)
        if not text:
            return ""
        note = self.t("summary_note_llm")
        title = self.t("meeting_summary_title")
        if text.lstrip().lower().startswith(title.lower()):
            return f"{text.strip()}\n\n{note}"
        return f"{title}:\n{note}\n\n{text.strip()}"

    def trim_transcript_for_summary(self, transcript: str, max_chars: int) -> str:
        if len(transcript) <= max_chars:
            return transcript
        head_chars = max_chars * 2 // 3
        tail_chars = max_chars - head_chars
        return (
            transcript[:head_chars].rstrip()
            + "\n\n[... middle of transcript omitted because it is too long ...]\n\n"
            + transcript[-tail_chars:].lstrip()
        )

    def local_llm_summary_prompt(self, transcript: str) -> str:
        return (
            "Create meeting minutes from the transcript below.\n\n"
            "Requirements:\n"
            "- Use the same language as the transcript. If Chinese is used, write Simplified Chinese.\n"
            "- Be concise but complete.\n"
            "- Do not invent missing details.\n"
            "- If an action item has no clear owner, write Owner: Unassigned.\n"
            "- Preserve speaker context when it helps identify ownership.\n\n"
            "Output format:\n"
            "Main discussion:\n"
            "- ...\n\n"
            "Decisions:\n"
            "- ...\n\n"
            "Action items:\n"
            "- Owner: ... | Task: ... | Due: ...\n\n"
            "Open questions:\n"
            "- ...\n\n"
            f"Transcript:\n{transcript}"
        )

    def extract_local_llm_text(self, data: object) -> str:
        if not isinstance(data, dict):
            return ""

        choices = data.get("choices")
        if isinstance(choices, list) and choices:
            first_choice = choices[0]
            if isinstance(first_choice, dict):
                message = first_choice.get("message")
                if isinstance(message, dict):
                    content = message.get("content")
                    if isinstance(content, str):
                        return content.strip()
                text = first_choice.get("text")
                if isinstance(text, str):
                    return text.strip()

        message = data.get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str):
                return content.strip()
        response = data.get("response")
        if isinstance(response, str):
            return response.strip()
        return ""

    def strip_llm_thinking(self, text: str) -> str:
        return re.sub(r"(?is)<think>.*?</think>", "", text).strip()

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

        if session.stop_auto_save:
            save_error: Optional[Exception] = None
            saved_paths: Optional[tuple[Path, Path]] = None
            try:
                saved_paths = self.save_session_to_output(session)
            except Exception as exc:
                save_error = exc
            session.cleanup()
            self.session = None
            self.clear_transcript()
            self.reset_screenshot_session()
            if self.transcript_window is not None:
                self.transcript_window.set_text("")
            if save_error is not None and not session.stop_reason.startswith("windows_"):
                messagebox.showerror(self.title(), f"{self.t('save_failed')}\n{save_error}")
            elif saved_paths is not None and not session.stop_reason.startswith("windows_"):
                mp3_path, text_path = saved_paths
                messagebox.showinfo(self.title(), f"{self.t('saved')}\n{mp3_path}\n{text_path}")
            return

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

    def save_session_to_output(self, session: RecordingSession) -> tuple[Path, Path]:
        output_dir = output_directory()
        output_dir.mkdir(parents=True, exist_ok=True)
        mp3_target = output_dir / f"{session.file_stem}.mp3"
        index = 1
        while mp3_target.exists() or mp3_target.with_suffix(".txt").exists():
            mp3_target = output_dir / f"{session.file_stem}_{index}.mp3"
            index += 1
        text_target = mp3_target.with_suffix(".txt")
        with self.transcript_lock:
            transcript = self.transcript_text
        mp3_target.write_bytes(session.mp3_buffer.getvalue())
        text_target.write_text(transcript, encoding="utf-8")
        return mp3_target, text_target

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

    def open_offline_transcript_window(self) -> None:
        with self.state_lock:
            state = self.state
        if state != "idle":
            messagebox.showinfo(self.title(), self.t("offline_live_busy"))
            return

        if self.offline_window is None:
            self.offline_window = OfflineTranscriptWindow(self.root, self)
        else:
            self.offline_window.window.deiconify()
            self.offline_window.window.lift()

    def open_system_settings_window(self) -> None:
        if self.system_settings_window is None:
            self.system_settings_window = SystemSettingsWindow(self.root, self)
        else:
            self.system_settings_window.window.deiconify()
            self.system_settings_window.window.lift()

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
