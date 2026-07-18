"""
Win32 handles, app states, and all layout/size constants.
Imported by every other module — no PyQt6 imports here.

Win32-only symbols (user32/kernel32/dwmapi and the constants that use them) are
only meaningful on Windows, where the full voice-overlay GUI runs. On other
platforms (e.g. a headless Linux edge node that only runs lan_mesh.py +
agents.execute_intent to receive dispatched tasks), those symbols are left as
None rather than raising ImportError -- code that actually needs them only
runs on Windows anyway (app.py, widgets.py), so this keeps constants.py (and
everything that transitively imports it, like config.py/lan_mesh.py) safely
importable everywhere.
"""
import os, sys, ctypes

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ── Win32 (None on non-Windows platforms -- see module docstring) ──────────────
if sys.platform == "win32":
    user32   = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    dwmapi   = ctypes.windll.dwmapi
else:
    user32 = kernel32 = dwmapi = None

GWL_EXSTYLE       = -20
WS_EX_LAYERED     = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
LWA_COLORKEY      = 0x00000001
WM_HOTKEY         = 0x0312
MOD_CTRL          = 0x0002
MOD_ALT           = 0x0001
VK_W              = 0x57
VK_B              = 0x42
VK_LBUTTON        = 0x01
HK_CIRCLE         = 1
HK_VOICE          = 2

# ── App states ─────────────────────────────────────────────────────────────────
IDLE = 0; LISTENING = 1; PROCESSING = 2; SPEAKING = 3; GUIDING = 4

# ── Colorkey (magenta-ish — transparent hole) ──────────────────────────────────
CK_RGB = (255, 0, 254)
CK_REF = 255 | (0 << 8) | (254 << 16)   # COLORREF for SetLayeredWindowAttributes

# ── File paths ─────────────────────────────────────────────────────────────────
CURSOR_IMG  = os.path.join(BASE_DIR, "cursor_img.png")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

# ── Overlay geometry ───────────────────────────────────────────────────────────
CIRCLE_SIZE     = 300
CIRCLE_HALF     = CIRCLE_SIZE // 2
TRAIL_MS        = 300
TICK_MS         = 10
TAIL_X          = 0
TAIL_Y          = 0
DOT_R           = 1       # dot radius (pixels)
RING_R          = 8       # ring radius
RING_LERP       = 0.10    # ring lag — lower = more trail

NUM_BARS        = 4
EQ_W            = 76
EQ_H            = 44
EQ_MAX_R        = 5
EQ_MIN_R        = 2

GUIDE_W         = 64
GUIDE_H         = 64
GUIDE_INNER_R   = 6
GUIDE_OUTER_MAX = 26

TRANS_DURATION  = 0.45   # seconds for cursor→EQ roll animation
