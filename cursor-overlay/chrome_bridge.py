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
import json, asyncio, logging, threading, itertools

import websockets

PORT = 8777


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

    def run_command(self, url: str, action: dict, timeout: float = 60):
        """Open `url` as a tab in the user's Chrome (via the extension) and run
        `action` there. Blocks for the content script's result."""
        self.start()
        fut = asyncio.run_coroutine_threadsafe(
            self._run_command(url, action, timeout), self._loop)
        return fut.result(timeout=timeout + 15)

    async def _run_command(self, url, action, timeout):
        # Wait for the extension's service worker to be connected
        t0 = self._loop.time()
        while self._sw is None and self._loop.time() - t0 < 25:
            await asyncio.sleep(0.3)
        if self._sw is None:
            return {"ok": False, "reason": "NO_EXTENSION"}

        # The extension opens the tab itself and runs the action there
        cid = next(self._ids)
        fut = self._loop.create_future()
        self._pending[cid] = fut
        try:
            await self._sw.send(json.dumps({"id": cid, "url": url, **action}))
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            return {"ok": False, "reason": "TIMEOUT"}
        except Exception as e:
            return {"ok": False, "reason": "SEND_FAILED", "error": str(e)}
        finally:
            self._pending.pop(cid, None)


BRIDGE = ChromeBridge()
