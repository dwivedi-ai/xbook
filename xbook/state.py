"""XDG-compliant state and structured logging.

State file:  $XDG_STATE_HOME/xbook/state.json (default ~/.local/state/xbook/state.json)
Log file:    $XDG_STATE_HOME/xbook/log.jsonl  (append-only, JSONL)
"""

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_COOLDOWN_SECONDS = 30
RATE_LIMIT_WARN_THRESHOLD = 0.2  # fraction; warn when remaining/limit < this


def _xdg_state_home() -> Path:
    raw = os.environ.get("XDG_STATE_HOME")
    return Path(raw) if raw else Path.home() / ".local" / "state"


def state_dir() -> Path:
    d = _xdg_state_home() / "xbook"
    d.mkdir(parents=True, exist_ok=True)
    return d


def state_path() -> Path:
    return state_dir() / "state.json"


def log_path() -> Path:
    return state_dir() / "log.jsonl"


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _empty_state() -> dict:
    return {
        "last_fetch_at": None,
        "last_fetch_count": 0,
        "last_rate_limit_remaining": None,
        "last_rate_limit_limit": None,
        "last_rate_limit_reset_at": None,
        "consecutive_failures": 0,
    }


def load_state() -> dict:
    path = state_path()
    if not path.exists():
        return _empty_state()
    try:
        with path.open() as f:
            data = json.load(f)
        # Merge with defaults so older state files gain new fields gracefully.
        merged = _empty_state()
        merged.update({k: v for k, v in data.items() if k in merged})
        return merged
    except (json.JSONDecodeError, OSError):
        # Corrupted state should not crash the tool.
        return _empty_state()


def save_state(state: dict) -> None:
    """Atomic write: temp file in the same dir, then rename."""
    path = state_path()
    parent = path.parent
    fd, tmp_name = tempfile.mkstemp(dir=parent, prefix=".state-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp_name, path)
    except Exception:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise


def append_log(entry: dict) -> None:
    """Append one JSON line. Best-effort; never raises into caller."""
    try:
        with log_path().open("a") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass


def seconds_since_last_fetch(state: dict) -> float | None:
    ts = state.get("last_fetch_at")
    if not ts:
        return None
    try:
        last = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - last).total_seconds()


def cooldown_remaining(state: dict, cooldown_seconds: float) -> float:
    """Return seconds remaining in the cooldown window (0 if not blocked)."""
    elapsed = seconds_since_last_fetch(state)
    if elapsed is None:
        return 0.0
    remaining = cooldown_seconds - elapsed
    return max(0.0, remaining)


def parse_rate_limit_headers(headers: dict[str, str]) -> dict:
    """Extract X-Rate-Limit-* fields from a response.headers dict (lowercased keys)."""
    out: dict = {}
    limit = headers.get("x-rate-limit-limit")
    remaining = headers.get("x-rate-limit-remaining")
    reset = headers.get("x-rate-limit-reset")
    if limit and limit.isdigit():
        out["limit"] = int(limit)
    if remaining and remaining.isdigit():
        out["remaining"] = int(remaining)
    if reset and reset.isdigit():
        reset_dt = datetime.fromtimestamp(int(reset), tz=timezone.utc)
        out["reset_at"] = reset_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    return out


def rate_limit_warning(info: dict) -> str | None:
    """If rate-limit headroom is below threshold, return a user-facing warning string."""
    limit = info.get("limit")
    remaining = info.get("remaining")
    if not limit or remaining is None or limit <= 0:
        return None
    fraction = remaining / limit
    if fraction >= RATE_LIMIT_WARN_THRESHOLD:
        return None
    reset = info.get("reset_at", "unknown")
    pct = int(fraction * 100)
    return (f"rate-limit headroom low: {remaining}/{limit} ({pct}%) remaining; "
            f"resets at {reset}")
