"""
dashboard.py -- native PyQt6 window showing every paired BashIn device (plus
this machine) on an interactive globe: click a device to see its live stats
(RAM, CPU, battery, temperature -- whatever's actually available on that
platform) and its recent task history (what was dispatched to/from it, and
whether it succeeded).

Visual language: dark sci-fi HUD. Rotating wireframe globe with a radar sweep
and starfield, neon cyan/violet accents matching the overlay's palette, glowing
device dots, monospace telemetry with animated stat bars.

Data sources (all already built, this window just visualizes them):
  - lan_mesh.MESH.list_devices()      -- who's paired, who's online right now
  - lan_mesh.MESH.get_telemetry(id)   -- last stats pushed BY a peer
  - lan_mesh.MESH.get_own_telemetry() -- this machine's own last self-read stats
  - task_history.for_device(id)       -- this device's local task log for id

Device dot positions on the globe are derived from a stable hash of each
device_id (not Python's randomized hash()), so a given device stays in the
same spot across restarts instead of jumping around.
"""
import hashlib, math, time

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QFrame, QScrollArea,
)
from PyQt6.QtCore    import Qt, QTimer, pyqtSignal, QPointF, QRectF
from PyQt6.QtGui     import (
    QPainter, QPen, QColor, QBrush, QFont, QRadialGradient,
    QConicalGradient, QLinearGradient,
)

import lan_mesh
import task_history

# ── Palette (matches the overlay's indigo/violet + adds HUD cyan) ─────────────
BG_DEEP       = QColor(7, 9, 16)
GLOBE_CORE    = QColor(16, 20, 36)
GLOBE_RIM     = QColor(96, 110, 170, 160)
GRID_COLOR    = QColor(72, 96, 160, 70)
GRID_BRIGHT   = QColor(100, 140, 220, 110)
SWEEP_COLOR   = QColor(56, 189, 248)          # radar sweep -- cyan
ONLINE_COLOR  = QColor(52, 226, 138)
OFFLINE_COLOR = QColor(110, 114, 128)
SELF_RING     = QColor(129, 140, 248)         # indigo, same family as overlay
ACCENT_CYAN   = "#38bdf8"
ACCENT_VIOLET = "#8b5cf6"
TEXT_DIM      = "#8a93ad"

_MONO = "Consolas"


def _stable_unit(device_id: str) -> float:
    """Deterministic float in [0, 1) from a device_id -- stable across
    restarts (Python's builtin hash() is randomized per-process, so it can't
    be used here without dots jumping around every launch)."""
    h = hashlib.md5(device_id.encode("utf-8")).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF


