"""Durable meeting-summary history and resumable chunk checkpoints."""
from __future__ import annotations

import copy
import json
import threading
import uuid
import logging
from pathlib import Path


class SummaryQueueStore:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.RLock()
        self.records = []
        self.page_capacity = 5  # Used until the queue view measures its viewport.
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                self.records = data["meetings"]
                if not isinstance(self.records, list):
                    raise ValueError("Invalid summary queue")
                required = {"id", "text_path", "transcript", "file_stem", "settings", "status", "progress", "detail", "chunks"}
                if any(not isinstance(r, dict) or not required.issubset(r) for r in self.records):
                    raise ValueError("Invalid summary queue record")
            except (ValueError, KeyError):
                self.records = []
                backup = path.with_name(path.name + ".corrupt-" + uuid.uuid4().hex)
                path.rename(backup)
                logging.exception("Invalid summary queue preserved at %s", backup)
            for record in self.records:
                if record.get("awaiting_transcript"):
                    record["status"] = "failed"
                    record["detail"] = "Transcription interrupted. Re-import the audio in Offline Transcript."
                    continue
                if record["status"] not in {"completed", "failed", "skipped"}:
                    record["status"] = "queued"
                    record["detail"] = "Waiting to resume"

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"meetings": self.records}, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.records)

    def set_page_capacity(self, capacity):
        with self.lock:
            self.page_capacity = max(1, int(capacity))

    def add(self, text_path, transcript, file_stem, settings, awaiting_transcript=False):
        with self.lock:
            for record in self.records:
                if record["text_path"] == str(text_path) and record["transcript"] == transcript:
                    return copy.deepcopy(record), False
            record = dict(id=uuid.uuid4().hex, text_path=str(text_path), transcript=transcript,
                          file_stem=file_stem, settings=copy.deepcopy(settings), status="queued",
                          progress=0, detail="Waiting for resources", chunks=[], awaiting_transcript=awaiting_transcript)
            if awaiting_transcript:
                record.update(status="transcribing", detail="等待转录完成 / Waiting for transcription to finish")
            if len(self.records) >= self.page_capacity:
                removable = {r["id"] for r in self.records
                             if r["status"] in {"completed", "skipped"}}
                oldest = [r["id"] for r in self.records if r["id"] in removable][:2]
                self.records = [r for r in self.records if r["id"] not in oldest]
            self.records.append(record)
            self._save()
            return copy.deepcopy(record), True

    def finish_transcript(self, text_path, transcript):
        """Release an existing placeholder only after its final text was saved."""
        with self.lock:
            record = next((r for r in self.records if r["text_path"] == str(text_path)
                           and r.get("awaiting_transcript")), None)
            if record is None:
                return None
            record.update(transcript=transcript, awaiting_transcript=False, chunks=[], progress=0,
                          status="queued" if transcript else "skipped",
                          detail="Waiting for resources" if transcript else "No speech recognized; summary not needed")
            self._save()
            return copy.deepcopy(record)

    def update(self, identity, **values):
        with self.lock:
            record = next(r for r in self.records if r["id"] == identity)
            if all(record.get(key) == value for key, value in values.items()):
                return
            record.update(copy.deepcopy(values))
            self._save()

    @property
    def pending_count(self):
        with self.lock:
            return sum(r["status"] not in {"completed", "skipped"} for r in self.records)
