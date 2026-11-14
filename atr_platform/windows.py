"""Windows-specific audio discovery and microphone activity detection."""
from __future__ import annotations
import contextlib
import os
import sys
import warnings

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



def capture_sources(microphone_mode, stop_event):
    import soundcard as sc
    speaker = sc.default_speaker()
    sources = [("source_speaker", sc.get_microphone(speaker.name, include_loopback=True))]
    if microphone_mode == "on" or (microphone_mode == "auto" and is_microphone_in_use_by_other_app()):
        with contextlib.suppress(Exception):
            sources.append(("source_microphone", sc.default_microphone()))
    return sources
