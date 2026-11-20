"""Best-effort visible meeting-window discovery; never infer an app from PCM."""
from dataclasses import dataclass
import re
import sys

@dataclass(frozen=True)
class Meeting:
    title: str
    app: str
    pid: int = 0
    active: bool = False

    @property
    def label(self):
        return f'{self.title or "Unnamed meeting"} [{self.app}]'


def safe_name(value, limit=100):
    value=re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', '_', str(value))
    value=re.sub(r'\s+', ' ', value).strip(' ._')
    # Truncate by UTF-8 bytes so Chinese titles fit filesystem component limits.
    value=value.encode('utf-8')[:limit].decode('utf-8',errors='ignore').rstrip(' .')
    if re.fullmatch(r'(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?',value):value='_'+value
    return value


def meeting_stem(timestamp, title='', app=''):
    return '_'.join([timestamp, safe_name(title) or 'Unnamed meeting'] + ([safe_name(app,40)] if safe_name(app,40) else []))


def classify(owner, title):
    owner=owner.lower()
    patterns=[('Teams',r'teams|msteams'),('Zoom',r'zoom'),('Feishu',r'feishu|lark|飞书'),
              ('VooV',r'voov|tencentmeeting|腾讯会议')]
    browser=any(x in owner for x in ['chrome','edge','safari','firefox','brave','arc'])
    if browser:
        patterns=[('Google Meet',r'google meet|meet\.google\.com|\bmeet\s*[-–—:]'),
                  ('Teams',r'microsoft teams|teams\.microsoft'),('Zoom',r'zoom meeting|zoom\.us'),
                  ('Feishu',r'feishu|lark meeting|飞书会议'),('VooV',r'voov|腾讯会议')]
        found=next((app for app,p in patterns if re.search(p,title,re.I)),None)
    else:found=next((app for app,p in patterns if re.search(p,owner)),None)
    if not found:return None
    cleaned=re.sub(r'\s*[-–—|]\s*(Google Chrome|Microsoft Edge|Safari|Mozilla Firefox|Brave|Arc)\s*$','',title,flags=re.I)
    cleaned=re.sub(r'^\s*(Google Meet|Meet|Microsoft Teams|Zoom Meeting|飞书会议|腾讯会议)\s*[-–—:|]\s*','',cleaned,flags=re.I)
    cleaned=re.sub(r'\s*[-–—|]\s*(Microsoft Teams|Zoom Meeting|Zoom Workplace|Google Meet|飞书会议|腾讯会议)\s*$','',cleaned,flags=re.I).strip()
    if cleaned.lower() in ['zoom','zoom workplace','microsoft teams','teams','feishu','lark','飞书','腾讯会议','voov meeting','google meet']:
        cleaned=''
    return found,cleaned


def visible_windows():
    if sys.platform=='darwin':
        import Quartz
        from AppKit import NSWorkspace
        front=NSWorkspace.sharedWorkspace().frontmostApplication().processIdentifier()
        windows=Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionOnScreenOnly|Quartz.kCGWindowListExcludeDesktopElements,Quartz.kCGNullWindowID)
        for w in windows:
            if int(w.get(Quartz.kCGWindowLayer,0))==0:
                pid=int(w.get(Quartz.kCGWindowOwnerPID,0))
                yield str(w.get(Quartz.kCGWindowOwnerName,'')),str(w.get(Quartz.kCGWindowName,'') or ''),pid,pid==front
    elif sys.platform=='win32':
        import ctypes
        from ctypes import wintypes
        import psutil
        user=ctypes.windll.user32
        # HWND is pointer-sized; explicit declarations are necessary on Win64.
        user.GetForegroundWindow.restype=wintypes.HWND
        user.GetWindowTextLengthW.argtypes=[wintypes.HWND]
        user.GetWindowTextW.argtypes=[wintypes.HWND,wintypes.LPWSTR,ctypes.c_int]
        user.IsWindowVisible.argtypes=[wintypes.HWND]
        user.GetWindowThreadProcessId.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.DWORD)]
        front=user.GetForegroundWindow();rows=[];active_pids=set()
        try:
            from pycaw.pycaw import AudioUtilities
            for session in AudioUtilities.GetAllSessions():
                if session.State==1 and session.Process:active_pids.add(session.Process.pid)
        except Exception:pass
        callback_type=ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,wintypes.LPARAM)
        def collect(hwnd,_):
            if user.IsWindowVisible(hwnd):
                length=user.GetWindowTextLengthW(hwnd)
                if length:
                    buf=ctypes.create_unicode_buffer(length+1);user.GetWindowTextW(hwnd,buf,length+1)
                    pid=wintypes.DWORD();user.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
                    try:owner=psutil.Process(pid.value).name()
                    except psutil.Error:owner=''
                    rows.append((owner,buf.value,pid.value,hwnd==front or pid.value in active_pids))
            return True
        user.EnumWindows.argtypes=[callback_type,wintypes.LPARAM]
        user.EnumWindows(callback_type(collect),0)
        yield from rows


def detect_meetings():
    candidates=[]
    try:
        for owner,title,pid,active in visible_windows():
            match=classify(owner,title)
            if match:
                item=Meeting(match[1],match[0],pid,active)
                if (item.title,item.app) not in [(x.title,x.app) for x in candidates]:candidates.append(item)
    except Exception:
        # Missing window-title permission must never block recording.
        return []
    return sorted(candidates,key=lambda x:(not bool(x.title),not x.active,x.app,x.title))
