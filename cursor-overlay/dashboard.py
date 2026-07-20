"""
dashboard.py -- native PyQt6 window showing every paired BashIn device (plus
this machine) on an interactive globe: click a device to see its live stats
(RAM, CPU, battery, temperature -- whatever's actually available on that
platform) and its recent task history (what was dispatched to/from it, and
whether it succeeded).

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
from PyQt6.QtCore    import Qt, QTimer, pyqtSignal, QPointF
from PyQt6.QtGui     import QPainter, QPen, QColor, QBrush, QFont, QRadialGradient

import lan_mesh
import task_history

GLOBE_BG      = QColor(15, 17, 26)
GLOBE_RIM     = QColor(80, 90, 140, 140)
GRID_COLOR    = QColor(70, 80, 120, 90)
ONLINE_COLOR  = QColor(70, 220, 140)
OFFLINE_COLOR = QColor(120, 120, 130)
SELF_RING     = QColor(129, 140, 248)


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
        self.setMinimumSize(360, 360)
        self._devices = []      # [{"device_id","name","online","is_self"}]
        self._selected_id = None
        self._pulse_t = 0.0
        self._dot_hitboxes = {}   # device_id -> (cx, cy, r)

    def set_devices(self, devices: list):
        self._devices = devices
        self.update()

    def set_selected(self, device_id):
        self._selected_id = device_id
        self.update()

    def tick(self, dt: float):
        self._pulse_t += dt
        self.update()

    def mousePressEvent(self, ev):
        pos = ev.position()
        for device_id, (cx, cy, r) in self._dot_hitboxes.items():
            if (pos.x() - cx) ** 2 + (pos.y() - cy) ** 2 <= (r + 6) ** 2:
                self.deviceClicked.emit(device_id)
                return

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        cx, cy = w / 2, h / 2
        radius = min(w, h) / 2 - 30

        # Globe body: radial gradient for a subtle sphere look
        grad = QRadialGradient(cx - radius * 0.3, cy - radius * 0.3, radius * 1.6)
        grad.setColorAt(0.0, QColor(40, 46, 74))
        grad.setColorAt(1.0, GLOBE_BG)
        p.setBrush(QBrush(grad))
        p.setPen(QPen(GLOBE_RIM, 2))
        p.drawEllipse(QPointF(cx, cy), radius, radius)

        # "Latitude" rings + a couple of "meridian" arcs, purely decorative
        p.setPen(QPen(GRID_COLOR, 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        for frac in (0.35, 0.6, 0.82):
            ry = radius * frac
            p.drawEllipse(QPointF(cx, cy), radius, ry)
        for frac in (0.35, 0.65):
            rx = radius * frac
            p.drawEllipse(QPointF(cx, cy), rx, radius)

        # Device dots, evenly-but-stably distributed around the globe's rim
        self._dot_hitboxes = {}
        n = max(len(self._devices), 1)
        for i, d in enumerate(self._devices):
            base_angle = (2 * math.pi * i / n) + (_stable_unit(d["device_id"]) * 0.6)
            # gentle vertical spread so dots aren't all on one ring
            lat = 0.35 + 0.55 * _stable_unit(d["device_id"] + "lat")
            dx = math.cos(base_angle) * radius * 0.92
            dy = math.sin(base_angle) * radius * 0.92 * lat
            dot_x, dot_y = cx + dx, cy + dy

            online = d.get("online")
            color  = ONLINE_COLOR if online else OFFLINE_COLOR
            base_r = 7 if d.get("is_self") else 6

            if online:
                pulse = (math.sin(self._pulse_t * 2.2 + base_angle) + 1) / 2   # 0..1
                glow_r = base_r + 5 + pulse * 5
                glow = QColor(color); glow.setAlpha(int(70 * (1 - pulse * 0.5)))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(glow))
                p.drawEllipse(QPointF(dot_x, dot_y), glow_r, glow_r)

            if d["device_id"] == self._selected_id:
                p.setPen(QPen(QColor(255, 255, 255, 220), 2))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(QPointF(dot_x, dot_y), base_r + 5, base_r + 5)

            if d.get("is_self"):
                p.setPen(QPen(SELF_RING, 2))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(QPointF(dot_x, dot_y), base_r + 3, base_r + 3)

            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(color))
            p.drawEllipse(QPointF(dot_x, dot_y), base_r, base_r)
            self._dot_hitboxes[d["device_id"]] = (dot_x, dot_y, base_r)

            p.setPen(QColor(225, 228, 240))
            p.setFont(QFont("Segoe UI", 9))
            label = d["name"] + (" (this PC)" if d.get("is_self") else "")
            p.drawText(int(dot_x + base_r + 6), int(dot_y + 4), label)


class DashboardWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("BashIn -- Device Mesh Dashboard")
        self.resize(920, 560)
        self._selected_id = None
        self._last_tick = time.monotonic()

        root = QHBoxLayout(self)

        self.globe = GlobeWidget()
        self.globe.deviceClicked.connect(self._on_device_selected)
        root.addWidget(self.globe, 3)

        # ── Detail panel ────────────────────────────────────────────────────
        panel = QFrame()
        panel.setFrameShape(QFrame.Shape.NoFrame)
        panel.setMinimumWidth(300)
        pv = QVBoxLayout(panel)

        self.name_label = QLabel("Select a device on the globe")
        self.name_label.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
        pv.addWidget(self.name_label)

        self.status_label = QLabel("")
        pv.addWidget(self.status_label)

        self.stats_label = QLabel("")
        self.stats_label.setWordWrap(True)
        self.stats_label.setTextFormat(Qt.TextFormat.RichText)
        pv.addWidget(self.stats_label)

        pv.addWidget(QLabel("Recent tasks:"))
        self.task_list = QListWidget()
        pv.addWidget(self.task_list, 1)

        scroll = QScrollArea()
        scroll.setWidget(panel)
        scroll.setWidgetResizable(True)
        root.addWidget(scroll, 2)

        # Fast timer: smooth pulse animation. Slow timer: pull fresh data.
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(50)
        self._anim_timer.timeout.connect(self._tick)
        self._anim_timer.start()

        self._data_timer = QTimer(self)
        self._data_timer.setInterval(2000)
        self._data_timer.timeout.connect(self._refresh_data)
        self._data_timer.start()

        self._refresh_data()

    def _tick(self):
        now = time.monotonic()
        dt = now - self._last_tick
        self._last_tick = now
        self.globe.tick(dt)

    def _refresh_data(self):
        raw = [d for d in lan_mesh.MESH.list_devices() if d.get("paired")]
        devices = [{"device_id": d["device_id"], "name": d["name"],
                   "online": bool(d.get("ip")), "is_self": False} for d in raw]
        devices.insert(0, {"device_id": lan_mesh.MESH.device_id,
                           "name": lan_mesh.MESH.device_name or "This PC",
                           "online": True, "is_self": True})
        self.globe.set_devices(devices)

        if self._selected_id:
            self._render_detail(self._selected_id, devices)

    def _on_device_selected(self, device_id: str):
        self._selected_id = device_id
        self.globe.set_selected(device_id)
        raw = [d for d in lan_mesh.MESH.list_devices() if d.get("paired")]
        devices = [{"device_id": d["device_id"], "name": d["name"],
                   "online": bool(d.get("ip")), "is_self": False} for d in raw]
        devices.insert(0, {"device_id": lan_mesh.MESH.device_id,
                           "name": lan_mesh.MESH.device_name or "This PC",
                           "online": True, "is_self": True})
        self._render_detail(device_id, devices)

    def _render_detail(self, device_id: str, devices: list):
        info = next((d for d in devices if d["device_id"] == device_id), None)
        if not info:
            return

        self.name_label.setText(info["name"] + (" (this PC)" if info["is_self"] else ""))
        self.status_label.setText(
            "● online" if info["online"] else "○ offline")
        self.status_label.setStyleSheet(
            f"color: {'#46dc8c' if info['online'] else '#78787f'};")

        stats = (lan_mesh.MESH.get_own_telemetry() if info["is_self"]
                 else lan_mesh.MESH.get_telemetry(device_id))
        self.stats_label.setText(self._format_stats(stats))

        self.task_list.clear()
        entries = task_history.for_device(device_id, limit=100)
        if not entries:
            it = QListWidgetItem("No tasks recorded yet.")
            it.setFlags(Qt.ItemFlag.NoItemFlags)
            self.task_list.addItem(it)
            return
        for e in entries:
            ts = time.strftime("%H:%M:%S", time.localtime(e.get("ts", 0)))
            badge = "OK" if e.get("ok") else "FAIL"
            src, tgt = e.get("source_name", "?"), e.get("target_name", "?")
            result = (e.get("result") or "")[:80]
            it = QListWidgetItem(f"[{ts}] {badge}  {e.get('intent','?')}  {src} -> {tgt}\n{result}")
            it.setFlags(Qt.ItemFlag.NoItemFlags)
            if not e.get("ok"):
                it.setForeground(QColor(230, 100, 100))
            self.task_list.addItem(it)

    @staticmethod
    def _format_stats(stats):
        if not stats:
            return "<i>No telemetry received yet.</i>"
        if stats.get("error"):
            return f"<i>{stats['error']}</i>"

        def row(label, value):
            return f"<b>{label}:</b> {value}<br>"

        out = ""
        if stats.get("ram_total_gb") is not None:
            out += row("RAM", f"{stats['ram_used_gb']:.1f} / {stats['ram_total_gb']:.1f} GB "
                              f"({stats.get('ram_pct', 0):.0f}%)")
        else:
            out += row("RAM", "N/A")

        if stats.get("cpu_pct") is not None:
            out += row("CPU", f"{stats['cpu_pct']:.0f}% "
                              f"({stats.get('cpu_count') or '?'} cores)")
        else:
            out += row("CPU", "N/A")

        if stats.get("battery_pct") is not None:
            plugged = " (plugged in)" if stats.get("battery_plugged") else ""
            out += row("Battery", f"{stats['battery_pct']:.0f}%{plugged}")
        else:
            out += row("Battery", "N/A (no battery on this device)")

        if stats.get("cpu_temp_c") is not None:
            out += row("CPU Temp", f"{stats['cpu_temp_c']:.1f}°C")
        else:
            out += row("CPU Temp", "N/A (not exposed on this platform)")

        age = time.time() - stats.get("received_at", time.time())
        out += f"<span style='color:#888'>updated {age:.0f}s ago</span>"
        return out


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
