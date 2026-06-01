"""
Cursor Overlay  v5
  Ctrl+Alt+W   → show / hide cursor overlay (wake-word listener activates while visible)
  Ctrl+Alt+B   → enter / exit conversation mode manually
  Wake phrase  → auto-enters conversation mode while cursor is visible
                 default: "hey listen"  (configurable via tray)

  While in conversation mode, saying "help me…" / "how do I…" triggers screen-aware
  step-by-step guidance with a pulsing crosshair that moves to each click target.
"""
import sys, os, winreg, ctypes, threading, time, json, tempfile, wave, math, base64, io, logging
from collections import deque

_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "overlay.log")
logging.basicConfig(filename=_LOG, level=logging.DEBUG,
                    format="%(asctime)s %(levelname)s %(message)s", filemode="w")
from ctypes import wintypes
import numpy as np

from PyQt6.QtWidgets import QApplication, QWidget, QSystemTrayIcon, QMenu, QInputDialog
from PyQt6.QtCore    import Qt, QTimer, QObject, pyqtSignal
from PyQt6.QtGui     import QPainter, QPen, QColor, QBrush, QIcon, QPixmap, QFont, QImage
import sounddevice as sd
from openai import OpenAI
from PIL import ImageGrab, Image

# ── Win32 ──────────────────────────────────────────────────────────────────────
user32   = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
dwmapi   = ctypes.windll.dwmapi
GWL_EXSTYLE       = -20
WS_EX_LAYERED     = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
LWA_COLORKEY      = 0x00000001
WM_HOTKEY         = 0x0312
MOD_CTRL = 0x0002;  MOD_ALT = 0x0001
VK_W = 0x57;  VK_B = 0x42;  VK_LBUTTON = 0x01
HK_CIRCLE = 1;  HK_VOICE = 2

_CK_Q   = QColor(255, 0, 254)
_CK_REF = 255 | (0 << 8) | (254 << 16)

# ── States ─────────────────────────────────────────────────────────────────────
IDLE = 0; LISTENING = 1; PROCESSING = 2; SPEAKING = 3; GUIDING = 4

# ── Single instance ────────────────────────────────────────────────────────────
_MUTEX = kernel32.CreateMutexW(None, True, "CursorOverlay_SingleInstance")
if kernel32.GetLastError() == 183:
    sys.exit(0)

# ── Constants ──────────────────────────────────────────────────────────────────
CURSOR_IMG  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cursor_img.png")
CIRCLE_SIZE = 20;  CIRCLE_HALF = CIRCLE_SIZE // 2
TRAIL_MS = 300;  TICK_MS = 10
TAIL_X = 24;  TAIL_Y = 30
NUM_BARS = 4;  EQ_W = 76;  EQ_H = 24;  EQ_MAX_R = 5;  EQ_MIN_R = 2
GUIDE_W = 64;  GUIDE_H = 64;  GUIDE_INNER_R = 6;  GUIDE_OUTER_MAX = 26
TRANS_DURATION = 0.45   # seconds for cursor→EQ roll animation

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

_GUIDE_SYS = """\
You are a smart screen-aware AI assistant. The user has shared their screen.
Respond ONLY with valid JSON — no markdown, no extra text.

For general conversation:
{{"type":"text","speech":"<response in {lang}>"}}

For tasks requiring on-screen navigation:
{{"type":"guide","speech":"<brief spoken intro in {lang}>","steps":[
  {{"speech":"<instruction in {lang}>","x":<int|null>,"y":<int|null>}}
]}}

Screenshot is {iw}×{ih} px. x,y are pixel coords in that image. Null = no click needed.
"""

# ── Config helpers ─────────────────────────────────────────────────────────────
def _load_cfg():
    try:
        with open(CONFIG_PATH) as f: return json.load(f)
    except Exception: return {}

def _save_cfg(cfg):
    with open(CONFIG_PATH, "w") as f: json.dump(cfg, f, indent=2)

# ── Startup registry ───────────────────────────────────────────────────────────
_STARTUP_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_APP_NAME    = "CursorOverlay"
_PYTHONW     = r"C:\Users\User\AppData\Local\Programs\Python\Python311\pythonw.exe"
_SCRIPT      = os.path.abspath(__file__)

def _register():
    try:
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _STARTUP_KEY, 0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(k, _APP_NAME, 0, winreg.REG_SZ, f'"{_PYTHONW}" "{_SCRIPT}"')
        winreg.CloseKey(k)
    except Exception: pass

def _unregister():
    try:
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _STARTUP_KEY, 0, winreg.KEY_SET_VALUE)
        winreg.DeleteValue(k, _APP_NAME)
        winreg.CloseKey(k)
    except Exception: pass

