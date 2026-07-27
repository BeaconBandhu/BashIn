"""
status_state.py -- tiny shared "what is BashIn doing right now" channel.

Separate from task_history.py on purpose: task_history is the permanent,
per-device audit log (every dispatched/received/local task, ever). This is
the opposite -- ephemeral, in-memory, single "current" slot, meant to be
polled by a live UI (the notch widget) every couple of seconds to show one
line: what's in flight right now, or what the last thing done was.

Text is always truncated to 50 chars (the notch's display budget) at write
time, so callers never have to think about truncation themselves.
"""
import threading, time

_lock = threading.Lock()
_current = {"text": "Idle", "done": True, "ts": time.time()}

MAX_CHARS = 50


def set_status(text: str, done: bool):
    text = (text or "").strip()
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS - 1].rstrip() + "…"
    with _lock:
        _current["text"] = text or ("Done" if done else "Working…")
        _current["done"] = done
        _current["ts"] = time.time()


def get_status() -> dict:
    with _lock:
        return dict(_current)
