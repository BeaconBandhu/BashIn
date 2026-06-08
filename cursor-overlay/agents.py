"""
Multi-agent orchestrator for BashIn.

Web agents (Swiggy, Calendar) drive a real, logged-in Chrome through Playwright
(see browser.py) — real DOM clicks and real cart/URL state, NO screenshots and
NO coordinate guessing. Spotify stays on the desktop app via pyautogui.

Flow per command:
  1. Regex intent detection   — instant, no LLM
  2. gpt-4o-mini param extract — song/product/event + qty/time
  3. Specialist agent executes — Playwright (web) or pyautogui (Spotify)
  4. Deterministic verification — DOM selectors / URL, not vision
  5. Honest success/failure    — never claims success it can't observe
"""
import re, time, json, logging, os, subprocess, datetime
from urllib.parse import quote
import pyautogui
from openai import OpenAI

from constants import BASE_DIR
from screen    import capture_screen
from browser   import BROWSER

MAX_RETRIES = 3
SPOTIFY = r"C:\Users\User\AppData\Roaming\Spotify\Spotify.exe"

pyautogui.FAILSAFE = True
pyautogui.PAUSE    = 0.04

# Where Playwright dumps a screenshot when a selector can't be found (for debugging)
SWIGGY_DEBUG_PNG   = os.path.join(BASE_DIR, "swiggy_debug.png")
CALENDAR_DEBUG_PNG = os.path.join(BASE_DIR, "calendar_debug.png")

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


# ── Swiggy Instamart Agent (Playwright, real DOM — selectors verified live) ───
# Discovered via DOM probe on the live site:
#   add button   → [data-testid="buttonpair-add"]   (an SVG "+" icon, no text)
#   cart bar     → [data-testid="veiwcartbar-container"]   (Swiggy's own typo)
#   variant modal→ aria-label "Please select variant of ..." / "Items list"
#   variant add  → [aria-label="Add 1 item to cart"]
#   close modal  → [aria-label="Close overlay"]
_SW_ADD      = '[data-testid="buttonpair-add"]'
_SW_CARTBAR  = '[data-testid="veiwcartbar-container"]'
_SW_OVERLAY  = '[aria-label^="Please select variant"], [aria-label="Items list"]'
_SW_VAR_ADD  = '[aria-label="Add 1 item to cart"]'
_SW_CLOSE_OV = '[aria-label="Close overlay"]'

# In the "select variant" overlay, find the SINGLE-unit row (e.g. "350 ml", NOT
# "350 ml x 6"/"x 2" multipacks) so a requested quantity = that many individual
# units. Correlates each add button to its row label by vertical position and
# returns the add-button index of the single unit, or -1 if there isn't one.
_JS_SINGLE_VARIANT_IDX = r"""
() => {
  const adds = [...document.querySelectorAll('[aria-label="Add 1 item to cart"]')];
  const rows = [...document.querySelectorAll('[aria-label*="rupee"]')];
  const rowFor = (btn) => {
    const by = btn.getBoundingClientRect().top;
    let best = '', bestd = 1e9;
    for (const r of rows) {
      const d = Math.abs(r.getBoundingClientRect().top - by);
      if (d < bestd) { bestd = d; best = r.getAttribute('aria-label'); }
    }
    return best || '';
  };
  for (let i = 0; i < adds.length; i++) {
    const label = rowFor(adds[i]);
    if (/\bml\b/i.test(label) && !/x\s*\d/i.test(label)) return i;   // no "x N" = single
  }
  return -1;
}
"""


