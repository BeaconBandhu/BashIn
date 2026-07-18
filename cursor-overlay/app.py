"""
App: main orchestrator — wires hotkeys, voice pipeline, guide mode, and tray together.
"""
import os, ctypes, threading, time, json, math, logging
from collections import deque
from ctypes import wintypes

import numpy as np
import sounddevice as sd
from openai import OpenAI

from PyQt6.QtWidgets import QApplication, QSystemTrayIcon, QMenu, QInputDialog, QMessageBox
from PyQt6.QtCore    import Qt, QTimer
from PyQt6.QtGui     import QPainter, QPen, QColor, QBrush, QIcon, QPixmap

from constants import (
    user32, kernel32,
    WM_HOTKEY, MOD_CTRL, MOD_ALT, VK_W, VK_B, VK_LBUTTON,
    HK_CIRCLE, HK_VOICE,
    IDLE, LISTENING, PROCESSING, SPEAKING, GUIDING,
    CIRCLE_HALF, TRAIL_MS, TICK_MS, TAIL_X, TAIL_Y,
    TRANS_DURATION, RING_LERP,
)
from config  import load_cfg, save_cfg, register, unregister, registered, ensure_identity
from audio   import WakeWordListener, VoiceRecorder
from screen  import capture_screen, GUIDE_SYS, APP_ANALYZE_PROMPT
from bridge   import Bridge
from widgets  import Overlay, ResponseBubble
from execute  import run_step
import agents
from chrome_bridge import BRIDGE
import lan_mesh


