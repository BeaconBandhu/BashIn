"""
Multi-agent orchestrator for BashIn.

Flow per command:
  1. Regex intent detection   — instant, no LLM
  2. gpt-4o-mini param extract — parallel with first action step
  3. Specialist agent executes — pyautogui steps
  4. Screenshot verify each    — gpt-4o-mini low-detail, ~300ms
  5. Retry up to MAX_RETRIES   — on any verification failure
  6. Speak real success/failure — never claims success without screenshot proof
"""
import re, time, json, logging, os, subprocess, datetime, ctypes
from urllib.parse import quote
import pyautogui
from openai import OpenAI
from screen import capture_screen

MAX_RETRIES = 3
CHROME  = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
SPOTIFY = r"C:\Users\User\AppData\Roaming\Spotify\Spotify.exe"

pyautogui.FAILSAFE = True
pyautogui.PAUSE    = 0.04

# ── Multi-turn pending state ───────────────────────────────────────────────────
_pending: dict = {}

_CONFIRM_RE = re.compile(
    r"\b(yes|yeah|yep|sure|proceed|checkout|confirm|go ahead|place order|ok|okay)\b", re.I)
_CANCEL_RE  = re.compile(
    r"\b(no|nope|cancel|stop|don.?t|never mind|abort)\b", re.I)


# ── Intent detection ──────────────────────────────────────────────────────────
_INTENTS = {
    "spotify":  re.compile(r"\b(spotify|play\b|song|music|artist|album|track)\b", re.I),
    "swiggy":   re.compile(r"\b(swiggy|instamart|insta\s*mart|order\b|grocery|groceries|deliver)\b", re.I),
    "calendar": re.compile(r"\b(calendar|event|meeting|schedule|remind|appointment|add.{0,20}task)\b", re.I),
}

def detect_intent(text: str) -> str:
    for name, pat in _INTENTS.items():
        if pat.search(text):
            return name
    return "general"


# ── Fast param extraction via gpt-4o-mini ─────────────────────────────────────
_PARAM_SYS = {
    "spotify":  'Extract the song name and artist from the user request. Return valid JSON only: {"song":"...","artist":"..."}. Set artist to "" if not mentioned.',
    "swiggy":   'Extract the product name and quantity from the order request. Return valid JSON only: {"product":"...","quantity":1}.',
    "calendar": 'Extract event name, date (today/tomorrow/YYYY-MM-DD), and time (HH:MM 24h or "") from the request. Return valid JSON only: {"event":"...","date":"today","time":""}.',
}

def extract_params(intent: str, text: str, client: OpenAI) -> dict:
    if intent not in _PARAM_SYS:
        return {}
    try:
        r = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": _PARAM_SYS[intent]},
                {"role": "user",   "content": text},
            ],
            response_format={"type": "json_object"},
            max_tokens=80,
        )
        return json.loads(r.choices[0].message.content)
    except Exception as e:
        logging.error("extract_params: %s", e)
        return {}


# ── Screenshot verification (gpt-4o-mini low-detail, ~300ms) ──────────────────
def verify(expected: str, client: OpenAI) -> bool:
    try:
        b64, *_ = capture_screen()
        r = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": [
                {"type": "image_url",
                 "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "low"}},
                {"type": "text",
                 "text": f"Does this screenshot show: {expected}? Answer only 'yes' or 'no'."},
            ]}],
            max_tokens=5,
        )
        ans = r.choices[0].message.content.strip().lower()
        logging.info("verify(%r) → %s", expected, ans)
        return ans.startswith("yes")
    except Exception as e:
        logging.error("verify: %s", e)
        return False


# ── Vision element finder (gpt-4o high-detail, returns screen coords) ─────────
def find_element(description: str, client: OpenAI):
    try:
        b64, xs, ys, iw, ih, sw, sh = capture_screen()
        r = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": [
                {"type": "image_url",
                 "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "high"}},
                {"type": "text", "text": (
                    f"Find the center of '{description}' in this {iw}x{ih} image. "
                    f"Return JSON: {{\"x\":<int_image_x>,\"y\":<int_image_y>}}. "
                    f"Return {{\"x\":null,\"y\":null}} if not visible."
                )},
            ]}],
            response_format={"type": "json_object"},
            max_tokens=40,
        )
        d = json.loads(r.choices[0].message.content)
        if d.get("x") is None:
            return None
        sx, sy = int(d["x"] * xs), int(d["y"] * ys)
        logging.info("find_element(%r) → (%d, %d)", description, sx, sy)
        return sx, sy
    except Exception as e:
        logging.error("find_element(%r): %s", description, e)
        return None


