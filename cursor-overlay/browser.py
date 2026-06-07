"""
browser.py — drives a debug-enabled Chrome through Playwright (CDP), on ONE thread.

Why a dedicated thread:
  Playwright's sync objects are bound to the thread that created them, but the
  overlay spawns a fresh worker thread for every voice command. So all browser
  work is funnelled here, onto one long-lived thread, and callers hand in a
  closure via BROWSER.submit(fn). This also keeps Chrome + the Playwright driver
  warm between commands and pins the Windows Proactor event-loop policy that
  Playwright's subprocess transport needs.

Chrome runs as its OWN process with --remote-debugging-port and a dedicated
"BashIn" profile, so your Swiggy / Google logins + delivery location persist
across restarts, and your everyday Chrome is left completely alone.

Setup (one time):
  pip install playwright          # NO 'playwright install' needed — we attach to
                                  # your real Chrome over CDP, not a bundled browser
"""
import os, sys, time, queue, threading, logging, subprocess, urllib.request

from constants import BASE_DIR

CHROME      = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
DEBUG_PORT  = 9222
DEBUG_URL   = f"http://127.0.0.1:{DEBUG_PORT}"
PROFILE_DIR = os.path.join(BASE_DIR, "chrome_bashin_profile")


def _debug_alive() -> bool:
    try:
        urllib.request.urlopen(f"{DEBUG_URL}/json/version", timeout=1)
        return True
    except Exception:
        return False


def _launch_chrome() -> bool:
    """Start a debug-enabled Chrome on the BashIn profile if not already up."""
    if _debug_alive():
        return True
    if not os.path.exists(CHROME):
        logging.error("browser: Chrome not found at %s", CHROME)
        return False
    os.makedirs(PROFILE_DIR, exist_ok=True)
    logging.info("browser: launching debug Chrome (profile=%s)", PROFILE_DIR)
    subprocess.Popen([
        CHROME,
        f"--remote-debugging-port={DEBUG_PORT}",
        f"--user-data-dir={PROFILE_DIR}",
        "--no-first-run",
        "--no-default-browser-check",
        "--start-maximized",
    ])
    for _ in range(40):                 # up to ~20s for Chrome to expose the port
        if _debug_alive():
            time.sleep(1.0)             # let the first tab settle
            return True
        time.sleep(0.5)
    logging.error("browser: debug Chrome did not come up on port %d", DEBUG_PORT)
    return False


class _BrowserThread(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True, name="BashInBrowser")
        self._q         = queue.Queue()
        self._ready     = threading.Event()
        self._start_err = None
        self._pw        = None
        self._browser   = None
        self._page      = None

    # ── public API (called from any thread) ────────────────────────────────────
    def submit(self, fn, timeout: float = 90):
        """Run fn(page) on the browser thread and block for its return value."""
        if not self.is_alive():
            self.start()
        self._ready.wait(timeout=35)
        if self._start_err:
            raise RuntimeError(self._start_err)
        box = {"done": threading.Event()}
        self._q.put((fn, box))
        if not box["done"].wait(timeout):
            raise TimeoutError("browser operation timed out")
        if "error" in box:
            raise box["error"]
        return box.get("result")

    # ── thread body ────────────────────────────────────────────────────────────
    def run(self):
        try:
            if sys.platform == "win32":
                import asyncio
                asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
            from playwright.sync_api import sync_playwright
            self._pw = sync_playwright().start()
        except Exception as e:
            self._start_err = f"Playwright not available: {e}"
            logging.error("browser: %s", self._start_err)
            self._ready.set()
            return
        self._ready.set()

        while True:
            fn, box = self._q.get()
            if fn is None:
                break
            try:
                box["result"] = fn(self._get_page())
            except Exception as e:
                logging.error("browser: op failed: %s", e)
                box["error"] = e
            finally:
                box["done"].set()

    # ── internals (only ever run on this thread) ───────────────────────────────
    def _connected(self) -> bool:
        try:
            return self._browser is not None and self._browser.is_connected()
        except Exception:
            return False

    def _get_page(self):
        if not _debug_alive():
            if not _launch_chrome():
                raise RuntimeError("Could not start the BashIn Chrome window.")
            self._browser = None        # force a fresh CDP connection

        if not self._connected():
            self._browser = self._pw.chromium.connect_over_cdp(DEBUG_URL)
            self._page    = None

        ctx = (self._browser.contexts[0]
               if self._browser.contexts else self._browser.new_context())

        # Reuse our automation tab if it's still open, else grab/open one
        if self._page is not None:
            try:
                if not self._page.is_closed():
                    return self._page
            except Exception:
                pass
        self._page = ctx.pages[0] if ctx.pages else ctx.new_page()
        return self._page


BROWSER = _BrowserThread()


def warm_up():
    """Optionally pre-launch Chrome + connect so the first command is snappy."""
    try:
        BROWSER.submit(lambda page: page.url, timeout=40)
        logging.info("browser: warmed up")
    except Exception as e:
        logging.warning("browser: warm-up skipped: %s", e)
