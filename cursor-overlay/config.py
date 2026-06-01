"""
Config file helpers and Windows startup-registry management.
"""
import os, json, winreg
from constants import BASE_DIR, CONFIG_PATH

_STARTUP_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_APP_NAME    = "CursorOverlay"
_PYTHONW     = r"C:\Users\User\AppData\Local\Programs\Python\Python311\pythonw.exe"
_SCRIPT      = os.path.join(BASE_DIR, "main.py")


def load_cfg():
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def save_cfg(cfg):
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)


def register():
    try:
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _STARTUP_KEY, 0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(k, _APP_NAME, 0, winreg.REG_SZ, f'"{_PYTHONW}" "{_SCRIPT}"')
        winreg.CloseKey(k)
    except Exception:
        pass


def unregister():
    try:
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _STARTUP_KEY, 0, winreg.KEY_SET_VALUE)
        winreg.DeleteValue(k, _APP_NAME)
        winreg.CloseKey(k)
    except Exception:
        pass


def registered():
    try:
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _STARTUP_KEY)
        winreg.QueryValueEx(k, _APP_NAME)
        winreg.CloseKey(k)
        return True
    except Exception:
        return False
