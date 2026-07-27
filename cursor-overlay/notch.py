"""
notch.py -- MacBook-style island notch, rendered as real HTML/CSS/JS
(notch_ui/) inside a QWebEngineView, not hand-painted QPainter graphics. The
native Qt window still owns: exact-fit positioning at top-center, real Windows
acrylic backdrop blur (the same verified SetWindowCompositionAttribute call as
the first build), and always-on-top/frameless/translucent flags. Everything
else -- fonts, icons, layout, and the expand/collapse animation itself -- is
real CSS/JS running in Chromium.

Why not CSS backdrop-filter for the blur itself: it can only blur OTHER LAYERS
WITHIN THE SAME PAGE, not whatever is behind the OS-level transparent window
(the user's real desktop). So the page background stays fully transparent and
the actual blur still comes from the native DWM acrylic call on the outer
window; CSS supplies only a semi-transparent tint, border, shadow, and the
text/icon layer drawn on top of that OS-level blur.

Python <-> JS bridge: deliberately NOT QWebChannel -- no qwebchannel.js loose
file exists on disk in this PyQt6-WebEngine install to sanity-check ahead of
time (it's compiled into a Qt binary resource, exposed via qrc:// only if
Qt's own auto-registration works, which is one more untested assumption).
Instead: Python pushes data into the page via
page().runJavaScript("window.bashinUpdate(...)"), and JS notifies Python of
actions via console.log("BASHIN_ACTION:...") messages, caught by overriding
QWebEnginePage.javaScriptConsoleMessage. Every hop here was verified directly
against this Qt/PyQt6-WebEngine install rather than assumed.

The native-window resize is now driven by two clean, one-shot triggers instead
of a QPropertyAnimation + signal-reconnection chain (the actual cause of the
original "stuck panel" bug): grow immediately on expand (before the CSS
animation needs the room), shrink back only after the browser's own
transitionend event confirms the collapse animation has genuinely finished.
"""
import ctypes, json, logging, os, threading
from datetime import datetime

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QApplication
from PyQt6.QtCore    import Qt, QRect, QTimer, QUrl
from PyQt6.QtGui     import QColor
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEnginePage

import lan_mesh
import status_state

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UI_DIR   = os.path.join(BASE_DIR, "notch_ui")

PEEK_W, PEEK_H = 190, 50
OPEN_W, OPEN_H = 420, 590


# ── real Windows acrylic blur-behind (unchanged from the first build, already
# verified via a real screen-level screenshot, not just a Qt-level grab) ──────
class _ACCENT_POLICY(ctypes.Structure):
    _fields_ = [
        ("AccentState", ctypes.c_int),
        ("AccentFlags", ctypes.c_int),
        ("GradientColor", ctypes.c_uint),
        ("AnimationId", ctypes.c_int),
    ]


class _WINDOWCOMPOSITIONATTRIBDATA(ctypes.Structure):
    _fields_ = [
        ("Attribute", ctypes.c_int),
        ("Data", ctypes.POINTER(_ACCENT_POLICY)),
        ("SizeOfData", ctypes.c_size_t),
    ]


def _enable_acrylic_blur(hwnd: int, tint_rgba=(14, 15, 20, 55)) -> bool:
    """Best-effort real backdrop blur. Never raises -- the panel still looks
    fine as a plain translucent glass panel without it."""
    try:
        ACCENT_ENABLE_ACRYLICBLURBEHIND = 4
        WCA_ACCENT_POLICY = 19
        r, g, b, a = tint_rgba
        gradient_color = (a << 24) | (b << 16) | (g << 8) | r
        accent = _ACCENT_POLICY()
        accent.AccentState = ACCENT_ENABLE_ACRYLICBLURBEHIND
        accent.AccentFlags = 2
        accent.GradientColor = gradient_color
        accent.AnimationId = 0
        data = _WINDOWCOMPOSITIONATTRIBDATA()
        data.Attribute = WCA_ACCENT_POLICY
        data.Data = ctypes.pointer(accent)
        data.SizeOfData = ctypes.sizeof(accent)
        return bool(ctypes.windll.user32.SetWindowCompositionAttribute(hwnd, ctypes.pointer(data)))
    except Exception as e:
        logging.debug("notch: acrylic blur unavailable: %s", e)
        return False


# ── background poller for "now playing" (own thread, own asyncio loop -- same
# dedicated-thread pattern as lan_mesh.py/chrome_bridge.py, so a slow/stalled
# WinRT call never blocks the Qt UI thread) ────────────────────────────────────
class MediaSessionPoller:
    def __init__(self, interval: float = 2.0):
        self._interval = interval
        self._lock = threading.Lock()
        self._info = {"title": "", "artist": "", "playing": False, "available": False}
        self._thread = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name="MediaSessionPoller")
        self._thread.start()

    def _run(self):
        import asyncio
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self._poll_forever())

    async def _poll_forever(self):
        import asyncio
        while True:
            try:
                info = await self._read_once()
            except Exception as e:
                logging.debug("notch: media session read failed: %s", e)
                info = {"title": "", "artist": "", "playing": False, "available": False}
            with self._lock:
                self._info = info
            await asyncio.sleep(self._interval)

    async def _read_once(self):
        from winsdk.windows.media.control import (
            GlobalSystemMediaTransportControlsSessionManager as MediaManager,
        )
        mgr = await MediaManager.request_async()
        session = mgr.get_current_session()
        if session is None:
            return {"title": "", "artist": "", "playing": False, "available": False}
        props = await session.try_get_media_properties_async()
        playback = session.get_playback_info()
        return {
            "title": props.title or "", "artist": props.artist or "",
            "playing": playback.playback_status == 4, "available": True,
        }

    def get_info(self) -> dict:
        with self._lock:
            return dict(self._info)


