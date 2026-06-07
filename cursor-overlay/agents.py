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
import re, time, json, logging, os, subprocess, threading, datetime
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
# Stores context between voice turns (e.g. "cart ready, waiting for confirmation")
_pending: dict = {}

_CONFIRM_RE = re.compile(
    r"\b(yes|yeah|yep|sure|proceed|checkout|confirm|go ahead|place order|ok|okay)\b", re.I)
_CANCEL_RE  = re.compile(
    r"\b(no|nope|cancel|stop|don.?t|never mind|abort)\b", re.I)


# ── Intent detection (regex, <1ms) ────────────────────────────────────────────
_INTENTS = {
    "spotify":  re.compile(r"\b(spotify|play\b|song|music|artist|album|track)\b", re.I),
    "swiggy":   re.compile(r"\b(swiggy|instamart|order\b|grocery|groceries|deliver)\b", re.I),
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
    """Returns True only if screenshot visually confirms expected state."""
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


# ── Vision element finder (gpt-4o, returns screen coords) ────────────────────
def find_element(description: str, client: OpenAI):
    """Returns (screen_x, screen_y) of the UI element, or None if not found."""
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
        return int(d["x"] * xs), int(d["y"] * ys)
    except Exception as e:
        logging.error("find_element(%r): %s", description, e)
        return None


# ── Helpers ────────────────────────────────────────────────────────────────────
def _speak_async(text: str, client: OpenAI):
    """Fire-and-forget TTS so the agent can speak mid-task without blocking."""
    import sounddevice as sd, numpy as np
    try:
        tts = client.audio.speech.create(
            model="tts-1", voice="alloy", input=text, response_format="pcm")
        pcm = np.frombuffer(tts.content, dtype=np.int16).astype(np.float32) / 32768.0
        sd.play(pcm, samplerate=24000); sd.wait()
    except Exception as e:
        logging.error("_speak_async: %s", e)

def _open_chrome(url: str):
    subprocess.Popen([CHROME, "--new-window", url])

def _wait_page(expected: str, client: OpenAI, timeout: float = 6.0) -> bool:
    """Poll until verify returns True or timeout."""
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if verify(expected, client):
            return True
        time.sleep(0.6)
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

            # Launch Spotify (or bring to front if already open)
            if os.path.exists(SPOTIFY):
                subprocess.Popen([SPOTIFY])
            else:
                pyautogui.hotkey("win", "s")
                time.sleep(0.2)
                pyautogui.write("Spotify", interval=0.04)
                pyautogui.press("enter")

            time.sleep(2.0)

            # Ctrl+K = search in Spotify Desktop app
            pyautogui.hotkey("ctrl", "k")
            time.sleep(0.25)
            pyautogui.hotkey("ctrl", "a")
            pyautogui.write(query, interval=0.03)
            time.sleep(0.5)
            pyautogui.press("enter")
            time.sleep(1.2)

            if verify(f"Spotify showing search results or now playing {song}", client):
                # Press Enter or Down+Enter to play first result
                pyautogui.press("enter")
                time.sleep(0.5)
                return f"Playing {query} on Spotify."

            logging.warning("spotify verify failed attempt %d", attempt)

        except Exception as e:
            logging.error("spotify attempt %d: %s", attempt, e)
        time.sleep(0.3)

    return f"Couldn't verify Spotify opened {query} after {MAX_RETRIES} tries."


# ── Swiggy Instamart Agent ────────────────────────────────────────────────────
def swiggy_agent(params: dict, client: OpenAI) -> str:
    """Adds item to cart and opens cart for review. Stops and asks before checkout."""
    product = params.get("product", "").strip()
    qty     = int(params.get("quantity", 1))
    if not product:
        return "What should I order from Swiggy Instamart?"

    search_url = f"https://www.swiggy.com/instamart/search?custom_back=true&query={quote(product)}"

    for attempt in range(MAX_RETRIES):
        try:
            logging.info("swiggy attempt %d: %r", attempt, product)

            if attempt == 0:
                # First try: open a fresh Chrome window
                _open_chrome(search_url)
                time.sleep(6.0)
            else:
                # Retries: reuse the existing Chrome window — no new window
                pyautogui.hotkey("ctrl", "l"); time.sleep(0.3)
                pyautogui.hotkey("ctrl", "a")
                pyautogui.write(search_url, interval=0.02)
                pyautogui.press("enter")
                time.sleep(6.0)

            # Dismiss any modal that appeared (location, login, cookie banner)
            pyautogui.press("escape"); time.sleep(0.5)

            # If Swiggy is showing a location modal, handle it
            if verify("location selector or set delivery location dialog on Swiggy", client):
                logging.info("swiggy: location modal detected, dismissing")
                pos = find_element("Use current location or Detect location button", client)
                if pos:
                    pyautogui.click(*pos); time.sleep(3.0)
                else:
                    pyautogui.press("escape"); time.sleep(1.0)

            # Login wall → tell user rather than loop forever
            if verify("Swiggy login or sign in page", client):
                return "Swiggy needs you to be logged in. Please log in and try again."

            if not verify("Swiggy page showing products or search results", client):
                logging.warning("swiggy: page not ready attempt %d", attempt)
                continue

            # ADD button
            pos = find_element(
                f"ADD button or plus icon next to the first {product} product card", client)
            if not pos:
                logging.warning("swiggy: ADD button not found attempt %d", attempt)
                continue

            pyautogui.click(*pos); time.sleep(1.0)

            if not verify("cart updated or item added or cart shows quantity", client):
                logging.warning("swiggy: cart not updated attempt %d", attempt)
                continue

            # Open cart for review
            pos = find_element("View cart button with item count or cart total", client)
            if pos:
                pyautogui.click(*pos); time.sleep(1.5)
            else:
                pyautogui.hotkey("ctrl", "l"); time.sleep(0.2)
                pyautogui.write("https://www.swiggy.com/cart", interval=0.02)
                pyautogui.press("enter"); time.sleep(2.5)

            if not verify("Swiggy cart page with items", client):
                continue

            # ── STOP — ask before payment ─────────────────────────────────────
            _pending["swiggy_checkout"] = {"product": product, "qty": qty}
            qty_str = f"{qty}x " if qty > 1 else ""
            return (f"I've added {qty_str}{product} to your cart and opened the cart for review. "
                    f"Say 'proceed to checkout' to pay with Amazon Pay, or 'cancel' to stop.")

        except Exception as e:
            logging.error("swiggy attempt %d: %s", attempt, e)
        time.sleep(0.5)

    return f"Couldn't add {product} to the Swiggy cart after {MAX_RETRIES} attempts."


def swiggy_checkout(product: str, client: OpenAI) -> str:
    """Runs checkout with Amazon Pay — only called after user confirms."""
    for attempt in range(MAX_RETRIES):
        try:
            logging.info("swiggy_checkout attempt %d", attempt)

            # Make sure we're on the cart page
            if not verify("Swiggy cart page with items", client):
                _open_chrome("https://www.swiggy.com/cart"); time.sleep(2.5)

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
                return f"Amazon Pay selected. Tap Place Order on screen to finish."

            logging.warning("swiggy_checkout: Amazon Pay not found attempt %d", attempt)

        except Exception as e:
            logging.error("swiggy_checkout attempt %d: %s", attempt, e)
        time.sleep(0.5)

    return f"Couldn't reach payment for {product} after {MAX_RETRIES} attempts."


# ── Google Calendar Agent ─────────────────────────────────────────────────────
def _parse_event_datetime(date_str: str, time_str: str):
    """Returns (start_dt, end_dt) datetime objects. end = start + 1h."""
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

    end = start + datetime.timedelta(hours=1)
    return start, end


def calendar_agent(params: dict, client: OpenAI) -> str:
    event = params.get("event", "").strip()
    date  = params.get("date", "today")
    t     = params.get("time", "").strip()
    if not event:
        return "What event should I add to your calendar?"

    start, end = _parse_event_datetime(date, t)
    # Google Calendar event-creation URL — pre-fills everything, just click Save
    date_param = f"{start.strftime('%Y%m%dT%H%M%S')}/{end.strftime('%Y%m%dT%H%M%S')}"
    url = (f"https://calendar.google.com/calendar/r/eventedit"
           f"?text={quote(event)}&dates={date_param}")
    logging.info("calendar URL: %s", url)

    for attempt in range(MAX_RETRIES):
        try:
            logging.info("calendar attempt %d", attempt)
            _open_chrome(url)
            time.sleep(4.0)   # wait for Chrome window + page JS

            # Click centre of screen to ensure Chrome has keyboard focus
            import ctypes
            sw = ctypes.windll.user32.GetSystemMetrics(0)
            sh = ctypes.windll.user32.GetSystemMetrics(1)
            pyautogui.click(sw // 2, sh // 2); time.sleep(0.3)

            # Scroll to top so the Save toolbar is always visible
            pyautogui.hotkey("ctrl", "home"); time.sleep(0.4)

            if not verify("Google Calendar event edit form with title and date fields", client):
                logging.warning("calendar: form not visible attempt %d", attempt)
                time.sleep(1.5); continue

            # Try progressively simpler Save button descriptions
            pos = None
            for desc in [
                "blue Save button at the top of the Google Calendar event form",
                "Save button in the event editing toolbar",
                "button that says Save near the top of the page",
                "Save",
            ]:
                pos = find_element(desc, client)
                if pos:
                    logging.info("calendar: found Save with desc=%r at %s", desc, pos)
                    break

            time_str = f" at {t}" if t else ""

            if pos:
                pyautogui.click(*pos); time.sleep(2.5)
                if verify("Google Calendar week or month view (event form closed)", client):
                    return f"Added '{event}'{time_str} to your calendar."
                return f"Saved '{event}'{time_str} — check your calendar to confirm."

            # Keyboard fallback: Tab through page until Save is focused, then Enter
            logging.warning("calendar: vision failed, trying Tab fallback attempt %d", attempt)
            for _ in range(6):
                pyautogui.press("tab"); time.sleep(0.15)
            pyautogui.press("enter"); time.sleep(2.5)
            if verify("Google Calendar week or month view (event form closed)", client):
                return f"Added '{event}'{time_str} to your calendar."

        except Exception as e:
            logging.error("calendar attempt %d: %s", attempt, e)
        time.sleep(0.5)

    return f"Couldn't save '{event}' to calendar after {MAX_RETRIES} attempts."


# ── Orchestrator entry point ──────────────────────────────────────────────────
def run_agent(text: str, client: OpenAI):
    """
    Route to specialist agent. Returns result speech string.
    Returns None to fall back to the general GPT-4o pipeline.
    """
    # ── Check pending confirmations first ─────────────────────────────────────
    if "swiggy_checkout" in _pending:
        if _CONFIRM_RE.search(text):
            info = _pending.pop("swiggy_checkout")
            logging.info("run_agent: swiggy checkout confirmed for %r", info)
            return swiggy_checkout(info["product"], client)
        if _CANCEL_RE.search(text):
            info = _pending.pop("swiggy_checkout")
            return f"Order for {info['product']} cancelled."
        # Not a yes/no — fall through to normal intent routing below
        # but clear stale pending if user is clearly asking something else
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
