"""
chrome_bridge.py — talks to the BashIn Chrome extension over a local WebSocket so
the voice agent can drive the user's OWN logged-in Chrome (no separate profile).

Flow:
  1. We open the target page as a tab in the user's real Chrome (chrome.exe <url>).
  2. The extension's content script runs there; its service worker connects back
     to this WS server (ws://127.0.0.1:8777).
  3. We send {id, action, tabMatch, ...}; the SW routes it to the right tab's
     content script, which performs real DOM clicks and replies {id, result}.

Runs the asyncio WS server on a dedicated thread; public methods are called from
the overlay's per-command worker threads and block on the result.
"""
import os, json, time, asyncio, logging, threading, subprocess, itertools

import websockets
from constants import BASE_DIR

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PORT   = 8777
# Open automation tabs in the user's default (personal, Swiggy-logged-in) profile
CHROME_PROFILE = "Default"


class ChromeBridge:
    def __init__(self):
        self._loop    = None
        self._sw      = None                 # the service-worker WebSocket
        self._pending = {}                   # id -> asyncio.Future
        self._ids     = itertools.count(1)
        self._ready   = threading.Event()
        self._thread  = threading.Thread(target=self._run, daemon=True, name="ChromeBridge")

    # ── lifecycle ──────────────────────────────────────────────────────────────
    def start(self):
        if not self._thread.is_alive():
            self._thread.start()
        self._ready.wait(timeout=10)

    def _run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._serve())
        except Exception as e:
            logging.error("chrome_bridge: server crashed: %s", e)

    async def _serve(self):
        async with websockets.serve(self._handler, "127.0.0.1", PORT,
                                    ping_interval=20, ping_timeout=60):
            logging.info("chrome_bridge: ws server listening on %d", PORT)
            self._ready.set()
            await asyncio.Future()           # run forever

    async def _handler(self, ws):
        self._sw = ws                        # latest service worker wins
        logging.info("chrome_bridge: extension connected")
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                if "id" in msg and "result" in msg:
                    fut = self._pending.get(msg["id"])
                    if fut and not fut.done():
                        fut.set_result(msg["result"])
        except Exception as e:
            logging.info("chrome_bridge: extension disconnected: %s", e)
        finally:
            if self._sw is ws:
                self._sw = None

    # ── public API (called from worker threads) ────────────────────────────────
    def is_connected(self) -> bool:
        return self._sw is not None

    def run_command(self, open_url: str, action: dict, tab_match: str, timeout: float = 60):
        """Open `open_url` in the user's Chrome and run `action` there. Blocks."""
        self.start()
        fut = asyncio.run_coroutine_threadsafe(
            self._run_command(open_url, action, tab_match, timeout), self._loop)
        return fut.result(timeout=timeout + 15)

    async def _run_command(self, open_url, action, tab_match, timeout):
        # 1. Open the page as a tab in the user's real Chrome
        try:
            subprocess.Popen([CHROME, f"--profile-directory={CHROME_PROFILE}", open_url])
        except Exception as e:
            return {"ok": False, "reason": "CHROME_LAUNCH", "error": str(e)}

        # 2. Wait for the extension's service worker to be connected
        t0 = self._loop.time()
        while self._sw is None and self._loop.time() - t0 < 25:
            await asyncio.sleep(0.3)
        if self._sw is None:
            return {"ok": False, "reason": "NO_EXTENSION"}

        # 3. Send the command and await the content script's result
        cid = next(self._ids)
        fut = self._loop.create_future()
        self._pending[cid] = fut
        try:
            await self._sw.send(json.dumps({"id": cid, "tabMatch": tab_match, **action}))
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            return {"ok": False, "reason": "TIMEOUT"}
        except Exception as e:
            return {"ok": False, "reason": "SEND_FAILED", "error": str(e)}
        finally:
            self._pending.pop(cid, None)


BRIDGE = ChromeBridge()
