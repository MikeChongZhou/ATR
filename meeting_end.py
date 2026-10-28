"""Conservative end-of-meeting reminder policy; never stops a recording itself."""
from dataclasses import dataclass


@dataclass
class MeetingEndPolicy:
    started_at: float
    silence_seconds: float = 180
    microphone_release_seconds: float = 60
    reminder_seconds: float = 600

    def __post_init__(self):
        self.last_speech_at = self.started_at
        self.last_audio_at = self.started_at
        self.microphone_seen = False
        self.microphone_released_at = None
        self.snoozed_until = 0
        self.prompt_open = False

    def observe_audio(self, now, has_speech):
        if now - self.last_audio_at > 5:
            self.last_speech_at = now  # Missing audio is not evidence of silence.
        self.last_audio_at = now
        if has_speech:
            self.last_speech_at = now

    def observe_microphone(self, now, active):
        if active:
            self.microphone_seen = True
            self.microphone_released_at = None
        elif self.microphone_seen and self.microphone_released_at is None:
            self.microphone_released_at = now

    def reason(self, now):
        if self.prompt_open or now < self.snoozed_until or now - self.last_audio_at > 5:
            return None
        silence = now - self.last_speech_at
        if silence >= self.silence_seconds:
            return "silence"
        release = self.microphone_released_at
        if release is not None and now - release >= self.microphone_release_seconds and silence >= self.microphone_release_seconds:
            return "microphone"
        return None

    def snooze(self, now, seconds=None):
        self.prompt_open = False
        self.snoozed_until = now + (self.reminder_seconds if seconds is None else seconds)
