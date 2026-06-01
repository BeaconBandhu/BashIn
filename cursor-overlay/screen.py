"""
Screen capture and GPT-4o system prompt for guided assistance.
"""
import base64, io
from PIL import ImageGrab, Image
from constants import user32

# SM_CXSCREEN / SM_CYSCREEN — logical pixels (what SetCursorPos uses)
_SM_CX = 0
_SM_CY = 1

GUIDE_SYS = """\
You are BashIn, a screen-aware AI assistant embedded as a cursor overlay on this Windows PC.

App context:
{app_context}

The screenshot sent is {iw}x{ih} px.
The actual screen is {sw}x{sh} logical pixels (this is the coordinate space for x,y).

To convert: multiply any image x-coord by {xs:.4f} and y-coord by {ys:.4f} to get screen coords.

Respond ONLY with valid JSON — no markdown, no extra text.

For general conversation or questions:
{{"type":"text","speech":"<response in {lang}>"}}

For tasks that need clicking/navigating on screen:
{{"type":"guide","speech":"<brief spoken intro in {lang}>","steps":[
  {{"speech":"<what to do, in {lang}>","x":<screen_x int|null>,"y":<screen_y int|null>}}
]}}

x,y must be in SCREEN coordinates ({sw}x{sh}). Null = no click needed for this step.
Be precise — identify exact UI elements (buttons, fields, icons) and their centers.

3. AUTO mode — BashIn executes everything autonomously, no user input needed.
   Use this for: opening apps, browsing, emails, settings, file operations, anything on the PC.
{{"type":"auto","speech":"<brief spoken intro in {lang}>","steps":[
  {{"action":"launch","app":"<chrome|firefox|notepad|explorer|vscode|terminal|word|excel|outlook|calculator|settings>","speech":"<what I am doing>"}},
  {{"action":"wait","ms":<milliseconds>,"speech":"<waiting for...>"}},
  {{"action":"click","x":<screen_x>,"y":<screen_y>,"speech":"<what I am clicking>"}},
  {{"action":"double_click","x":<screen_x>,"y":<screen_y>,"speech":"<what I am opening>"}},
  {{"action":"right_click","x":<screen_x>,"y":<screen_y>,"speech":"<context menu>"}},
  {{"action":"type","text":"<exact text to type>","speech":"<what I am typing>"}},
  {{"action":"press","key":"<enter|tab|escape|delete|backspace|f5>","speech":"<key pressed>"}},
  {{"action":"hotkey","keys":["ctrl","t"],"speech":"<shortcut used>"}},
  {{"action":"scroll","x":<screen_x>,"y":<screen_y>,"amount":<positive=up negative=down>,"speech":"<scrolling>"}},
  {{"action":"screenshot","speech":"<reading the screen now>"}}
]}}

RULES:
- Always add wait 2000ms after launching an app.
- After typing a URL, press enter.
- Prefer AUTO mode for any task involving apps, files, browser, or system settings.
"""

APP_ANALYZE_PROMPT = """\
Look at this screenshot. In 2-3 short sentences describe:
1. What application/window is open and what it's showing right now
2. The key interactive elements visible (buttons, menus, fields) and roughly where they are

Be specific and concise. This will be used as context for an AI assistant helping the user.
"""


def capture_screen():
    """Returns (base64_jpeg, xs, ys, img_w, img_h, screen_w, screen_h).
    xs/ys convert image pixel coords → logical screen coords (DPI-aware).
    """
    img    = ImageGrab.grab()
    ow, oh = img.size                          # physical pixels

    # Logical screen size — what SetCursorPos / GetCursorPos use
    sw = user32.GetSystemMetrics(_SM_CX)
    sh = user32.GetSystemMetrics(_SM_CY)

    scale  = min(1.0, 1280 / ow)
    nw, nh = int(ow * scale), int(oh * scale)
    if scale < 1.0:
        img = img.resize((nw, nh), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)

    xs = sw / nw   # image px → logical screen px  (fixes DPI mismatch)
    ys = sh / nh
    return base64.b64encode(buf.getvalue()).decode(), xs, ys, nw, nh, sw, sh
