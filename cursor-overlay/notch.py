"""
notch.py -- a MacBook-style "island notch" control panel for BashIn: a small
pill hangs from the top-center of the screen; click it and it drops down into
a full Liquid-Glass panel showing the date/time, Spotify now-playing, whether
BashIn is active, its current task, paired devices, and quick actions
(Dashboard / Pair New Device / Enter Pairing Code).

Windows-only (uses the undocumented DWM acrylic blur-behind API and the
WinRT System Media Transport Controls for now-playing) -- callers on other
platforms simply don't construct NotchWidget.

Two things verified live before building this, not assumed:
  - SetWindowCompositionAttribute(ACCENT_ENABLE_ACRYLICBLURBEHIND) produces a
    REAL blur of whatever is behind the window (confirmed via a screen-level
    screenshot, not just a Qt-level grab), on this machine/Windows build.
  - winsdk's GlobalSystemMediaTransportControlsSessionManager reads Spotify's
    (or any app's) now-playing title/artist/playback state with zero OAuth,
    zero API keys -- confirmed against a real, currently-playing Spotify track.
"""
import ctypes, logging, threading, time
from datetime import datetime

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame, QApplication,
)
from PyQt6.QtCore    import Qt, QTimer, QRect, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui      import QPainter, QPainterPath, QColor, QPen, QBrush, QFont

import lan_mesh
import status_state

_MONO = "Consolas"
_SANS = "Segoe UI"

PEEK_W, PEEK_H   = 170, 22
OPEN_W, OPEN_H   = 400, 470
CORNER_R         = 22
ANIM_MS          = 300

GLASS_FILL   = QColor(18, 20, 28, 165)
GLASS_BORDER = QColor(255, 255, 255, 40)
GLASS_SHEEN  = QColor(255, 255, 255, 22)
TEXT_MAIN    = QColor(235, 238, 245)
TEXT_DIM     = QColor(150, 156, 175)
DOT_GREEN    = QColor(58, 220, 140)
DOT_AMBER    = QColor(240, 180, 70)
DOT_GRAY     = QColor(120, 124, 138)


# ── real Windows acrylic blur-behind (verified via probe) ─────────────────────
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


def _enable_acrylic_blur(hwnd: int, tint_rgba=(18, 20, 28, 130)) -> bool:
    """Best-effort real backdrop blur. Returns False (never raises) if the
    undocumented API isn't available on this Windows build -- the panel still
    looks fine as a plain translucent glass panel without it."""
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
        res = ctypes.windll.user32.SetWindowCompositionAttribute(hwnd, ctypes.pointer(data))
        return bool(res)
    except Exception as e:
        logging.debug("notch: acrylic blur unavailable: %s", e)
        return False


# ── background poller for "now playing" (own thread, own asyncio loop -- ─────
# same dedicated-thread pattern as lan_mesh.py/chrome_bridge.py, so a slow/
# stalled WinRT call never blocks the Qt UI thread) ────────────────────────────
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


def _row(label_text: str, value_widget=None):
    row = QWidget()
    row.setStyleSheet("background: transparent;")
    h = QHBoxLayout(row)
    h.setContentsMargins(0, 0, 0, 0)
    lbl = QLabel(label_text)
    lbl.setStyleSheet(f"color: #969cAf; font-family: '{_SANS}'; font-size: 9pt; background: transparent;")
    h.addWidget(lbl)
    h.addStretch(1)
    if value_widget is not None:
        h.addWidget(value_widget)
    return row