def _registered():
    try:
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _STARTUP_KEY)
        winreg.QueryValueEx(k, _APP_NAME)
        winreg.CloseKey(k); return True
    except Exception: return False

# ── Screen capture ─────────────────────────────────────────────────────────────
def capture_screen():
    img = ImageGrab.grab()
    ow, oh = img.size
    scale  = min(1.0, 1280 / ow)
    nw, nh = int(ow * scale), int(oh * scale)
    if scale < 1.0:
        img = img.resize((nw, nh), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode(), ow/nw, oh/nh, nw, nh

# ── Wake-word listener ─────────────────────────────────────────────────────────
class WakeWordListener:
    SR             = 16000
    ENERGY_THRESH  = 0.035    # RMS to start capturing
    CAPTURE_SECS   = 2.0      # record this many seconds after spike
    COOLDOWN_SECS  = 4.0
    PRE_MS         = 250      # ms of audio to prepend (catch start of word)

    def __init__(self, get_phrase, get_key, on_wake):
        self.get_phrase      = get_phrase
        self.get_key         = get_key
        self.on_wake         = on_wake
        self._active         = False
        self._last_wake      = 0.0
        self._capturing      = False
        self._capture_buf    = []
        self._capture_frames = 0
        # short pre-buffer to catch word onset
        self._ring  = deque(maxlen=int(self.SR * self.PRE_MS / 1000))
        self._stream = None

    def start(self):
        if self._active: return
        self._active = True
        self._ring.clear()
        try:
            self._stream = sd.InputStream(
                samplerate=self.SR, channels=1, dtype="float32",
                blocksize=1024, callback=self._cb
            )
            self._stream.start()
            logging.info("WakeWordListener started, phrase=%r", self.get_phrase())
        except Exception as e:
            self._active = False
            logging.error("WakeWordListener failed to start: %s", e)

    def stop(self):
        self._active = False
        if self._stream:
            try: self._stream.stop(); self._stream.close()
            except Exception: pass
            self._stream = None

    def _cb(self, indata, frames, t, status):
        self._ring.extend(indata[:, 0])
        if not self._active: return
        if time.monotonic() - self._last_wake < self.COOLDOWN_SECS: return

        rms = float(np.sqrt(np.mean(indata ** 2)))

        if self._capturing:
            # accumulate audio after the energy spike
            self._capture_buf.append(indata[:, 0].copy())
            self._capture_frames += frames
            if self._capture_frames >= int(self.CAPTURE_SECS * self.SR):
                self._capturing = False
                self._last_wake = time.monotonic()
                audio = np.concatenate(self._capture_buf)
                threading.Thread(target=self._check, args=(audio,), daemon=True).start()
        elif rms >= self.ENERGY_THRESH:
            # spike detected — start recording forward from now
            logging.debug("Wake energy spike rms=%.4f, start forward capture", rms)
            self._capturing      = True
            pre = np.array(list(self._ring))   # 250ms pre-buffer
            self._capture_buf    = [pre]
            self._capture_frames = len(pre)

    def _check(self, audio):
        try:
            key    = self.get_key()
            phrase = self.get_phrase().lower().strip()
            if not key or not phrase: return
            tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            with wave.open(tmp.name, "wb") as wf:
                wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(self.SR)
                wf.writeframes((audio * 32767).astype(np.int16).tobytes())
            client = OpenAI(api_key=key)
            with open(tmp.name, "rb") as f:
                result = client.audio.transcriptions.create(model="whisper-1", file=f)
            transcript = result.text.lower().strip()
            logging.info("Wake check: transcript=%r  phrase=%r  match=%s",
                         transcript, phrase, phrase in transcript)
            if phrase in transcript:
                self.on_wake()
        except Exception: pass
        finally:
            try: os.unlink(tmp.name)
            except Exception: pass

# ── Voice recorder (VAD) ───────────────────────────────────────────────────────
class VoiceRecorder:
    SR = 16000;  SILENCE_THRESH = 0.015;  SILENCE_SECS = 1.5;  MIN_SPEECH_SECS = 0.4

    def __init__(self, on_levels, on_silence):
        self.on_levels = on_levels;  self.on_silence = on_silence
        self._chunks = [];  self._stream = None
        self._speech_f = 0;  self._silence_f = 0;  self._triggered = False

    def start(self):
        self._chunks = [];  self._speech_f = 0;  self._silence_f = 0;  self._triggered = False
        self._stream = sd.InputStream(samplerate=self.SR, channels=1, dtype="float32",
                                      blocksize=512, callback=self._cb)
        self._stream.start()

    def _cb(self, indata, n_frames, _t, _status):
        self._chunks.append(indata.copy())
        fft    = np.abs(np.fft.rfft(indata[:, 0], n=512))
        levels = [min(float(np.mean(b)) * 10, 1.0) for b in np.array_split(fft[:128], NUM_BARS)]
        self.on_levels(levels)
        rms = float(np.sqrt(np.mean(indata ** 2)))
        if rms >= self.SILENCE_THRESH: self._speech_f  += n_frames;  self._silence_f  = 0
        else:                          self._silence_f += n_frames
        if (not self._triggered
                and self._speech_f  >= int(self.MIN_SPEECH_SECS * self.SR)
                and self._silence_f >= int(self.SILENCE_SECS    * self.SR)):
            self._triggered = True;  self.on_silence()

    def stop(self):
        if self._stream:
            try: self._stream.stop(); self._stream.close()
            except Exception: pass
            self._stream = None
        if not self._chunks: return None
        audio = np.concatenate(self._chunks)
        tmp   = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        with wave.open(tmp.name, "wb") as wf:
            wf.setnchannels(1);  wf.setsampwidth(2);  wf.setframerate(self.SR)
            wf.writeframes((audio * 32767).astype(np.int16).tobytes())
        return tmp.name

# ── Signal bridge ──────────────────────────────────────────────────────────────
class Bridge(QObject):
    toggle_circle    = pyqtSignal()
    toggle_voice     = pyqtSignal()
    wake_detected    = pyqtSignal()
    set_levels       = pyqtSignal(list)
    show_response    = pyqtSignal(str)
    show_error       = pyqtSignal(str)
    silence_detected = pyqtSignal()
    begin_processing = pyqtSignal()
    start_speaking   = pyqtSignal()
    stop_speaking    = pyqtSignal()

# ── Overlay widget ─────────────────────────────────────────────────────────────
class Overlay(QWidget):
    def __init__(self):
        super().__init__()
        self.eq_mode      = False
        self.guide_mode   = False
        self._transitioning = False
        self._trans_t     = 0.0
        self.levels       = [0.0] * NUM_BARS
        self._guide_pulse = 0.0

        # Build alpha-thresholded cursor pixmap
        img = QPixmap(CURSOR_IMG).scaled(
            CIRCLE_SIZE, CIRCLE_SIZE,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.FastTransformation
        ).toImage().convertToFormat(QImage.Format.Format_ARGB32)
        ck = _CK_Q.rgba()
        for y in range(img.height()):
            for x in range(img.width()):
                px = img.pixel(x, y)
                img.setPixel(x, y, ck if ((px >> 24) & 0xFF) < 128
                             else (px & 0x00FFFFFF) | 0xFF000000)
        self._pixmap = QPixmap.fromImage(img)

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint |
                            Qt.WindowType.WindowStaysOnTopHint |
                            Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(CIRCLE_SIZE, CIRCLE_SIZE)

        self._composed = QPixmap(CIRCLE_SIZE, CIRCLE_SIZE)
        self._composed.fill(_CK_Q)
        p = QPainter(self._composed); p.drawPixmap(0, 0, self._pixmap); p.end()

    # ── win32 transparency + DWM ───────────────────────────────────────────────
    def _apply_win32(self):
        hwnd  = int(self.winId())
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED | WS_EX_TRANSPARENT)
        user32.SetLayeredWindowAttributes(hwnd, _CK_REF, 0, LWA_COLORKEY)
        v = ctypes.c_int(1)
        dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(v), ctypes.sizeof(v))
        dwmapi.DwmSetWindowAttribute(hwnd,  2, ctypes.byref(v), ctypes.sizeof(v))

    def showEvent(self, e):
        super().showEvent(e); self._apply_win32()

    # ── mode transitions ───────────────────────────────────────────────────────
    def enter_eq(self, cx, cy):
        self.eq_mode = True;  self.guide_mode = False;  self._transitioning = False
        self.levels  = [0.0] * NUM_BARS
        self.setFixedSize(EQ_W, EQ_H)
        self.move(cx - EQ_W // 2, cy - EQ_H // 2)
        self.show();  self._apply_win32()

    def leave_eq(self):
        self.eq_mode = False;  self.guide_mode = False;  self._transitioning = False
        self.setFixedSize(CIRCLE_SIZE, CIRCLE_SIZE);  self.update()

    def enter_guide(self, tx, ty):
        self.guide_mode = True;  self.eq_mode = False;  self._transitioning = False
        self.setFixedSize(GUIDE_W, GUIDE_H)
        self.move(tx - GUIDE_W // 2, ty - GUIDE_H // 2)
        self.show();  self._apply_win32()

    def leave_guide(self):
        self.guide_mode = False;  self.eq_mode = False;  self._transitioning = False
        self.setFixedSize(CIRCLE_SIZE, CIRCLE_SIZE);  self.update()

    # ── rolling transition  cursor → EQ ───────────────────────────────────────
    def begin_transition(self, cx, cy):
        """Start roll-in animation. Widget expands to EQ size immediately."""
        self._transitioning = True;  self._trans_t = 0.0
        self.eq_mode = False;  self.guide_mode = False
        self.levels  = [0.0] * NUM_BARS
        self.setFixedSize(EQ_W, EQ_H)
        self.move(cx - EQ_W // 2, cy - EQ_H // 2)
        self.show();  self._apply_win32()

    def set_trans_t(self, t):
        self._trans_t = t;  self.update()

    def finish_transition(self):
        self._transitioning = False;  self.eq_mode = True;  self.update()

    # ── level / pulse ──────────────────────────────────────────────────────────
    def update_levels(self, levels):
        for i in range(NUM_BARS):
            self.levels[i] = self.levels[i] * 0.55 + levels[i] * 0.45
        self.update()

    def update_guide_pulse(self, pulse):
        self._guide_pulse = pulse;  self.update()

    # ── paint ──────────────────────────────────────────────────────────────────
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # ── rolling transition animation ───────────────────────────────────────
        if self._transitioning:
            t  = self._trans_t
            sm = t * t * (3 - 2 * t)   # smoothstep

            p.fillRect(self.rect(), _CK_Q)

            # cursor image rolls away to the right, shrinking
            cur_scale = max(0.0, 1.0 - sm * 2.2)
            if cur_scale > 0.01:
                sz  = max(2, int(CIRCLE_SIZE * cur_scale))
                img = self._pixmap.scaled(sz, sz,
                      Qt.AspectRatioMode.KeepAspectRatio,
                      Qt.TransformationMode.FastTransformation)
                # drift rightward as it rolls away
                cx_img = int(EQ_W * 0.18 + sm * EQ_W * 0.7)
                cy_img = EQ_H // 2
                p.drawPixmap(cx_img - sz // 2, cy_img - sz // 2, img)

            # dots roll in from the right, staggered
            step = EQ_W // NUM_BARS
            cy0  = EQ_H // 2
            for i in range(NUM_BARS):
                start = 0.18 + i * 0.17
                end   = start + 0.52
                if t < start: continue
                dt   = min(1.0, (t - start) / (end - start))
                ease = dt * dt * (3 - 2 * dt)

                fx = step // 2 + i * step            # final x
                # rolls in from right edge
                cx_d = int(EQ_W + EQ_MAX_R + (fx - EQ_W - EQ_MAX_R) * ease)
                # small bounce as it settles
                bounce = int(3 * (1 - ease) * abs(math.sin(dt * math.pi * 3.5)))
                cy_d   = cy0 - bounce

                r = max(1, int(ease * EQ_MAX_R))
                p.setBrush(QBrush(QColor(int(80 + ease * 140), int(200 - ease * 120), 255)))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawEllipse(cx_d - r, cy_d - r, r * 2, r * 2)
            return

        # ── guide crosshair ────────────────────────────────────────────────────
        if self.guide_mode:
            p.fillRect(self.rect(), _CK_Q)
            pulse   = self._guide_pulse
            cx, cy  = GUIDE_W // 2, GUIDE_H // 2
            r_outer = int(GUIDE_INNER_R * 1.8 + pulse * (GUIDE_OUTER_MAX - GUIDE_INNER_R * 1.8))
            p.setPen(QPen(QColor(255, 80, 60), 2));  p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(cx - r_outer, cy - r_outer, r_outer * 2, r_outer * 2)
            p.setPen(QPen(QColor(255, 160, 60), 2))
            p.drawEllipse(cx - GUIDE_INNER_R, cy - GUIDE_INNER_R,
                          GUIDE_INNER_R * 2, GUIDE_INNER_R * 2)
            p.setPen(Qt.PenStyle.NoPen);  p.setBrush(QBrush(QColor(255, 60, 60)))
            p.drawEllipse(cx - 3, cy - 3, 6, 6)
            gap = GUIDE_INNER_R + 2
            p.setPen(QPen(QColor(255, 80, 60), 1))
            p.drawLine(2, cy, cx - gap, cy);        p.drawLine(cx + gap, cy, GUIDE_W - 2, cy)
            p.drawLine(cx, 2, cx, cy - gap);        p.drawLine(cx, cy + gap, cx, GUIDE_H - 2)
            return

        # ── equalizer dots ─────────────────────────────────────────────────────
        if self.eq_mode:
            p.fillRect(self.rect(), _CK_Q)
            step = EQ_W // NUM_BARS;  cy0 = EQ_H // 2
            for i, lvl in enumerate(self.levels):
                r   = EQ_MIN_R + int((EQ_MAX_R - EQ_MIN_R) * lvl)
                cx  = step // 2 + i * step
                p.setBrush(QBrush(QColor(int(80 + lvl * 140), int(200 - lvl * 120), 255)))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawEllipse(cx - r, cy0 - r, r * 2, r * 2)
            return

        # ── cursor image ───────────────────────────────────────────────────────
        p.drawPixmap(0, 0, self._composed)

# ── Response bubble ────────────────────────────────────────────────────────────
class ResponseBubble(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint |
                            Qt.WindowType.WindowStaysOnTopHint |
                            Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._text  = ""
        self._timer = QTimer(self);  self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def show_text(self, text, ax, ay, timeout_ms=12000):
        self._text = text
        lines = max(3, len(text) // 45 + text.count("\n") + 1)
        h     = min(lines * 20 + 28, 320)
        self.setFixedSize(400, h)
        scr = QApplication.primaryScreen().geometry()
        self.move(min(ax, scr.width() - 410), min(ay + 24, scr.height() - h - 10))
        self.show();  self.raise_();  self._timer.stop()
        if timeout_ms > 0: self._timer.start(timeout_ms)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QBrush(QColor(18, 18, 28, 235)))
        p.setPen(QPen(QColor(80, 120, 255), 1))
        p.drawRoundedRect(0, 0, self.width()-1, self.height()-1, 10, 10)
        p.setPen(QColor(220, 220, 245));  p.setFont(QFont("Segoe UI", 9))
        p.drawText(self.rect().adjusted(12, 10, -12, -10), Qt.TextFlag.TextWordWrap, self._text)

    def mousePressEvent(self, _): self.hide()

# ── Application ────────────────────────────────────────────────────────────────
class App:
    def __init__(self):
        self.qt      = QApplication(sys.argv);  self.qt.setQuitOnLastWindowClosed(False)
        self.overlay = Overlay();  self.bubble = ResponseBubble();  self.bridge = Bridge()
        self.history = deque()

        self.conv_state    = IDLE
        self._conv_history = []
        self._anchor       = (0, 0)
        self._anim_mode    = "listening"
        self._pending_guide= None

        # guide state
        self._guide_steps  = [];  self._guide_idx = 0
        self._guide_xs     = 1.0;  self._guide_ys = 1.0
        self._guide_step_time = 0.0;  self._was_lbutton = False

        cfg = _load_cfg()
        self.api_key    = cfg.get("openai_api_key", "")
        self.wake_phrase= cfg.get("wake_phrase", "hey")

        self.recorder = VoiceRecorder(
            on_levels  = lambda lvl: self.bridge.set_levels.emit(lvl),
            on_silence = lambda: self.bridge.silence_detected.emit()
        )
        self._wake = WakeWordListener(
            get_phrase = lambda: self.wake_phrase,
            get_key    = lambda: self.api_key,
            on_wake    = lambda: self.bridge.wake_detected.emit()
        )

        # timers
        self._anim_timer  = QTimer();  self._anim_timer.setInterval(50)
        self._anim_timer.timeout.connect(self._animate)
        self._trans_timer = QTimer();  self._trans_timer.setInterval(16)
        self._trans_timer.timeout.connect(self._trans_step)
        self._trans_elapsed = 0.0

        # signals
        self.bridge.toggle_circle.connect(self._toggle_circle)
        self.bridge.toggle_voice.connect(self._toggle_voice)
        self.bridge.wake_detected.connect(self._on_wake_detected)
        self.bridge.set_levels.connect(self._on_levels)
        self.bridge.show_response.connect(self._on_response)
        self.bridge.show_error.connect(self._on_error)
        self.bridge.silence_detected.connect(self._on_silence_detected)
        self.bridge.begin_processing.connect(self._on_begin_processing)
        self.bridge.start_speaking.connect(self._on_start_speaking)
        self.bridge.stop_speaking.connect(self._on_stop_speaking)

        self.timer = QTimer();  self.timer.timeout.connect(self._tick)
        self.timer.start(TICK_MS)

        self._build_tray()
        threading.Thread(target=self._hotkey_loop, daemon=True).start()
        if not _registered(): _register()

        msg  = ("No OpenAI key — right-click tray → Set API Key"
                if not self.api_key else
                f'Ready  |  W: cursor  B: talk  wake: "{self.wake_phrase}"')
        self.tray.showMessage("Cursor Overlay", msg,
            QSystemTrayIcon.MessageIcon.Warning if not self.api_key
            else QSystemTrayIcon.MessageIcon.Information, 3000)

    # ── tray ──────────────────────────────────────────────────────────────────
    def _build_tray(self):
        px = QPixmap(16, 16);  px.fill(Qt.GlobalColor.transparent)
        p  = QPainter(px);  p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(20,20,20),1));  p.setBrush(QBrush(QColor(255,255,255)))
        p.drawEllipse(1,1,13,13);  p.end()
        self.tray = QSystemTrayIcon(QIcon(px))
        self.tray.setToolTip("Cursor Overlay")
        m = QMenu()
        m.addAction("Toggle Cursor  (Ctrl+Alt+W)",     self._toggle_circle)
        m.addAction("Conversation Mode  (Ctrl+Alt+B)", self._toggle_voice)
        m.addSeparator()
        m.addAction("Set OpenAI API Key",              self._prompt_api_key)
        m.addAction("Set Wake Phrase",                 self._prompt_wake_phrase)
        m.addAction("Clear conversation history",      self._clear_history)
        m.addSeparator()
        m.addAction("Remove from startup",             _unregister)
        m.addAction("Quit",                            self._quit)
        self.tray.setContextMenu(m);  self.tray.show()

    def _prompt_api_key(self):
        key, ok = QInputDialog.getText(None, "OpenAI API Key", "Paste your OpenAI API key:")
        if ok and key.strip():
            self.api_key = key.strip()
            cfg = _load_cfg();  cfg["openai_api_key"] = self.api_key;  _save_cfg(cfg)
            self.tray.showMessage("Cursor Overlay", "API key saved!",
                                  QSystemTrayIcon.MessageIcon.Information, 2000)

    def _prompt_wake_phrase(self):
        phrase, ok = QInputDialog.getText(None, "Wake Phrase",
                         f'Current wake phrase: "{self.wake_phrase}"\nEnter new phrase:')
        if ok and phrase.strip():
            self.wake_phrase = phrase.strip().lower()
            cfg = _load_cfg();  cfg["wake_phrase"] = self.wake_phrase;  _save_cfg(cfg)
            self.tray.showMessage("Cursor Overlay",
                f'Wake phrase set to: "{self.wake_phrase}"',
                QSystemTrayIcon.MessageIcon.Information, 2000)

    def _clear_history(self):
        self._conv_history = []
        self.tray.showMessage("Cursor Overlay", "History cleared.",
                              QSystemTrayIcon.MessageIcon.Information, 2000)

    # ── hotkeys ───────────────────────────────────────────────────────────────
    def _hotkey_loop(self):
        user32.RegisterHotKey(None, HK_CIRCLE, MOD_CTRL | MOD_ALT, VK_W)
        user32.RegisterHotKey(None, HK_VOICE,  MOD_CTRL | MOD_ALT, VK_B)
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            if msg.message == WM_HOTKEY:
                if msg.wParam == HK_CIRCLE: self.bridge.toggle_circle.emit()
                elif msg.wParam == HK_VOICE: self.bridge.toggle_voice.emit()

    # ── cursor toggle ─────────────────────────────────────────────────────────
    def _toggle_circle(self):
        if self.conv_state != IDLE: return
        if self.overlay.isVisible():
            self._wake.stop()
            self.overlay.hide()
        else:
            self.overlay.leave_eq()
            self.overlay.show()
            if self.api_key:
                self._wake.start()

    # ── wake word ──────────────────────────────────────────────────────────────
    def _on_wake_detected(self):
        logging.info("Wake detected: conv_state=%d overlay_visible=%s",
                     self.conv_state, self.overlay.isVisible())
        if self.conv_state == IDLE and self.overlay.isVisible():
            self.tray.showMessage("Cursor Overlay", "Wake word detected — listening!",
                                  QSystemTrayIcon.MessageIcon.Information, 1500)
            self._enter_conversation()

    # ── conversation toggle ───────────────────────────────────────────────────
    def _toggle_voice(self):
        if self.conv_state == IDLE: self._enter_conversation()
        else:                       self._exit_conversation()

    def _enter_conversation(self):
        logging.info("Entering conversation mode")
        if not self.api_key:
            self.tray.showMessage("Cursor Overlay", "Set OpenAI API key first",
                                  QSystemTrayIcon.MessageIcon.Warning, 3000); return
        self._wake.stop()
        self.conv_state = LISTENING
        self._start_recording()
        self.tray.showMessage("Cursor Overlay",
                              "Listening… say 'help me …' for guided steps",
                              QSystemTrayIcon.MessageIcon.Information, 2000)

    def _exit_conversation(self):
        sd.stop();  self.recorder.stop()
        self._trans_timer.stop();  self._anim_timer.stop()
        self._pending_guide = None;  self.conv_state = IDLE
        self.overlay.leave_eq();  self.overlay.leave_guide();  self.overlay.hide()
        self.bubble.hide()
        self.tray.showMessage("Cursor Overlay", "Conversation ended.",
                              QSystemTrayIcon.MessageIcon.Information, 1500)

    # ── recording / transition ────────────────────────────────────────────────
    def _start_recording(self):
        if self.conv_state != LISTENING: return
        self._wake.stop()
        pt = wintypes.POINT();  user32.GetCursorPos(ctypes.byref(pt))
        self._anchor = (pt.x, pt.y);  self._anim_mode = "listening"
        cx = pt.x + TAIL_X;  cy = pt.y + TAIL_Y

        # cursor is showing and idle → roll animation into EQ
        if (self.overlay.isVisible()
                and not self.overlay.eq_mode
                and not self.overlay.guide_mode
                and not self.overlay._transitioning):
            self.overlay.begin_transition(cx, cy)
            self._trans_elapsed = 0.0
            self._trans_timer.start()
        else:
            self.overlay.enter_eq(cx, cy)
            self.recorder.start()

    def _trans_step(self):
        self._trans_elapsed += 0.016
        t = min(1.0, self._trans_elapsed / TRANS_DURATION)
        self.overlay.set_trans_t(t)
        if t >= 1.0:
            self._trans_timer.stop()
            self.overlay.finish_transition()
            if self.conv_state == LISTENING:
                self.recorder.start()

    # ── VAD silence ───────────────────────────────────────────────────────────
    def _on_silence_detected(self):
        if self.conv_state != LISTENING: return
        self.conv_state = PROCESSING
        wav = self.recorder.stop()
        self.bridge.begin_processing.emit()
        if wav:
            threading.Thread(target=self._process, args=(wav,), daemon=True).start()
        else:
            self.conv_state = LISTENING;  self._start_recording()

    def _on_begin_processing(self):
        self._anim_mode = "processing";  self._anim_timer.start()

    # ── AI pipeline ───────────────────────────────────────────────────────────
    def _process(self, wav_path):
        logging.info("Processing started")
        try:
            client = OpenAI(api_key=self.api_key)
            with open(wav_path, "rb") as f:
                tr = client.audio.transcriptions.create(
                    model="whisper-1", file=f, response_format="verbose_json")
            text = tr.text.strip()
            lang = getattr(tr, "language", None) or "english"
            logging.info("STT result: %r  lang=%s", text, lang)
            if not text: return

            b64, xs, ys, iw, ih = capture_screen()
            self._conv_history.append({"role": "user", "content": text})

            sys_p = _GUIDE_SYS.format(lang=lang, iw=iw, ih=ih)
            msgs  = [{"role": "system", "content": sys_p}]
            for m in self._conv_history[-10:]:
                if m["role"] == "assistant":
                    msgs.append({"role": "assistant", "content": m["content"]})
            msgs.append({"role": "user", "content": [
                {"type": "image_url",
                 "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "high"}},
                {"type": "text", "text": text}
            ]})

            resp   = client.chat.completions.create(
                model="gpt-4o", messages=msgs,
                response_format={"type": "json_object"}, max_tokens=800)
            result = json.loads(resp.choices[0].message.content)

            speech = result.get("speech", "")
            rtype  = result.get("type", "text")
            self._conv_history.append({"role": "assistant", "content": speech})

            if rtype == "guide":
                steps = result.get("steps", [])
                self.bridge.show_response.emit(
                    f"You: {text}\n\n{speech}\n\n"
                    + "\n".join(f"Step {i+1}: {s.get('speech','')}"
                                for i, s in enumerate(steps)))
                self._pending_guide = (steps, xs, ys)
            else:
                self.bridge.show_response.emit(f"You: {text}\n\n{speech}")
                self._pending_guide = None

            self.bridge.start_speaking.emit()
            tts = client.audio.speech.create(
                model="tts-1", voice="alloy", input=speech, response_format="pcm")
            pcm = np.frombuffer(tts.content, dtype=np.int16).astype(np.float32) / 32768.0
            sd.play(pcm, samplerate=24000);  sd.wait()

        except Exception as e:
            if self.conv_state != IDLE:
                self.bridge.show_error.emit(f"Error: {str(e)[:140]}")
            self._pending_guide = None
        finally:
            self.bridge.stop_speaking.emit()
            try: os.unlink(wav_path)
            except Exception: pass

    # ── speaking state ────────────────────────────────────────────────────────
    def _on_start_speaking(self):
        self.conv_state = SPEAKING;  self._anim_mode = "speaking";  self._anim_timer.start()

    def _on_stop_speaking(self):
        self._anim_timer.stop()
        if self.conv_state == IDLE:
            self.overlay.leave_eq();  self.overlay.leave_guide();  self.overlay.hide(); return
        if self._pending_guide is not None:
            steps, xs, ys = self._pending_guide;  self._pending_guide = None
            self._start_guide_mode(steps, xs, ys)
        else:
            self.conv_state = LISTENING;  self._start_recording()

    # ── guide mode ────────────────────────────────────────────────────────────
    def _start_guide_mode(self, steps, xs, ys):
        self.conv_state    = GUIDING
        self._guide_steps  = steps;  self._guide_idx = 0
        self._guide_xs     = xs;     self._guide_ys  = ys
        self._was_lbutton  = False;  self._guide_step_time = time.monotonic()
        self._anim_mode    = "guide";  self._anim_timer.start()
        self._show_guide_step(0)

    def _show_guide_step(self, idx):
        step  = self._guide_steps[idx]
        speech = step.get("speech", "")
        rx, ry = step.get("x"), step.get("y")
        total  = len(self._guide_steps)
        if rx is not None and ry is not None:
            self.overlay.enter_guide(int(rx * self._guide_xs), int(ry * self._guide_ys))
        self.bubble.show_text(
            f"Step {idx+1} / {total}\n{speech}\n\n"
            + ("Click the highlighted location to continue."
               if rx is not None else "Click anywhere to continue."),
            *self._anchor, timeout_ms=0)
        threading.Thread(target=self._tts_step, args=(speech,), daemon=True).start()
        self._guide_step_time = time.monotonic()

    def _tts_step(self, text):
        try:
            client = OpenAI(api_key=self.api_key)
            tts    = client.audio.speech.create(
                model="tts-1", voice="alloy", input=text, response_format="pcm")
            pcm    = np.frombuffer(tts.content, dtype=np.int16).astype(np.float32) / 32768.0
            sd.play(pcm, samplerate=24000);  sd.wait()
        except Exception: pass

    def _advance_guide_step(self):
        self._guide_idx += 1
        if self._guide_idx >= len(self._guide_steps): self._guide_done()
        else: self._show_guide_step(self._guide_idx)

    def _guide_done(self):
        self._anim_timer.stop()
        self.overlay.leave_guide();  self.overlay.hide()
        self.bubble.show_text("All steps complete! Ask me anything.",
                              *self._anchor, timeout_ms=8000)
        threading.Thread(target=self._tts_step,
                         args=("Done! All steps complete.",), daemon=True).start()
        self.conv_state = LISTENING;  self._start_recording()

    # ── animation ─────────────────────────────────────────────────────────────
    def _animate(self):
        t = time.monotonic()
        if self._anim_mode == "speaking":
            self.overlay.update_levels(
                [0.35 + 0.65 * abs(math.sin(t * (1.8 + i * 0.9) * math.pi))
                 for i in range(NUM_BARS)])
        elif self._anim_mode == "processing":
            pulse = 0.25 + 0.35 * abs(math.sin(t * 1.1 * math.pi))
            self.overlay.update_levels([pulse] * NUM_BARS)
        elif self._anim_mode == "guide":
            self.overlay.update_guide_pulse(abs(math.sin(t * 2.2 * math.pi)))

    # ── slots ─────────────────────────────────────────────────────────────────
    def _on_levels(self, levels):
        if self.conv_state == LISTENING: self.overlay.update_levels(levels)

    def _on_response(self, text):
        self.bubble.show_text(text, *self._anchor)

    def _on_error(self, msg):
        self.tray.showMessage("Cursor Overlay", msg,
                              QSystemTrayIcon.MessageIcon.Warning, 4000)

    # ── position / click tick ─────────────────────────────────────────────────
    def _tick(self):
        pt  = wintypes.POINT();  user32.GetCursorPos(ctypes.byref(pt))
        now = time.monotonic()
        self.history.append((now, pt.x, pt.y))
        cutoff = now - (TRAIL_MS / 1000) * 2
        while self.history and self.history[0][0] < cutoff:
            self.history.popleft()

        # guide-mode click detection
        if self.conv_state == GUIDING:
            lbtn = bool(user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000)
            if (self._was_lbutton and not lbtn
                    and now - self._guide_step_time > 0.6):
                self._advance_guide_step()
            self._was_lbutton = lbtn
            return

        self._was_lbutton = False
        if not self.overlay.isVisible(): return

        target = now - TRAIL_MS / 1000
        tx, ty = pt.x, pt.y
        for ts, x, y in self.history:
            if ts >= target: tx, ty = x, y; break

        if self.overlay.eq_mode or self.overlay._transitioning:
            self.overlay.move(tx + TAIL_X - EQ_W // 2,    ty + TAIL_Y - EQ_H // 2)
        elif not self.overlay.guide_mode:
            self.overlay.move(tx + TAIL_X - CIRCLE_HALF,  ty + TAIL_Y - CIRCLE_HALF)

    def _quit(self):
        self._wake.stop()
        user32.UnregisterHotKey(None, HK_CIRCLE);  user32.UnregisterHotKey(None, HK_VOICE)
        kernel32.ReleaseMutex(_MUTEX);  self.qt.quit()

    def run(self): sys.exit(self.qt.exec())


if __name__ == "__main__":
    App().run()
