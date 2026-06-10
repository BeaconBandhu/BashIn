"""
Qt widgets: Overlay (cursor image / EQ dots / guide crosshair) and ResponseBubble.
"""
import math, ctypes, time

from PyQt6.QtWidgets import QWidget, QApplication
from PyQt6.QtCore    import Qt, QTimer, QPointF
from PyQt6.QtGui     import QPainter, QPen, QColor, QBrush, QFont, QPolygonF

from constants import (
    user32, dwmapi,
    GWL_EXSTYLE, WS_EX_LAYERED, WS_EX_TRANSPARENT,
    CIRCLE_SIZE, CIRCLE_HALF, DOT_R, RING_R,
    EQ_W, EQ_H, EQ_MAX_R, EQ_MIN_R, NUM_BARS,
    GUIDE_W, GUIDE_H, GUIDE_INNER_R, GUIDE_OUTER_MAX,
)


class Overlay(QWidget):
    def __init__(self):
        super().__init__()
        self.eq_mode        = False
        self.guide_mode     = False
        self._transitioning = False
        self._trans_t       = 0.0
        self.levels         = [0.0] * NUM_BARS
        self._guide_pulse   = 0.0
        self._eq_style      = "listen"   # "listen" | "speak"
        self._ring_pulse    = 0.0        # sonar pulse 0-1 during processing
        self._speak_phase   = 0.0        # travelling wave phase for speak
        self._audio_level   = 0.0        # smoothed peak level for listen ripples
        # dot offset from window centre (cursor tip - ring centre)
        self._dot_dx        = 0
        self._dot_dy        = 0

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint |
                            Qt.WindowType.WindowStaysOnTopHint |
                            Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(CIRCLE_SIZE, CIRCLE_SIZE)

    # ── Win32 transparency + DWM ───────────────────────────────────────────────
    def _apply_win32(self):
        hwnd  = int(self.winId())
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE,
                              style | WS_EX_LAYERED | WS_EX_TRANSPARENT)
        v = ctypes.c_int(1)
        dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(v), ctypes.sizeof(v))
        dwmapi.DwmSetWindowAttribute(hwnd,  2, ctypes.byref(v), ctypes.sizeof(v))

    def showEvent(self, e):
        super().showEvent(e)
        self._apply_win32()

    def set_dot_offset(self, dx, dy):
        self._dot_dx = dx
        self._dot_dy = dy
        self.update()

    # ── Mode transitions ───────────────────────────────────────────────────────
    def enter_eq(self, cx, cy):
        self.eq_mode = True; self.guide_mode = False; self._transitioning = False
        self.levels       = [0.0] * NUM_BARS
        self._audio_level = 0.0
        self.setFixedSize(CIRCLE_SIZE, CIRCLE_SIZE)
        self.move(cx - CIRCLE_HALF, cy - CIRCLE_HALF)
        self.show(); self._apply_win32()

    def leave_eq(self):
        self.eq_mode = False; self.guide_mode = False; self._transitioning = False
        self.setFixedSize(CIRCLE_SIZE, CIRCLE_SIZE)
        self.update()

    def enter_guide(self, tx, ty):
        self.guide_mode = True; self.eq_mode = False; self._transitioning = False
        self.setFixedSize(GUIDE_W, GUIDE_H)
        self.move(tx - GUIDE_W // 2, ty - GUIDE_H // 2)
        self.show(); self._apply_win32()

    def leave_guide(self):
        self.guide_mode = False; self.eq_mode = False; self._transitioning = False
        self.setFixedSize(CIRCLE_SIZE, CIRCLE_SIZE)
        self.update()

    # ── Rolling transition cursor → EQ ────────────────────────────────────────
    def begin_transition(self, cx, cy):
        self._transitioning = True; self._trans_t = 0.0
        self.eq_mode = False; self.guide_mode = False
        self.levels       = [0.0] * NUM_BARS
        self._audio_level = 0.0
        self.setFixedSize(CIRCLE_SIZE, CIRCLE_SIZE)
        self.move(cx - CIRCLE_HALF, cy - CIRCLE_HALF)
        self.show(); self._apply_win32()

    def set_trans_t(self, t):
        self._trans_t = t
        self.update()

    def finish_transition(self):
        self._transitioning = False; self.eq_mode = True
        self.update()

    # ── Level / pulse setters ──────────────────────────────────────────────────
    def update_levels(self, levels):
        for i in range(NUM_BARS):
            self.levels[i] = self.levels[i] * 0.55 + levels[i] * 0.45
        self._audio_level = max(self.levels)
        self.update()

    def update_guide_pulse(self, pulse):
        self._guide_pulse = pulse
        self.update()

    def set_eq_style(self, style):
        self._eq_style = style
        self.update()

    def update_ring_pulse(self, pulse):
        self._ring_pulse = pulse
        self.update()

    # ── Paint ──────────────────────────────────────────────────────────────────
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        cx = self.width()  // 2
        cy = self.height() // 2

        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        p.fillRect(self.rect(), Qt.GlobalColor.transparent)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

        # Guide crosshair: pulsing outer ring + inner dot + crosshair lines
        if self.guide_mode:
            pulse   = self._guide_pulse
            r_outer = int(GUIDE_INNER_R * 1.8 + pulse * (GUIDE_OUTER_MAX - GUIDE_INNER_R * 1.8))
            p.setPen(QPen(QColor(255, 80, 60), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(cx - r_outer, cy - r_outer, r_outer * 2, r_outer * 2)
            p.setPen(QPen(QColor(255, 160, 60), 2))
            p.drawEllipse(cx - GUIDE_INNER_R, cy - GUIDE_INNER_R,
                          GUIDE_INNER_R * 2, GUIDE_INNER_R * 2)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(QColor(255, 60, 60)))
            p.drawEllipse(cx - 3, cy - 3, 6, 6)
            gap = GUIDE_INNER_R + 2
            p.setPen(QPen(QColor(255, 80, 60), 1))
            p.drawLine(2, cy, cx - gap, cy)
            p.drawLine(cx + gap, cy, self.width() - 2, cy)
            p.drawLine(cx, 2, cx, cy - gap)
            p.drawLine(cx, cy + gap, cx, self.height() - 2)
            return

        # Active mode: listening (pulsing ripple rings) or speaking (wavy circle)
        if self.eq_mode or self._transitioning:
            if self._eq_style == "speak":
                # Flowing wavy circle — sine wave travelling around the ring perimeter
                phase = self._speak_phase
                pts = []
                for j in range(73):
                    theta = 2 * math.pi * j / 72
                    r = RING_R + 3.5 * math.sin(5 * theta + phase)
                    pts.append(QPointF(cx + r * math.cos(theta),
                                       cy + r * math.sin(theta)))
                p.setPen(QPen(QColor(210, 35, 35, 200), 2))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawPolyline(QPolygonF(pts))
            else:
                # Listen: 3 sonar rings + pulsing red ring (mirrors website cp-sonar)
                lvl    = self._audio_level
                now    = time.monotonic()
                period = 1.8
                p.setBrush(Qt.BrushStyle.NoBrush)
                # 3 sonar rings staggered 0.6s each, expand and fade
                for i in range(3):
                    prog = ((now + i * 0.6) % period) / period   # 0→1 per cycle
                    r    = RING_R + prog * (RING_R * 1.5 + lvl * RING_R)
                    a    = int((70 + lvl * 110) * (1.0 - prog))
                    if a > 4:
                        p.setPen(QPen(QColor(239, 68, 68, a), 1))
                        p.drawEllipse(int(cx - r), int(cy - r), int(r * 2), int(r * 2))
                # Main ring: red, breathing glow (listenRingPulse)
                ring_a = int(160 + 55 * math.sin(now * math.pi * 1.67))
                p.setPen(QPen(QColor(239, 68, 68, max(105, ring_a)), 2))
                p.drawEllipse(cx - RING_R, cy - RING_R, RING_R * 2, RING_R * 2)
            # Dot: red in listen mode, white in speak mode
            dot_col = QColor(239, 68, 68, 220) if self._eq_style == "listen" else QColor(255, 255, 255)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(dot_col))
            p.drawEllipse(cx - DOT_R, cy - DOT_R, DOT_R * 2, DOT_R * 2)
            return

        # Processing: orbit ring + 3 spinning violet dots (matches website cp-orbit)
        if self._ring_pulse > 0:
            now      = time.monotonic()
            orbit_r  = RING_R + 4
            spin     = now * (2 * math.pi / 1.4)   # full rotation every 1.4s

            # Indigo glow ring
            p.setPen(QPen(QColor(129, 140, 248, 210), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(cx - RING_R, cy - RING_R, RING_R * 2, RING_R * 2)

            # 3 orbit dots equally spaced (120° apart), rotating
            p.setPen(Qt.PenStyle.NoPen)
            for i in range(3):
                a  = spin + i * (2 * math.pi / 3)
                ox = int(cx + orbit_r * math.cos(a))
                oy = int(cy + orbit_r * math.sin(a))
                p.setBrush(QBrush(QColor(139, 92, 246, 235)))
                p.drawEllipse(ox - 1, oy - 1, 2, 2)

            # Indigo dot at center
            p.setBrush(QBrush(QColor(129, 140, 248, 220)))
            p.drawEllipse(cx - DOT_R, cy - DOT_R, DOT_R * 2, DOT_R * 2)
            return

        # Idle: lagging purple ring + white trailing dot
        p.setPen(QPen(QColor(148, 102, 255), 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(cx - RING_R, cy - RING_R, RING_R * 2, RING_R * 2)
        dx = cx + self._dot_dx
        dy = cy + self._dot_dy
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(255, 255, 255)))
        p.drawEllipse(int(dx - DOT_R), int(dy - DOT_R), DOT_R * 2, DOT_R * 2)


class ResponseBubble(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint |
                            Qt.WindowType.WindowStaysOnTopHint |
                            Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._text  = ""
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def show_text(self, text, ax, ay, timeout_ms=12000):
        self._text = text
        lines = max(3, len(text) // 45 + text.count("\n") + 1)
        h     = min(lines * 20 + 28, 320)
        self.setFixedSize(400, h)
        scr = QApplication.primaryScreen().geometry()
        self.move(min(ax, scr.width() - 410), min(ay + 24, scr.height() - h - 10))
        self.show()
        self.raise_()
        self._timer.stop()
        if timeout_ms > 0:
            self._timer.start(timeout_ms)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QBrush(QColor(18, 18, 28, 235)))
        p.setPen(QPen(QColor(80, 120, 255), 1))
        p.drawRoundedRect(0, 0, self.width() - 1, self.height() - 1, 10, 10)
        p.setPen(QColor(220, 220, 245))
        p.setFont(QFont("Segoe UI", 9))
        p.drawText(self.rect().adjusted(12, 10, -12, -10),
                   Qt.TextFlag.TextWordWrap, self._text)

    def mousePressEvent(self, _):
        self.hide()