class GlobeWidget(QWidget):
    deviceClicked = pyqtSignal(str)   # device_id

    def __init__(self):
        super().__init__()
        self.setMinimumSize(400, 400)
        self._devices = []      # [{"device_id","name","online","is_self"}]
        self._selected_id = None
        self._t = 0.0
        self._dot_hitboxes = {}   # device_id -> (cx, cy, r)

    def set_devices(self, devices: list):
        self._devices = devices
        self.update()

    def set_selected(self, device_id):
        self._selected_id = device_id
        self.update()

    def tick(self, dt: float):
        self._t += dt
        self.update()

    def mousePressEvent(self, ev):
        pos = ev.position()
        for device_id, (cx, cy, r) in self._dot_hitboxes.items():
            if (pos.x() - cx) ** 2 + (pos.y() - cy) ** 2 <= (r + 8) ** 2:
                self.deviceClicked.emit(device_id)
                return

    # ── painting ──────────────────────────────────────────────────────────────
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        cx, cy = w / 2, h / 2
        radius = min(w, h) / 2 - 44

        p.fillRect(self.rect(), BG_DEEP)
        self._paint_stars(p, w, h)
        self._paint_globe(p, cx, cy, radius)
        self._paint_sweep(p, cx, cy, radius)
        self._paint_ticks(p, cx, cy, radius)
        self._paint_devices(p, cx, cy, radius)

    def _paint_stars(self, p, w, h):
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(70):
            ux = _stable_unit(f"star-x-{i}")
            uy = _stable_unit(f"star-y-{i}")
            tw = (math.sin(self._t * 0.8 + i * 1.7) + 1) / 2       # slow twinkle
            a  = int(28 + 60 * tw * _stable_unit(f"star-a-{i}"))
            p.setBrush(QBrush(QColor(180, 200, 255, a)))
            r = 1.0 + _stable_unit(f"star-r-{i}")
            p.drawEllipse(QPointF(ux * w, uy * h), r, r)

    def _paint_globe(self, p, cx, cy, radius):
        # Halo behind the sphere
        halo = QRadialGradient(cx, cy, radius * 1.35)
        halo.setColorAt(0.72, QColor(56, 90, 200, 0))
        halo.setColorAt(0.92, QColor(56, 120, 248, 26))
        halo.setColorAt(1.0,  QColor(56, 120, 248, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(halo))
        p.drawEllipse(QPointF(cx, cy), radius * 1.35, radius * 1.35)

        # Sphere body
        grad = QRadialGradient(cx - radius * 0.35, cy - radius * 0.35, radius * 1.7)
        grad.setColorAt(0.0, QColor(44, 54, 92))
        grad.setColorAt(0.6, GLOBE_CORE)
        grad.setColorAt(1.0, QColor(10, 12, 22))
        p.setBrush(QBrush(grad))
        p.setPen(QPen(GLOBE_RIM, 1.6))
        p.drawEllipse(QPointF(cx, cy), radius, radius)

        # Latitude rings
        p.setBrush(Qt.BrushStyle.NoBrush)
        for frac in (0.30, 0.55, 0.78, 0.93):
            p.setPen(QPen(GRID_COLOR, 1))
            p.drawEllipse(QPointF(cx, cy), radius, radius * frac)

        # Rotating meridians: rx = |cos(phase)| gives a turning-sphere illusion
        spin = self._t * 0.5
        for k in range(4):
            phase = spin + k * (math.pi / 4)
            rx = abs(math.cos(phase)) * radius
            front = math.cos(phase) >= 0
            p.setPen(QPen(GRID_BRIGHT if front else GRID_COLOR, 1.2 if front else 1))
            p.drawEllipse(QPointF(cx, cy), rx, radius)

    def _paint_sweep(self, p, cx, cy, radius):
        # Radar sweep: conical gradient tail behind a bright leading edge
        angle_deg = (self._t * 40.0) % 360.0
        grad = QConicalGradient(QPointF(cx, cy), -angle_deg)
        c_head = QColor(SWEEP_COLOR); c_head.setAlpha(70)
        c_tail = QColor(SWEEP_COLOR); c_tail.setAlpha(0)
        grad.setColorAt(0.00, c_head)
        grad.setColorAt(0.18, c_tail)
        grad.setColorAt(1.00, c_tail)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(grad))
        p.drawEllipse(QPointF(cx, cy), radius, radius)

        # Leading edge line
        rad = math.radians(angle_deg)
        edge = QColor(SWEEP_COLOR); edge.setAlpha(150)
        p.setPen(QPen(edge, 1.4))
        p.drawLine(QPointF(cx, cy),
                   QPointF(cx + math.cos(rad) * radius, cy + math.sin(rad) * radius))

    def _paint_ticks(self, p, cx, cy, radius):
        # 60 tick marks around the rim, brighter every 5th -- instrument-dial feel
        for i in range(60):
            a = 2 * math.pi * i / 60
            major = (i % 5 == 0)
            inner = radius + 6
            outer = radius + (14 if major else 10)
            col = QColor(140, 160, 220, 130 if major else 60)
            p.setPen(QPen(col, 1.4 if major else 1))
            p.drawLine(QPointF(cx + math.cos(a) * inner, cy + math.sin(a) * inner),
                       QPointF(cx + math.cos(a) * outer, cy + math.sin(a) * outer))

    def _paint_devices(self, p, cx, cy, radius):
        self._dot_hitboxes = {}
        n = max(len(self._devices), 1)
        for i, d in enumerate(self._devices):
            base_angle = (2 * math.pi * i / n) + (_stable_unit(d["device_id"]) * 0.6)
            lat = 0.35 + 0.55 * _stable_unit(d["device_id"] + "lat")
            dot_x = cx + math.cos(base_angle) * radius * 0.92
            dot_y = cy + math.sin(base_angle) * radius * 0.92 * lat

            online = d.get("online")
            color  = ONLINE_COLOR if online else OFFLINE_COLOR
            base_r = 7 if d.get("is_self") else 6

            if online:
                pulse = (math.sin(self._t * 2.2 + base_angle) + 1) / 2
                glow = QRadialGradient(dot_x, dot_y, base_r + 14)
                gc = QColor(color); gc.setAlpha(int(90 * (1 - pulse * 0.45)))
                g0 = QColor(color); g0.setAlpha(0)
                glow.setColorAt(0.0, gc)
                glow.setColorAt(1.0, g0)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(glow))
                p.drawEllipse(QPointF(dot_x, dot_y), base_r + 8 + pulse * 5, base_r + 8 + pulse * 5)

            if d["device_id"] == self._selected_id:
                # Targeting reticle: ring + four corner ticks
                p.setPen(QPen(QColor(255, 255, 255, 210), 1.6))
                p.setBrush(Qt.BrushStyle.NoBrush)
                rr = base_r + 7
                p.drawEllipse(QPointF(dot_x, dot_y), rr, rr)
                for qa in (45, 135, 225, 315):
                    ra = math.radians(qa + self._t * 30)   # slow reticle rotation
                    p.drawLine(QPointF(dot_x + math.cos(ra) * (rr + 2), dot_y + math.sin(ra) * (rr + 2)),
                               QPointF(dot_x + math.cos(ra) * (rr + 7), dot_y + math.sin(ra) * (rr + 7)))

            if d.get("is_self"):
                p.setPen(QPen(SELF_RING, 2))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(QPointF(dot_x, dot_y), base_r + 3.5, base_r + 3.5)

            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(color))
            p.drawEllipse(QPointF(dot_x, dot_y), base_r, base_r)
            hot = QColor(255, 255, 255, 170)
            p.setBrush(QBrush(hot))
            p.drawEllipse(QPointF(dot_x - base_r * 0.3, dot_y - base_r * 0.3),
                          base_r * 0.3, base_r * 0.3)
            self._dot_hitboxes[d["device_id"]] = (dot_x, dot_y, base_r)

            label = d["name"] + (" · THIS PC" if d.get("is_self") else "")
            p.setFont(QFont(_MONO, 9))
            p.setPen(QColor(200, 210, 235, 230))
            # Flip the label to the dot's left on the right half of the widget,
            # so names near the edge don't clip out of view
            if dot_x > self.width() * 0.62:
                tw = p.fontMetrics().horizontalAdvance(label)
                p.drawText(int(dot_x - base_r - 8 - tw), int(dot_y + 4), label)
            else:
                p.drawText(int(dot_x + base_r + 8), int(dot_y + 4), label)