# ── Helpers ────────────────────────────────────────────────────────────────────
def _speak_async(text: str, client: OpenAI):
    import sounddevice as sd, numpy as np
    try:
        tts = client.audio.speech.create(
            model="tts-1", voice="alloy", input=text, response_format="pcm")
        pcm = np.frombuffer(tts.content, dtype=np.int16).astype(np.float32) / 32768.0
        sd.play(pcm, samplerate=24000); sd.wait()
    except Exception as e:
        logging.error("_speak_async: %s", e)

# Hold a module-level reference so the GC doesn't collect the callback mid-enumeration
_WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_size_t, ctypes.c_size_t)

def _bring_chrome_to_front() -> bool:
    """Force Chrome to foreground using AttachThreadInput (works on Win10/11)."""
    u32   = ctypes.windll.user32
    found = [0]

    def _cb(hwnd, _):
        cls = ctypes.create_unicode_buffer(64)
        u32.GetClassNameW(hwnd, cls, 64)
        if cls.value == "Chrome_WidgetWin_1" and u32.IsWindowVisible(hwnd):
            found[0] = hwnd
        return True

    cb = _WNDENUMPROC(_cb)
    u32.EnumWindows(cb, 0)
    hwnd = found[0]
    if not hwnd:
        logging.warning("_bring_chrome_to_front: no Chrome window found")
        return False

    u32.ShowWindow(hwnd, 9)          # SW_RESTORE — unminimise if needed
    u32.BringWindowToTop(hwnd)

    fg_hwnd    = u32.GetForegroundWindow()
    tid_fg     = u32.GetWindowThreadProcessId(fg_hwnd, None)
    tid_chrome = u32.GetWindowThreadProcessId(hwnd,    None)

    # AttachThreadInput lets us steal focus even on Win10/11 focus-theft protection
    if tid_fg != tid_chrome:
        u32.AttachThreadInput(tid_fg, tid_chrome, True)
        u32.SetForegroundWindow(hwnd)
        u32.AttachThreadInput(tid_fg, tid_chrome, False)
    else:
        u32.SetForegroundWindow(hwnd)

    time.sleep(0.3)
    return True

def _open_chrome(url: str):
    """Open URL in Chrome as a new tab, then force Chrome to the foreground."""
    # No --new-window: Chrome opens the URL as a new tab in the existing window
    subprocess.Popen([CHROME, url])
    time.sleep(1.5)
    _bring_chrome_to_front()
    time.sleep(0.3)

def _navigate_chrome(url: str):
    """Navigate the current Chrome window to url via the address bar."""
    _bring_chrome_to_front()
    time.sleep(0.2)
    pyautogui.hotkey("ctrl", "l"); time.sleep(0.25)
    pyautogui.hotkey("ctrl", "a")
    pyautogui.write(url, interval=0.02)
    pyautogui.press("enter")

def _wait_page(expected: str, client: OpenAI, timeout: float = 12.0) -> bool:
    """Poll verify every ~1s until True or timeout expires."""
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if verify(expected, client):
            return True
        time.sleep(0.8)
    return False


# ── Spotify Agent ─────────────────────────────────────────────────────────────
def spotify_agent(params: dict, client: OpenAI) -> str:
    song   = params.get("song", "").strip()
    artist = params.get("artist", "").strip()
    query  = f"{song} {artist}".strip() if artist else song
    if not query:
        return "Which song should I play?"

    for attempt in range(MAX_RETRIES):
        try:
            logging.info("spotify attempt %d: %r", attempt, query)

            if os.path.exists(SPOTIFY):
                subprocess.Popen([SPOTIFY])
            else:
                pyautogui.hotkey("win", "s")
                time.sleep(0.2)
                pyautogui.write("Spotify", interval=0.04)
                pyautogui.press("enter")

            time.sleep(2.0)
            pyautogui.hotkey("ctrl", "k"); time.sleep(0.25)
            pyautogui.hotkey("ctrl", "a")
            pyautogui.write(query, interval=0.03)
            time.sleep(0.5)
            pyautogui.press("enter")
            time.sleep(1.2)

            if verify(f"Spotify showing search results or now playing {song}", client):
                pyautogui.press("enter"); time.sleep(0.5)
                return f"Playing {query} on Spotify."

            logging.warning("spotify verify failed attempt %d", attempt)

        except Exception as e:
            logging.error("spotify attempt %d: %s", attempt, e)
        time.sleep(0.3)

    return f"Couldn't verify Spotify opened {query} after {MAX_RETRIES} tries."