class NotchWidget(QWidget):
    """get_conv_state: callable -> str ('IDLE'/'LISTENING'/'PROCESSING'/'SPEAKING'/'GUIDING')
    on_open_dashboard / on_pair_new_device / on_enter_pairing_code: callables,
    reusing the SAME dialogs the tray menu already uses (no duplicated UI logic)."""

    def __init__(self, get_conv_state, on_open_dashboard, on_pair_new_device, on_enter_pairing_code):
        super().__init__()
        self._get_conv_state = get_conv_state
        self._on_open_dashboard = on_open_dashboard
        self._on_pair_new_device = on_pair_new_device
        self._on_enter_pairing_code = on_enter_pairing_code
        self._open = False
        self._blur_applied = False

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        self._build_ui()
        self._place_peek()

        self._anim = QPropertyAnimation(self, b"geometry")
        self._anim.setDuration(ANIM_MS)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(1000)
        self._refresh_timer.timeout.connect(self._refresh)
        self._refresh_timer.start()

        MEDIA.start()
        self._refresh()

    # ── layout ───────────────────────────────────────────────────────────────
    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 6, 18, 14)
        outer.setSpacing(0)

        self._peek_label = QLabel("")
        self._peek_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._peek_label.setStyleSheet(
            f"color: #d8dcea; font-family: '{_MONO}'; font-size: 9pt; background: transparent;")
        self._peek_label.setFixedHeight(PEEK_H - 6)
        # Let clicks fall through to NotchWidget.mousePressEvent (a QLabel would
        # otherwise silently swallow the click and "tap to open" wouldn't fire).
        self._peek_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        outer.addWidget(self._peek_label)

        self._content = QWidget()
        self._content.setStyleSheet("background: transparent;")
        cv = QVBoxLayout(self._content)
        cv.setContentsMargins(4, 6, 4, 0)
        cv.setSpacing(10)

        self.datetime_label = QLabel("")
        self.datetime_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.datetime_label.setStyleSheet(
            f"color: {TEXT_MAIN.name()}; font-family: '{_SANS}'; font-size: 15pt; "
            f"font-weight: 600; background: transparent;")
        self.datetime_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        cv.addWidget(self.datetime_label)
        cv.addWidget(self._divider())

        cv.addWidget(self._section_header("NOW PLAYING"))
        self.song_label = QLabel("Nothing playing")
        self.song_label.setStyleSheet(
            f"color: {TEXT_MAIN.name()}; font-family: '{_SANS}'; font-size: 11pt; "
            f"font-weight: 600; background: transparent;")
        self.song_label.setWordWrap(True)
        self.artist_label = QLabel("")
        self.artist_label.setStyleSheet(
            f"color: {TEXT_DIM.name()}; font-family: '{_SANS}'; font-size: 9pt; background: transparent;")
        cv.addWidget(self.song_label)
        cv.addWidget(self.artist_label)
        cv.addWidget(self._divider())

        cv.addWidget(self._section_header("BASHIN"))
        self.bashin_status_label = QLabel("● Idle")
        self.bashin_status_label.setStyleSheet(
            f"font-family: '{_SANS}'; font-size: 10pt; background: transparent;")
        cv.addWidget(self.bashin_status_label)

        self.task_label = QLabel("Idle")
        self.task_label.setWordWrap(True)
        self.task_label.setStyleSheet(
            f"color: {TEXT_MAIN.name()}; font-family: '{_SANS}'; font-size: 9pt; background: transparent;")
        self.task_age_label = QLabel("")
        self.task_age_label.setStyleSheet(
            f"color: {TEXT_DIM.name()}; font-family: '{_SANS}'; font-size: 8pt; background: transparent;")
        cv.addWidget(self.task_label)
        cv.addWidget(self.task_age_label)
        cv.addWidget(self._divider())

        cv.addWidget(self._section_header("DEVICES"))
        self.devices_container = QVBoxLayout()
        self.devices_container.setSpacing(4)
        dev_wrap = QWidget()
        dev_wrap.setStyleSheet("background: transparent;")
        dev_wrap.setLayout(self.devices_container)
        cv.addWidget(dev_wrap)
        cv.addWidget(self._divider())

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.btn_dashboard = self._glass_button("Dashboard")
        self.btn_pair = self._glass_button("Pair New")
        self.btn_enter_code = self._glass_button("Enter Code")
        self.btn_dashboard.clicked.connect(lambda: self._on_open_dashboard())
        self.btn_pair.clicked.connect(lambda: self._on_pair_new_device())
        self.btn_enter_code.clicked.connect(lambda: self._on_enter_pairing_code())
        actions.addWidget(self.btn_dashboard)
        actions.addWidget(self.btn_pair)
        actions.addWidget(self.btn_enter_code)
        cv.addLayout(actions)
        cv.addStretch(1)

        self._content.setVisible(False)
        outer.addWidget(self._content)

    def _section_header(self, text):
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"color: #6f7590; font-family: '{_MONO}'; font-size: 8pt; "
            f"letter-spacing: 2px; background: transparent;")
        return lbl

    def _divider(self):
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet("background: rgba(255,255,255,28); border: none;")
        return line

    def _glass_button(self, text):
        b = QPushButton(text)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setStyleSheet(f"""
            QPushButton {{
                color: {TEXT_MAIN.name()}; font-family: '{_SANS}'; font-size: 8.5pt;
                background: rgba(255,255,255,18);
                border: 1px solid rgba(255,255,255,45);
                border-radius: 10px; padding: 6px 4px;
            }}
            QPushButton:hover {{ background: rgba(255,255,255,35); }}
            QPushButton:pressed {{ background: rgba(255,255,255,55); }}
        """)
        return b

    # ── geometry / positioning ───────────────────────────────────────────────
    def _screen_center_x(self, width):
        screen = QApplication.primaryScreen().geometry()
        return screen.x() + (screen.width() - width) // 2

    def _place_peek(self):
        x = self._screen_center_x(PEEK_W)
        self.setGeometry(QRect(x, 0, PEEK_W, PEEK_H))

    # ── open/close ───────────────────────────────────────────────────────────
    def toggle(self):
        if self._open:
            self._collapse()
        else:
            self._expand()

    def _expand(self):
        self._open = True
        self._peek_label.setVisible(False)
        self._content.setVisible(True)
        x = self._screen_center_x(OPEN_W)
        self._anim.stop()
        self._anim.setStartValue(self.geometry())
        self._anim.setEndValue(QRect(x, 0, OPEN_W, OPEN_H))
        self._anim.start()
        self._refresh()

    def _collapse(self):
        self._open = False
        x = self._screen_center_x(PEEK_W)
        self._anim.stop()
        self._anim.setStartValue(self.geometry())
        self._anim.setEndValue(QRect(x, 0, PEEK_W, PEEK_H))
        self._anim.finished.connect(self._after_collapse)
        self._anim.start()

    def _after_collapse(self):
        try:
            self._anim.finished.disconnect(self._after_collapse)
        except Exception:
            pass
        self._content.setVisible(False)
        self._peek_label.setVisible(True)

    # ── events ───────────────────────────────────────────────────────────────
    def showEvent(self, e):
        super().showEvent(e)
        if not self._blur_applied:
            self._blur_applied = _enable_acrylic_blur(int(self.winId()))

    def mousePressEvent(self, ev):
        # Clicking the peek pill, or the date/time header while open, toggles.
        if not self._open or ev.position().y() < 70:
            self.toggle()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Explicitly clear the whole window to transparent first -- without
        # this, pixels outside the rounded path (but inside the rectangular
        # window) default to opaque black instead of true transparency, so
        # the corners show as black squares poking out past the curve. Same
        # fix widgets.py's Overlay already relies on.
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        p.fillRect(self.rect(), Qt.GlobalColor.transparent)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

        rect = self.rect().adjusted(0, 0, -1, -1)

        path = QPainterPath()
        path.addRoundedRect(float(rect.x()), float(rect.y()), float(rect.width()), float(rect.height()),
                            CORNER_R, CORNER_R)

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(GLASS_FILL))
        p.drawPath(path)

        # subtle top sheen -- a soft lighter strip near the top, like light
        # catching the top edge of a glass panel
        sheen_path = QPainterPath()
        sheen_h = min(60, rect.height() // 3)
        sheen_path.addRoundedRect(float(rect.x()), float(rect.y()), float(rect.width()), float(sheen_h),
                                  CORNER_R, CORNER_R)
        p.setBrush(QBrush(GLASS_SHEEN))
        p.setClipPath(path)
        p.drawPath(sheen_path)
        p.setClipping(False)

        p.setPen(QPen(GLASS_BORDER, 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)

    # ── live refresh ─────────────────────────────────────────────────────────
    def _refresh(self):
        now = datetime.now()
        compact = now.strftime("%I:%M %p").lstrip("0")
        full = now.strftime("%a, %b %d · %I:%M %p").replace(" 0", " ")
        self._peek_label.setText(compact)
        self.datetime_label.setText(full)

        if not self._open:
            return   # skip the heavier updates while collapsed

        media = MEDIA.get_info()
        if media.get("available") and media.get("title"):
            dot = "🟢" if media.get("playing") else "⏸"
            self.song_label.setText(f"{dot} {media['title']}")
            self.artist_label.setText(media.get("artist", ""))
        else:
            self.song_label.setText("Nothing playing")
            self.artist_label.setText("")

        state = (self._get_conv_state() or "IDLE").upper()
        state_map = {
            "IDLE":       ("○ Idle",       DOT_GRAY),
            "LISTENING":  ("● Listening",  DOT_GREEN),
            "PROCESSING": ("● Processing", DOT_AMBER),
            "SPEAKING":   ("● Speaking",   DOT_GREEN),
            "GUIDING":    ("● Guiding",    DOT_AMBER),
        }
        text, color = state_map.get(state, ("○ Idle", DOT_GRAY))
        self.bashin_status_label.setText(text)
        self.bashin_status_label.setStyleSheet(
            f"color: {color.name()}; font-family: '{_SANS}'; font-size: 10pt; background: transparent;")

        task = status_state.get_status()
        prefix = "" if task.get("done", True) else "⋯ "
        self.task_label.setText(prefix + task.get("text", "Idle"))
        age = max(0, int(time.time() - task.get("ts", time.time())))
        self.task_age_label.setText(f"{age}s ago" if age > 0 else "just now")

        while self.devices_container.count():
            item = self.devices_container.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        devices = [d for d in lan_mesh.MESH.list_devices() if d.get("paired")]
        if not devices:
            lbl = QLabel("No paired devices")
            lbl.setStyleSheet(f"color: {TEXT_DIM.name()}; font-family: '{_SANS}'; font-size: 9pt; background: transparent;")
            self.devices_container.addWidget(lbl)
        for d in devices:
            online = bool(d.get("ip"))
            dot = "●" if online else "○"
            color = DOT_GREEN if online else DOT_GRAY
            lbl = QLabel(f"{dot} {d['name']}")
            lbl.setStyleSheet(f"color: {color.name()}; font-family: '{_SANS}'; font-size: 9.5pt; background: transparent;")
            self.devices_container.addWidget(lbl)
