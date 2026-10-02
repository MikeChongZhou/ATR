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
import shlex
import subprocess
import sys
import threading
import time
import warnings
from dataclasses import dataclass, field
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Callable, Optional

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

DEFAULT_LLM_FULL_SUMMARY_PROMPT = (
    "Create meeting minutes from the transcript below.\n\n"
    "Requirements:\n"
    "- Use the same language as the transcript. If Chinese is used, write Simplified Chinese.\n"
    "- Be concise but complete.\n"
    "- Do not invent missing details.\n"
    "- Only list action items that are explicitly assigned, requested, or committed in the transcript.\n"
    "- Never convert questions, discussion topics, compliments, or background facts into action items.\n"
    "- If an action item has no clear owner, write Owner: Unassigned.\n"
    "- If an action item has no explicit due date in the transcript, write Due: Unspecified.\n"
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
    "Transcript:\n"
    "{transcript}"
)

DEFAULT_LLM_CHUNK_SUMMARY_PROMPT = (
    "Summarize transcript chunk {index} of {total}. This is not the final meeting summary.\n\n"
    "Extract only durable facts from this chunk:\n"
    "- discussion topics\n"
    "- decisions\n"
    "- action items only when explicitly assigned, requested, or committed\n"
    "- open questions\n"
    "- important numbers, customer/account names, and deadlines\n\n"
    "Do not invent details. Do not turn questions or discussion topics into action items. "
    "Keep the summary compact but specific.\n\n"
    "Transcript chunk:\n"
    "{transcript}"
)

DEFAULT_LLM_MERGE_SUMMARY_PROMPT = (
    "Create the final meeting minutes by merging these chunk summaries.\n\n"
    "Requirements:\n"
    "- Use the same language as the summaries. If Chinese is used, write Simplified Chinese.\n"
    "- Remove duplicates across chunks.\n"
    "- Keep important numbers, customer/account names, decisions, and action owners.\n"
    "- Do not invent missing owners or due dates.\n\n"
    "- Only keep action items that were explicitly assigned, requested, or committed.\n"
    "- If no explicit action item exists, write exactly: - None.\n\n"
    "Output format:\n"
    "Main discussion:\n"
    "- ...\n\n"
    "Decisions:\n"
    "- ...\n\n"
    "Action items:\n"
    "- Owner: ... | Task: ... | Due: ...\n\n"
    "Open questions:\n"
    "- ...\n\n"
    "Chunk summaries:\n"
    "{chunk_summaries}"
)


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
        "summary_llm_unavailable": "Local LLM summary is unavailable, disabled, or timed out. No rule-based summary was generated.",
        "summary_write_failed": "Meeting summary could not be written. Please close the transcript file and try again or regenerate the summary.",
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
        "summary_llm_unavailable": "本地大模型会议纪要不可用、未启用或已超时；不会再生成规则版会议纪要。",
        "summary_write_failed": "会议总结无法写入。请关闭转录文本文件后重试，或重新生成总结。",
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


def configure_logging() -> None:
    log_path = app_directory() / "local_meeting_recorder.log"
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    if not any(isinstance(handler, RotatingFileHandler) for handler in root_logger.handlers):
        handler = RotatingFileHandler(
            log_path,
            maxBytes=1_000_000,
            backupCount=3,
            encoding="utf-8",
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s [%(threadName)s] %(message)s")
        )
        root_logger.addHandler(handler)
    logging.info("logging started: %s", log_path)


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
        "llm_summary_enabled": True,
        "llm_summary_base_url": "http://127.0.0.1:8080/v1/chat/completions",
        "llm_summary_model": "Qwen/Qwen3-0.6B-GGUF:Q8_0",
        "llm_summary_timeout_seconds": 90.0,
        "llm_summary_max_input_chars": 12000,
        "llm_summary_chunk_chars": 6000,
        "llm_summary_max_output_tokens": 800,
        "llm_summary_temperature": 0.2,
        "llm_summary_prompt": "",
        "llm_full_summary_prompt": DEFAULT_LLM_FULL_SUMMARY_PROMPT,
        "llm_chunk_summary_prompt": DEFAULT_LLM_CHUNK_SUMMARY_PROMPT,
        "llm_merge_summary_prompt": DEFAULT_LLM_MERGE_SUMMARY_PROMPT,
        "llm_server_managed": True,
        "llm_server_executable": "",
        "llm_server_model_ref": "Qwen/Qwen3-0.6B-GGUF:Q8_0",
        "llm_server_host": "127.0.0.1",
        "llm_server_port": 8080,
        "llm_server_context": 4096,
        "llm_server_slots": 1,
        "llm_server_extra_args": "",
        "llm_server_start_timeout_seconds": 180.0,
        "llm_pause_cpu_percent": 80.0,
        "llm_pause_memory_percent": 85.0,
        "llm_pause_available_memory_mb": 2000.0,
        "llm_resume_idle_seconds": 30.0,
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