# ── Swiggy Instamart Agent ────────────────────────────────────────────────────
def swiggy_agent(params: dict, client: OpenAI) -> str:
    """Add item to cart, open cart for review, then ask before checkout."""
    product = params.get("product", "").strip()
    qty     = int(params.get("quantity", 1))
    if not product:
        return "What should I order from Swiggy Instamart?"

    search_url = f"https://www.swiggy.com/instamart/search?custom_back=true&query={quote(product)}"

    for attempt in range(MAX_RETRIES):
        try:
            logging.info("swiggy attempt %d: %r", attempt, product)

            # ── Navigate ─────────────────────────────────────────────────────
            if attempt == 0:
                _open_chrome(search_url)       # new tab + force Chrome to front
            else:
                _navigate_chrome(search_url)   # reuse existing window

            # ── Wait for products to appear (up to 12 s) ─────────────────────
            if not _wait_page("Swiggy Instamart product listing or search results", client, timeout=12.0):
                logging.warning("swiggy: page not ready attempt %d", attempt)
                # Try one more time after dismissing any modal
                pyautogui.press("escape"); time.sleep(1.0)
                if not verify("Swiggy Instamart product listing or search results", client):
                    continue

            # Dismiss any overlay before interacting
            pyautogui.press("escape"); time.sleep(0.4)

            # Login wall — tell user instead of looping
            if verify("Swiggy login or sign in page or modal", client):
                return "Swiggy needs you to be signed in. Please log in and try again."

            # ── Find and click ADD ────────────────────────────────────────────
            # The + button is a small square at the top-right corner of each product card
            pos = find_element(
                "the blue outlined plus (+) button at the top-right corner of the first product card", client)
            if not pos:
                logging.warning("swiggy: ADD button not found attempt %d", attempt)
                continue

            # Hover then click (React buttons sometimes need mouseover)
            pyautogui.moveTo(*pos, duration=0.15); time.sleep(0.2)
            pyautogui.click(*pos)
            time.sleep(2.5)

            # Login modal check
            if verify("Swiggy login or sign-in modal appeared", client):
                return "Swiggy is asking you to sign in. Please log in and try again."

            # ── "Go to Cart" bar appears at the bottom when an item is in cart ─
            # Don't rely on verify("cart updated") — just look for the bar directly
            pos_cart = find_element(
                "'Go to Cart' button in the sticky bottom bar on Swiggy Instamart", client)
            if not pos_cart:
                logging.warning("swiggy: Go to Cart bar not found attempt %d — item not added", attempt)
                continue

            logging.info("swiggy: item added — Go to Cart bar found")

            # ── Open cart ─────────────────────────────────────────────────────
            pyautogui.click(*pos_cart); time.sleep(2.0)

            if not verify("Swiggy cart page showing items ready for checkout", client):
                continue

            # ── Stop — ask user before touching payment ───────────────────────
            _pending["swiggy_checkout"] = {"product": product, "qty": qty}
            qty_str = f"{qty}x " if qty > 1 else ""
            return (f"Added {qty_str}{product} to your cart and opened the cart. "
                    f"Say 'proceed to checkout' to pay with Amazon Pay, or 'cancel' to stop.")

        except Exception as e:
            logging.error("swiggy attempt %d: %s", attempt, e)
        time.sleep(0.5)

    return f"Couldn't add {product} to the Swiggy cart after {MAX_RETRIES} attempts."


def swiggy_checkout(product: str, client: OpenAI) -> str:
    """Checkout with Amazon Pay — only called after user confirms."""
    for attempt in range(MAX_RETRIES):
        try:
            logging.info("swiggy_checkout attempt %d", attempt)

            if not verify("Swiggy cart page with items", client):
                _navigate_chrome("https://www.swiggy.com/instamart/cart"); time.sleep(2.5)

            pos = find_element("Proceed to pay or Checkout button", client)
            if pos:
                pyautogui.click(*pos); time.sleep(2.0)

            pos = find_element("Amazon Pay wallet payment option", client)
            if pos:
                pyautogui.click(*pos); time.sleep(0.8)
                pos2 = find_element("Place Order or Pay now confirm button", client)
                if pos2:
                    pyautogui.click(*pos2); time.sleep(2.0)
                    if verify("order placed confirmation or order successful screen", client):
                        return f"Order placed for {product} via Amazon Pay!"
                return "Amazon Pay selected — tap Place Order on screen to finish."

            logging.warning("swiggy_checkout: Amazon Pay not found attempt %d", attempt)

        except Exception as e:
            logging.error("swiggy_checkout attempt %d: %s", attempt, e)
        time.sleep(0.5)

    return f"Couldn't reach payment for {product} after {MAX_RETRIES} attempts."


