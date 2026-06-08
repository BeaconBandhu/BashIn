"""
browser.py — drives a real Chrome through Playwright, on ONE dedicated thread.

Playwright LAUNCHES and OWNS the Chrome (launch_persistent_context) using a
dedicated "BashIn" profile dir. We do NOT attach over CDP — connect_over_cdp to
an externally-launched Chrome is fragile on Win + Chrome 148 (it throws
"Browser context management is not supported" when relaunched alongside the
user's normal Chrome). Letting Playwright own the process avoids all of that.

The profile (cursor-overlay/chrome_bashin_profile) persists Swiggy / Google
logins across restarts; the user's everyday Chrome (default profile) is separate
and untouched.

Why a dedicated thread: Playwright's sync objects are thread-bound, but the
overlay spawns a fresh worker thread per voice command — so all browser work is
funnelled here via BROWSER.submit(fn), and Chrome + the driver stay warm.

Setup: pip install playwright   (no 'playwright install' — we use channel="chrome")
"""
import os, sys, time, queue, threading, logging, subprocess

from constants import BASE_DIR

PROFILE_DIR = os.path.join(BASE_DIR, "chrome_bashin_profile")

_LAUNCH_ARGS = [
    "--no-first-run",
    "--no-default-browser-check",
    "--start-maximized",
    "--disable-blink-features=AutomationControlled",
]


def _kill_profile_chrome():
    """Kill any chrome.exe still holding the automation profile (stale lock)."""
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
             "Where-Object { $_.CommandLine -like '*chrome_bashin_profile*' } | "
             "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"],
            capture_output=True, timeout=15)
    except Exception as e:
        logging.warning("browser: kill stale chrome: %s", e)


class _BrowserThread(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True, name="BashInBrowser")
        self._q         = queue.Queue()
        self._ready     = threading.Event()
        self._start_err = None
        self._pw        = None
        self._ctx       = None
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
    def _ctx_alive(self) -> bool:
        try:
            if self._ctx is None:
                return False
            _ = self._ctx.pages          # throws if the context/browser is closed
            return True
        except Exception:
            return False

    def _launch(self):
        _kill_profile_chrome()           # clear any stale lock on the profile dir
        time.sleep(1.0)
        os.makedirs(PROFILE_DIR, exist_ok=True)
        logging.info("browser: launching Chrome (profile=%s)", PROFILE_DIR)
        self._ctx = self._pw.chromium.launch_persistent_context(
            PROFILE_DIR,
            channel="chrome",
            headless=False,
            no_viewport=True,
            ignore_default_args=["--enable-automation"],
            args=_LAUNCH_ARGS,
        )
        self._page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()

    def _get_page(self):
        if not self._ctx_alive():
            try:
                self._launch()
            except Exception as e:
                # One retry after force-clearing the profile lock
                logging.warning("browser: launch failed (%s); retrying", e)
                _kill_profile_chrome()
                time.sleep(1.5)
                self._launch()
        if self._page is None or self._page.is_closed():
            self._page = self._ctx.new_page()
        return self._page


BROWSER = _BrowserThread()


def warm_up():
    """Optionally pre-launch Chrome so the first command is snappy."""
    try:
        BROWSER.submit(lambda page: page.url, timeout=45)
        logging.info("browser: warmed up")
    except Exception as e:
        logging.warning("browser: warm-up skipped: %s", e)