def swiggy_agent(params: dict, client: OpenAI) -> str:
    product = params.get("product", "").strip()
    qty     = int(params.get("quantity", 1))
    if not product:
        return "What should I order from Swiggy Instamart?"

    search_url = (f"https://www.swiggy.com/instamart/search"
                  f"?custom_back=true&query={quote(product)}")

    def task(page):
        page.bring_to_front()
        page.goto(search_url, wait_until="domcontentloaded", timeout=30000)

        # Wait for product add buttons to render
        try:
            page.wait_for_selector(_SW_ADD, timeout=15000)
        except Exception:
            page.screenshot(path=SWIGGY_DEBUG_PNG)
            low = (page.content() or "").lower()
            logging.warning("swiggy: no products. title=%r url=%s",
                            page.title(), page.url)
            if "log in" in low or "sign in" in low or "login" in low:
                return "LOGIN"
            return "NOPRODUCTS"

        # Click the first product's "+" add control
        page.locator(_SW_ADD).first.scroll_into_view_if_needed()
        page.locator(_SW_ADD).first.click()
        page.wait_for_timeout(1200)

        # Multi-variant products pop a "select variant" overlay (350ml / x2 / x6).
        # Pick the SINGLE-unit row so qty = that many individual units, not packs.
        if page.locator(_SW_OVERLAY).count():
            if not page.locator(_SW_VAR_ADD).count():
                page.screenshot(path=SWIGGY_DEBUG_PNG)
                return "NOVARIANT"
            idx = page.evaluate(_JS_SINGLE_VARIANT_IDX)
            if idx is None or idx < 0:
                idx = 0                                       # no single unit → first
                logging.info("swiggy: no single-unit variant, using first row")
            else:
                logging.info("swiggy: single-unit variant at row %d, adding %d", idx, qty)
            target = page.locator(_SW_VAR_ADD).nth(idx)        # stable across clicks
            for _ in range(qty):                               # qty individual units
                target.click()
                page.wait_for_timeout(350)
            if page.locator(_SW_CLOSE_OV).count():
                page.locator(_SW_CLOSE_OV).first.click()
                page.wait_for_timeout(600)
        else:
            # Single-variant product — clicking the same control increments qty
            for _ in range(max(0, qty - 1)):
                page.wait_for_timeout(400)
                page.locator(_SW_ADD).first.click()

        # Confirm the cart bar shows an item count
        bar = page.locator(_SW_CARTBAR)
        try:
            bar.first.wait_for(state="visible", timeout=6000)
        except Exception:
            page.screenshot(path=SWIGGY_DEBUG_PNG)
            return "NOCART"
        bar_text = bar.first.inner_text()
        if not re.search(r"\bitem", bar_text, re.I):
            page.screenshot(path=SWIGGY_DEBUG_PNG)
            logging.warning("swiggy: cart bar has no item count: %r", bar_text)
            return "NOCART"
        logging.info("swiggy: cart = %r", bar_text.replace("\n", " | ")[:120])

        # Open the cart for review
        try:
            page.get_by_text("Go to Cart", exact=False).first.click(timeout=4000)
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_timeout(1500)
        except Exception:
            logging.warning("swiggy: couldn't click Go to Cart; leaving on results")
        return "OK"

    try:
        res = BROWSER.submit(task, timeout=80)
    except RuntimeError as e:
        if "Playwright not available" in str(e):
            return ("Playwright isn't installed yet. Open a terminal and run: "
                    "pip install playwright")
        return f"Browser error on Swiggy: {str(e)[:120]}"
    except Exception as e:
        logging.error("swiggy: %s", e)
        return f"I hit an error driving Chrome for Swiggy: {str(e)[:120]}"

    if res == "LOGIN":
        return ("The BashIn Chrome window isn't logged into Swiggy yet. I've opened it — "
                "please log in once, then ask me again.")
    if res == "NOPRODUCTS":
        return (f"I opened Swiggy but couldn't see any products for {product} — most likely "
                f"the delivery location isn't set in the BashIn Chrome window. Set it once, "
                f"then ask again.")
    if res == "NOVARIANT":
        return f"A variant picker opened for {product} but I couldn't select a size."
    if res == "NOCART":
        return f"I clicked add for {product} but couldn't confirm the cart updated."
    if res != "OK":
        return f"I couldn't add {product} to the Swiggy cart."

    _pending["swiggy_checkout"] = {"product": product, "qty": qty}
    qty_str = f"{qty}x " if qty > 1 else ""
    return (f"Added {qty_str}{product} to your cart and opened it for review. "
            f"Say 'proceed to checkout' to pay with Amazon Pay, or 'cancel' to stop.")


