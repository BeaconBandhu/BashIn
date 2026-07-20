"""
edge_node.py -- headless entry point for a BashIn "edge" device: no GUI, no
microphone, no PyQt overlay, no Win32 hotkeys. Just the LAN mesh server
(lan_mesh.py), so this machine can RECEIVE voice-dispatched tasks from another
BashIn instance on the same LAN and execute them (Spotify/Swiggy/Calendar/
Forms) via agents.py.

Use this on Linux/macOS edge devices, or any machine you don't want running
the full voice-overlay GUI -- it just sits there as a dispatch target.

Run:
    python3 edge_node.py

Notes:
  - Swiggy/Calendar/Forms need a Chrome browser + the BashIn Bridge extension
    (cursor-overlay/extension/) running on THIS machine to actually execute
    those tasks here -- chrome_bridge.py's websocket server is cross-platform.
  - Spotify needs pyautogui + a real display + the Spotify desktop app; if
    unavailable (e.g. a headless box), spotify_agent reports that clearly
    instead of crashing (see agents.py's _PYAUTOGUI_OK guard).
  - This machine needs its OWN OpenAI API key set (option 's' below) --
    each device executes with its own key; the sender's key is never sent.
  - Install psutil too (`pip3 install psutil`) if you want this device's RAM/
    CPU/battery/temperature to show up in the dashboard (Tray -> Open
    Dashboard, on whichever machine runs the full GUI). Without it, this
    device just reports "psutil not installed" instead of crashing.
"""
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

import config
import lan_mesh


def _prompt(msg):
    try:
        return input(msg).strip()
    except EOFError:
        return ""


def set_api_key():
    cfg = config.load_cfg()
    key = _prompt("Paste your OpenAI API key: ")
    if key:
        cfg["openai_api_key"] = key
        config.save_cfg(cfg)
        print("Saved.")


def pair_new_device():
    code = lan_mesh.MESH.begin_pairing()
    print(f"\nOn the OTHER device, choose 'Enter Pairing Code' and enter:\n\n    {code}\n")
    print("(valid for 120 seconds, single use)")


def enter_pairing_code():
    candidates = [d for d in lan_mesh.MESH.list_devices() if not d.get("paired")]
    if not candidates:
        print("No unpaired devices visible on this network yet. Make sure both "
              "devices are on the same WiFi/LAN and try again in a few seconds.")
        return
    print("\nUnpaired devices visible on the LAN:")
    for i, d in enumerate(candidates):
        print(f"  [{i}] {d['name']}")
    idx = _prompt("Which device number? ")
    try:
        target = candidates[int(idx)]
    except (ValueError, IndexError):
        print("Invalid choice.")
        return
    code = _prompt(f"Code shown on {target['name']}: ")
    ok, msg = lan_mesh.MESH.attempt_pairing(target["device_id"], code)
    print(("OK: " if ok else "FAILED: ") + msg)


def list_devices():
    devices = lan_mesh.MESH.list_devices()
    if not devices:
        print("No devices seen yet (paired or otherwise).")
        return
    for d in devices:
        state = "online" if d.get("ip") else "offline"
        paired = "paired" if d.get("paired") else "unpaired"
        print(f"  {d['name']}  [{state}, {paired}]  id={d['device_id']}")


MENU = """
Commands:
  p  - Pair a new device (show a code for the OTHER device to enter)
  e  - Enter a pairing code shown on another device
  l  - List devices seen on this network
  s  - Set this device's OpenAI API key
  q  - Quit
"""


def main():
    cfg = config.ensure_identity(config.load_cfg())
    print(f"Device name: {cfg['device_name']}   (edit device_name in config.json to rename)")
    print(f"Device id:   {cfg['device_id']}")
    if not cfg.get("openai_api_key"):
        print("\nNo OpenAI API key set yet on this device -- required to execute "
              "dispatched tasks. Use option 's' below to set it.")

    lan_mesh.MESH.set_pairing_result_callback(
        lambda ok, msg: print(("\nPAIRED: " if ok else "\nPAIRING FAILED: ") + msg))
    lan_mesh.MESH.start()
    print(f"\nLAN mesh running on port {cfg['mesh_port']}. Listening for dispatched tasks...")
    print(MENU)

    while True:
        cmd = _prompt("> ").lower()
        if cmd == "p":
            pair_new_device()
        elif cmd == "e":
            enter_pairing_code()
        elif cmd == "l":
            list_devices()
        elif cmd == "s":
            set_api_key()
        elif cmd == "q":
            break
        elif cmd:
            print(MENU)


if __name__ == "__main__":
    main()
