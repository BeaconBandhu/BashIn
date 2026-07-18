"""
lan_mesh.py -- LAN device mesh for cross-PC voice task dispatch.

Lets a voice command on one BashIn instance ("play this on my laptop") execute
on another BashIn instance on the same LAN, with the result spoken back here.

Architecture mirrors chrome_bridge.py's pattern: a dedicated thread runs its own
asyncio event loop; public methods are synchronous and block on the result, so
callers on any other thread (app.py's Qt thread, agents.py's worker threads)
can use this like a normal function call.

Pipeline:
  1. Discovery -- zeroconf advertises/browses "_bashin._tcp.local." so every
     instance maintains a live registry of other reachable BashIn instances.
  2. Pairing -- a one-time 6-digit-code handshake between two devices
     establishes a shared secret (persisted in config.json's paired_devices).
     Only paired devices may dispatch tasks to each other.
  3. Websocket server on 0.0.0.0:mesh_port accepts HMAC-authenticated dispatch
     requests from paired peers and executes them via agents.execute_intent(),
     returning the spoken result.
  4. dispatch(...) is the client-side call -- look up a target device, open a
     connection, send an authenticated request, return the spoken result.
     Never raises; always returns a spoken-friendly string.

Cross-network relay is an explicit future phase (see project plan) -- this is
LAN-only, via mDNS. mDNS/multicast must be permitted on the network; this can
fail on guest WiFi with client isolation or some corporate networks -- callers
should surface list_devices() being empty as an explanatory message, not a
silent failure.
"""
import asyncio, hashlib, hmac, json, logging, secrets, socket, threading, time
from datetime import datetime, timezone

import websockets
from zeroconf import ServiceInfo, Zeroconf, ServiceBrowser

import config
import agents
from openai import OpenAI

SERVICE_TYPE          = "_bashin._tcp.local."
# NOTE: device online/offline is driven by zeroconf's OWN remove_service signal
# (a real goodbye packet, or genuine mDNS record TTL expiry) -- NOT a short
# local timestamp guess. zeroconf's add_service/update_service callbacks only
# fire on actual network events, which can be many minutes apart in steady
# state; evicting a registry entry just because no NEW event arrived within a
# short window was wrongly marking fully-online devices as offline. This is a
# generous safety net only (registry-leak guard if remove_service is ever
# missed), not the eviction mechanism -- see _DeviceListener.remove_service.
REGISTRY_STALE_SECS   = 6 * 3600
SWEEP_INTERVAL_SECS   = 900
NONCE_WINDOW_SECS     = 60
PAIR_CODE_WINDOW_SECS = 120


