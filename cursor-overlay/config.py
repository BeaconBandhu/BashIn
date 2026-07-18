"""
Config file helpers and Windows startup-registry management.

The registry-based autostart (register/unregister/registered) is Windows-only;
on other platforms (e.g. a headless Linux edge node) those functions are no-ops
so this module -- and lan_mesh.py, which depends on it for device identity --
stays importable everywhere. Use your platform's own autostart mechanism
(systemd, launchd, cron @reboot, etc.) on non-Windows machines instead.
"""
import os, sys, json, socket, uuid
from constants import BASE_DIR, CONFIG_PATH

MESH_PORT_DEFAULT = 8790   # distinct from chrome_bridge.py's 8777

if sys.platform == "win32":
    import winreg
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


def ensure_identity(cfg: dict) -> dict:
    """Fills device_id/device_name/mesh_port/paired_devices if missing (LAN mesh
    identity), persists the change, and returns cfg. Backward-compatible with
    config.json files from before the mesh feature existed."""
    changed = False
    if "device_id" not in cfg:
        cfg["device_id"] = str(uuid.uuid4())
        changed = True
    if "device_name" not in cfg:
        cfg["device_name"] = socket.gethostname()
        changed = True
    if "mesh_port" not in cfg:
        cfg["mesh_port"] = MESH_PORT_DEFAULT
        changed = True
    if "paired_devices" not in cfg:
        cfg["paired_devices"] = {}
        changed = True
    if changed:
        save_cfg(cfg)
    return cfg


if sys.platform == "win32":
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
else:
    def register():
        pass   # use systemd/launchd/cron on non-Windows instead

    def unregister():
        pass

    def registered():
        return False