class StatBar(QWidget):
    """One telemetry row: label, monospace value, and a neon gradient bar.
    pct=None renders an empty track with the value text only (honest N/A)."""

    def __init__(self, label: str):
        super().__init__()
        self._label = label
        self._pct = None       # 0..100 or None
        self._value = "—"
        self.setFixedHeight(40)

    def set(self, pct, value: str):
        self._pct = pct
        self._value = value
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()

        p.setFont(QFont(_MONO, 9))
        p.setPen(QColor(TEXT_DIM))
        p.drawText(0, 14, self._label)
        p.setPen(QColor(224, 230, 245))
        fm_w = p.fontMetrics().horizontalAdvance(self._value)
        p.drawText(w - fm_w, 14, self._value)

        track = QRectF(0, 22, w, 8)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(28, 33, 52)))
        p.drawRoundedRect(track, 4, 4)

        if self._pct is not None:
            fill_w = max(8.0, w * min(max(self._pct, 0), 100) / 100.0)
            grad = QLinearGradient(0, 0, w, 0)
            grad.setColorAt(0.0, QColor(56, 189, 248))
            grad.setColorAt(1.0, QColor(139, 92, 246))
            p.setBrush(QBrush(grad))
            p.drawRoundedRect(QRectF(0, 22, fill_w, 8), 4, 4)


class DashboardWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("BashIn — Device Mesh")
        self.resize(1000, 600)
        self._selected_id = None
        self._last_tick = time.monotonic()

        self.setStyleSheet(f"""
            DashboardWindow {{ background: #070910; }}
            QLabel  {{ color: #dfe4f2; background: transparent; }}
            QFrame#panel {{
                background: #0c0f1c;
                border: 1px solid #1e2540;
                border-radius: 10px;
            }}
            QLabel#header {{
                color: {ACCENT_CYAN};
                font-family: {_MONO};
                font-size: 11px;
                letter-spacing: 3px;
            }}
            QLabel#devname {{ font-size: 17px; font-weight: bold; letter-spacing: 1px; }}
            QLabel#dim {{ color: {TEXT_DIM}; font-family: {_MONO}; font-size: 9pt; }}
            QListWidget {{
                background: #0a0d18;
                border: 1px solid #1e2540;
                border-radius: 8px;
                color: #c6cde0;
                font-family: {_MONO};
                font-size: 9pt;
                outline: none;
            }}
            QListWidget::item {{ padding: 5px 8px; border-bottom: 1px solid #141a2e; }}
            QScrollArea {{ border: none; background: transparent; }}
            QScrollBar:vertical {{
                background: #0a0d18; width: 8px; border-radius: 4px;
            }}
            QScrollBar::handle:vertical {{
                background: #2a3354; border-radius: 4px; min-height: 24px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 14)
        outer.setSpacing(10)

        # ── Top HUD bar ──────────────────────────────────────────────────────
        top = QHBoxLayout()
        title = QLabel("◉  BASHIN MESH — DEVICE GRID")
        title.setObjectName("header")
        top.addWidget(title)
        top.addStretch(1)
        self.count_label = QLabel("")
        self.count_label.setObjectName("header")
        top.addWidget(self.count_label)
        outer.addLayout(top)

        root = QHBoxLayout()
        root.setSpacing(12)
        outer.addLayout(root, 1)

        self.globe = GlobeWidget()
        self.globe.deviceClicked.connect(self._on_device_selected)
        root.addWidget(self.globe, 3)

        # ── Detail panel ─────────────────────────────────────────────────────
        panel = QFrame()
        panel.setObjectName("panel")
        panel.setMinimumWidth(320)
        pv = QVBoxLayout(panel)
        pv.setContentsMargins(16, 14, 16, 14)
        pv.setSpacing(8)

        self.name_label = QLabel("SELECT A NODE")
        self.name_label.setObjectName("devname")
        pv.addWidget(self.name_label)

        self.status_label = QLabel("· click a device on the globe ·")
        self.status_label.setObjectName("dim")
        pv.addWidget(self.status_label)

        tele_hdr = QLabel("TELEMETRY")
        tele_hdr.setObjectName("header")
        pv.addSpacing(6)
        pv.addWidget(tele_hdr)

        self.bar_ram  = StatBar("RAM")
        self.bar_cpu  = StatBar("CPU")
        self.bar_batt = StatBar("BATTERY")
        self.bar_temp = StatBar("CPU TEMP")
        for b in (self.bar_ram, self.bar_cpu, self.bar_batt, self.bar_temp):
            pv.addWidget(b)

        self.updated_label = QLabel("")
        self.updated_label.setObjectName("dim")
        pv.addWidget(self.updated_label)

        task_hdr = QLabel("TASK LOG")
        task_hdr.setObjectName("header")
        pv.addSpacing(6)
        pv.addWidget(task_hdr)

        self.task_list = QListWidget()
        pv.addWidget(self.task_list, 1)

        scroll = QScrollArea()
        scroll.setWidget(panel)
        scroll.setWidgetResizable(True)
        root.addWidget(scroll, 2)

        # Fast timer: smooth animation. Slow timer: pull fresh data.
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(50)
        self._anim_timer.timeout.connect(self._tick)
        self._anim_timer.start()

        self._data_timer = QTimer(self)
        self._data_timer.setInterval(2000)
        self._data_timer.timeout.connect(self._refresh_data)
        self._data_timer.start()

        self._refresh_data()

    # ── data plumbing ────────────────────────────────────────────────────────
    def _tick(self):
        now = time.monotonic()
        dt = now - self._last_tick
        self._last_tick = now
        self.globe.tick(dt)

    def _current_devices(self) -> list:
        raw = [d for d in lan_mesh.MESH.list_devices() if d.get("paired")]
        devices = [{"device_id": d["device_id"], "name": d["name"],
                   "online": bool(d.get("ip")), "is_self": False} for d in raw]
        devices.insert(0, {"device_id": lan_mesh.MESH.device_id,
                           "name": lan_mesh.MESH.device_name or "This PC",
                           "online": True, "is_self": True})
        return devices

    def _refresh_data(self):
        devices = self._current_devices()
        self.globe.set_devices(devices)
        online = sum(1 for d in devices if d["online"])
        self.count_label.setText(f"{online}/{len(devices)} NODES ONLINE")
        if self._selected_id:
            self._render_detail(self._selected_id, devices)

    def _on_device_selected(self, device_id: str):
        self._selected_id = device_id
        self.globe.set_selected(device_id)
        self._render_detail(device_id, self._current_devices())

    def _render_detail(self, device_id: str, devices: list):
        info = next((d for d in devices if d["device_id"] == device_id), None)
        if not info:
            return

        self.name_label.setText(info["name"].upper() + ("  ·  THIS PC" if info["is_self"] else ""))
        if info["online"]:
            self.status_label.setText("● ONLINE")
            self.status_label.setStyleSheet(f"color: #34e28a; font-family: {_MONO};")
        else:
            self.status_label.setText("○ OFFLINE")
            self.status_label.setStyleSheet(f"color: #6e7280; font-family: {_MONO};")

        stats = (lan_mesh.MESH.get_own_telemetry() if info["is_self"]
                 else lan_mesh.MESH.get_telemetry(device_id))
        self._render_stats(stats)

        self.task_list.clear()
        entries = task_history.for_device(device_id, limit=100)
        if not entries:
            it = QListWidgetItem("no tasks recorded yet")
            it.setFlags(Qt.ItemFlag.NoItemFlags)
            it.setForeground(QColor(TEXT_DIM))
            self.task_list.addItem(it)
            return
        for e in entries:
            ts = time.strftime("%H:%M:%S", time.localtime(e.get("ts", 0)))
            ok = e.get("ok")
            badge = "▮ OK  " if ok else "▮ FAIL"
            src, tgt = e.get("source_name", "?"), e.get("target_name", "?")
            result = (e.get("result") or "")[:76]
            it = QListWidgetItem(f"{ts}  {badge}  {e.get('intent','?').upper()}  {src} → {tgt}\n{result}")
            it.setFlags(Qt.ItemFlag.NoItemFlags)
            it.setForeground(QColor(52, 226, 138) if ok else QColor(244, 96, 108))
            self.task_list.addItem(it)

    def _render_stats(self, stats):
        if not stats:
            for b in (self.bar_ram, self.bar_cpu, self.bar_batt, self.bar_temp):
                b.set(None, "awaiting signal")
            self.updated_label.setText("no telemetry received yet")
            return
        if stats.get("error"):
            for b in (self.bar_ram, self.bar_cpu, self.bar_batt, self.bar_temp):
                b.set(None, "—")
            self.updated_label.setText(stats["error"])
            return

        if stats.get("ram_total_gb") is not None:
            self.bar_ram.set(stats.get("ram_pct"),
                             f"{stats['ram_used_gb']:.1f}/{stats['ram_total_gb']:.1f} GB")
        else:
            self.bar_ram.set(None, "N/A")

        if stats.get("cpu_pct") is not None:
            self.bar_cpu.set(stats["cpu_pct"],
                             f"{stats['cpu_pct']:.0f}%  ·  {stats.get('cpu_count') or '?'} cores")
        else:
            self.bar_cpu.set(None, "N/A")

        if stats.get("battery_pct") is not None:
            plugged = " ⚡" if stats.get("battery_plugged") else ""
            self.bar_batt.set(stats["battery_pct"], f"{stats['battery_pct']:.0f}%{plugged}")
        else:
            self.bar_batt.set(None, "N/A · no battery")

        if stats.get("cpu_temp_c") is not None:
            # scale 0..100C for the bar -- fine for CPU-temperature purposes
            self.bar_temp.set(min(stats["cpu_temp_c"], 100.0), f"{stats['cpu_temp_c']:.1f}°C")
        else:
            self.bar_temp.set(None, "N/A · not exposed")

        age = time.time() - stats.get("received_at", time.time())
        self.updated_label.setText(f"last signal {age:.0f}s ago")


_window = None

def open_dashboard():
    """Singleton: reuse the existing window if already open, else create one."""
    global _window
    if _window is None or not _window.isVisible():
        _window = DashboardWindow()
    _window.show()
    _window.raise_()
    _window.activateWindow()
    return _window
