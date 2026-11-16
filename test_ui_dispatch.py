"""Regression for Cocoa tray callbacks re-entering Tcl without a thread state."""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock
from ui_dispatch import UiDispatcher


class DispatcherTests(unittest.TestCase):
    def test_post_never_calls_tk_and_executes_on_tk_timer(self):
        root = Mock()
        dispatch = UiDispatcher(root)
        action = Mock()
        dispatch.post(action, "settings")
        root.after.assert_not_called()
        action.assert_not_called()
        dispatch.start()
        timer_callback = root.after.call_args.args[1]
        timer_callback()
        action.assert_called_once_with("settings")
        dispatch.stop()
        dispatch.post(action, "ignored")
        dispatch._drain()
        action.assert_called_once()

    def test_one_callback_error_does_not_drop_later_actions(self):
        root = Mock()
        dispatch = UiDispatcher(root)
        action = Mock()
        dispatch.post(Mock(side_effect=ValueError("bad action")))
        dispatch.post(action)
        dispatch._drain()
        root.report_callback_exception.assert_called_once()
        action.assert_called_once()
        dispatch.stop()


@unittest.skipUnless(sys.platform == "darwin", "Cocoa/Tk regression")
class NativeMenuTests(unittest.TestCase):
    def test_native_settings_queue_about_and_exit_callbacks_do_not_abort(self):
        # A subprocess is essential: the original bug SIGABRTs Python and cannot
        # be caught by unittest. Native timers enter through Cocoa, rather than
        # invoking the menu handler from a Tk callback (which misses the bug).
        script = r'''
import json, os, threading, tkinter as tk
import faulthandler
faulthandler.enable()
import main
from Foundation import NSTimer
root = tk.Tk()
app = main.MeetingRecorderApp(root)
errors = []
root.report_callback_exception = lambda *error: errors.append(str(error[1]))
observed = []
timers = []
steps = [(5, 'settings'), (4, 'offline'), (3, 'recording'), (8, 'about'), (1, 'queue'), (5, 'settings')]

def schedule(index):
    def click(timer):
        menu_index, expected = steps[index]
        print("NATIVE", expected, flush=True)
        app.icon._menu_handle[0].performActionForItemAtIndex_(menu_index)
    timers.append(NSTimer.scheduledTimerWithTimeInterval_repeats_block_(.2, False, click))
    root.after(500, lambda: check(index))

def check(index):
    expected = steps[index][1]
    actual = app.main_notebook.select() if app.main_notebook else None
    if actual != str(app.main_tabs.get(expected)):
        errors.append('Wrong tab: ' + expected)
    observed.append(expected)
    if index + 1 < len(steps):
        schedule(index + 1)
    else:
        timers.append(NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            .1, False, lambda timer: app.icon._menu_handle[0].performActionForItemAtIndex_(9)))

watchdog = threading.Timer(15, lambda: os._exit(124))
watchdog.daemon = True
watchdog.start()
schedule(0)
app.run()
watchdog.cancel()
assert not errors, errors
assert len(observed) == len(steps), observed
print("RESULT=" + json.dumps(observed), flush=True)
'''
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, "-c", script],
                                    cwd=Path(__file__).resolve().parent,
                                    env={**os.environ, "ATR_DATA_DIR": directory, "ATR_DISABLE_AUTO_SETUP": "1"},
                                    capture_output=True, text=True, timeout=25)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout.split("RESULT=")[-1].strip()),
                         ["settings", "offline", "recording", "about", "queue", "settings"])


if __name__ == "__main__":
    unittest.main()