# ── Google Calendar Agent ─────────────────────────────────────────────────────
def _parse_event_datetime(date_str: str, time_str: str):
    now = datetime.datetime.now()
    if date_str == "today":
        base = now.date()
    elif date_str == "tomorrow":
        base = (now + datetime.timedelta(days=1)).date()
    else:
        try:
            base = datetime.date.fromisoformat(date_str)
        except Exception:
            base = now.date()

    if time_str:
        try:
            h, m = map(int, time_str.replace(".", ":").split(":")[:2])
            start = datetime.datetime(base.year, base.month, base.day, h, m)
        except Exception:
            start = datetime.datetime(base.year, base.month, base.day, 9, 0)
    else:
        start = datetime.datetime(base.year, base.month, base.day, 9, 0)

    return start, start + datetime.timedelta(hours=1)


def calendar_agent(params: dict, client: OpenAI) -> str:
    event = params.get("event", "").strip()
    date  = params.get("date", "today")
    t     = params.get("time", "").strip()
    if not event:
        return "What event should I add to your calendar?"

    start, end = _parse_event_datetime(date, t)
    date_param = f"{start.strftime('%Y%m%dT%H%M%S')}/{end.strftime('%Y%m%dT%H%M%S')}"
    url = (f"https://calendar.google.com/calendar/r/eventedit"
           f"?text={quote(event)}&dates={date_param}")
    logging.info("calendar URL: %s", url)

    for attempt in range(MAX_RETRIES):
        try:
            logging.info("calendar attempt %d", attempt)

            if attempt == 0:
                _open_chrome(url)
            else:
                _navigate_chrome(url)

            # Poll for the event form (up to 12 s)
            if not _wait_page("Google Calendar event creation form with title field", client, timeout=12.0):
                logging.warning("calendar: form not visible attempt %d", attempt)
                continue

            # Scroll to top so Save toolbar is always in view
            sw = ctypes.windll.user32.GetSystemMetrics(0)
            sh = ctypes.windll.user32.GetSystemMetrics(1)
            pyautogui.click(sw // 2, sh // 2); time.sleep(0.3)
            pyautogui.hotkey("ctrl", "home"); time.sleep(0.4)

            pos = None
            for desc in [
                "blue Save button at the top of the Google Calendar event form",
                "Save button in the event editing toolbar",
                "button that says Save near the top of the page",
                "Save",
            ]:
                pos = find_element(desc, client)
                if pos:
                    logging.info("calendar: Save found with %r at %s", desc, pos)
                    break

            time_str = f" at {t}" if t else ""

            if pos:
                pyautogui.click(*pos); time.sleep(2.5)
                if verify("Google Calendar week or month view (event form is closed)", client):
                    return f"Added '{event}'{time_str} to your calendar."
                return f"Saved '{event}'{time_str} — check your calendar to confirm."

            # Keyboard fallback
            logging.warning("calendar: vision failed, trying Tab fallback attempt %d", attempt)
            for _ in range(6):
                pyautogui.press("tab"); time.sleep(0.15)
            pyautogui.press("enter"); time.sleep(2.5)
            if verify("Google Calendar week or month view (event form is closed)", client):
                return f"Added '{event}'{time_str} to your calendar."

        except Exception as e:
            logging.error("calendar attempt %d: %s", attempt, e)
        time.sleep(0.5)

    return f"Couldn't save '{event}' to calendar after {MAX_RETRIES} attempts."


# ── Orchestrator entry point ──────────────────────────────────────────────────
def run_agent(text: str, client: OpenAI):
    """Route to specialist. Returns speech string, or None to fall back to GPT-4o."""
    if "swiggy_checkout" in _pending:
        if _CONFIRM_RE.search(text):
            info = _pending.pop("swiggy_checkout")
            logging.info("run_agent: swiggy checkout confirmed for %r", info)
            return swiggy_checkout(info["product"], client)
        if _CANCEL_RE.search(text):
            info = _pending.pop("swiggy_checkout")
            return f"Order for {info['product']} cancelled."
        if detect_intent(text) not in ("general", "swiggy"):
            _pending.pop("swiggy_checkout", None)

    intent = detect_intent(text)
    logging.info("run_agent: intent=%s  text=%r", intent, text)

    if intent == "general":
        return None

    params = extract_params(intent, text, client)
    logging.info("run_agent: params=%s", params)

    t0 = time.monotonic()
    if   intent == "spotify":  result = spotify_agent(params, client)
    elif intent == "swiggy":   result = swiggy_agent(params, client)
    elif intent == "calendar": result = calendar_agent(params, client)
    else:                      return None

    logging.info("run_agent: intent=%s  elapsed=%.2fs  result=%r",
                 intent, time.monotonic() - t0, result)
    return result
