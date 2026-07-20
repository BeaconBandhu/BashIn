"""
task_history.py -- persisted log of every task BashIn has dispatched, received,
or executed locally, for the dashboard's per-device task view.

Append-only JSON-lines file so writes are O(1) and never need a read-modify-
write of the whole (growing) file. One JSON object per line:
  {"ts": <unix>, "source_id":, "source_name":, "target_id":, "target_name":,
   "intent":, "params": {...}, "result": "<spoken text>", "ok": <bool guess>,
   "duration_s": <float>}

Scope note: this is per-device local history, not a globally-synced log. A
device only knows about tasks it dispatched OUT, received IN, or ran locally
for itself -- it does NOT automatically know about tasks exchanged between two
OTHER paired devices it wasn't involved in. That's the honest, distributed-
systems-correct scope without adding a whole history-sync protocol.

"ok" is a heuristic (keyword-scan of the result string for failure language) --
there's no structured success/failure signal from the *_agent functions today,
they only ever return spoken strings. Good enough for a status badge, not
authoritative; treat it as approximate.
"""
import os, json, time, logging

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(BASE_DIR, "mesh_tasks.jsonl")
MAX_LOAD = 500   # cap how many lines load() ever reads back

_FAILURE_HINTS = (
    "couldn't", "could not", "can't", "cannot", "failed", "error",
    "isn't paired", "looks offline", "don't know a device",
    "no openai", "needs an openai", "doesn't have an openai",
    "unavailable", "not configured", "not visible",
)


def _guess_ok(result) -> bool:
    if not result or not isinstance(result, str):
        return False
    low = result.lower()
    return not any(h in low for h in _FAILURE_HINTS)


def record(source_id: str, source_name: str, target_id: str, target_name: str,
          intent: str, params: dict, result, duration_s: float) -> dict:
    """Append one task entry. Never raises (logging errors only)."""
    entry = {
        "ts": time.time(),
        "source_id": source_id, "source_name": source_name,
        "target_id": target_id, "target_name": target_name,
        "intent": intent, "params": params or {},
        "result": result, "ok": _guess_ok(result),
        "duration_s": round(duration_s, 2),
    }
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        logging.error("task_history: failed to record: %s", e)
    return entry


def load(limit: int = MAX_LOAD) -> list:
    """Most-recent-first list of task entries (across all devices)."""
    if not os.path.exists(LOG_PATH):
        return []
    try:
        with open(LOG_PATH, encoding="utf-8") as f:
            lines = f.readlines()
    except Exception as e:
        logging.error("task_history: failed to load: %s", e)
        return []
    out = []
    for line in reversed(lines[-limit:]):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def for_device(device_id: str, limit: int = 200) -> list:
    """Most-recent-first entries where `device_id` was the source or target."""
    out = []
    for e in load(limit=MAX_LOAD):
        if e.get("source_id") == device_id or e.get("target_id") == device_id:
            out.append(e)
            if len(out) >= limit:
                break
    return out
