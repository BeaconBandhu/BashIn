"""
Multi-agent orchestrator for BashIn.

Web agents (Swiggy, Calendar) drive the user's OWN logged-in Chrome through the
BashIn Bridge extension (see extension/ + chrome_bridge.py): BashIn opens the page
as a tab in the real Chrome and the extension's content script performs real DOM
clicks. No separate profile, no screenshots, no coordinate guessing.
Spotify stays on the desktop app via pyautogui.

Flow per command:
  1. Regex intent detection   — instant, no LLM
  2. gpt-4o-mini param extract — song/product/event + qty/time
  3. Specialist agent executes — extension (web) or pyautogui (Spotify)
  4. Deterministic result      — DOM state from the content script, not vision
  5. Honest success/failure    — never claims success it can't observe
"""
import re, time, json, logging, os, subprocess, datetime
from urllib.parse import quote
import pyautogui
from openai import OpenAI

from screen       import capture_screen
from chrome_bridge import BRIDGE

MAX_RETRIES = 3
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


# ── Screenshot verification (used only by Spotify desktop app) ────────────────
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


# ── Spotify Agent (desktop app, pyautogui — already reliable) ─────────────────
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
                pyautogui.hotkey("win", "s"); time.sleep(0.2)
                pyautogui.write("Spotify", interval=0.04); pyautogui.press("enter")

            time.sleep(2.0)
            pyautogui.hotkey("ctrl", "k"); time.sleep(0.25)
            pyautogui.hotkey("ctrl", "a")
            pyautogui.write(query, interval=0.03); time.sleep(0.5)
            pyautogui.press("enter"); time.sleep(1.2)

            if verify(f"Spotify showing search results or now playing {song}", client):
                pyautogui.press("enter"); time.sleep(0.5)
                return f"Playing {query} on Spotify."
            logging.warning("spotify verify failed attempt %d", attempt)
        except Exception as e:
            logging.error("spotify attempt %d: %s", attempt, e)
        time.sleep(0.3)
    return f"Couldn't verify Spotify opened {query} after {MAX_RETRIES} tries."


# ── Swiggy Instamart Agent (via BashIn extension in the user's real Chrome) ────
# DOM logic lives in extension/content.js. Result dict: {ok, text?, reason?}.
def swiggy_agent(params: dict, client: OpenAI) -> str:
    product = params.get("product", "").strip()
    qty     = int(params.get("quantity", 1))
    if not product:
        return "What should I order from Swiggy Instamart?"

    search_url = (f"https://www.swiggy.com/instamart/search"
                  f"?custom_back=true&query={quote(product)}")
    try:
        res = BRIDGE.run_command(search_url, {"action": "swiggy_buy", "qty": qty},
                                 "instamart/search", timeout=70)
    except Exception as e:
        logging.error("swiggy: %s", e)
        return f"I hit an error driving Chrome for Swiggy: {str(e)[:120]}"

    logging.info("swiggy: bridge result=%s", res)
    if isinstance(res, dict) and res.get("ok"):
        _pending["swiggy_checkout"] = {"product": product, "qty": qty}
        qty_str = f"{qty}x " if qty > 1 else ""
        return (f"Added {qty_str}{product} to your cart and opened it for review. "
                f"Say 'proceed to checkout' to pay with Amazon Pay, or 'cancel' to stop.")

    reason = res.get("reason", "") if isinstance(res, dict) else ""
    if reason in ("NO_EXTENSION", "NO_TAB", "NO_CONTENT", "TIMEOUT"):
        return ("I couldn't reach the BashIn Chrome extension. Make sure Chrome is open "
                "and the BashIn Bridge extension is enabled, then try again.")
    if reason == "NOPRODUCTS":
        return (f"I opened Swiggy but couldn't see products for {product} — check the "
                f"delivery location is set in Chrome, then ask again.")
    if reason == "NOVARIANT":
        return f"A size picker opened for {product} but I couldn't select a single unit."
    if reason == "NOCART":
        return f"I clicked add for {product} but couldn't confirm the cart updated."
    return f"I couldn't add {product} to the Swiggy cart."


def swiggy_checkout(product: str, client: OpenAI) -> str:
    """Best-effort checkout with Amazon Pay. Reports honestly; never fakes success."""
    cart_url = "https://www.swiggy.com/instamart/cart"
    try:
        res = BRIDGE.run_command(cart_url, {"action": "swiggy_checkout"},
                                 "swiggy.com", timeout=70)
    except Exception as e:
        logging.error("swiggy_checkout: %s", e)
        return f"I hit an error during checkout: {str(e)[:120]}"

    logging.info("swiggy_checkout: bridge result=%s", res)
    reason = res.get("reason", "") if isinstance(res, dict) else ""
    return {
        "LOGIN":           "Swiggy wants you to log in before payment. Please log in once in Chrome.",
        "NOAMAZON":        "I reached checkout but couldn't find Amazon Pay — please pick a payment method on screen.",
        "AMAZON_SELECTED": "Amazon Pay is selected — tap Place Order on screen to finish.",
        "PLACED":          f"Order placed for {product} via Amazon Pay!",
        "SUBMITTED":       f"I submitted the order for {product} — please check the screen to confirm.",
    }.get(reason, f"I couldn't complete checkout for {product}.")


# ── Google Calendar Agent (via BashIn extension) ──────────────────────────────
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

    try:
        res = BRIDGE.run_command(url, {"action": "calendar_save"},
                                 "calendar.google.com", timeout=50)
    except Exception as e:
        logging.error("calendar: %s", e)
        return f"I hit an error driving Chrome for Calendar: {str(e)[:120]}"

    logging.info("calendar: bridge result=%s", res)
    time_str = f" at {t}" if t else ""
    reason   = res.get("reason", "") if isinstance(res, dict) else ""
    if isinstance(res, dict) and res.get("ok"):
        return f"Added '{event}'{time_str} to your calendar."
    if reason in ("NO_EXTENSION", "NO_TAB", "NO_CONTENT", "TIMEOUT"):
        return ("I couldn't reach the BashIn Chrome extension. Make sure Chrome is open "
                "and the extension is enabled, then try again.")
    if reason == "NOSAVE":
        return f"I opened the event form for '{event}' but couldn't find the Save button."
    return f"I clicked Save for '{event}'{time_str} — please check your calendar to confirm."


# ── Orchestrator entry point ──────────────────────────────────────────────────
def run_agent(text: str, client: OpenAI):
    """Route to specialist. Returns speech string, or None to fall back to GPT-4o."""
    if "swiggy_checkout" in _pending:
        # Cancel ALWAYS wins over confirm — if the user says "cancel" anywhere in
        # the utterance (even alongside "proceed"), never touch payment.
        if _CANCEL_RE.search(text):
            info = _pending.pop("swiggy_checkout")
            logging.info("run_agent: swiggy checkout cancelled for %r", info)
            return f"Okay, cancelled. {info['product']} is still in your cart but I won't pay."
        if _CONFIRM_RE.search(text):
            info = _pending.pop("swiggy_checkout")
            logging.info("run_agent: swiggy checkout confirmed for %r", info)
            return swiggy_checkout(info["product"], client)
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