MEDIA = MediaSessionPoller()


class _NotchPage(QWebEnginePage):
    """Catches JS console.log("BASHIN_ACTION:xxx") messages -- the JS->Python
    half of the bridge (see module docstring for why not QWebChannel)."""
    def __init__(self, parent, on_action):
        super().__init__(parent)
        self._on_action = on_action

    def javaScriptConsoleMessage(self, level, message, lineNumber, sourceID):
        if message.startswith("BASHIN_ACTION:"):
            try:
                self._on_action(message[len("BASHIN_ACTION:"):])
            except Exception as e:
                logging.error("notch: action handler failed: %s", e)


class NotchWidget(QWidget):
    """get_conv_state: callable -> str ('IDLE'/'LISTENING'/'PROCESSING'/'SPEAKING'/'GUIDING')
    on_open_dashboard / on_pair_new_device / on_enter_pairing_code: callables,
    reusing the SAME dialogs the tray menu already uses (no duplicated UI logic)."""

    def __init__(self, get_conv_state, on_open_dashboard, on_pair_new_device, on_enter_pairing_code):
        super().__init__()
        self._get_conv_state = get_conv_state
        self._callbacks = {
            "open_dashboard":      on_open_dashboard,
            "pair_new_device":     on_pair_new_device,
            "enter_pairing_code":  on_enter_pairing_code,
        }
        self._open = False
        self._blur_applied = False

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.view = QWebEngineView()
        self.view.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        page = _NotchPage(self.view, self._handle_action)
        page.setBackgroundColor(QColor(0, 0, 0, 0))   # transparent page -> real DWM blur shows through
        self.view.setPage(page)
        layout.addWidget(self.view)

        self.view.load(QUrl.fromLocalFile(os.path.join(UI_DIR, "index.html")))
        self._place_peek()

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(1000)
        self._refresh_timer.timeout.connect(self._push_update)
        self._refresh_timer.start()

        MEDIA.start()

    # ── positioning ──────────────────────────────────────────────────────────
    def _screen_center_x(self, width):
        screen = QApplication.primaryScreen().geometry()
        return screen.x() + (screen.width() - width) // 2

    def _place_peek(self):
        x = self._screen_center_x(PEEK_W)
        self.setGeometry(QRect(x, 0, PEEK_W, PEEK_H))

    def _place_open(self):
        x = self._screen_center_x(OPEN_W)
        self.setGeometry(QRect(x, 0, OPEN_W, OPEN_H))

    # ── JS -> Python action bridge ───────────────────────────────────────────
    def _handle_action(self, action: str):
        if action == "will_expand":
            self._open = True
            self._place_open()        # grow the native window FIRST so the CSS animation has room
            self._push_update()       # populate the panel immediately, don't wait for the 1s tick
        elif action == "did_collapse":
            self._open = False
            self._place_peek()        # shrink back ONLY after the browser confirms the fade-out finished
        elif action in self._callbacks:
            self._callbacks[action]()

    # ── real Windows acrylic blur ────────────────────────────────────────────
    def showEvent(self, e):
        super().showEvent(e)
        if not self._blur_applied:
            self._blur_applied = _enable_acrylic_blur(int(self.winId()))

    # ── live data push (Python -> JS) ────────────────────────────────────────
    def _push_update(self):
        now = datetime.now()
        payload = {
            "time": now.strftime("%I:%M %p").lstrip("0"),
            "date": now.strftime("%a, %b %d").replace(" 0", " "),
        }
        if self._open:
            payload["media"] = MEDIA.get_info()
            payload["bashin_state"] = (self._get_conv_state() or "IDLE").upper()
            payload["task"] = status_state.get_status()
            devices = [d for d in lan_mesh.MESH.list_devices() if d.get("paired")]
            payload["devices"] = [{"name": d["name"], "online": bool(d.get("ip"))} for d in devices]
        try:
            self.view.page().runJavaScript(
                f"window.bashinUpdate && window.bashinUpdate({json.dumps(payload)})")
        except Exception as e:
            logging.debug("notch: push update failed: %s", e)


_window = None

def open_notch(get_conv_state, on_open_dashboard, on_pair_new_device, on_enter_pairing_code):
    """Singleton, mirroring dashboard.py's open_dashboard() convention."""
    global _window
    if _window is None:
        _window = NotchWidget(get_conv_state, on_open_dashboard, on_pair_new_device, on_enter_pairing_code)
    _window.show()
    return _window