def _lan_ip() -> str:
    """Best-effort LAN-facing IP via the UDP-connect trick (no packet is actually sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


class _DeviceListener:
    """zeroconf ServiceListener -- updates the mesh's live registry on add/update,
    and EVICTS on remove_service -- zeroconf's own "this device is genuinely
    gone" signal (an explicit goodbye packet, or real mDNS record TTL expiry).
    This is the actual online/offline source of truth; see REGISTRY_STALE_SECS
    for why a local timestamp guess was wrong."""
    def __init__(self, mesh: "LanMesh"):
        self._mesh = mesh

    def add_service(self, zc, type_, name):
        self._resolve(zc, type_, name)

    def update_service(self, zc, type_, name):
        self._resolve(zc, type_, name)

    def remove_service(self, zc, type_, name):
        with self._mesh._lock:
            device_id = self._mesh._name_to_id.pop(name, None)
            if device_id:
                self._mesh._registry.pop(device_id, None)
        if device_id:
            logging.info("lan_mesh: %s went away (zeroconf remove_service)", name)

    def _resolve(self, zc, type_, name):
        try:
            info = zc.get_service_info(type_, name, timeout=3000)
            if not info or not info.addresses:
                return
            props = {k.decode(): v.decode() for k, v in info.properties.items()}
            device_id   = props.get("device_id")
            device_name = props.get("device_name", "?")
            if not device_id or device_id == self._mesh.device_id:
                return   # ignore our own advertisement
            addr = socket.inet_ntoa(info.addresses[0])
            with self._mesh._lock:
                self._mesh._name_to_id[name] = device_id
                self._mesh._registry[device_id] = {
                    "device_id": device_id, "name": device_name,
                    "ip": addr, "port": info.port, "last_seen": time.time(),
                }
        except Exception as e:
            logging.debug("lan_mesh: resolve failed for %s: %s", name, e)


class LanMesh:
    def __init__(self):
        self.device_id   = None
        self.device_name = None
        self.mesh_port   = None
        self._paired     = {}     # device_id -> {"name", "secret", "paired_at"}
        self._registry   = {}     # device_id -> {"name","ip","port","last_seen"}
        self._name_to_id = {}     # zeroconf service name -> device_id (for remove_service)
        self._lock       = threading.Lock()

        self._loop          = None
        self._ready          = threading.Event()
        self._thread         = None
        self._zc             = None
        self._service_info   = None
        self._browser        = None
        self._nonce_cache    = {}   # nonce -> expiry_ts (replay protection)
        self._pending_pair_code = None   # (code, expiry_ts) while armed as a pairing target
        self._on_pairing_result = None   # optional callback(ok: bool, msg: str)
        self._persist_to_disk   = True   # False for tests: pairing stays in-memory only

    # ── configuration ────────────────────────────────────────────────────────
    def configure(self, device_id: str, device_name: str, mesh_port: int,
                  paired_devices: dict | None = None, persist_to_disk: bool = True):
        """Set identity directly -- used by tests to bypass config.json entirely.
        persist_to_disk=False keeps all pairing in-memory (never touches config.json) --
        always use this for tests/probes to avoid corrupting the real config."""
        self.device_id       = device_id
        self.device_name     = device_name
        self.mesh_port       = mesh_port
        self._paired         = dict(paired_devices or {})
        self._persist_to_disk = persist_to_disk

    def configure_from_config(self):
        cfg = config.ensure_identity(config.load_cfg())
        self.configure(cfg["device_id"], cfg["device_name"], cfg["mesh_port"], cfg["paired_devices"])

    def set_pairing_result_callback(self, cb):
        self._on_pairing_result = cb

    # ── lifecycle ────────────────────────────────────────────────────────────
    def start(self):
        if self.device_id is None:
            self.configure_from_config()
        if self._thread and self._thread.is_alive():
            return
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="LanMesh")
        self._thread.start()
        self._ready.wait(timeout=10)

    def stop(self):
        if self._zc:
            try:
                if self._service_info:
                    self._zc.unregister_service(self._service_info)
                self._zc.close()
            except Exception:
                pass
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)

    def _run(self):
        # zeroconf's async engine detects "the" event loop on the calling thread;
        # if we set OUR OWN loop first and then call zeroconf's sync API from a
        # coroutine running on it, zeroconf tries to schedule work back onto that
        # same (busy) loop and deadlocks (EventLoopBlocked). So: advertise on the
        # LAN *before* this thread has any asyncio loop set, then create/set our
        # own loop afterward purely for the websocket server.
        try:
            self._advertise()
        except Exception as e:
            logging.error("lan_mesh: advertise failed: %s", e)

        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._serve_forever())
        except Exception as e:
            logging.error("lan_mesh: server crashed: %s", e)

    async def _serve_forever(self):
        async with websockets.serve(self._handler, "0.0.0.0", self.mesh_port,
                                    ping_interval=20, ping_timeout=60):
            logging.info("lan_mesh: listening on 0.0.0.0:%d as %r (%s)",
                         self.mesh_port, self.device_name, self.device_id)
            self._ready.set()
            self._loop.call_later(SWEEP_INTERVAL_SECS, self._sweep_stale)
            await asyncio.Future()   # run forever, until stop() cancels the loop

    def _advertise(self):
        try:
            self._zc = Zeroconf()
            ip = _lan_ip()
            props = {"device_id": self.device_id, "device_name": self.device_name, "v": "1"}
            instance = f"{self.device_name}-{self.device_id[:8]}.{SERVICE_TYPE}"
            self._service_info = ServiceInfo(
                SERVICE_TYPE, instance,
                addresses=[socket.inet_aton(ip)], port=self.mesh_port,
                properties=props,
            )
            self._zc.register_service(self._service_info)
            self._browser = ServiceBrowser(self._zc, SERVICE_TYPE, _DeviceListener(self))
            logging.info("lan_mesh: advertising as %r at %s:%d", self.device_name, ip, self.mesh_port)
        except Exception as e:
            logging.error("lan_mesh: zeroconf setup failed (%s) -- LAN discovery unavailable. "
                          "This can happen if multicast is blocked on this network.", e)

    def _sweep_stale(self):
        """Generous safety net only -- real eviction is remove_service-driven
        (see _DeviceListener). This just guards against a registry entry being
        stranded forever in the rare case zeroconf ever misses a removal event,
        and prunes the nonce replay cache."""
        cutoff = time.time() - REGISTRY_STALE_SECS
        with self._lock:
            dead = [k for k, v in self._registry.items() if v["last_seen"] < cutoff]
            for k in dead:
                del self._registry[k]
            if dead:
                dead_set = set(dead)
                stale_names = [n for n, did in self._name_to_id.items() if did in dead_set]
                for n in stale_names:
                    del self._name_to_id[n]
        now = time.time()
        self._nonce_cache = {n: exp for n, exp in self._nonce_cache.items() if exp > now}
        if self._loop:
            self._loop.call_later(SWEEP_INTERVAL_SECS, self._sweep_stale)

    # ── registry (public, safe from any thread) ─────────────────────────────────
    def list_devices(self) -> list:
        """Live snapshot: every device seen on the LAN, plus paired devices not
        currently visible (shown offline). Safe to poll from the Qt thread."""
        with self._lock:
            out = []
            for d in self._registry.values():
                d2 = dict(d)
                d2["paired"] = d["device_id"] in self._paired
                out.append(d2)
            seen = {d["device_id"] for d in out}
            for pid, info in self._paired.items():
                if pid not in seen:
                    out.append({"device_id": pid, "name": info.get("name", pid[:8]),
                               "ip": None, "port": None, "last_seen": 0, "paired": True})
            return out

    def match_device_mention(self, text: str):
        """Case-insensitive substring match of `text` against paired device names
        (longest name first, so a specific name wins over a shorter substring)."""
        text_l = (text or "").lower()
        candidates = [d for d in self.list_devices() if d["paired"]]
        candidates.sort(key=lambda d: len(d["name"] or ""), reverse=True)
        for d in candidates:
            if d["name"] and d["name"].lower() in text_l:
                return d["device_id"]
        return None

    def _resolve_target(self, target: str):
        """Accept a device_id or a device name (case-insensitive); None if unknown."""
        for d in self.list_devices():
            if d["device_id"] == target or (d["name"] and d["name"].lower() == str(target).lower()):
                return d
        return None

    # ── pairing ──────────────────────────────────────────────────────────────
    def begin_pairing(self) -> str:
        """Arm this device to accept ONE pairing attempt within 120s. Returns the code to display."""
        if self._loop is None:
            self.start()
        code = f"{secrets.randbelow(1_000_000):06d}"
        self._pending_pair_code = (code, time.time() + PAIR_CODE_WINDOW_SECS)
        logging.info("lan_mesh: pairing armed, expires in %ds", PAIR_CODE_WINDOW_SECS)
        return code

    def attempt_pairing(self, target_device_id: str, code: str, timeout: float = 8.0):
        """Try to pair with a device that has an armed code showing on its screen.
        Returns (ok, message); also fires the pairing-result callback if set."""
        if self._loop is None:
            self.start()
        target = self._resolve_target(target_device_id)
        if not target or not target.get("ip"):
            msg = "That device isn't visible on the network right now."
            self._notify_pairing(False, msg)
            return False, msg
        fut = asyncio.run_coroutine_threadsafe(self._attempt_pairing_async(target, code), self._loop)
        try:
            ok, msg = fut.result(timeout=timeout + 5)
        except Exception as e:
            ok, msg = False, f"Pairing failed: {str(e)[:120]}"
        self._notify_pairing(ok, msg)
        return ok, msg

    def _notify_pairing(self, ok: bool, msg: str):
        if self._on_pairing_result:
            try:
                self._on_pairing_result(ok, msg)
            except Exception:
                pass

    async def _attempt_pairing_async(self, target: dict, code: str):
        uri = f"ws://{target['ip']}:{target['port']}"
        try:
            async with websockets.connect(uri, open_timeout=5) as ws:
                await ws.send(json.dumps({
                    "type": "pair_request", "sender_id": self.device_id,
                    "sender_name": self.device_name, "code": code,
                }))
                raw = await asyncio.wait_for(ws.recv(), timeout=6)
                reply = json.loads(raw)
        except Exception as e:
            return False, f"Couldn't reach {target['name']}: {str(e)[:100]}"

        if not reply.get("ok"):
            return False, f"Pairing rejected: {reply.get('error', 'unknown error')}"
        peer_id, peer_name, secret = reply["target_id"], reply["target_name"], reply["secret"]
        self._persist_pair(peer_id, peer_name, secret)
        return True, f"Paired with {peer_name}."

    def _persist_pair(self, peer_id: str, peer_name: str, secret: str):
        entry = {"name": peer_name, "secret": secret,
                "paired_at": datetime.now(timezone.utc).isoformat()}
        with self._lock:
            self._paired[peer_id] = entry
        if not self._persist_to_disk:
            return   # test/probe mode -- keep the pairing in-memory only
        try:
            cfg = config.load_cfg()
            cfg.setdefault("paired_devices", {})[peer_id] = entry
            config.save_cfg(cfg)
        except Exception as e:
            logging.error("lan_mesh: failed to persist pairing: %s", e)

    # ── dispatch (client side) ───────────────────────────────────────────────
    def dispatch(self, target: str, intent: str, params: dict,
                raw_text: str | None = None, timeout: float = 45.0) -> str:
        """Send a task to a paired device. Never raises -- always a spoken string."""
        if self._loop is None:
            self.start()
        dev = self._resolve_target(target)
        if not dev:
            return f"I don't know a device called {target}."
        if dev["device_id"] not in self._paired:
            return f"{dev['name']} isn't paired with this device yet."
        if not dev.get("ip"):
            return f"{dev['name']} looks offline right now."

        fut = asyncio.run_coroutine_threadsafe(
            self._dispatch_async(dev, intent, params, raw_text, timeout), self._loop)
        try:
            return fut.result(timeout=timeout + 10)
        except Exception as e:
            return f"I couldn't reach {dev['name']}: {str(e)[:120]}"

    async def _dispatch_async(self, dev, intent, params, raw_text, timeout):
        secret = self._paired[dev["device_id"]]["secret"].encode()
        nonce  = secrets.token_hex(8)
        ts     = int(time.time())
        msg = f"{self.device_id}|{nonce}|{ts}|{intent}|{json.dumps(params, sort_keys=True)}|{raw_text or ''}"
        sig = hmac.new(secret, msg.encode(), hashlib.sha256).hexdigest()
        payload = {
            "type": "dispatch", "sender_id": self.device_id, "nonce": nonce, "ts": ts,
            "intent": intent, "params": params, "raw_text": raw_text, "hmac": sig,
        }
        uri = f"ws://{dev['ip']}:{dev['port']}"
        try:
            async with websockets.connect(uri, open_timeout=5) as ws:
                await ws.send(json.dumps(payload))
                raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                reply = json.loads(raw)
        except Exception as e:
            return f"I couldn't reach {dev['name']}: {str(e)[:120]}"

        if reply.get("ok"):
            return reply.get("result") or "Done."
        return f"{dev['name']} couldn't run that: {reply.get('error', 'unknown error')}"

    # ── server side (handles incoming connections) ──────────────────────────────
    async def _handler(self, ws):
        try:
            raw = await ws.recv()
            msg = json.loads(raw)
        except Exception:
            return
        try:
            if msg.get("type") == "dispatch":
                reply = await self._handle_dispatch(msg)
            elif msg.get("type") == "pair_request":
                reply = self._handle_pair_request(msg)
            else:
                reply = {"ok": False, "error": "UNKNOWN_TYPE"}
        except Exception as e:
            logging.error("lan_mesh: handler error: %s", e)
            reply = {"ok": False, "error": "SERVER_ERROR"}
        try:
            await ws.send(json.dumps(reply))
        except Exception:
            pass

    def _handle_pair_request(self, msg: dict) -> dict:
        sender_id, sender_name = msg.get("sender_id"), msg.get("sender_name", "?")
        code = msg.get("code", "")
        armed = self._pending_pair_code
        self._pending_pair_code = None   # single-use regardless of outcome
        if not armed or armed[0] != code or time.time() > armed[1]:
            return {"type": "pair_response", "ok": False, "error": "BAD_CODE"}
        secret = secrets.token_urlsafe(32)
        self._persist_pair(sender_id, sender_name, secret)
        self._notify_pairing(True, f"Paired with {sender_name}.")
        return {"type": "pair_response", "ok": True,
                "target_id": self.device_id, "target_name": self.device_name, "secret": secret}

    async def _handle_dispatch(self, msg: dict) -> dict:
        sender_id = msg.get("sender_id", "")
        peer = self._paired.get(sender_id)
        if not peer:
            return {"type": "dispatch_result", "ok": False, "error": "UNPAIRED"}

        ts = msg.get("ts", 0)
        if abs(time.time() - ts) > NONCE_WINDOW_SECS:
            return {"type": "dispatch_result", "ok": False, "error": "STALE"}

        nonce = msg.get("nonce", "")
        if nonce in self._nonce_cache:
            return {"type": "dispatch_result", "ok": False, "error": "REPLAY"}
        self._nonce_cache[nonce] = time.time() + NONCE_WINDOW_SECS

        intent, params, raw_text = msg.get("intent"), msg.get("params", {}), msg.get("raw_text")
        expected = f"{sender_id}|{nonce}|{ts}|{intent}|{json.dumps(params, sort_keys=True)}|{raw_text or ''}"
        expected_sig = hmac.new(peer["secret"].encode(), expected.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected_sig, msg.get("hmac", "")):
            return {"type": "dispatch_result", "ok": False, "error": "BAD_HMAC"}

        logging.info("lan_mesh: dispatch from %s: intent=%s params=%s", peer["name"], intent, params)
        result = await self._execute_remote(intent, params, raw_text)
        return {"type": "dispatch_result", "ok": True, "result": result, "error": None}

    async def _execute_remote(self, intent: str, params: dict, raw_text):
        """Runs the task on THIS (receiving) machine, using its OWN OpenAI key --
        the sender's key is never transmitted."""
        loop = asyncio.get_running_loop()
        def _run():
            cfg = config.load_cfg()
            key = cfg.get("openai_api_key", "")
            if not key:
                return "This device doesn't have an OpenAI API key configured yet."
            client = OpenAI(api_key=key)
            return agents.execute_intent(intent, params, client, raw_text=raw_text)
        return await loop.run_in_executor(None, _run)


MESH = LanMesh()
