import unittest
import time
from unittest.mock import Mock
from meeting_end import MeetingEndPolicy


class MeetingEndTests(unittest.TestCase):
    def silence(self, policy, until):
        for now in range(int(policy.last_audio_at) + 1, until + 1):
            policy.observe_audio(now, False)

    def test_silence_and_speech_reset(self):
        policy = MeetingEndPolicy(0)
        self.silence(policy, 179)
        self.assertIsNone(policy.reason(179))
        policy.observe_audio(180, True)
        self.silence(policy, 359)
        self.assertIsNone(policy.reason(359))
        policy.observe_audio(360, False)
        self.assertEqual(policy.reason(360), "silence")

    def test_microphone_release_requires_silence(self):
        policy = MeetingEndPolicy(0)
        policy.observe_microphone(0, True)
        policy.observe_microphone(1, False)
        self.silence(policy, 61)
        self.assertEqual(policy.reason(61), "microphone")
        policy.observe_audio(62, True)
        self.assertIsNone(policy.reason(62))

    def test_listen_only_meeting_does_not_trigger_mic_release(self):
        policy = MeetingEndPolicy(0)
        policy.observe_microphone(1, False)
        self.silence(policy, 90)
        self.assertIsNone(policy.reason(90))

    def test_audio_gap_is_not_silence(self):
        policy = MeetingEndPolicy(0)
        self.assertIsNone(policy.reason(200))
        policy.observe_audio(200, False)
        self.assertIsNone(policy.reason(200))

    def test_snooze_and_duplicate_dialog_suppression(self):
        policy = MeetingEndPolicy(0)
        self.silence(policy, 180)
        self.assertEqual(policy.reason(180), "silence")
        policy.prompt_open = True
        self.assertIsNone(policy.reason(180))
        policy.snooze(180)
        self.silence(policy, 779)
        self.assertIsNone(policy.reason(779))
        policy.observe_audio(780, False)
        self.assertEqual(policy.reason(780), "silence")

    def test_microphone_resumed_cancels_release_signal(self):
        policy = MeetingEndPolicy(0)
        policy.observe_microphone(0, True)
        policy.observe_microphone(1, False)
        policy.observe_microphone(30, True)
        self.silence(policy, 90)
        self.assertIsNone(policy.reason(90))

    def test_dialog_requires_explicit_stop_and_continue_snoozes(self):
        import tkinter as tk
        from tkinter import ttk
        import main
        root = tk.Tk()
        root.withdraw()
        try:
            app = main.MeetingRecorderApp.__new__(main.MeetingRecorderApp)
            app.root = root
            app.state = "recording"
            app.shutting_down = False
            app.stop_recording = Mock()
            session = main.RecordingSession("test")
            app.session = session
            now = time.monotonic()
            policy = MeetingEndPolicy(now - 200)
            policy.last_audio_at = now
            session.meeting_end_policy = policy

            def buttons(widget):
                return [child for child in widget.winfo_children() if isinstance(child, ttk.Button)] + [
                    button for child in widget.winfo_children() for button in buttons(child)]

            app.show_meeting_end_reminder(session, "silence")
            app.stop_recording.assert_not_called()
            next(button for button in buttons(session.meeting_end_dialog) if "Continue" in button.cget("text")).invoke()
            app.stop_recording.assert_not_called()
            self.assertIsNone(session.meeting_end_dialog)
            self.assertGreater(policy.snoozed_until, now + 590)
            policy.snoozed_until = 0
            policy.last_audio_at = time.monotonic()
            app.show_meeting_end_reminder(session, "silence")
            next(button for button in buttons(session.meeting_end_dialog) if "Stop & save" in button.cget("text")).invoke()
            app.stop_recording.assert_called_once()
        finally:
            for callback in root.tk.splitlist(root.tk.call("after", "info")):
                root.tk.call("after", "cancel", callback)
            root.destroy()


if __name__ == "__main__":
    unittest.main()
