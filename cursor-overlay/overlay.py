"""
Cursor Highlight Overlay
  Ctrl+Alt+W  →  toggle white circle on/off
  Circle trails cursor by 0.3 s, fully click-through
  Single-instance mutex prevents duplicate hotkey conflicts
  Auto-registers to Windows startup on first run
"""
import sys, os, winreg, ctypes, threading, time
from collections import deque
from ctypes import wintypes

from PyQt6.QtWidgets import QApplication, QWidget, QSystemTrayIcon, QMenu
from PyQt6.QtCore    import Qt, QTimer, QObject, pyqtSignal
from PyQt6.QtGui     import QPainter, QPen, QColor, QBrush, QIcon, QPixmap

# ── Win32 ──────────────────────────────────────────────────────────────────────
user32            = ctypes.windll.user32
kernel32          = ctypes.windll.kernel32
GWL_EXSTYLE       = -20
WS_EX_LAYERED     = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
LWA_COLORKEY      = 0x00000001
WM_HOTKEY         = 0x0312
MOD_CTRL          = 0x0002
MOD_ALT           = 0x0001
VK_W              = 0x57
HOTKEY_ID         = 1

# colorkey: unique color Windows treats as transparent
# RGB(255, 0, 254) — won't appear in the white/black circle
_CK_Q   = QColor(255, 0, 254)
_CK_REF = 255 | (0 << 8) | (254 << 16)   # COLORREF = 0x00FE00FF

# ── Single instance ────────────────────────────────────────────────────────────
_MUTEX = kernel32.CreateMutexW(None, True, "CursorOverlay_SingleInstance")
if kernel32.GetLastError() == 183:   # ERROR_ALREADY_EXISTS
    sys.exit(0)

# ── Config ─────────────────────────────────────────────────────────────────────
SIZE      = 13          # small, just under cursor size
HALF      = SIZE // 2
TRAIL_MS  = 300
TICK_MS   = 10
# offset — far enough from cursor tip so they never overlap
TAIL_X    = 18
TAIL_Y    = 22

# ── Startup registry ───────────────────────────────────────────────────────────
STARTUP_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_NAME    = "CursorOverlay"
PYTHONW     = r"C:\Users\User\AppData\Local\Programs\Python\Python311\pythonw.exe"
SCRIPT      = os.path.abspath(__file__)

def _register():
    try:
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_KEY, 0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, f'"{PYTHONW}" "{SCRIPT}"')
        winreg.CloseKey(k)
    except Exception:
        pass

def _unregister():
    try:
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_KEY, 0, winreg.KEY_SET_VALUE)
        winreg.DeleteValue(k, APP_NAME)
        winreg.CloseKey(k)
    except Exception:
        pass

def _registered():
    try:
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_KEY)
        winreg.QueryValueEx(k, APP_NAME)
        winreg.CloseKey(k)
        return True
    except Exception:
        return False

# ── Qt signal bridge (hotkey thread → main thread) ─────────────────────────────
class Bridge(QObject):
    toggle = pyqtSignal()

# ── Transparent circle widget ──────────────────────────────────────────────────
class Circle(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint  |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(SIZE, SIZE)

    def showEvent(self, e):
        super().showEvent(e)
        hwnd  = int(self.winId())
        # layered + click-through
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE,
                              style | WS_EX_LAYERED | WS_EX_TRANSPARENT)
        # colorkey: _CK_REF color becomes fully transparent
        user32.SetLayeredWindowAttributes(hwnd, _CK_REF, 0, LWA_COLORKEY)

    def paintEvent(self, _):
        p = QPainter(self)
        # fill background with colorkey — Windows makes this color invisible
        p.fillRect(self.rect(), _CK_Q)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(0, 0, 0), 1.5))
        p.setBrush(QBrush(QColor(255, 255, 255)))
        p.drawEllipse(1, 1, SIZE - 2, SIZE - 2)

# ── Application ────────────────────────────────────────────────────────────────
class App:
    def __init__(self):
        self.qt      = QApplication(sys.argv)
        self.qt.setQuitOnLastWindowClosed(False)
        self.circle  = Circle()
        self.bridge  = Bridge()
        self.history = deque()

        self.bridge.toggle.connect(self._toggle)

        self.timer = QTimer()
        self.timer.timeout.connect(self._tick)
        self.timer.start(TICK_MS)

        self._build_tray()

        threading.Thread(target=self._hotkey_loop, daemon=True).start()

        if not _registered():
            _register()

        self.tray.showMessage(
            "Cursor Overlay",
            "Ready — press Ctrl+Alt+W to show/hide circle",
            QSystemTrayIcon.MessageIcon.Information,
            3000
        )

    def _build_tray(self):
        px = QPixmap(16, 16)
        px.fill(Qt.GlobalColor.transparent)
        p = QPainter(px)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(20, 20, 20), 1))
        p.setBrush(QBrush(QColor(255, 255, 255)))
        p.drawEllipse(1, 1, 13, 13)
        p.end()

        self.tray = QSystemTrayIcon(QIcon(px))
        self.tray.setToolTip("Cursor Overlay  |  Ctrl+Alt+W")

        m = QMenu()
        m.addAction("Toggle  (Ctrl+Alt+W)", self._toggle)
        m.addSeparator()
        m.addAction("Remove from startup", _unregister)
        m.addAction("Quit", self._quit)
        self.tray.setContextMenu(m)
        self.tray.activated.connect(
            lambda r: self._toggle()
            if r == QSystemTrayIcon.ActivationReason.DoubleClick else None
        )
        self.tray.show()

    def _hotkey_loop(self):
        ok = user32.RegisterHotKey(None, HOTKEY_ID, MOD_CTRL | MOD_ALT, VK_W)
        if not ok:
            self.tray.showMessage("Cursor Overlay",
                                  "Hotkey Ctrl+Alt+W already in use by another app",
                                  QSystemTrayIcon.MessageIcon.Warning, 4000)
            return
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                self.bridge.toggle.emit()

    def _toggle(self):
        if self.circle.isVisible():
            self.circle.hide()
        else:
            self.circle.show()

    def _tick(self):
        pt  = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        now = time.monotonic()
        self.history.append((now, pt.x, pt.y))

        cutoff = now - (TRAIL_MS / 1000) * 2
        while self.history and self.history[0][0] < cutoff:
            self.history.popleft()

        if not self.circle.isVisible():
            return

        target = now - TRAIL_MS / 1000
        tx, ty = pt.x, pt.y
        for ts, x, y in self.history:
            if ts >= target:
                tx, ty = x, y
                break

        # place circle at the tail (bottom-right) of the cursor tip
        self.circle.move(tx + TAIL_X - HALF, ty + TAIL_Y - HALF)

    def _quit(self):
        user32.UnregisterHotKey(None, HOTKEY_ID)
        kernel32.ReleaseMutex(_MUTEX)
        self.qt.quit()

    def run(self):
        sys.exit(self.qt.exec())


if __name__ == "__main__":
    App().run()