class App:
    def __init__(self, mutex=None):
        self._mutex = mutex

        self.qt      = QApplication.instance() or QApplication([])
        self.qt.setQuitOnLastWindowClosed(False)
        self.overlay = Overlay()
        self.bubble  = ResponseBubble()
        self.bubble.show_text = lambda *args, **kwargs: None
        self.bridge  = Bridge()
        self.history = deque()

        self.conv_state       = IDLE
        self._conv_history    = []
        self._anchor          = (0, 0)
        self._anim_mode       = "listening"
        self._pending_guide   = None
        self._pending_auto    = None

        self._guide_steps     = []
        self._guide_idx       = 0
        self._guide_xs        = 1.0
        self._guide_ys        = 1.0
        self._guide_step_time = 0.0
        self._was_lbutton     = False
        self._guide_target    = None   # (tx, ty) in screen coords, or None
        self._click_pos       = None   # cursor pos recorded on mouse-down
        self._ring_x          = 0.0
        self._ring_y          = 0.0

        # App awareness
        self._app_name        = ""     # foreground window title
        self._app_context     = ""     # GPT-4o description of current app/screen
        self._last_app_name   = ""     # detect app switches

        cfg = ensure_identity(load_cfg())
        self.api_key     = cfg.get("openai_api_key", "")
        self.wake_phrase = cfg.get("wake_phrase", "hey")

        self.recorder = VoiceRecorder(
            on_levels  = lambda lvl: self.bridge.set_levels.emit(lvl),
            on_silence = lambda: self.bridge.silence_detected.emit(),
        )
        self._wake = WakeWordListener(
            get_phrase = lambda: self.wake_phrase,
            get_key    = lambda: self.api_key,
            on_wake    = lambda: self.bridge.wake_detected.emit(),
        )

        self._anim_timer    = QTimer(); self._anim_timer.setInterval(50)
        self._anim_timer.timeout.connect(self._animate)
        self._trans_timer   = QTimer(); self._trans_timer.setInterval(16)
        self._trans_timer.timeout.connect(self._trans_step)
        self._trans_elapsed = 0.0

        # Guide-mode: smooth cursor-image glide to target
        self._guide_move_timer   = QTimer(); self._guide_move_timer.setInterval(16)
        self._guide_move_timer.timeout.connect(self._guide_move_step)
        self._gm_sx = self._gm_sy = 0   # start widget pos
        self._gm_tx = self._gm_ty = 0   # target widget pos
        self._gm_elapsed  = 0.0
        self._gm_duration = 0.55         # seconds — feels fast but trackable

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
        self.bridge.mesh_pairing_result.connect(self._on_pairing_result)

        self.timer = QTimer(); self.timer.timeout.connect(self._tick)
        self.timer.start(TICK_MS)

        # Periodic tray "Devices" list refresh (mirrors _anim_timer's pattern) —
        # polls lan_mesh's lock-protected registry snapshot, safe from the Qt thread.
        self._mesh_timer = QTimer(); self._mesh_timer.setInterval(3000)
        self._mesh_timer.timeout.connect(self._refresh_devices_menu)
        self._mesh_timer.start()

        self._build_tray()
        # Start the local WS server so the BashIn Chrome extension can connect
        try:
            BRIDGE.start()
        except Exception as e:
            logging.warning("BRIDGE.start failed: %s", e)
        # Start the LAN device mesh (discovery + dispatch server) for cross-PC tasks
        try:
            lan_mesh.MESH.set_pairing_result_callback(
                lambda ok, msg: self.bridge.mesh_pairing_result.emit(ok, msg))
            lan_mesh.MESH.start()
        except Exception as e:
            logging.warning("lan_mesh.MESH.start failed: %s", e)
        threading.Thread(target=self._hotkey_loop, daemon=True).start()
        if not registered():
            register()

        msg = ("No OpenAI key — right-click tray → Set API Key"
               if not self.api_key else
               f'Ready  |  W: cursor  B: talk  wake: "{self.wake_phrase}"')
        self.tray.showMessage(
            "Cursor Overlay", msg,
            QSystemTrayIcon.MessageIcon.Warning if not self.api_key
            else QSystemTrayIcon.MessageIcon.Information, 3000)

    # ── Tray ──────────────────────────────────────────────────────────────────
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
        self.tray.setToolTip("Cursor Overlay")
        m = QMenu()
        m.addAction("Toggle Cursor  (Ctrl+Alt+W)",     self._toggle_circle)
        m.addAction("Conversation Mode  (Ctrl+Alt+B)", self._toggle_voice)
        m.addSeparator()
        m.addAction("Set OpenAI API Key",              self._prompt_api_key)
        m.addAction("Set Wake Phrase",                 self._prompt_wake_phrase)
        m.addAction("Clear conversation history",      self._clear_history)
        m.addSeparator()
        self._devices_menu = m.addMenu("Devices")      # rebuilt by _refresh_devices_menu()
        m.addAction("Pair New Device...",              self._pair_new_device)
        m.addAction("Enter Pairing Code...",           self._enter_pairing_code)
        m.addSeparator()
        m.addAction("Remove from startup",             unregister)
        m.addAction("Quit",                            self._quit)
        self.tray.setContextMenu(m)
        self.tray.show()

    # ── LAN device mesh (tray UI) ─────────────────────────────────────────────
    def _refresh_devices_menu(self):
        self._devices_menu.clear()
        devices = [d for d in lan_mesh.MESH.list_devices() if d.get("paired")]
        if not devices:
            a = self._devices_menu.addAction("No paired devices yet")
            a.setEnabled(False)
            return
        for d in devices:
            online = bool(d.get("ip"))
            dot = "●" if online else "○"
            state = "online" if online else "offline"
            a = self._devices_menu.addAction(f"{dot} {d['name']} ({state})")
            a.setEnabled(False)

    def _pair_new_device(self):
        code = lan_mesh.MESH.begin_pairing()
        QMessageBox.information(
            None, "Pairing Code",
            f"Enter this code on the OTHER device (Tray → Enter Pairing Code) "
            f"within 2 minutes:\n\n{code}")

    def _enter_pairing_code(self):
        candidates = [d for d in lan_mesh.MESH.list_devices() if not d.get("paired")]
        if not candidates:
            QMessageBox.information(
                None, "Enter Pairing Code",
                "No unpaired devices found on this network yet. Make sure both "
                "devices are on the same WiFi/LAN, and that the other device has "
                "clicked \"Pair New Device...\" to show its code.")
            return
        names = [d["name"] for d in candidates]
        name, ok = QInputDialog.getItem(None, "Enter Pairing Code",
                                        "Which device?", names, 0, False)
        if not ok:
            return
        target = next(d for d in candidates if d["name"] == name)
        code, ok = QInputDialog.getText(None, "Enter Pairing Code",
                                        f"Code shown on {name}:")
        if not ok or not code.strip():
            return
        # attempt_pairing blocks briefly (≤ ~13s); acceptable on a tray-action click
        lan_mesh.MESH.attempt_pairing(target["device_id"], code.strip())

    def _on_pairing_result(self, ok: bool, msg: str):
        self.tray.showMessage(
            "Cursor Overlay", msg,
            QSystemTrayIcon.MessageIcon.Information if ok
            else QSystemTrayIcon.MessageIcon.Warning, 4000)
        self._refresh_devices_menu()

    def _prompt_api_key(self):
        key, ok = QInputDialog.getText(None, "OpenAI API Key", "Paste your OpenAI API key:")
        if ok and key.strip():
            self.api_key = key.strip()
            cfg = load_cfg(); cfg["openai_api_key"] = self.api_key; save_cfg(cfg)
            self.tray.showMessage("Cursor Overlay", "API key saved!",
                                  QSystemTrayIcon.MessageIcon.Information, 2000)

    def _prompt_wake_phrase(self):
        phrase, ok = QInputDialog.getText(
            None, "Wake Phrase",
            f'Current wake phrase: "{self.wake_phrase}"\nEnter new phrase:')
        if ok and phrase.strip():
            self.wake_phrase = phrase.strip().lower()
            cfg = load_cfg(); cfg["wake_phrase"] = self.wake_phrase; save_cfg(cfg)
            self.tray.showMessage("Cursor Overlay",
                                  f'Wake phrase set to: "{self.wake_phrase}"',
                                  QSystemTrayIcon.MessageIcon.Information, 2000)

    def _clear_history(self):
        self._conv_history = []
        self.tray.showMessage("Cursor Overlay", "History cleared.",
                              QSystemTrayIcon.MessageIcon.Information, 2000)

    # ── Hotkeys ───────────────────────────────────────────────────────────────
    def _hotkey_loop(self):
        user32.RegisterHotKey(None, HK_CIRCLE, MOD_CTRL | MOD_ALT, VK_W)
        user32.RegisterHotKey(None, HK_VOICE,  MOD_CTRL | MOD_ALT, VK_B)
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            if msg.message == WM_HOTKEY:
                if msg.wParam == HK_CIRCLE:
                    self.bridge.toggle_circle.emit()
                elif msg.wParam == HK_VOICE:
                    self.bridge.toggle_voice.emit()

    # ── Cursor toggle ─────────────────────────────────────────────────────────
    def _toggle_circle(self):
        if self.conv_state != IDLE:
            return
        if self.overlay.isVisible():
            self._wake.stop()
            self.overlay.hide()
        else:
            self.overlay.leave_eq()
            self.overlay.show()
            if self.api_key:
                self._wake.start()

    # ── Wake word ─────────────────────────────────────────────────────────────
    def _on_wake_detected(self):
        logging.info("Wake detected: conv_state=%d overlay_visible=%s",
                     self.conv_state, self.overlay.isVisible())
        if self.conv_state == IDLE and self.overlay.isVisible():
            self.tray.showMessage("Cursor Overlay", "Wake word detected — listening!",
                                  QSystemTrayIcon.MessageIcon.Information, 1500)
            self._enter_conversation()

    # ── Conversation toggle ───────────────────────────────────────────────────
    def _toggle_voice(self):
        if self.conv_state == IDLE:
            self._enter_conversation()
        else:
            self._exit_conversation()

    def _get_foreground_title(self):
        hwnd = user32.GetForegroundWindow()
        n    = user32.GetWindowTextLengthW(hwnd)
        if n == 0:
            return "Desktop"
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        return buf.value or "Unknown"

    def _enter_conversation(self):
        logging.info("Entering conversation mode")
        if not self.api_key:
            self.tray.showMessage("Cursor Overlay", "Set OpenAI API key first",
                                  QSystemTrayIcon.MessageIcon.Warning, 3000)
            return
        self._wake.stop()
        self.conv_state = LISTENING

        # Capture the current app so the first response is already context-aware
        self._app_name      = self._get_foreground_title()
        self._last_app_name = self._app_name
        self._app_context   = f"App: {self._app_name} (analyzing...)"
        threading.Thread(target=self._refresh_app_context, daemon=True).start()

        self._start_recording()
        self.tray.showMessage("Cursor Overlay",
                              f'Listening… ({self._app_name})',
                              QSystemTrayIcon.MessageIcon.Information, 2000)

    def _refresh_app_context(self):
        """Background: capture screen + ask GPT-4o to describe the current app."""
        try:
            client        = OpenAI(api_key=self.api_key)
            b64, *_       = capture_screen()
            resp          = client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": [
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "high"}},
                    {"type": "text", "text": APP_ANALYZE_PROMPT},
                ]}],
                max_tokens=300)
            self._app_context = (
                f"App: {self._app_name}\n"
                + resp.choices[0].message.content.strip()
            )
            logging.info("App context updated: %s", self._app_context[:120])
        except Exception as e:
            self._app_context = f"App: {self._app_name}"
            logging.warning("App context failed: %s", e)

    def _exit_conversation(self):
        sd.stop()
        self.recorder.stop()
        self._trans_timer.stop()
        self._anim_timer.stop()
        self._guide_move_timer.stop()
        self._pending_guide = None
        self.conv_state     = IDLE
        self.overlay.leave_eq()
        self.overlay.leave_guide()
        self.overlay.hide()
        self.bubble.hide()
        self.tray.showMessage("Cursor Overlay", "Conversation ended.",
                              QSystemTrayIcon.MessageIcon.Information, 1500)

    # ── Recording / transition ────────────────────────────────────────────────
    def _start_recording(self):
        if self.conv_state != LISTENING:
            return
        self._wake.stop()
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        self._anchor    = (pt.x, pt.y)
        self._anim_mode = "listening"
        self.overlay.set_eq_style("listen")
        self._anim_timer.start()   # keeps sonar repainting even in silence
        cx = pt.x + TAIL_X
        cy = pt.y + TAIL_Y

        if (self.overlay.isVisible()
                and not self.overlay.eq_mode
                and not self.overlay.guide_mode
                and not self.overlay._transitioning):
            logging.info("_start_recording: transition path")
            self.overlay.begin_transition(cx, cy)
            self._trans_elapsed = 0.0
            self._trans_timer.start()
        else:
            logging.info("_start_recording: direct path")
            self.overlay.enter_eq(cx, cy)
            self._begin_capture()

    def _begin_capture(self):
        try:
            self.recorder.start()
            logging.info("recorder started OK")
        except Exception as e:
            logging.error("recorder.start FAILED: %s", e)
            # fall back to listening so the user can retry
            self.conv_state = LISTENING

    def _trans_step(self):
        self._trans_elapsed += 0.016
        t = min(1.0, self._trans_elapsed / TRANS_DURATION)
        self.overlay.set_trans_t(t)
        if t >= 1.0:
            self._trans_timer.stop()
            self.overlay.finish_transition()
            if self.conv_state == LISTENING:
                self._begin_capture()

    # ── VAD silence ───────────────────────────────────────────────────────────
    def _on_silence_detected(self):
        if self.conv_state != LISTENING:
            return
        self.conv_state = PROCESSING
        wav = self.recorder.stop()
        self.bridge.begin_processing.emit()
        if wav:
            threading.Thread(target=self._process, args=(wav,), daemon=True).start()
        else:
            self.conv_state = LISTENING
            self._start_recording()

    def _on_begin_processing(self):
        self._anim_mode = "processing"
        self.overlay.leave_eq()
        self.overlay.update_ring_pulse(1.0)   # activate orbit animation
        self._anim_timer.start()

    # ── AI pipeline ───────────────────────────────────────────────────────────
    def _process(self, wav_path):
        logging.info("Processing started")
        try:
            client = OpenAI(api_key=self.api_key)

            # Whisper prompt: domain vocab reduces accent misreadings
            _WHISPER_PROMPT = (
                f"BashIn, {self._app_name}, cursor overlay, overlay.py, mesh-ai, "
                "Python, PyQt6, GitHub, VS Code, file, folder, Bluetooth, settings, "
                "open, close, show, hide, help me, how do I, can you, please"
            )

            with open(wav_path, "rb") as f:
                tr = client.audio.transcriptions.create(
                    model="whisper-1", file=f,
                    response_format="verbose_json",
                    prompt=_WHISPER_PROMPT)
            text = tr.text.strip()
            lang = getattr(tr, "language", None) or "english"
            logging.info("STT result: %r  lang=%s", text, lang)
            if not text:
                return

            # ── Specialist agent fast path (local, or dispatched to a paired LAN device) ──
            intent, params = agents.route_intent(text, client)
            target_id = lan_mesh.MESH.match_device_mention(text) if intent != "general" else None
            if target_id:
                agent_result = lan_mesh.MESH.dispatch(target_id, intent, params,
                                                      raw_text=text, timeout=45)
            elif intent != "general":
                agent_result = agents.execute_intent(intent, params, client, raw_text=text)
            else:
                agent_result = None
            if agent_result is not None:
                self._conv_history.append({"role": "user",      "content": text})
                self._conv_history.append({"role": "assistant", "content": agent_result})
                self.bridge.start_speaking.emit()
                tts = client.audio.speech.create(
                    model="tts-1", voice="alloy",
                    input=agent_result, response_format="pcm")
                pcm = np.frombuffer(tts.content, dtype=np.int16).astype(np.float32) / 32768.0
                sd.play(pcm, samplerate=24000)
                sd.wait()
                return
            # ── General GPT-4o pipeline ───────────────────────────────────────

            b64, xs, ys, iw, ih, sw, sh = capture_screen()

            # Detect app switch — if user moved to a different app, refresh context
            current_app = self._get_foreground_title()
            if current_app != self._last_app_name:
                logging.info("App switched: %r → %r", self._last_app_name, current_app)
                self._app_name      = current_app
                self._last_app_name = current_app
                self._app_context   = f"App: {current_app} (just switched here)"
                # Reset history so the new app gets a fresh context
                self._conv_history  = []
                # Kick off background context refresh for next message
                threading.Thread(target=self._refresh_app_context, daemon=True).start()

            self._conv_history.append({"role": "user", "content": text})

            sys_p = GUIDE_SYS.format(
                lang=lang, iw=iw, ih=ih, sw=sw, sh=sh,
                xs=xs, ys=ys,
                app_context=self._app_context or f"App: {self._app_name}",
            )
            msgs  = [{"role": "system", "content": sys_p}]
            for m in self._conv_history[-10:]:
                if m["role"] == "assistant":
                    msgs.append({"role": "assistant", "content": m["content"]})
            msgs.append({"role": "user", "content": [
                {"type": "image_url",
                 "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "high"}},
                {"type": "text", "text": text},
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
                    + "\n".join(f"Step {i+1}: {s.get('speech', '')}"
                                for i, s in enumerate(steps)))
                self._pending_guide = (steps, xs, ys)
            elif rtype == "auto":
                steps = result.get("steps", [])
                self.bridge.show_response.emit(
                    f"You: {text}\n\n{speech}\n\n"
                    + "\n".join(f"▶ {s.get('speech', '')}"
                                for s in steps))
                self._pending_auto = (steps, client, speech)
                self._pending_guide = None
            else:
                self.bridge.show_response.emit(f"You: {text}\n\n{speech}")
                self._pending_guide = None

            self.bridge.start_speaking.emit()
            tts = client.audio.speech.create(
                model="tts-1", voice="alloy", input=speech, response_format="pcm")
            pcm = np.frombuffer(tts.content, dtype=np.int16).astype(np.float32) / 32768.0
            sd.play(pcm, samplerate=24000)
            sd.wait()

        except Exception as e:
            logging.error("_process failed: %s", e)
            if self.conv_state != IDLE:
                self.bridge.show_error.emit(f"Error: {str(e)[:140]}")
            self._pending_guide = None
        finally:
            self.bridge.stop_speaking.emit()
            try:
                os.unlink(wav_path)
            except Exception:
                pass

    # ── Speaking state ────────────────────────────────────────────────────────
    def _on_start_speaking(self):
        self.conv_state = SPEAKING
        self._anim_mode = "speaking"
        ax, ay = self._anchor
        self.overlay.update_ring_pulse(0.0)
        self.overlay.enter_eq(ax, ay)
        self.overlay.set_eq_style("speak")
        self._anim_timer.start()

    def _on_stop_speaking(self):
        self._anim_timer.stop()
        if self.conv_state == IDLE:
            self.overlay.leave_eq()
            self.overlay.leave_guide()
            self.overlay.hide()
            return
        if self._pending_auto is not None:
            steps, client, _ = self._pending_auto
            self._pending_auto = None
            threading.Thread(target=self._run_auto_steps,
                             args=(steps, client), daemon=True).start()
        elif self._pending_guide is not None:
            steps, xs, ys       = self._pending_guide
            self._pending_guide = None
            self._start_guide_mode(steps, xs, ys)
        else:
            self.conv_state = LISTENING
            self._start_recording()

    def _run_auto_steps(self, steps, client):
        """Execute autonomous action steps then re-enter listening mode."""
        try:
            for i, step in enumerate(steps):
                if self.conv_state == IDLE:
                    break
                speech = step.get("speech", "")
                logging.info("auto step %d/%d: %s", i + 1, len(steps), speech)

                if speech:
                    tts = client.audio.speech.create(
                        model="tts-1", voice="alloy",
                        input=speech, response_format="pcm")
                    pcm = np.frombuffer(tts.content,
                                        dtype=np.int16).astype(np.float32) / 32768.0
                    sd.play(pcm, samplerate=24000)

                action = step.get("action", "click")

                # For screenshot steps, re-capture and continue
                if action == "screenshot":
                    sd.wait()
                    capture_screen()  # re-capture to refresh context
                    logging.info("auto: re-captured screen mid-task")
                    continue

                run_step(step)
                sd.wait()

            self.tray.showMessage("BashIn", "Task complete!",
                                  QSystemTrayIcon.MessageIcon.Information, 2000)
        except Exception as e:
            logging.error("_run_auto_steps failed: %s", e)
            self.tray.showMessage("BashIn", f"Auto error: {str(e)[:100]}",
                                  QSystemTrayIcon.MessageIcon.Warning, 3000)
        finally:
            if self.conv_state != IDLE:
                self.conv_state = LISTENING
                self._start_recording()

    # ── Guide mode ────────────────────────────────────────────────────────────
    def _start_guide_mode(self, steps, xs, ys):
        self.conv_state       = GUIDING
        self._guide_steps     = steps
        self._guide_idx       = 0
        self._guide_xs        = xs
        self._guide_ys        = ys
        self._was_lbutton     = False
        self._guide_target    = None
        self._click_pos       = None
        self._guide_step_time = time.monotonic()
        self._guide_move_timer.stop()
        self._anim_mode       = "guide"
        self._anim_timer.start()
        self._show_guide_step(0)

    def _show_guide_step(self, idx):
        step   = self._guide_steps[idx]
        speech = step.get("speech", "")
        rx, ry = step.get("x"), step.get("y")
        total  = len(self._guide_steps)

        # Always reset to cursor-image mode before each step's glide animation
        self._guide_move_timer.stop()
        self.overlay.leave_eq()
        self.overlay.leave_guide()

        if not self.overlay.isVisible():
            self.overlay.show()
            self.overlay._apply_win32()

        if rx is not None and ry is not None:
            tx = int(rx * self._guide_xs)
            ty = int(ry * self._guide_ys)
            self._guide_target = (tx, ty)

            # Animate from current widget position to target (centered)
            pos              = self.overlay.pos()
            self._gm_sx      = pos.x()
            self._gm_sy      = pos.y()
            self._gm_tx      = tx - CIRCLE_HALF
            self._gm_ty      = ty - CIRCLE_HALF
            self._gm_elapsed = 0.0
            self._guide_move_timer.start()
        else:
            self._guide_target = None

        self.bubble.show_text(
            f"Step {idx + 1} / {total}\n{speech}\n\n"
            + ("Cursor is moving to the target — click anywhere to confirm."
               if rx is not None else "Click anywhere to continue."),
            *self._anchor, timeout_ms=0)
        threading.Thread(target=self._tts_step, args=(speech,), daemon=True).start()
        self._guide_step_time = time.monotonic()

    def _guide_move_step(self):
        """QTimer slot: glide overlay to guide target, then pulse to signal 'ready to click'."""
        self._gm_elapsed += 0.016
        t    = min(1.0, self._gm_elapsed / self._gm_duration)
        ease = t * t * (3 - 2 * t)   # smoothstep
        self.overlay.move(
            int(self._gm_sx + (self._gm_tx - self._gm_sx) * ease),
            int(self._gm_sy + (self._gm_ty - self._gm_sy) * ease),
        )
        if t >= 1.0:
            self._guide_move_timer.stop()
            # Switch to pulsing EQ dots to signal "I'm here — click to continue"
            self.overlay.enter_eq(
                self._gm_tx + CIRCLE_HALF,
                self._gm_ty + CIRCLE_HALF,
            )

    def _tts_step(self, text):
        try:
            client = OpenAI(api_key=self.api_key)
            tts    = client.audio.speech.create(
                model="tts-1", voice="alloy", input=text, response_format="pcm")
            pcm    = np.frombuffer(tts.content, dtype=np.int16).astype(np.float32) / 32768.0
            sd.play(pcm, samplerate=24000)
            sd.wait()
        except Exception:
            pass

    def _advance_guide_step(self):
        self._guide_idx += 1
        if self._guide_idx >= len(self._guide_steps):
            self._guide_done()
        else:
            self._show_guide_step(self._guide_idx)

    def _guide_done(self):
        self._anim_timer.stop()
        self._guide_move_timer.stop()
        self.overlay.leave_guide()
        self.overlay.hide()
        self.bubble.show_text("All steps complete! Ask me anything.",
                              *self._anchor, timeout_ms=8000)
        threading.Thread(target=self._tts_step,
                         args=("Done! All steps complete.",), daemon=True).start()
        self.conv_state = LISTENING
        self._start_recording()

    # ── Animation ─────────────────────────────────────────────────────────────
    def _animate(self):
        t = time.monotonic()
        if self._anim_mode == "listening":
            self.overlay.update()   # sonar timing is driven by time.monotonic() in paintEvent
        elif self._anim_mode == "speaking":
            # Advance travelling wave around ring perimeter
            self.overlay._speak_phase = (t * 3.5) % (2 * math.pi)
            self.overlay.update()
        elif self._anim_mode == "processing":
            self.overlay.update()   # orbit runs off time.monotonic() in paintEvent
        elif self._anim_mode == "guide":
            self.overlay.update_guide_pulse(abs(math.sin(t * 2.2 * math.pi)))

    # ── Signal slots ──────────────────────────────────────────────────────────
    def _on_levels(self, levels):
        if self.conv_state == LISTENING:
            self.overlay.update_levels(levels)

    def _on_response(self, text):
        pass   # bubble removed — agent speaks results directly via TTS

    def _on_error(self, msg):
        self.tray.showMessage("Cursor Overlay", msg,
                              QSystemTrayIcon.MessageIcon.Warning, 4000)

    # ── Position / click tick ─────────────────────────────────────────────────
    def _tick(self):
        pt  = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        now = time.monotonic()
        self.history.append((now, pt.x, pt.y))
        cutoff = now - (TRAIL_MS / 1000) * 2
        while self.history and self.history[0][0] < cutoff:
            self.history.popleft()

        if self.conv_state == GUIDING:
            lbtn = bool(user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000)

            # Any click (after animation settles) advances the step.
            # The overlay cursor already showed WHERE — the click is just confirmation.
            if (self._was_lbutton and not lbtn
                    and now - self._guide_step_time > 1.0):   # 1s guard: animation is 0.55s
                self._advance_guide_step()

            self._was_lbutton = lbtn
            return

        self._was_lbutton = False
        if not self.overlay.isVisible():
            return

        target = now - TRAIL_MS / 1000
        tx, ty = pt.x, pt.y
        for ts, x, y in self.history:
            if ts >= target:
                tx, ty = x, y
                break

        if self.overlay.eq_mode or self.overlay._transitioning:
            self.overlay.move(tx + TAIL_X - CIRCLE_HALF, ty + TAIL_Y - CIRCLE_HALF)
        elif not self.overlay.guide_mode:
            if self._ring_x == 0.0:
                self._ring_x = float(pt.x)
            if self._ring_y == 0.0:
                self._ring_y = float(pt.y)
            self._ring_x += (pt.x - self._ring_x) * RING_LERP
            self._ring_y += (pt.y - self._ring_y) * RING_LERP
            rx, ry = int(self._ring_x), int(self._ring_y)
            self.overlay.move(rx - CIRCLE_HALF, ry - CIRCLE_HALF)
            self.overlay.set_dot_offset(pt.x - rx, pt.y - ry)

    # ── Quit ──────────────────────────────────────────────────────────────────
    def _quit(self):
        self._wake.stop()
        user32.UnregisterHotKey(None, HK_CIRCLE)
        user32.UnregisterHotKey(None, HK_VOICE)
        if self._mutex:
            kernel32.ReleaseMutex(self._mutex)
        self.qt.quit()

    def run(self):
        import sys
        sys.exit(self.qt.exec())
