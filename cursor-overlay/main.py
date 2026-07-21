"""
Entry point — single-instance guard, logging setup, then launch App.

  python main.py          (console, shows logs)
  pythonw main.py         (no console window)
"""
import sys, os, logging

# ── Logging ────────────────────────────────────────────────────────────────────
_BASE = os.path.dirname(os.path.abspath(__file__))
_LOG  = os.path.join(_BASE, "overlay.log")
logging.basicConfig(filename=_LOG, level=logging.DEBUG,
                    format="%(asctime)s %(levelname)s %(message)s", filemode="w")

# Silence noisy HTTP-level debug logs from openai/httpx
for _noisy in ("httpx", "httpcore", "openai._base_client", "openai.http_client",
               "websockets", "websockets.server", "asyncio"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

# ── Single-instance mutex ──────────────────────────────────────────────────────
from constants import kernel32
_MUTEX = kernel32.CreateMutexW(None, True, "CursorOverlay_SingleInstance")
if kernel32.GetLastError() == 183:          # ERROR_ALREADY_EXISTS
    sys.exit(0)

# Windows groups/caches taskbar icons by process identity; a plain pythonw.exe
# process is otherwise treated as "just Python" and can show a generic/cached
# icon instead of a window's own setWindowIcon(), even when that's set
# correctly. Declaring an explicit AppUserModelID tells Windows this process is
# its own distinct app, BEFORE any window is created (must run early, once).
if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "BeaconBandhu.BashIn.Overlay")
    except Exception:
        pass

# ── Run ────────────────────────────────────────────────────────────────────────
from app import App

if __name__ == "__main__":
    App(mutex=_MUTEX).run()
