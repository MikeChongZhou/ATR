from __future__ import annotations

import contextlib
import gc
import os
import sys
from typing import Any


def lower_current_process_priority() -> None:
    if sys.platform != "win32":
        return
    with contextlib.suppress(Exception):
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        idle_priority_class = 0x00000040
        kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), idle_priority_class)


def limit_native_threads() -> None:
    for name in (
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
    ):
        os.environ.setdefault(name, "1")


def make_text_converter(config: dict[str, Any]) -> Any:
    if not config.get("simplify_chinese", False):
        return None
    try:
        from opencc import OpenCC

        return OpenCC("t2s")
    except Exception:
        return None


def run_transcriber_process(input_queue: Any, output_queue: Any, config: dict[str, Any]) -> None:
    lower_current_process_priority()
    limit_native_threads()

    model = None
    text_converter = make_text_converter(config)
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        output_queue.put({"kind": "missing"})
        output_queue.put({"kind": "done"})
        return

    try:
        model = WhisperModel(
            config["model_path"],
            device=config["device"],
            compute_type=config["compute_type"],
            cpu_threads=config["cpu_threads"],
            num_workers=config["num_workers"],
        )
    except Exception as exc:
        output_queue.put({"kind": "error", "message": str(exc)})
        output_queue.put({"kind": "done"})
        return

    try:
        while True:
            job = input_queue.get()
            if job is None:
                break

            audio = job.get("audio")
            if audio is None or getattr(audio, "size", 0) == 0:
                continue

            try:
                transcribe_options = {
                    "beam_size": int(job.get("beam_size", 1)),
                    "language": config["language"],
                    "vad_filter": True,
                    "vad_parameters": config["vad_parameters"],
                    "condition_on_previous_text": bool(config.get("condition_on_previous_text", False)),
                }
                if config.get("initial_prompt"):
                    transcribe_options["initial_prompt"] = str(config["initial_prompt"])
                segments, _info = model.transcribe(audio, **transcribe_options)
                text = "".join(segment.text for segment in segments).strip()
                if text and text_converter is not None:
                    text = text_converter.convert(text).strip()
            except Exception as exc:
                output_queue.put({"kind": "error", "message": str(exc)})
                continue

            if text:
                output_queue.put(
                    {
                        "kind": "realtime",
                        "source_name": job.get("source_name", "source_speaker"),
                        "text": text,
                    }
                )
    finally:
        with contextlib.suppress(Exception):
            del model
        gc.collect()
        output_queue.put({"kind": "done"})
