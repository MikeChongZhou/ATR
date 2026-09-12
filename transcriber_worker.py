from __future__ import annotations

import gc
from pathlib import Path
from typing import Any


def usable_model_dir(path: Path) -> bool:
    return path.exists() and (path / "config.json").exists() and (path / "model.bin").exists()


def resolve_model_path(config: dict[str, Any]) -> str:
    local_model_dir = Path(str(config["local_model_dir"]))
    fallback_model_dir = Path(str(config["fallback_model_dir"]))
    if usable_model_dir(local_model_dir):
        return str(local_model_dir)
    if usable_model_dir(fallback_model_dir):
        return str(fallback_model_dir)
    return str(config["model"])


def drain_until_done(input_queue: Any) -> None:
    while True:
        item = input_queue.get()
        if item is None:
            return


def run_transcriber_process(
    input_queue: Any,
    output_queue: Any,
    config: dict[str, Any],
    cancel_realtime_event: Any = None,
) -> None:
    model = None
    try:
        from faster_whisper import WhisperModel

        model = WhisperModel(
            resolve_model_path(config),
            device=config["device"],
            compute_type=config["compute_type"],
            cpu_threads=int(config["cpu_threads"]),
            num_workers=int(config["num_workers"]),
        )
    except ImportError as exc:
        output_queue.put({"kind": "missing", "message": str(exc)})
        drain_until_done(input_queue)
        output_queue.put({"kind": "done"})
        return
    except Exception as exc:
        output_queue.put({"kind": "error", "message": str(exc)})
        drain_until_done(input_queue)
        output_queue.put({"kind": "done"})
        return

    try:
        while True:
            job = input_queue.get()
            if job is None:
                break

            kind = job.get("kind")
            if kind == "realtime" and cancel_realtime_event is not None and cancel_realtime_event.is_set():
                continue
            audio = job.get("audio")
            if audio is None or getattr(audio, "size", 0) == 0:
                continue

            try:
                segments, _info = model.transcribe(
                    audio,
                    beam_size=int(job["beam_size"]),
                    language=config["language"],
                    vad_filter=True,
                    vad_parameters=config["vad_parameters"],
                    condition_on_previous_text=bool(job["condition_on_previous_text"]),
                )
                if kind == "realtime":
                    text = "".join(segment.text for segment in segments).strip()
                    output_queue.put(
                        {
                            "kind": "realtime",
                            "source_name": job["source_name"],
                            "start": float(job.get("start", 0.0)),
                            "end": float(job.get("end", job.get("start", 0.0))),
                            "text": text,
                        }
                    )
                elif kind == "review":
                    start = float(job["start"])
                    end = float(job.get("end", start))
                    entries = [
                        (start + float(segment.start), start + float(segment.end), segment.text.strip())
                        for segment in segments
                        if segment.text.strip()
                    ]
                    output_queue.put(
                        {
                            "kind": "review",
                            "source_name": job["source_name"],
                            "start": start,
                            "end": end,
                            "entries": entries,
                        }
                    )
            except Exception as exc:
                output_queue.put({"kind": "error", "message": str(exc)})
    finally:
        model = None
        gc.collect()
        output_queue.put({"kind": "done"})
