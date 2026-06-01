"""
Execute engine — runs autonomous action steps on the PC.
Actions: click, double_click, right_click, type, hotkey, launch, wait, scroll, screenshot.
"""
import os, time, subprocess, logging
import pyautogui

pyautogui.FAILSAFE = True   # move mouse to top-left corner to abort
pyautogui.PAUSE    = 0.08

# Common app paths — GPT returns a name, we resolve to an exe
_APP_MAP = {
    "chrome":      r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "firefox":     r"C:\Program Files\Mozilla Firefox\firefox.exe",
    "notepad":     "notepad.exe",
    "explorer":    "explorer.exe",
    "calculator":  "calc.exe",
    "paint":       "mspaint.exe",
    "word":        r"C:\Program Files\Microsoft Office\root\Office16\WINWORD.EXE",
    "excel":       r"C:\Program Files\Microsoft Office\root\Office16\EXCEL.EXE",
    "outlook":     r"C:\Program Files\Microsoft Office\root\Office16\OUTLOOK.EXE",
    "vscode":      r"C:\Users\User\AppData\Local\Programs\Microsoft VS Code\Code.exe",
    "terminal":    "wt.exe",
    "cmd":         "cmd.exe",
    "settings":    "ms-settings:",
    "task manager":"taskmgr.exe",
    "snipping":    "snippingtool.exe",
}


def run_step(step: dict) -> str:
    """Execute one action step. Returns a status string for logging."""
    action = step.get("action", "click")

    if action == "click":
        x, y = int(step["x"]), int(step["y"])
        pyautogui.moveTo(x, y, duration=0.25, tween=pyautogui.easeOutQuad)
        time.sleep(0.08)
        pyautogui.click()
        logging.info("execute: click (%d, %d)", x, y)

    elif action == "double_click":
        x, y = int(step["x"]), int(step["y"])
        pyautogui.moveTo(x, y, duration=0.25, tween=pyautogui.easeOutQuad)
        time.sleep(0.08)
        pyautogui.doubleClick()
        logging.info("execute: double_click (%d, %d)", x, y)

    elif action == "right_click":
        x, y = int(step["x"]), int(step["y"])
        pyautogui.moveTo(x, y, duration=0.25, tween=pyautogui.easeOutQuad)
        time.sleep(0.08)
        pyautogui.rightClick()
        logging.info("execute: right_click (%d, %d)", x, y)

    elif action == "type":
        text = step.get("text", "")
        # use pyperclip + hotkey for reliable unicode support
        import pyperclip
        pyperclip.copy(text)
        pyautogui.hotkey("ctrl", "v")
        logging.info("execute: type %r", text[:60])

    elif action == "press":
        key = step.get("key", "enter")
        pyautogui.press(key)
        logging.info("execute: press %r", key)

    elif action == "hotkey":
        keys = step.get("keys", [])
        pyautogui.hotkey(*keys)
        logging.info("execute: hotkey %s", "+".join(keys))

    elif action == "launch":
        app = step.get("app", "").lower().strip()
        path = _APP_MAP.get(app, app)   # fallback: treat value as direct command
        if path.startswith("ms-"):
            os.startfile(path)
        else:
            subprocess.Popen(path, shell=isinstance(path, str) and " " not in path)
        logging.info("execute: launch %r → %r", app, path)

    elif action == "wait":
        ms = step.get("ms", 1000)
        time.sleep(ms / 1000)
        logging.info("execute: wait %dms", ms)

    elif action == "scroll":
        x    = step.get("x")
        y    = step.get("y")
        amt  = step.get("amount", 3)
        if x and y:
            pyautogui.moveTo(int(x), int(y), duration=0.2)
        pyautogui.scroll(amt)
        logging.info("execute: scroll %+d at (%s,%s)", amt, x, y)

    elif action == "screenshot":
        pass   # caller re-captures screen after this step

    else:
        logging.warning("execute: unknown action %r", action)

    return action