def default_offline_transcript_settings() -> dict[str, Any]:
    language = DEFAULT_TRANSCRIPTION_LANGUAGE
    return {
        "language": language,
        "slice_seconds": realtime_transcribe_seconds(language),
        "overlap_seconds": 0.0,
        "beam_size": realtime_beam_size(language),
        "min_rms": MIN_TRANSCRIBE_RMS,
        "vad_filter": True,
        "vad_silence_ms": int(VAD_PARAMETERS.get("min_silence_duration_ms", 500)),
        "condition_on_previous_text": condition_on_previous_text(language),
        "simplify_chinese": simplify_chinese_text(language),
        "play_chunks": False,
        "log_timing": False,
        "sample_rate": TRANSCRIBE_SAMPLE_RATE,
        "device": WHISPER_DEVICE,
        "compute_type": WHISPER_COMPUTE_TYPE,
        "cpu_threads": WHISPER_CPU_THREADS,
        "num_workers": WHISPER_NUM_WORKERS,
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


def summary_debug_directory() -> Path:
    return app_directory() / "summary_debug"


def close_process_queue_without_join(process_queue: object, label: str) -> None:
    logging.info("close process queue without join: %s", label)
    with contextlib.suppress(Exception):
        process_queue.cancel_join_thread()
    with contextlib.suppress(Exception):
        process_queue.close()


def recording_output_paths(file_stem: str) -> tuple[str, Path, Path, Path, Path]:
    output_dir = output_directory()
    output_dir.mkdir(parents=True, exist_ok=True)
    for index in range(10000):
        candidate_stem = file_stem if index == 0 else f"{file_stem}_{index}"
        mp3_final = output_dir / f"{candidate_stem}.mp3"
        text_final = output_dir / f"{candidate_stem}.txt"
        mp3_partial = output_dir / f"{candidate_stem}.mp3.partial"
        text_partial = output_dir / f"{candidate_stem}.txt.partial"
        if not any(path.exists() for path in (mp3_final, text_final, mp3_partial, text_partial)):
            return candidate_stem, mp3_partial, text_partial, mp3_final, text_final
    raise FileExistsError(f"Could not reserve output filename for {file_stem}")


@dataclass
class AudioFrameSet:
    sources: dict[str, np.ndarray]


@dataclass
class TranscriptJob:
    source_name: str
    audio: np.ndarray
    start: float = 0.0
    end: float = 0.0


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
    mp3_partial_path: Optional[Path] = None
    text_partial_path: Optional[Path] = None
    mp3_final_path: Optional[Path] = None
    text_final_path: Optional[Path] = None
    transcription_language: str = DEFAULT_TRANSCRIPTION_LANGUAGE
    realtime_settings: dict[str, Any] = field(default_factory=default_realtime_settings)
    recognition_prompt: str = ""
    stop_event: threading.Event = field(default_factory=threading.Event)
    active_event: threading.Event = field(default_factory=threading.Event)
    audio_queue: "queue.Queue[Optional[TranscriptJob]]" = field(default_factory=queue.Queue)
    review_queue: "queue.Queue[Optional[ReviewJob]]" = field(default_factory=queue.Queue)
    minutes_queue: "queue.Queue[Optional[str]]" = field(default_factory=queue.Queue)
    mp3_queue: "queue.Queue[Optional[AudioFrameSet]]" = field(default_factory=queue.Queue)
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
    transcript_blocks: list[TranscriptBlock] = field(default_factory=list)
    transcript_body_text: str = ""
    meeting_minutes_text: str = ""
    transcript_text: str = ""
    transcript_lock: threading.Lock = field(default_factory=threading.Lock)
    text_saved_path: Optional[Path] = None
    stop_reason: str = ""

    def cleanup(self) -> None:
        return


@dataclass
class SummaryJob:
    text_path: Path
    transcript: str
    file_stem: str
    settings: dict[str, Any]


class SummaryPaused(Exception):
    pass


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
        self.path: Optional[Path] = None
        self.length_value = 0.0
        self.position_value = 0.0
        self.state = "closed"
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread: Optional[threading.Thread] = None

    def open(self, path: Path) -> float:
        with self.lock:
            self.close()
            try:
                import av
            except ImportError as exc:
                raise RuntimeError("PyAV is required for offline audio playback.") from exc

            length = 0.0
            with av.open(str(path)) as container:
                if container.duration is not None:
                    length = max(0.0, float(container.duration) / 1_000_000)
                if not length:
                    audio_stream = next((stream for stream in container.streams if stream.type == "audio"), None)
                    if audio_stream is not None and audio_stream.duration and audio_stream.time_base:
                        length = max(0.0, float(audio_stream.duration * audio_stream.time_base))
            self.path = path
            self.length_value = length
            self.position_value = 0.0
            self.state = "stopped"
            return self.length_value

    def close(self) -> None:
        self.stop(reset=True)
        with self.lock:
            self.path = None
            self.length_value = 0.0
            self.position_value = 0.0
            self.state = "closed"

    def play(self) -> None:
        with self.lock:
            if self.path is None:
                return
            if self.thread is not None and self.thread.is_alive() and self.state == "playing":
                return
            if self.length_value and self.position_value >= max(0.0, self.length_value - 0.05):
                self.position_value = 0.0
            self.stop_event = threading.Event()
            start_position = self.position_value
            path = self.path
            self.state = "playing"
            self.thread = threading.Thread(
                target=self._playback_worker,
                args=(path, start_position, self.stop_event),
                name="offline-audio-player",
                daemon=True,
            )
            self.thread.start()

    def play_range(self, start_seconds: float, end_seconds: float, wait: bool = True) -> None:
        self.stop(reset=False)
        with self.lock:
            if self.path is None:
                return
            self.position_value = max(0.0, min(start_seconds, self.length_value or start_seconds))
        self.play()
        if wait:
            deadline = time.monotonic() + max(0.0, end_seconds - start_seconds)
            while time.monotonic() < deadline and self.mode() == "playing":
                time.sleep(0.05)
            self.pause()

    def pause(self) -> None:
        thread: Optional[threading.Thread]
        with self.lock:
            if self.path is None or self.state != "playing":
                return
            self.stop_event.set()
            thread = self.thread
            self.thread = None
            self.state = "paused"
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1)

    def stop(self, reset: bool = False) -> None:
        thread: Optional[threading.Thread]
        with self.lock:
            self.stop_event.set()
            thread = self.thread
            self.thread = None
            if self.path is not None:
                self.state = "stopped"
            if reset:
                self.position_value = 0.0
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1)

    def mode(self) -> str:
        with self.lock:
            return self.state

    def position_seconds(self) -> float:
        with self.lock:
            return max(0.0, self.position_value)

    def seek_relative(self, delta_seconds: float) -> None:
        should_resume = False
        with self.lock:
            if self.path is None:
                return
            should_resume = self.state == "playing"
        if should_resume:
            self.pause()
        with self.lock:
            target = max(0.0, self.position_value + delta_seconds)
            if self.length_value:
                target = min(target, self.length_value)
            self.position_value = target
            if self.path is not None and self.state == "closed":
                self.state = "stopped"
        if should_resume:
            self.play()

    def length_seconds(self) -> float:
        with self.lock:
            return self.length_value

    def _playback_worker(self, path: Path, start_seconds: float, stop_event: threading.Event) -> None:
        try:
            import av
            import soundcard as sc

            with av.open(str(path)) as container:
                stream = next((item for item in container.streams if item.type == "audio"), None)
                if stream is None:
                    raise RuntimeError("No audio stream was found.")
                sample_rate = int(stream.codec_context.sample_rate or RECORD_SAMPLE_RATE)
                seek_succeeded = False
                try:
                    container.seek(max(0, int(start_seconds * 1_000_000)), any_frame=False, backward=True)
                    seek_succeeded = True
                except Exception:
                    seek_succeeded = False
                resampler = av.audio.resampler.AudioResampler(format="flt", layout="mono", rate=sample_rate)
                block_size = max(1, int(sample_rate * 0.1))
                speaker = sc.default_speaker()
                played = 0
                fallback_skipped = 0
                fallback_target_skip = 0 if seek_succeeded else max(0, int(start_seconds * sample_rate))

                with speaker.player(samplerate=sample_rate, channels=1, blocksize=block_size) as player:
                    for packet in container.demux(stream):
                        if stop_event.is_set():
                            break
                        for frame in packet.decode():
                            if stop_event.is_set():
                                break
                            frame_start: Optional[float] = None
                            if frame.pts is not None and frame.time_base is not None:
                                frame_start = float(frame.pts * frame.time_base)
                                frame_rate = int(getattr(frame, "sample_rate", 0) or sample_rate)
                                frame_end = frame_start + frame.samples / frame_rate
                                if frame_end <= start_seconds:
                                    continue
                            frames = resampler.resample(frame)
                            if not isinstance(frames, list):
                                frames = [frames]
                            for resampled in frames:
                                audio = self._frame_to_mono_float(resampled)
                                if audio.size == 0:
                                    continue
                                if frame_start is not None and frame_start < start_seconds:
                                    trim = min(audio.size, int((start_seconds - frame_start) * sample_rate))
                                    audio = audio[trim:]
                                    frame_start = start_seconds
                                    if audio.size == 0:
                                        continue
                                elif fallback_skipped < fallback_target_skip:
                                    trim = min(audio.size, fallback_target_skip - fallback_skipped)
                                    audio = audio[trim:]
                                    fallback_skipped += trim
                                    if audio.size == 0:
                                        continue
                                for offset in range(0, audio.size, block_size):
                                    if stop_event.is_set():
                                        break
                                    block = np.clip(audio[offset : offset + block_size], -1.0, 1.0).reshape(-1, 1)
                                    player.play(block)
                                    played += block.shape[0]
                                    with self.lock:
                                        self.position_value = min(
                                            self.length_value or float("inf"),
                                            start_seconds + played / sample_rate,
                                        )
        except Exception as exc:
            logging.warning("Offline audio playback failed: %s", exc)
        finally:
            with self.lock:
                if self.thread is threading.current_thread():
                    self.thread = None
                if self.state == "playing":
                    self.state = "stopped"
                    if self.length_value and self.position_value >= max(0.0, self.length_value - 0.25):
                        self.position_value = self.length_value

    def _frame_to_mono_float(self, frame: object) -> np.ndarray:
        array = np.asarray(frame.to_ndarray())
        if array.dtype.kind in {"i", "u"}:
            info = np.iinfo(array.dtype)
            array = array.astype(np.float32) / max(abs(info.min), info.max)
        else:
            array = array.astype(np.float32, copy=False)
        if array.ndim == 2:
            if array.shape[0] <= array.shape[1]:
                array = array.mean(axis=0)
            else:
                array = array.mean(axis=1)
        elif array.ndim != 1:
            array = array.reshape(-1)
        return np.ascontiguousarray(array, dtype=np.float32)


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
        self.loading_settings = False
        self.settings_dirty = False

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
        self.log_timing_var = tk.BooleanVar(value=False)
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
        self.load_saved_settings()
        self.register_settings_dirty_tracking()
        self.poll_audio_progress()

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.window, padding=12)
        outer.pack(fill=tk.BOTH, expand=True)
        outer.columnconfigure(0, weight=0, minsize=300)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(0, weight=1)

        settings_container = ttk.Frame(outer, width=300)
        settings_container.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        settings_container.columnconfigure(0, weight=1)
        settings_container.rowconfigure(0, weight=1)

        settings_canvas = tk.Canvas(settings_container, width=300, highlightthickness=0)
        settings_scrollbar = ttk.Scrollbar(settings_container, orient=tk.VERTICAL, command=settings_canvas.yview)
        settings_canvas.configure(yscrollcommand=settings_scrollbar.set)
        settings_canvas.grid(row=0, column=0, sticky="nsew")
        settings_scrollbar.grid(row=0, column=1, sticky="ns")

        settings = ttk.Frame(settings_canvas)
        settings_window = settings_canvas.create_window((0, 0), window=settings, anchor="nw")
        settings.columnconfigure(0, weight=1)

        def update_settings_scroll_region(_event: object) -> None:
            settings_canvas.configure(scrollregion=settings_canvas.bbox("all"))

        def resize_settings_window(event: object) -> None:
            settings_canvas.itemconfigure(settings_window, width=event.width)

        def on_settings_mousewheel(event: object) -> None:
            settings_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        settings.bind("<Configure>", update_settings_scroll_region)
        settings_canvas.bind("<Configure>", resize_settings_window)
        settings_canvas.bind("<Enter>", lambda _event: settings_canvas.bind_all("<MouseWheel>", on_settings_mousewheel))
        settings_canvas.bind("<Leave>", lambda _event: settings_canvas.unbind_all("<MouseWheel>"))

        self.save_settings_button = ttk.Button(settings, text="Save Settings", command=self.save_settings)
        self.save_settings_button.grid(row=0, column=0, sticky="ew")

        basic = ttk.LabelFrame(settings, text="Basic", padding=10)
        basic.grid(row=1, column=0, sticky="ew", pady=(12, 0))
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
        advanced.grid(row=2, column=0, sticky="ew", pady=(12, 0))
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
        ttk.Checkbutton(
            advanced,
            text="Log chunk timing",
            variable=self.log_timing_var,
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(4, 0))

        engine = ttk.LabelFrame(settings, text="Engine", padding=10)
        engine.grid(row=3, column=0, sticky="ew", pady=(12, 0))
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
        self.mark_settings_dirty()

    def load_saved_settings(self) -> None:
        self.loading_settings = True
        try:
            settings = merge_dict(default_offline_transcript_settings(), self.app.offline_transcript_settings)
            language = str(settings.get("language", DEFAULT_TRANSCRIPTION_LANGUAGE))
            if language not in TRANSCRIPTION_MODEL_CONFIGS:
                language = DEFAULT_TRANSCRIPTION_LANGUAGE
            self.language_var.set(language)
            self.slice_seconds_var.set(float(settings.get("slice_seconds", realtime_transcribe_seconds(language))))
            self.overlap_seconds_var.set(float(settings.get("overlap_seconds", 0.0)))
            self.beam_size_var.set(int(settings.get("beam_size", realtime_beam_size(language))))
            self.min_rms_var.set(float(settings.get("min_rms", MIN_TRANSCRIBE_RMS)))
            self.vad_var.set(bool(settings.get("vad_filter", True)))
            self.vad_silence_ms_var.set(int(settings.get("vad_silence_ms", VAD_PARAMETERS.get("min_silence_duration_ms", 500))))
            self.condition_var.set(bool(settings.get("condition_on_previous_text", condition_on_previous_text(language))))
            self.simplify_var.set(bool(settings.get("simplify_chinese", simplify_chinese_text(language))))
            self.play_chunks_var.set(bool(settings.get("play_chunks", False)))
            self.log_timing_var.set(bool(settings.get("log_timing", False)))
            self.sample_rate_var.set(int(settings.get("sample_rate", TRANSCRIBE_SAMPLE_RATE)))
            self.device_var.set(str(settings.get("device", WHISPER_DEVICE)))
            self.compute_type_var.set(str(settings.get("compute_type", WHISPER_COMPUTE_TYPE)))
            self.cpu_threads_var.set(int(settings.get("cpu_threads", WHISPER_CPU_THREADS)))
            self.num_workers_var.set(int(settings.get("num_workers", WHISPER_NUM_WORKERS)))
            self.settings_dirty = False
        finally:
            self.loading_settings = False
        self.refresh_settings_button()

    def register_settings_dirty_tracking(self) -> None:
        for variable in (
            self.language_var,
            self.slice_seconds_var,
            self.overlap_seconds_var,
            self.beam_size_var,
            self.device_var,
            self.compute_type_var,
            self.cpu_threads_var,
            self.num_workers_var,
            self.vad_var,
            self.vad_silence_ms_var,
            self.condition_var,
            self.min_rms_var,
            self.simplify_var,
            self.play_chunks_var,
            self.log_timing_var,
            self.sample_rate_var,
        ):
            variable.trace_add("write", lambda *_args: self.mark_settings_dirty())

    def mark_settings_dirty(self) -> None:
        if self.loading_settings:
            return
        self.settings_dirty = True
        self.refresh_settings_button()

    def refresh_settings_button(self) -> None:
        if hasattr(self, "save_settings_button"):
            text = "Save Settings" + (" *" if self.settings_dirty else "")
            self.save_settings_button.configure(text=text)

    def collect_offline_settings(self) -> dict[str, Any]:
        language = self.language_var.get()
        if language not in TRANSCRIPTION_MODEL_CONFIGS:
            language = DEFAULT_TRANSCRIPTION_LANGUAGE
        return {
            "language": language,
            "slice_seconds": max(1.0, float(self.slice_seconds_var.get())),
            "overlap_seconds": max(0.0, float(self.overlap_seconds_var.get())),
            "beam_size": max(1, int(self.beam_size_var.get())),
            "min_rms": max(0.0, float(self.min_rms_var.get())),
            "vad_filter": bool(self.vad_var.get()),
            "vad_silence_ms": max(0, int(self.vad_silence_ms_var.get())),
            "condition_on_previous_text": bool(self.condition_var.get()),
            "simplify_chinese": bool(self.simplify_var.get()),
            "play_chunks": bool(self.play_chunks_var.get()),
            "log_timing": bool(self.log_timing_var.get()),
            "sample_rate": max(8000, int(self.sample_rate_var.get())),
            "device": self.device_var.get().strip() or WHISPER_DEVICE,
            "compute_type": self.compute_type_var.get().strip() or WHISPER_COMPUTE_TYPE,
            "cpu_threads": max(1, int(self.cpu_threads_var.get())),
            "num_workers": max(1, int(self.num_workers_var.get())),
        }

    def save_settings(self, show_message: bool = True) -> bool:
        try:
            settings = self.collect_offline_settings()
        except Exception as exc:
            messagebox.showerror(self.window.title(), f"Invalid transcript settings:\n{exc}")
            return False
        self.app.set_offline_transcript_settings(settings)
        self.settings_dirty = False
        self.refresh_settings_button()
        if show_message:
            messagebox.showinfo(self.window.title(), "Offline transcript settings saved.")
        return True

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
            minutes = self.app.build_meeting_summary(transcript, debug_name=self.offline_file_stem)
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
        prompt = self.app.realtime_initial_prompt(language)
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
            "log_timing": bool(self.log_timing_var.get()),
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
            close_process_queue_without_join(process_queue, "offline-transcriber")
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
            log_timing = bool(settings.get("log_timing", False))
            start_seconds = 0.0
            job_id = 0
            transcript_started_at = time.monotonic()

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
                    chunk_started_at = time.monotonic()
                    if not self.submit_chunk(job_id, chunk, start_seconds, end_seconds, int(settings["beam_size"])):
                        break
                    submitted_at = time.monotonic()
                    playback_thread = self.start_chunk_playback(chunk, sample_rate, bool(settings["play_chunks"]))
                    result = self.wait_for_chunk_result(job_id)
                    completed_at = time.monotonic()
                    if result is not None:
                        text = str(result.get("text", "")).strip()
                        if log_timing:
                            self.append_timing_log(
                                start_seconds,
                                end_seconds,
                                submitted_at - chunk_started_at,
                                completed_at - submitted_at,
                                completed_at - chunk_started_at,
                                completed_at - transcript_started_at,
                            )
                        if text:
                            self.append_transcript(start_seconds, end_seconds, text)
                    elif self.transcriber_process is not None and getattr(self.transcriber_process, "exitcode", None) is not None:
                        if log_timing:
                            self.append_timing_log(
                                start_seconds,
                                end_seconds,
                                submitted_at - chunk_started_at,
                                completed_at - submitted_at,
                                completed_at - chunk_started_at,
                                completed_at - transcript_started_at,
                                note="transcriber exited",
                            )
                        break
                    if playback_thread is not None:
                        playback_thread.join(timeout=max(0.1, end_seconds - start_seconds + 1.0))
                elif log_timing:
                    self.append_timing_log(
                        start_seconds,
                        end_seconds,
                        0.0,
                        0.0,
                        0.0,
                        time.monotonic() - transcript_started_at,
                        note=f"skipped RMS {audio_rms(chunk):.5f} < {min_rms:.5f}",
                    )

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

    def append_timing_log(
        self,
        start_seconds: float,
        end_seconds: float,
        submit_seconds: float,
        whisper_seconds: float,
        total_seconds: float,
        elapsed_seconds: float,
        note: str = "",
    ) -> None:
        audio_seconds = max(0.001, end_seconds - start_seconds)
        rtf = whisper_seconds / audio_seconds
        backlog_seconds = elapsed_seconds - end_seconds
        completed_at = dt.datetime.now().strftime("%H:%M:%S")
        message = (
            f"[Timing] {format_duration(start_seconds)}-{format_duration(end_seconds)} | "
            f"audio {audio_seconds:.1f}s | submit {submit_seconds:.2f}s | "
            f"whisper {whisper_seconds:.2f}s | total {total_seconds:.2f}s | "
            f"RTF {rtf:.2f} | backlog {backlog_seconds:+.1f}s | done {completed_at}"
        )
        if note:
            message += f" | {note}"
        logging.info("offline %s", message)

        def update() -> None:
            if self.closed:
                return
            if self.text.index("end-1c") != "1.0":
                self.text.insert(tk.END, "\n")
            self.text.insert(tk.END, message + "\n")
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
        if self.settings_dirty:
            choice = messagebox.askyesnocancel(
                self.window.title(),
                "Offline transcript settings have changed. Save before closing?",
            )
            if choice is None:
                return
            if choice and not self.save_settings(show_message=False):
                return
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
        self.summary_prompt_widgets: dict[str, tk.Text] = {}
        self.language_vars: dict[str, dict[str, tk.Variable]] = {}
        self.prompt_widgets: dict[str, tk.Text] = {}

        self._build_ui()
        self.load_values(app.realtime_settings)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.window, padding=12)
        outer.pack(fill=tk.BOTH, expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)

        button_row = ttk.Frame(outer)
        button_row.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        ttk.Button(button_row, text="Defaults", command=self.reset_defaults).pack(side=tk.LEFT)
        ttk.Button(button_row, text="Close", command=self.close).pack(side=tk.RIGHT)
        ttk.Button(button_row, text="Save", command=self.save).pack(side=tk.RIGHT, padx=(0, 8))

        notebook = ttk.Notebook(outer)
        notebook.grid(row=1, column=0, sticky="nsew")

        common = self._create_scrollable_tab(notebook, "Common")
        common.columnconfigure(1, weight=1)
        self._add_common_controls(common)

        summary = self._create_scrollable_tab(notebook, "Summary")
        summary.columnconfigure(1, weight=1)
        self._add_summary_controls(summary)

        for language, label in (("en", "English"), ("zh", "Chinese")):
            frame = self._create_scrollable_tab(notebook, label)
            frame.columnconfigure(1, weight=1)
            self._add_language_controls(frame, language)

    def _create_scrollable_tab(self, notebook: ttk.Notebook, title: str) -> ttk.Frame:
        tab = ttk.Frame(notebook)
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=1)
        notebook.add(tab, text=title)

        canvas = tk.Canvas(tab, highlightthickness=0)
        scrollbar = ttk.Scrollbar(tab, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        content = ttk.Frame(canvas, padding=12)
        content_window = canvas.create_window((0, 0), window=content, anchor="nw")

        def update_scroll_region(_event: object) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))

        def resize_content(event: object) -> None:
            canvas.itemconfigure(content_window, width=event.width)

        content.bind("<Configure>", update_scroll_region)
        canvas.bind("<Configure>", resize_content)
        return content

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
        for key, label in (
            ("vad_min_silence_duration_ms", "VAD min silence ms"),
            ("min_transcribe_rms", "Min transcript RMS"),
            ("transcriber_input_queue_maxsize", "Realtime input queue max"),
            ("chunk_seconds", "Audio chunk seconds"),
            ("capture_read_seconds", "Capture read seconds"),
            ("capture_block_seconds", "Capture block seconds"),
            ("auto_screenshot_interval_seconds", "Auto screenshot interval sec"),
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
            "llm_summary_chunk_chars": tk.IntVar(),
            "llm_summary_max_output_tokens": tk.IntVar(),
            "llm_summary_temperature": tk.DoubleVar(),
            "llm_server_managed": tk.BooleanVar(),
            "llm_server_executable": tk.StringVar(),
            "llm_server_model_ref": tk.StringVar(),
            "llm_server_host": tk.StringVar(),
            "llm_server_port": tk.IntVar(),
            "llm_server_context": tk.IntVar(),
            "llm_server_slots": tk.IntVar(),
            "llm_server_extra_args": tk.StringVar(),
            "llm_server_start_timeout_seconds": tk.DoubleVar(),
            "llm_pause_cpu_percent": tk.DoubleVar(),
            "llm_pause_memory_percent": tk.DoubleVar(),
            "llm_pause_available_memory_mb": tk.DoubleVar(),
            "llm_resume_idle_seconds": tk.DoubleVar(),
        }
        row = 0
        ttk.Checkbutton(
            parent,
            text="Use local LLM for final meeting summary",
            variable=self.summary_vars["llm_summary_enabled"],
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(0, 8))
        row += 1
        ttk.Checkbutton(
            parent,
            text="Managed llama server",
            variable=self.summary_vars["llm_server_managed"],
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(0, 8))
        row += 1
        for key, label in (
            ("llm_summary_base_url", "OpenAI-compatible URL"),
            ("llm_summary_model", "Model"),
            ("llm_summary_timeout_seconds", "Timeout seconds"),
            ("llm_summary_chunk_chars", "Chunk chars"),
            ("llm_summary_max_input_chars", "Final merge max chars"),
            ("llm_summary_max_output_tokens", "Max output tokens"),
            ("llm_summary_temperature", "Temperature"),
            ("llm_server_executable", "llama executable"),
            ("llm_server_model_ref", "llama model ref"),
            ("llm_server_host", "llama host"),
            ("llm_server_port", "llama port"),
            ("llm_server_context", "llama context"),
            ("llm_server_slots", "llama slots"),
            ("llm_server_extra_args", "llama extra args"),
            ("llm_server_start_timeout_seconds", "llama start timeout"),
            ("llm_pause_cpu_percent", "Pause CPU %"),
            ("llm_pause_memory_percent", "Pause memory %"),
            ("llm_pause_available_memory_mb", "Pause avail MB"),
            ("llm_resume_idle_seconds", "Resume idle seconds"),
        ):
            self._entry(parent, label, self.summary_vars[key], row)
            row += 1

        ttk.Label(parent, text="Summary prompt templates").grid(
            row=row,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(14, 4),
        )
        row += 1
        prompt_notebook = ttk.Notebook(parent)
        prompt_notebook.grid(row=row, column=0, columnspan=2, sticky="nsew")
        parent.rowconfigure(row, weight=1)

        for key, label in (
            ("llm_full_summary_prompt", "Full"),
            ("llm_chunk_summary_prompt", "Chunk"),
            ("llm_merge_summary_prompt", "Merge"),
        ):
            frame = ttk.Frame(prompt_notebook, padding=6)
            frame.columnconfigure(0, weight=1)
            frame.rowconfigure(0, weight=1)
            prompt_notebook.add(frame, text=label)
            text = tk.Text(frame, height=12, wrap=tk.WORD, font=("Microsoft YaHei UI", 10))
            text.grid(row=0, column=0, sticky="nsew")
            scrollbar = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=text.yview)
            scrollbar.grid(row=0, column=1, sticky="ns")
            text.configure(yscrollcommand=scrollbar.set)
            self.summary_prompt_widgets[key] = text
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
        defaults = default_realtime_settings()
        for key, widget in self.summary_prompt_widgets.items():
            widget.delete("1.0", tk.END)
            widget.insert(tk.END, str(settings.get(key, defaults.get(key, "")) or ""))

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
        for key, widget in self.summary_prompt_widgets.items():
            settings[key] = widget.get("1.0", tk.END).strip()
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
        self.offline_transcript_settings = default_offline_transcript_settings()
        self.recording_prompts: dict[str, str] = {"en": "", "zh": ""}
        self.emergency_stop_lock = threading.Lock()
        self.emergency_stop_started = False
        self.summary_jobs: "queue.Queue[Optional[SummaryJob]]" = queue.Queue()
        self.summary_thread: Optional[threading.Thread] = None
        self.summary_lock = threading.Lock()
        self.llm_process: Optional[subprocess.Popen] = None
        self.llm_process_lock = threading.Lock()
        self.cpu_sample: Optional[tuple[int, int, int]] = None
        self.summary_last_busy_at = 0.0
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
        offline_settings = default_offline_transcript_settings()
        prompts = {"en": "", "zh": ""}
        path = user_settings_path()
        migrated_settings = False
        try:
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                stored_settings = data.get("realtime_settings")
                needs_llm_migration = isinstance(stored_settings, dict) and "llm_server_managed" not in stored_settings
                merge_dict(settings, stored_settings)
                merge_dict(offline_settings, data.get("offline_transcript_settings"))
                if needs_llm_migration:
                    settings["llm_summary_enabled"] = True
                    settings["llm_summary_model"] = "Qwen/Qwen3-0.6B-GGUF:Q8_0"
                    settings["llm_server_model_ref"] = "Qwen/Qwen3-0.6B-GGUF:Q8_0"
                    settings["llm_server_managed"] = True
                    settings["llm_server_context"] = 4096
                    settings["llm_server_slots"] = 1
                    migrated_settings = True
                if isinstance(data.get("recording_prompts"), dict):
                    prompts.update({key: str(value) for key, value in data["recording_prompts"].items()})
        except Exception:
            settings = default_realtime_settings()
            offline_settings = default_offline_transcript_settings()
            prompts = {"en": "", "zh": ""}
        self.realtime_settings = settings
        self.offline_transcript_settings = offline_settings
        self.recording_prompts = prompts
        if migrated_settings:
            self.save_user_settings()

    def save_user_settings(self) -> None:
        payload = {
            "realtime_settings": self.realtime_settings,
            "offline_transcript_settings": self.offline_transcript_settings,
            "recording_prompts": self.recording_prompts,
        }
        path = user_settings_path()
        with contextlib.suppress(Exception):
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def set_realtime_settings(self, settings: dict[str, Any]) -> None:
        self.realtime_settings = merge_dict(default_realtime_settings(), settings)
        self.save_user_settings()

    def set_offline_transcript_settings(self, settings: dict[str, Any]) -> None:
        self.offline_transcript_settings = merge_dict(default_offline_transcript_settings(), settings)
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

    def write_partial_transcript(self, session: RecordingSession, text: str) -> None:
        if session.text_partial_path is None:
            return
        try:
            session.text_partial_path.parent.mkdir(parents=True, exist_ok=True)
            session.text_partial_path.write_text(text, encoding="utf-8")
        except Exception as exc:
            logging.warning("Could not write partial transcript %s: %s", session.text_partial_path, exc)

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
        with contextlib.suppress(queue.Full):
            self.summary_jobs.put_nowait(None)
        self.stop_managed_llm_server()
        with contextlib.suppress(Exception):
            self.icon.stop()

        session = self.session
        if session is not None:
            session.stop_event.set()
            session.active_event.set()
            self.stop_transcription_source_queue(session)
            self.cancel_pending_transcription(session)
            with contextlib.suppress(queue.Full):
                session.mp3_queue.put_nowait(None)
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
            with contextlib.suppress(Exception):
                self.save_session_to_output(session, queue_summary=False)
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

        self.stop_managed_llm_server()
        try:
            file_stem, mp3_partial_path, text_partial_path, mp3_final_path, text_final_path = recording_output_paths(
                recording_file_stem()
            )
        except Exception as exc:
            messagebox.showerror(self.title(), f"{self.t('save_failed')}\n{exc}")
            return
        self.begin_screenshot_session(file_stem)
        self.set_auto_screenshot_enabled(auto_screenshot_requested)

        settings_snapshot = copy.deepcopy(self.realtime_settings)
        session = RecordingSession(
            file_stem=file_stem,
            mp3_partial_path=mp3_partial_path,
            text_partial_path=text_partial_path,
            mp3_final_path=mp3_final_path,
            text_final_path=text_final_path,
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
        self.write_partial_transcript(session, "")

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
        self.request_stop_recording(auto_save=True, emergency=False, reason="")

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
            logging.info("stop[%s] ignored because state is already stopping", getattr(session, "file_stem", "unknown"))
            return

        session.stop_reason = reason
        logging.info(
            "stop[%s] requested auto_save=%s emergency=%s reason=%s state=%s",
            session.file_stem,
            auto_save,
            emergency,
            reason,
            state,
        )
        if emergency:
            self.auto_screenshot_enabled = False
            self.screenshot_stop_event.set()
            self.stop_managed_llm_server()
        else:
            self.set_auto_screenshot_enabled(False)
        session.stop_event.set()
        session.active_event.set()
        if emergency:
            with contextlib.suppress(queue.Full):
                session.mp3_queue.put_nowait(None)
            self.stop_transcription_source_queue(session)
            self.cancel_pending_transcription(session)
            session.review_queue.put(None)
        self.cancel_pending_minutes(session)
        with self.state_lock:
            self.state = "stopping"
        self.update_tray_menu()
        logging.info("stop[%s] state set to stopping", session.file_stem)

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
        source_queues: dict[str, "queue.Queue[Optional[np.ndarray]]"] = {}
        source_done: dict[str, bool] = {}
        chunk_frames = max(1, int(RECORD_SAMPLE_RATE * CHUNK_SECONDS))
        chunk_seconds = CHUNK_SECONDS
        capture_block_seconds = CAPTURE_BLOCK_SECONDS

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

            source_queues = {
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

                if session.stop_event.is_set():
                    logging.info("stop[%s] dropping collected chunks because stop was requested", session.file_stem)
                    break
                self.enqueue_recorded_chunks(session, chunks)

            if all(source_done.values()) and not session.stop_event.is_set():
                raise RuntimeError(self.t("all_sources_unavailable"))
        except Exception as exc:
            self.root.after(0, lambda error=exc: self.handle_recording_error(session, error))
        finally:
            session.stop_event.set()
            for thread in source_threads:
                thread.join(timeout=2)
            if source_queues:
                logging.info(
                    "stop[%s] capture stopped; post-stop source buffers will not be enqueued",
                    session.file_stem,
                )
            session.mp3_queue.put(None)
            if not AUDIO_ONLY_DIAGNOSTIC:
                self.stop_transcription_source_queue(session)

    def enqueue_recorded_chunks(self, session: RecordingSession, chunks: dict[str, np.ndarray]) -> None:
        if session.stop_event.is_set():
            logging.info("stop[%s] dropping captured chunk because stop was requested", session.file_stem)
            return
        chunks = filter_microphone_bleed(chunks)
        if not chunks:
            return
        if session.stop_event.is_set():
            logging.info("stop[%s] dropping filtered chunk because stop was requested", session.file_stem)
            return

        mp3_chunks = chunks
        if not MIX_MICROPHONE_IN_MP3:
            mp3_chunks = {name: audio for name, audio in chunks.items() if name != "source_microphone"}
        if mp3_chunks:
            session.mp3_queue.put(AudioFrameSet(mp3_chunks))
        if not AUDIO_ONLY_DIAGNOSTIC:
            self.offer_transcription_source_frame(session, AudioFrameSet(chunks))

    def offer_transcription_source_frame(self, session: RecordingSession, frame_set: AudioFrameSet) -> None:
        while not session.stop_event.is_set():
            try:
                session.transcription_source_queue.put(frame_set, timeout=0.1)
                return
            except queue.Full:
                continue
        logging.info("stop[%s] transcription source offer skipped after stop", session.file_stem)

    def stop_transcription_source_queue(self, session: RecordingSession) -> None:
        deadline = time.monotonic() + 5.0
        while True:
            try:
                session.transcription_source_queue.put(None, timeout=0.1)
                return
            except queue.Full:
                if time.monotonic() < deadline:
                    continue
                logging.warning("stop[%s] transcription source queue full while stopping; dropping one frame for sentinel", session.file_stem)
                with contextlib.suppress(queue.Empty):
                    session.transcription_source_queue.get_nowait()

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
        buffer_start_frames: dict[str, int] = {source: 0 for source in AUDIO_SOURCES}
        source_total_frames: dict[str, int] = {source: 0 for source in AUDIO_SOURCES}
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
                    continue

                if frame_set is None:
                    break
                if not session.active_event.wait(0.1):
                    continue

                for source_name in AUDIO_SOURCES:
                    source_audio = frame_set.sources.get(source_name)
                    if source_audio is None or source_audio.size == 0:
                        continue

                    transcript_audio = resample_audio(source_audio, RECORD_SAMPLE_RATE, sample_rate)
                    source_start_frame = source_total_frames[source_name]
                    source_total_frames[source_name] += transcript_audio.size
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
                            buffer_start_frames[source_name] = source_total_frames[source_name]
                        continue
                    if not transcription_buffers[source_name]:
                        buffer_start_frames[source_name] = source_start_frame
                    transcription_buffers[source_name].append(transcript_audio)
                    buffered_frames[source_name] += transcript_audio.size
                    if buffered_frames[source_name] >= transcribe_frames:
                        combined = np.concatenate(transcription_buffers[source_name])
                        start = buffer_start_frames[source_name] / sample_rate
                        end = (buffer_start_frames[source_name] + combined.size) / sample_rate
                        session.audio_queue.put(TranscriptJob(source_name, combined, start, end))
                        if overlap_frames:
                            overlap = combined[-overlap_frames:].copy()
                            transcription_buffers[source_name] = [overlap]
                            buffered_frames[source_name] = overlap.size
                            carried_overlap_frames[source_name] = overlap.size
                            buffer_start_frames[source_name] = max(
                                0,
                                buffer_start_frames[source_name] + combined.size - overlap.size,
                            )
                        else:
                            transcription_buffers[source_name].clear()
                            buffered_frames[source_name] = 0
                            carried_overlap_frames[source_name] = 0
                            buffer_start_frames[source_name] = source_total_frames[source_name]

            for source_name, source_buffer in transcription_buffers.items():
                new_frames = buffered_frames[source_name] - carried_overlap_frames[source_name]
                if new_frames >= min_frames and source_buffer:
                    combined = np.concatenate(source_buffer)
                    start = buffer_start_frames[source_name] / sample_rate
                    end = (buffer_start_frames[source_name] + combined.size) / sample_rate
                    session.audio_queue.put(TranscriptJob(source_name, combined, start, end))
        finally:
            if ENABLE_BACKGROUND_REVIEW:
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
            if session.mp3_partial_path is None:
                raise RuntimeError("MP3 partial path is not available")
            session.mp3_partial_path.parent.mkdir(parents=True, exist_ok=True)

            encoder = lameenc.Encoder()
            encoder.set_bit_rate(128)
            encoder.set_in_sample_rate(RECORD_SAMPLE_RATE)
            encoder.set_channels(CHANNELS)
            encoder.set_quality(2)

            with session.mp3_partial_path.open("wb") as mp3_file:
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
                        mp3_file.write(mp3_chunk)
                        mp3_file.flush()

                final_chunk = encoder.flush()
                if final_chunk:
                    mp3_file.write(final_chunk)
                mp3_file.flush()
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
        while True:
            process = session.transcriber_process
            if process is not None and getattr(process, "exitcode", None) is not None:
                return
            try:
                input_queue.put(job, timeout=0.2)
                return
            except queue.Full:
                continue

    def signal_transcriber_stop(self, session: RecordingSession, force_drop: bool = False) -> None:
        input_queue = session.transcriber_input_queue
        if input_queue is None:
            return
        while True:
            try:
                input_queue.put(None, timeout=0.2)
                return
            except queue.Full:
                if not force_drop:
                    continue
                with contextlib.suppress(queue.Empty):
                    input_queue.get_nowait()

    def stop_transcriber_process(
        self,
        session: RecordingSession,
        timeout: Optional[float] = 2.0,
        terminate: bool = False,
    ) -> None:
        timeout_label = "complete" if timeout is None else f"{timeout:.2f}s"
        logging.info("stop[%s] stopping transcriber process timeout=%s terminate=%s", session.file_stem, timeout_label, terminate)
        self.signal_transcriber_stop(session, force_drop=terminate)

        process = session.transcriber_process
        if process is not None:
            started = time.monotonic()
            if timeout is None:
                last_log = started
                while process.is_alive() and not self.shutting_down:
                    process.join(timeout=1)
                    now = time.monotonic()
                    if process.is_alive() and now - last_log >= 10:
                        logging.info(
                            "stop[%s] waiting for transcriber process elapsed=%.2fs",
                            session.file_stem,
                            now - started,
                        )
                        last_log = now
            else:
                process.join(timeout=timeout)
            logging.info(
                "stop[%s] transcriber join done alive=%s elapsed=%.2fs",
                session.file_stem,
                process.is_alive(),
                time.monotonic() - started,
            )
            if terminate and process.is_alive():
                with contextlib.suppress(Exception):
                    logging.info("stop[%s] terminating transcriber process", session.file_stem)
                    process.terminate()
                process.join(timeout=1)
                logging.info("stop[%s] transcriber after terminate alive=%s", session.file_stem, process.is_alive())
            if process.is_alive() and not terminate:
                logging.info("stop[%s] transcriber still alive; keeping queues open", session.file_stem)
                return
            with contextlib.suppress(Exception):
                process.close()
            session.transcriber_process = None

        result_thread = session.transcription_result_thread
        if result_thread is not None and result_thread is not threading.current_thread():
            started = time.monotonic()
            if timeout is None:
                last_log = started
                while result_thread.is_alive() and not self.shutting_down:
                    result_thread.join(timeout=1)
                    now = time.monotonic()
                    if result_thread.is_alive() and now - last_log >= 10:
                        logging.info(
                            "stop[%s] waiting for transcription result thread elapsed=%.2fs",
                            session.file_stem,
                            now - started,
                        )
                        last_log = now
            else:
                result_thread.join(timeout=timeout)
            logging.info(
                "stop[%s] transcription result thread joined alive=%s elapsed=%.2fs",
                session.file_stem,
                result_thread.is_alive(),
                time.monotonic() - started,
            )
            if result_thread.is_alive() and not terminate:
                logging.info("stop[%s] result thread still alive; keeping queues open", session.file_stem)
                return
        session.transcription_result_thread = None
        self.close_transcriber_queues(session)
        logging.info("stop[%s] transcriber cleanup finished", session.file_stem)

    def close_transcriber_queues(self, session: RecordingSession) -> None:
        for queue_name in ("transcriber_input_queue", "transcriber_output_queue"):
            process_queue = getattr(session, queue_name)
            if process_queue is None:
                continue
            close_process_queue_without_join(process_queue, f"{session.file_stem}:{queue_name}")
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
                if text:
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

                language_config = session.realtime_settings.get("languages", {}).get(session.transcription_language, {})
                if not isinstance(language_config, dict):
                    language_config = {}
                self.submit_transcriber_job(
                    session,
                    {
                        "source_name": job.source_name,
                        "start": job.start,
                        "end": job.end,
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
            if minutes:
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
        with session.transcript_lock:
            session.meeting_minutes_text = minutes
            document = self.compose_transcript_document(session.transcript_body_text, session.meeting_minutes_text)
            session.transcript_text = document

        with self.transcript_lock:
            self.meeting_minutes_text = session.meeting_minutes_text
            self.transcript_body_text = session.transcript_body_text
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
        recorder_timeout = 3
        writer_timeout = 3
        worker_timeout = 0.5 if emergency else 2
        transcriber_timeout = 0.5 if emergency else 2
        save_error: Optional[Exception] = None
        saved_paths: Optional[tuple[Path, Path]] = None
        try:
            logging.info("stop[%s] finish worker started emergency=%s", session.file_stem, emergency)
            for label, thread, timeout in (
                ("recorder", session.recorder_thread, recorder_timeout),
                ("writer", session.writer_thread, writer_timeout),
            ):
                if thread is None:
                    continue
                started = time.monotonic()
                logging.info("stop[%s] joining %s timeout=%.2fs", session.file_stem, label, timeout)
                thread.join(timeout=timeout)
                logging.info(
                    "stop[%s] joined %s alive=%s elapsed=%.2fs",
                    session.file_stem,
                    label,
                    thread.is_alive(),
                    time.monotonic() - started,
                )

            if emergency:
                for label, thread, timeout in (
                    ("transcription-preparer", session.transcription_preparer_thread, worker_timeout),
                    ("transcription-relay", session.transcription_thread, worker_timeout),
                    ("review", session.review_thread, worker_timeout),
                ):
                    if thread is None:
                        continue
                    started = time.monotonic()
                    logging.info("stop[%s] joining %s timeout=%.2fs", session.file_stem, label, timeout)
                    thread.join(timeout=timeout)
                    logging.info(
                        "stop[%s] joined %s alive=%s elapsed=%.2fs",
                        session.file_stem,
                        label,
                        thread.is_alive(),
                        time.monotonic() - started,
                    )
                self.stop_transcriber_process(session, timeout=transcriber_timeout, terminate=True)
                self.cancel_pending_minutes(session)
                if session.minutes_thread is not None:
                    started = time.monotonic()
                    session.minutes_thread.join(timeout=0.5)
                    logging.info(
                        "stop[%s] joined minutes alive=%s elapsed=%.2fs",
                        session.file_stem,
                        session.minutes_thread.is_alive(),
                        time.monotonic() - started,
                    )

            logging.info("stop[%s] saving session queue_summary=False", session.file_stem)
            saved_paths = self.save_session_to_output(session, queue_summary=False)
            logging.info("stop[%s] saved paths=%s", session.file_stem, saved_paths)
        except Exception as exc:
            logging.exception("stop[%s] finish worker failed", session.file_stem)
            save_error = exc

        if not emergency and not direct_complete:
            self.root.after(
                0,
                lambda paths=saved_paths, error=save_error: self.complete_stop(
                    session,
                    saved_paths=paths,
                    save_error=error,
                ),
            )
            self.finish_transcription_after_stop(session)
            return

        if not direct_complete:
            self.mark_recording_idle_after_stop(session)
        if direct_complete:
            self.complete_stop(session, saved_paths=saved_paths, save_error=save_error)
        else:
            self.root.after(
                0,
                lambda paths=saved_paths, error=save_error: self.complete_stop(
                    session,
                    saved_paths=paths,
                    save_error=error,
                ),
            )

    def finish_transcription_after_stop(self, session: RecordingSession) -> None:
        if AUDIO_ONLY_DIAGNOSTIC:
            return
        try:
            for label, thread in (
                ("transcription-preparer", session.transcription_preparer_thread),
                ("transcription-relay", session.transcription_thread),
                ("review", session.review_thread),
            ):
                if thread is None:
                    continue
                started = time.monotonic()
                logging.info("stop[%s] background waiting for %s", session.file_stem, label)
                while thread.is_alive() and not self.shutting_down:
                    thread.join(timeout=1)
                logging.info(
                    "stop[%s] background %s done alive=%s elapsed=%.2fs",
                    session.file_stem,
                    label,
                    thread.is_alive(),
                    time.monotonic() - started,
                )
            self.stop_transcriber_process(session, timeout=None, terminate=False)
            if session.minutes_thread is not None:
                started = time.monotonic()
                session.minutes_thread.join(timeout=1)
                logging.info(
                    "stop[%s] background minutes joined alive=%s elapsed=%.2fs",
                    session.file_stem,
                    session.minutes_thread.is_alive(),
                    time.monotonic() - started,
                )
            logging.info("stop[%s] background finalizing transcript", session.file_stem)
            self.finalize_transcript_from_review(session)
            queue_summary = not session.stop_reason.startswith("windows_") and not self.shutting_down
            logging.info("stop[%s] background saving final transcript queue_summary=%s", session.file_stem, queue_summary)
            self.save_session_to_output(session, queue_summary=queue_summary)
        except Exception:
            logging.exception("stop[%s] background transcription finish failed", session.file_stem)

    def mark_recording_idle_after_stop(self, session: RecordingSession) -> None:
        if self.session is not session:
            logging.info("stop[%s] skip idle mark because session changed", session.file_stem)
            return
        with self.state_lock:
            self.state = "idle"
        self.update_tray_menu()
        logging.info("stop[%s] state set to idle from finish worker", session.file_stem)

    def finalize_transcript_from_review(self, session: RecordingSession) -> None:
        with session.final_lock:
            entries = list(session.final_entries)

        transcript = self.format_transcript_entries(entries)
        if not transcript:
            with session.transcript_lock:
                transcript = session.transcript_body_text or self.transcript_without_existing_minutes(session.transcript_text)

        summary = self.build_meeting_summary(transcript, allow_llm=False)
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

    def build_meeting_summary(
        self,
        transcript: str,
        live: bool = False,
        allow_llm: bool = True,
        settings: Optional[dict[str, Any]] = None,
        debug_name: Optional[str] = None,
    ) -> str:
        if live:
            return ""
        if allow_llm:
            llm_summary = self.build_local_llm_meeting_summary(transcript, settings=settings, debug_name=debug_name)
            if llm_summary:
                return llm_summary
            return self.local_llm_unavailable_summary()
        return ""

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

    def build_local_llm_meeting_summary(
        self,
        transcript: str,
        settings: Optional[dict[str, Any]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
        pause_wait: Optional[Callable[[], None]] = None,
        debug_name: Optional[str] = None,
    ) -> str:
        settings = settings or self.realtime_settings
        if not bool(settings.get("llm_summary_enabled", False)):
            logging.info("summary build skipped: local LLM summary disabled")
            return ""

        cleaned_transcript = self.transcript_without_existing_minutes(transcript).strip()
        if not cleaned_transcript:
            logging.info("summary build skipped: empty transcript")
            return ""

        url = self.summary_url(settings)
        model = str(settings.get("llm_summary_model", "") or "").strip()
        if not url or not model:
            logging.info("summary build skipped: missing url or model url=%s model=%s", bool(url), bool(model))
            return ""

        timeout = max(5.0, float(settings.get("llm_summary_timeout_seconds", 90.0)))
        merge_max_chars = max(2000, int(settings.get("llm_summary_max_input_chars", 12000)))
        chunk_chars = max(1000, int(settings.get("llm_summary_chunk_chars", 6000)))
        max_output_tokens = max(128, int(settings.get("llm_summary_max_output_tokens", 800)))
        temperature = max(0.0, float(settings.get("llm_summary_temperature", 0.2)))
        full_prompt_template = self.summary_template_from_settings(
            settings,
            "llm_full_summary_prompt",
            DEFAULT_LLM_FULL_SUMMARY_PROMPT,
        )
        chunk_prompt_template = self.summary_template_from_settings(
            settings,
            "llm_chunk_summary_prompt",
            DEFAULT_LLM_CHUNK_SUMMARY_PROMPT,
        )
        merge_prompt_template = self.summary_template_from_settings(
            settings,
            "llm_merge_summary_prompt",
            DEFAULT_LLM_MERGE_SUMMARY_PROMPT,
        )

        chunks = self.split_transcript_for_summary(cleaned_transcript, chunk_chars)
        if not chunks:
            logging.info("summary build skipped: no chunks transcript_chars=%d", len(cleaned_transcript))
            return ""
        logging.info(
            "summary build started: transcript_chars=%d chunks=%d chunk_chars=%d model=%s",
            len(cleaned_transcript),
            len(chunks),
            chunk_chars,
            model,
        )

        def call_prompt(prompt: str, token_budget: int) -> str:
            while not self.shutting_down:
                if pause_wait is not None:
                    pause_wait()
                try:
                    return self.call_local_llm_summary(
                        url,
                        model,
                        prompt,
                        temperature,
                        timeout,
                        token_budget,
                        settings=settings,
                        cancel_check=cancel_check,
                    )
                except SummaryPaused:
                    if pause_wait is not None:
                        pause_wait()
                    continue
            return ""

        if len(chunks) == 1:
            prompt = self.local_llm_summary_prompt(chunks[0], full_prompt_template)
            text = call_prompt(prompt, max_output_tokens)
        else:
            chunk_summaries: list[str] = []
            chunk_token_budget = max(256, min(max_output_tokens, 600))
            for index, chunk in enumerate(chunks, start=1):
                prompt = self.local_llm_chunk_summary_prompt(chunk, index, len(chunks), chunk_prompt_template)
                logging.info("summary chunk request %d/%d chars=%d", index, len(chunks), len(chunk))
                chunk_summary = call_prompt(prompt, chunk_token_budget)
                if not chunk_summary:
                    logging.info("summary chunk request %d/%d returned no text", index, len(chunks))
                    return ""
                chunk_summaries.append(f"Chunk {index}:\n{chunk_summary}")

            merged_input = "\n\n".join(chunk_summaries)
            untrimmed_merged_input = merged_input
            merged_input = self.trim_transcript_for_summary(merged_input, merge_max_chars)
            logging.info("summary merge request chars=%d", len(merged_input))
            prompt = self.local_llm_merge_summary_prompt(merged_input, merge_prompt_template)
            text = call_prompt(prompt, max_output_tokens)
        if not text:
            logging.info("summary build returned no text")
            return ""
        text = self.sanitize_llm_summary(text, cleaned_transcript)
        if len(chunks) > 1:
            self.write_summary_debug_file(
                debug_name,
                cleaned_transcript,
                chunks,
                chunk_summaries,
                untrimmed_merged_input,
                merged_input,
                text,
                chunk_chars,
                merge_max_chars,
                model,
            )
        logging.info("summary build completed output_chars=%d", len(text))
        note = self.t("summary_note_llm")
        title = self.t("meeting_summary_title")
        if text.lstrip().lower().startswith(title.lower()):
            return f"{text.strip()}\n\n{note}"
        return f"{title}:\n{note}\n\n{text.strip()}"

    def local_llm_unavailable_summary(self) -> str:
        return f"{self.t('meeting_summary_title')}:\n{self.t('summary_llm_unavailable')}"

    def call_local_llm_summary(
        self,
        url: str,
        model: str,
        prompt: str,
        temperature: float,
        timeout: float,
        max_tokens: int,
        settings: Optional[dict[str, Any]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> str:
        settings = settings or self.realtime_settings
        if bool(settings.get("llm_server_managed", True)) and not self.ensure_managed_llm_server(settings):
            logging.info("summary request skipped: managed LLM server unavailable")
            return ""

        result_queue: "queue.Queue[tuple[str, object]]" = queue.Queue(maxsize=1)
        logging.info(
            "summary request started: model=%s max_tokens=%d timeout=%.1fs prompt_chars=%d",
            model,
            max_tokens,
            timeout,
            len(prompt),
        )

        def request_worker() -> None:
            try:
                import urllib.request

                payload = {
                    "model": model,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "You create accurate meeting minutes from transcripts. "
                                "Do not invent facts, owners, dates, or decisions. "
                                "Do not include reasoning or hidden thinking."
                            ),
                        },
                        {"role": "user", "content": "/no_think\n" + prompt},
                    ],
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "stream": False,
                    "chat_template_kwargs": {"enable_thinking": False},
                }
                request = urllib.request.Request(
                    url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    data = json.loads(response.read().decode("utf-8"))
                result_queue.put(("ok", data))
            except Exception as exc:
                with contextlib.suppress(queue.Full):
                    result_queue.put(("error", exc))

        thread = threading.Thread(target=request_worker, name="llm-request", daemon=True)
        thread.start()

        while thread.is_alive():
            try:
                status, value = result_queue.get(timeout=1)
                break
            except queue.Empty:
                if cancel_check is not None and cancel_check():
                    logging.info("summary request paused/cancelled due to resource policy")
                    self.stop_managed_llm_server()
                    raise SummaryPaused()
        else:
            try:
                status, value = result_queue.get_nowait()
            except queue.Empty:
                logging.info("summary request ended without response")
                return ""

        if status != "ok":
            logging.info("summary request failed: %s", value)
            return ""
        data = value

        text = self.extract_local_llm_text(data)
        if not text:
            logging.info("summary request returned empty text")
            return ""
        logging.info("summary request completed output_chars=%d", len(text))
        return self.strip_llm_thinking(text)

    def split_transcript_for_summary(self, transcript: str, max_chars: int) -> list[str]:
        blocks = [block.strip() for block in re.split(r"\n\s*\n", transcript) if block.strip()]
        if not blocks:
            return []

        chunks: list[str] = []
        current: list[str] = []
        current_len = 0

        def flush_current() -> None:
            nonlocal current, current_len
            if current:
                chunks.append("\n\n".join(current))
                current = []
                current_len = 0

        for block in blocks:
            block_len = len(block)
            if block_len > max_chars:
                flush_current()
                for start in range(0, block_len, max_chars):
                    chunks.append(block[start : start + max_chars].strip())
                continue

            separator_len = 2 if current else 0
            if current and current_len + separator_len + block_len > max_chars:
                flush_current()

            current.append(block)
            current_len += separator_len + block_len

        flush_current()
        return [chunk for chunk in chunks if chunk]

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

    def write_summary_debug_file(
        self,
        debug_name: Optional[str],
        transcript: str,
        transcript_chunks: list[str],
        chunk_summaries: list[str],
        untrimmed_merged_input: str,
        merged_input: str,
        final_summary: str,
        chunk_chars: int,
        merge_max_chars: int,
        model: str,
    ) -> None:
        try:
            directory = summary_debug_directory()
            directory.mkdir(parents=True, exist_ok=True)
            safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", (debug_name or recording_file_stem()).strip())
            timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
            target = directory / f"{safe_name}_summary_chunks_{timestamp}.txt"
            latest = directory / "latest_summary_chunks.txt"

            lines = [
                "Summary chunk debug",
                f"Created: {dt.datetime.now().isoformat(timespec='seconds')}",
                f"Model: {model}",
                f"Transcript chars: {len(transcript)}",
                f"Transcript chunks: {len(transcript_chunks)}",
                f"Chunk chars setting: {chunk_chars}",
                f"Merge max chars setting: {merge_max_chars}",
                f"Untrimmed merge input chars: {len(untrimmed_merged_input)}",
                f"Merge input chars after trim: {len(merged_input)}",
                "",
            ]
            for index, chunk in enumerate(transcript_chunks, start=1):
                lines.extend(
                    [
                        f"===== Transcript Chunk {index}/{len(transcript_chunks)} | chars={len(chunk)} =====",
                        chunk,
                        "",
                    ]
                )
                summary = chunk_summaries[index - 1] if index - 1 < len(chunk_summaries) else ""
                lines.extend(
                    [
                        f"===== Chunk Summary {index}/{len(transcript_chunks)} =====",
                        summary,
                        "",
                    ]
                )
            lines.extend(
                [
                    "===== Merge Input After Trim =====",
                    merged_input,
                    "",
                    "===== Final Summary Before Header =====",
                    final_summary,
                    "",
                ]
            )
            payload = "\n".join(lines)
            target.write_text(payload, encoding="utf-8")
            latest.write_text(payload, encoding="utf-8")
            logging.info("summary debug written: %s", target)
        except Exception:
            logging.exception("summary debug write failed")

    def summary_template_from_settings(self, settings: dict[str, Any], key: str, default: str) -> str:
        template = str(settings.get(key, "") or "").strip()
        return template or default

    def render_summary_template(
        self,
        template: str,
        content_key: str,
        content_label: str,
        content: str,
        replacements: Optional[dict[str, object]] = None,
    ) -> str:
        prompt = template.strip()
        for key, value in (replacements or {}).items():
            prompt = prompt.replace("{" + key + "}", str(value))
        content_token = "{" + content_key + "}"
        if content_token in prompt:
            return prompt.replace(content_token, content)
        return f"{prompt}\n\n{content_label}:\n{content}"

    def local_llm_summary_prompt(self, transcript: str, prompt_template: str = DEFAULT_LLM_FULL_SUMMARY_PROMPT) -> str:
        return self.render_summary_template(
            prompt_template,
            "transcript",
            "Transcript",
            transcript,
        )

    def local_llm_chunk_summary_prompt(
        self,
        transcript: str,
        index: int,
        total: int,
        prompt_template: str = DEFAULT_LLM_CHUNK_SUMMARY_PROMPT,
    ) -> str:
        return self.render_summary_template(
            prompt_template,
            "transcript",
            "Transcript chunk",
            transcript,
            {"index": index, "total": total},
        )

    def local_llm_merge_summary_prompt(
        self,
        chunk_summaries: str,
        prompt_template: str = DEFAULT_LLM_MERGE_SUMMARY_PROMPT,
    ) -> str:
        return self.render_summary_template(
            prompt_template,
            "chunk_summaries",
            "Chunk summaries",
            chunk_summaries,
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

    def sanitize_llm_summary(self, summary: str, transcript: str) -> str:
        if self.transcript_has_explicit_action_cue(transcript):
            return self.remove_unsupported_due_dates(summary, transcript)

        lines = summary.splitlines()
        action_index: Optional[int] = None
        next_index = len(lines)
        action_headers = {"action items", "行动项", "待办事项"}
        section_headers = {
            "main discussion",
            "主要讨论",
            "decisions",
            "决定",
            "决策",
            "open questions",
            "开放问题",
            "未决问题",
        }

        for index, line in enumerate(lines):
            label = line.strip().lstrip("#-* ").rstrip(":：").strip().lower()
            if label in action_headers:
                action_index = index
                break
        if action_index is None:
            return summary

        for index in range(action_index + 1, len(lines)):
            label = lines[index].strip().lstrip("#-* ").rstrip(":：").strip().lower()
            if label in section_headers:
                next_index = index
                break

        is_chinese = bool(re.search(r"[\u4e00-\u9fff]", transcript))
        header_line = "行动项:" if is_chinese else "Action items:"
        none_line = "- 无明确行动项。" if is_chinese else "- None."
        sanitized_lines = lines[:action_index] + [header_line, none_line]
        if next_index < len(lines):
            if sanitized_lines and sanitized_lines[-1].strip():
                sanitized_lines.append("")
            sanitized_lines.extend(lines[next_index:])
        logging.info("summary action items cleared: no explicit action cue found in transcript")
        return "\n".join(sanitized_lines).strip()

    def transcript_has_explicit_action_cue(self, transcript: str) -> bool:
        patterns = (
            r"\b(action item|todo|to-do|follow up|assigned to|responsible for|owner|deadline|due date)\b",
            r"\b(can you|could you|please)\b",
            r"\b(?:i|we|you|they|he|she)\s+(?:will|need to|needs to|should|must|have to|am going to|are going to|is going to)\b",
            r"(?:行动项|待办|跟进|负责|请|安排|确认|发送|更新|修复|检查|截止|完成)",
        )
        return any(re.search(pattern, transcript, flags=re.IGNORECASE) for pattern in patterns)

    def remove_unsupported_due_dates(self, summary: str, transcript: str) -> str:
        def replace_due(match: re.Match[str]) -> str:
            due_value = match.group(1).strip()
            if not due_value or due_value.lower() in {"unspecified", "none", "n/a"}:
                return match.group(0)
            if due_value in transcript:
                return match.group(0)
            logging.info("summary due date removed because it is not present in transcript: %s", due_value)
            return "Due: Unspecified"

        return re.sub(r"Due:\s*([^|\n]+)", replace_due, summary)

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

    def complete_stop(
        self,
        session: RecordingSession,
        saved_paths: Optional[tuple[Path, Path]] = None,
        save_error: Optional[Exception] = None,
    ) -> None:
        if self.session is not session:
            logging.info("stop[%s] complete ignored because session changed", session.file_stem)
            return

        with self.state_lock:
            self.state = "idle"
        self.update_tray_menu()
        logging.info("stop[%s] complete_stop set state idle", session.file_stem)

        notify_user = not session.stop_reason.startswith("windows_") and not self.shutting_down
        session.cleanup()
        self.session = None
        self.clear_transcript()
        self.reset_screenshot_session()
        if self.transcript_window is not None:
            self.transcript_window.set_text("")
        if save_error is not None and notify_user:
            messagebox.showerror(self.title(), f"{self.t('save_failed')}\n{save_error}")
        elif saved_paths is not None and notify_user:
            mp3_path, text_path = saved_paths
            messagebox.showinfo(self.title(), f"{self.t('saved')}\n{mp3_path}\n{text_path}")
        logging.info("stop[%s] complete_stop finished", session.file_stem)

    def save_session_to_output(self, session: RecordingSession, queue_summary: bool = True) -> tuple[Path, Path]:
        with session.transcript_lock:
            transcript = session.transcript_text
        if not transcript and self.session is session:
            with self.transcript_lock:
                transcript = self.transcript_text
        mp3_target = session.mp3_final_path or (output_directory() / f"{session.file_stem}.mp3")
        text_target = session.text_final_path or mp3_target.with_suffix(".txt")
        mp3_target.parent.mkdir(parents=True, exist_ok=True)
        self.write_partial_transcript(session, transcript)

        if session.mp3_partial_path is not None and session.mp3_partial_path.exists():
            logging.info("stop[%s] rename mp3 partial %s -> %s", session.file_stem, session.mp3_partial_path, mp3_target)
            if session.mp3_partial_path.resolve() != mp3_target.resolve():
                session.mp3_partial_path.replace(mp3_target)
        elif not mp3_target.exists():
            logging.info("stop[%s] mp3 partial missing; creating empty file %s", session.file_stem, mp3_target)
            mp3_target.write_bytes(b"")

        if session.text_partial_path is not None and session.text_partial_path.exists():
            logging.info("stop[%s] rename text partial %s -> %s", session.file_stem, session.text_partial_path, text_target)
            if session.text_partial_path.resolve() != text_target.resolve():
                session.text_partial_path.replace(text_target)
            else:
                text_target.write_text(transcript, encoding="utf-8")
        else:
            logging.info("stop[%s] text partial missing; writing transcript %s", session.file_stem, text_target)
            text_target.write_text(transcript, encoding="utf-8")
        session.mp3_partial_path = mp3_target
        session.text_partial_path = text_target
        session.mp3_final_path = mp3_target
        session.text_final_path = text_target
        session.text_saved_path = text_target

        if queue_summary:
            self.queue_background_summary(text_target, transcript, session.file_stem)
        return mp3_target, text_target

    def queue_background_summary(self, text_path: Path, transcript: str, file_stem: str) -> None:
        settings = copy.deepcopy(self.realtime_settings)
        if not bool(settings.get("llm_summary_enabled", False)):
            logging.info("summary[%s] not queued: local LLM summary disabled", file_stem)
            return
        cleaned = self.transcript_without_existing_minutes(transcript).strip()
        if not cleaned:
            logging.info("summary[%s] not queued: empty transcript", file_stem)
            return
        logging.info("summary[%s] queued text_path=%s transcript_chars=%d", file_stem, text_path, len(cleaned))
        self.summary_jobs.put(SummaryJob(text_path, cleaned, file_stem, settings))
        with self.summary_lock:
            if self.summary_thread is None or not self.summary_thread.is_alive():
                self.summary_thread = threading.Thread(
                    target=self.background_summary_worker,
                    name="background-summary",
                    daemon=True,
                )
                self.summary_thread.start()
                logging.info("summary worker started")

    def background_summary_worker(self) -> None:
        lower_current_thread_priority()
        logging.info("summary worker running")
        while not self.shutting_down:
            try:
                job = self.summary_jobs.get(timeout=1)
            except queue.Empty:
                with self.summary_lock:
                    if self.summary_jobs.empty():
                        self.summary_thread = None
                        self.stop_managed_llm_server()
                        logging.info("summary worker exiting: queue empty")
                        return
                continue
            if job is None:
                self.stop_managed_llm_server()
                logging.info("summary worker exiting: stop sentinel")
                return
            self.run_background_summary_job(job)

    def run_background_summary_job(self, job: SummaryJob) -> None:
        if not bool(job.settings.get("llm_summary_enabled", False)):
            logging.info("summary[%s] skipped in worker: disabled", job.file_stem)
            return

        logging.info("summary[%s] started", job.file_stem)
        while not self.shutting_down:
            self.wait_for_summary_resources(job.settings)
            if self.shutting_down:
                logging.info("summary[%s] cancelled: app shutting down", job.file_stem)
                return
            try:
                summary = self.build_local_llm_meeting_summary(
                    job.transcript,
                    settings=job.settings,
                    cancel_check=lambda settings=job.settings: self.summary_should_pause(settings, during_llm=True),
                    pause_wait=lambda settings=job.settings: self.wait_for_summary_resources(settings),
                    debug_name=job.file_stem,
                )
            except SummaryPaused:
                logging.info("summary[%s] paused by resource policy", job.file_stem)
                continue
            if summary:
                if self.write_background_summary(job, summary):
                    logging.info("summary[%s] written", job.file_stem)
                else:
                    logging.info("summary[%s] not written; user notification queued", job.file_stem)
                self.stop_managed_llm_server()
                return
            logging.info("summary[%s] produced no summary; waiting before retry", job.file_stem)
            self.wait_for_summary_resources(job.settings)

    def write_background_summary(self, job: SummaryJob, summary: str) -> bool:
        try:
            current = job.text_path.read_text(encoding="utf-8") if job.text_path.exists() else job.transcript
            transcript = self.transcript_without_existing_minutes(current).strip() or job.transcript
            document = f"{transcript}\n\n{summary.strip()}" if transcript else summary.strip()
            job.text_path.write_text(document, encoding="utf-8")
            return True
        except Exception as exc:
            logging.exception("summary[%s] write failed path=%s", job.file_stem, job.text_path)
            if not self.shutting_down:
                self.root.after(
                    0,
                    lambda error=exc, path=job.text_path: messagebox.showwarning(
                        self.title(),
                        f"{self.t('summary_write_failed')}\n\n{path}\n\n{self.t('details')}: {error}",
                    ),
                )
            return False

    def wait_for_summary_resources(self, settings: dict[str, Any]) -> None:
        while not self.shutting_down and self.summary_should_pause(settings):
            logging.info("summary paused: waiting for idle resources")
            self.stop_managed_llm_server()
            time.sleep(5)

    def summary_should_pause(self, settings: dict[str, Any], during_llm: bool = False) -> bool:
        with self.state_lock:
            if self.state != "idle":
                self.summary_last_busy_at = time.monotonic()
                return True

        if is_microphone_in_use_by_other_app():
            self.summary_last_busy_at = time.monotonic()
            return True

        cpu_limit = max(0.0, float(settings.get("llm_pause_cpu_percent", 80.0)))
        memory_limit = max(0.0, float(settings.get("llm_pause_memory_percent", 85.0)))
        available_limit = max(0.0, float(settings.get("llm_pause_available_memory_mb", 2000.0)))

        cpu_percent = self.system_cpu_percent()
        memory_percent, available_mb = self.system_memory_status()
        if not during_llm and cpu_percent >= cpu_limit:
            self.summary_last_busy_at = time.monotonic()
            return True
        if memory_percent >= memory_limit:
            self.summary_last_busy_at = time.monotonic()
            return True
        if available_mb and available_mb <= available_limit:
            self.summary_last_busy_at = time.monotonic()
            return True

        resume_seconds = max(0.0, float(settings.get("llm_resume_idle_seconds", 30.0)))
        return time.monotonic() - self.summary_last_busy_at < resume_seconds

    def system_cpu_percent(self) -> float:
        if sys.platform != "win32":
            return 0.0
        try:
            import ctypes
            from ctypes import wintypes

            idle = wintypes.FILETIME()
            kernel = wintypes.FILETIME()
            user = wintypes.FILETIME()
            if not ctypes.windll.kernel32.GetSystemTimes(
                ctypes.byref(idle),
                ctypes.byref(kernel),
                ctypes.byref(user),
            ):
                return 0.0

            def filetime_to_int(value: object) -> int:
                return (int(value.dwHighDateTime) << 32) + int(value.dwLowDateTime)

            idle_value = filetime_to_int(idle)
            kernel_value = filetime_to_int(kernel)
            user_value = filetime_to_int(user)
            total = kernel_value + user_value
            current = (idle_value, kernel_value, user_value)
            previous = self.cpu_sample
            self.cpu_sample = current
            if previous is None:
                return 0.0
            previous_idle, previous_kernel, previous_user = previous
            total_delta = total - (previous_kernel + previous_user)
            idle_delta = idle_value - previous_idle
            if total_delta <= 0:
                return 0.0
            busy = max(0, total_delta - idle_delta)
            return min(100.0, max(0.0, busy / total_delta * 100.0))
        except Exception:
            return 0.0

    def system_memory_status(self) -> tuple[float, float]:
        if sys.platform != "win32":
            return 0.0, 0.0
        try:
            import ctypes

            class MemoryStatusEx(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = MemoryStatusEx()
            status.dwLength = ctypes.sizeof(status)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return 0.0, 0.0
            return float(status.dwMemoryLoad), float(status.ullAvailPhys) / (1024 * 1024)
        except Exception:
            return 0.0, 0.0

    def summary_url(self, settings: dict[str, Any]) -> str:
        if bool(settings.get("llm_server_managed", True)):
            host = str(settings.get("llm_server_host", "127.0.0.1") or "127.0.0.1").strip()
            port = max(1, int(settings.get("llm_server_port", 8080)))
            return f"http://{host}:{port}/v1/chat/completions"
        return str(settings.get("llm_summary_base_url", "") or "").strip()

    def resolve_llm_executable(self, settings: dict[str, Any]) -> str:
        configured = str(settings.get("llm_server_executable", "") or "").strip()
        if configured:
            return configured
        candidates = (
            app_directory() / "llm" / "llama.exe",
            app_directory() / "llm" / "llama-server.exe",
            app_directory() / "llama.exe",
            app_directory() / "llama-server.exe",
        )
        for candidate in candidates:
            if candidate.exists():
                return str(candidate)
        return "llama"

    def llm_health_ok(self, settings: dict[str, Any]) -> bool:
        try:
            import urllib.request

            host = str(settings.get("llm_server_host", "127.0.0.1") or "127.0.0.1").strip()
            port = max(1, int(settings.get("llm_server_port", 8080)))
            with urllib.request.urlopen(f"http://{host}:{port}/health", timeout=2) as response:
                return 200 <= int(response.status) < 300
        except Exception:
            return False

    def ensure_managed_llm_server(self, settings: dict[str, Any]) -> bool:
        if not bool(settings.get("llm_server_managed", True)):
            logging.info("summary managed LLM disabled; expecting external server")
            return True
        if self.llm_health_ok(settings):
            logging.info("summary managed LLM already healthy")
            return True

        with self.llm_process_lock:
            process = self.llm_process
            if process is not None and process.poll() is not None:
                logging.info("summary managed LLM previous process exited code=%s", process.poll())
                self.llm_process = None
            if self.llm_process is None:
                executable = self.resolve_llm_executable(settings)
                model_ref = str(settings.get("llm_server_model_ref", settings.get("llm_summary_model", "")) or "").strip()
                host = str(settings.get("llm_server_host", "127.0.0.1") or "127.0.0.1").strip()
                port = str(max(1, int(settings.get("llm_server_port", 8080))))
                context_size = str(max(512, int(settings.get("llm_server_context", 4096))))
                slots = str(max(1, int(settings.get("llm_server_slots", 1))))
                command = [executable]
                executable_name = Path(executable).name.lower()
                if "llama-server" not in executable_name:
                    command.append("serve")
                command.extend(["-hf", model_ref, "--host", host, "--port", port, "-c", context_size, "-np", slots])
                extra_args = str(settings.get("llm_server_extra_args", "") or "").strip()
                if extra_args:
                    command.extend(shlex.split(extra_args, posix=False))
                creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
                try:
                    logging.info("summary starting managed LLM: executable=%s model=%s host=%s port=%s context=%s slots=%s", executable, model_ref, host, port, context_size, slots)
                    self.llm_process = subprocess.Popen(
                        command,
                        cwd=str(app_directory()),
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=creationflags,
                    )
                except Exception:
                    logging.exception("summary failed to start managed LLM executable=%s", executable)
                    self.llm_process = None
                    return False

        deadline = time.monotonic() + max(5.0, float(settings.get("llm_server_start_timeout_seconds", 180.0)))
        while time.monotonic() < deadline and not self.shutting_down:
            if self.summary_should_pause(settings, during_llm=True):
                logging.info("summary managed LLM startup paused by resource policy")
                return False
            if self.llm_health_ok(settings):
                logging.info("summary managed LLM healthy")
                return True
            time.sleep(1)
        healthy = self.llm_health_ok(settings)
        logging.info("summary managed LLM startup finished healthy=%s", healthy)
        return healthy

    def stop_managed_llm_server(self) -> None:
        with self.llm_process_lock:
            process = self.llm_process
            self.llm_process = None
        if process is None:
            return
        with contextlib.suppress(Exception):
            if process.poll() is None:
                logging.info("summary stopping managed LLM")
                process.terminate()
                process.wait(timeout=5)
        with contextlib.suppress(Exception):
            if process.poll() is None:
                logging.info("summary killing managed LLM")
                process.kill()
        with contextlib.suppress(Exception):
            process.wait(timeout=2)

    def append_transcript(self, session: RecordingSession, source_name: str, text: str) -> None:
        sentences = split_sentences(text)
        if not sentences:
            return

        with session.transcript_lock:
            if session.transcript_blocks and session.transcript_blocks[-1].source_name == source_name:
                session.transcript_blocks[-1].sentences.extend(sentences)
            else:
                session.transcript_blocks.append(TranscriptBlock(source_name, sentences))
            transcript = self.format_transcript_blocks(session.transcript_blocks)
            session.transcript_body_text = transcript
            document = self.compose_transcript_document(session.transcript_body_text, session.meeting_minutes_text)
            session.transcript_text = document

        if self.session is session:
            with self.transcript_lock:
                self.transcript_blocks = list(session.transcript_blocks)
                self.transcript_body_text = session.transcript_body_text
                self.meeting_minutes_text = session.meeting_minutes_text
                self.transcript_text = session.transcript_text

        self.write_partial_transcript(session, document)
        if self.session is session and not session.stop_event.is_set():
            self.queue_minutes_update(session, transcript)
            self.root.after(0, lambda active_session=session, current_text=document: self.refresh_transcript_text(active_session, current_text))

    def replace_transcript_document(self, session: RecordingSession, text: str) -> None:
        with session.transcript_lock:
            session.transcript_blocks = []
            session.transcript_body_text = text
            session.meeting_minutes_text = ""
            session.transcript_text = text
        if self.session is session:
            with self.transcript_lock:
                self.transcript_blocks = []
                self.transcript_body_text = text
                self.meeting_minutes_text = ""
                self.transcript_text = text
        self.write_partial_transcript(session, text)
        if self.session is session:
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
            with contextlib.suppress(Exception):
                self.save_session_to_output(session, queue_summary=False)
            session.cleanup()
            self.session = None
            self.clear_transcript()
            return
        messagebox.showerror(self.title(), f"{self.t('recording_failed')}\n{exc}")

        with contextlib.suppress(Exception):
            self.save_session_to_output(session, queue_summary=False)
        session.cleanup()
        self.session = None
        self.clear_transcript()
        if self.transcript_window is not None:
            self.transcript_window.set_text("")


def main() -> None:
    mp.freeze_support()
    quiet_library_noise()
    configure_logging()
    root = tk.Tk()
    root.title(APP_NAME)
    app = MeetingRecorderApp(root)
    app.run()


if __name__ == "__main__":
    main()