def swiggy_checkout(product: str, client: OpenAI) -> str:
    """Best-effort checkout with Amazon Pay. Reports honestly; never fakes success."""
    def task(page):
        page.bring_to_front()
        if "cart" not in page.url:
            page.get_by_text("Go to Cart", exact=False).first.click(timeout=5000)
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_timeout(1500)

        proceed = page.locator(
            "text=/proceed to pay|proceed to checkout|click to pay|continue|checkout/i")
        if proceed.count():
            proceed.first.click()
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_timeout(1800)

        if "login" in (page.url + page.title()).lower():
            return "LOGIN"

        amazon = page.locator("text=/amazon pay/i")
        if not amazon.count():
            page.screenshot(path=SWIGGY_DEBUG_PNG)
            return "NOAMAZON"
        amazon.first.click()
        page.wait_for_timeout(1000)

        place = page.locator("text=/place order|pay now|make payment/i")
        if not place.count():
            return "AMAZON_SELECTED"
        place.first.click()
        page.wait_for_timeout(2500)

        if page.locator("text=/order placed|order confirmed|thank you|order id/i").count():
            return "PLACED"
        return "SUBMITTED"

    try:
        res = BROWSER.submit(task, timeout=70)
    except Exception as e:
        logging.error("swiggy_checkout: %s", e)
        return f"I hit an error during checkout: {str(e)[:120]}"

    return {
        "LOGIN":           "Swiggy wants you to log in before payment. Please log in once in the BashIn Chrome window.",
        "NOAMAZON":        "I reached checkout but couldn't find Amazon Pay — please pick a payment method on screen.",
        "AMAZON_SELECTED": "Amazon Pay is selected — tap Place Order on screen to finish.",
        "PLACED":          f"Order placed for {product} via Amazon Pay!",
        "SUBMITTED":       f"I submitted the order for {product} — please check the screen to confirm.",
    }.get(res, f"I couldn't complete checkout for {product}.")


# ── Google Calendar Agent (Playwright, real DOM) ──────────────────────────────
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

    def task(page):
        page.bring_to_front()
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(1500)

        if "accounts.google.com" in page.url or "signin" in page.url.lower():
            return "LOGIN"

        # The Save button carries the accessible name "Save" — deterministic.
        save = page.get_by_role("button", name=re.compile(r"^save$", re.I))
        try:
            save.first.wait_for(state="visible", timeout=15000)
        except Exception:
            page.screenshot(path=CALENDAR_DEBUG_PNG)
            logging.warning("calendar: Save not found. url=%s png=%s",
                            page.url, CALENDAR_DEBUG_PNG)
            return "NOSAVE"

        save.first.click()
        # Success = navigation away from the event-edit URL
        try:
            page.wait_for_url(lambda u: "eventedit" not in u, timeout=8000)
            return "OK"
        except Exception:
            page.wait_for_timeout(1500)
            return "OK" if "eventedit" not in page.url else "UNSURE"

    try:
        res = BROWSER.submit(task, timeout=60)
    except RuntimeError as e:
        if "Playwright not available" in str(e):
            return "Playwright isn't installed yet. Run: pip install playwright"
        return f"Browser error on Calendar: {str(e)[:120]}"
    except Exception as e:
        logging.error("calendar: %s", e)
        return f"I hit an error driving Chrome for Calendar: {str(e)[:120]}"

    time_str = f" at {t}" if t else ""
    if res == "LOGIN":
        return ("The BashIn Chrome window isn't logged into Google yet. I've opened it — "
                "please sign in once, then ask me again.")
    if res == "NOSAVE":
        return f"I opened the event form for '{event}' but couldn't find the Save button."
    if res == "UNSURE":
        return f"I clicked Save for '{event}'{time_str} — please check your calendar to confirm."
    if res == "OK":
        return f"Added '{event}'{time_str} to your calendar."
    return f"I couldn't save '{event}' to your calendar."


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
