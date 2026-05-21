"""xbook — export your latest X bookmarks to JSON via headless Playwright.

Read-only by design (see docs/adr/0003). Safety guardrails:
  - 30s default cooldown between fetches (override with --force or --cooldown)
  - Rate-limit headers captured and surfaced as warnings
  - Structured log appended to $XDG_STATE_HOME/xbook/log.jsonl
"""

import argparse
import asyncio
import json
import sys
import time

from .cookies import load as load_cookies, setup_wizard
from .errors import BrowserDepsMissing, BrowserNotInstalled, ExpiredCookies, MissingCookies, RateLimited, ResponseShapeChanged
from .fetcher import fetch_bookmarks
from .state import (
    DEFAULT_COOLDOWN_SECONDS,
    append_log,
    cooldown_remaining,
    load_state,
    log_path,
    now_iso,
    rate_limit_warning,
    save_state,
    state_path,
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="xbook",
        description="Export your latest X bookmarks to JSON. Read-only by design.",
    )
    p.add_argument("-n", "--count", type=int, default=20,
                   help="number of bookmarks to fetch (default: 20)")
    p.add_argument("-o", "--output", default="bookmarks.json",
                   help="output file path (default: bookmarks.json)")
    p.add_argument("--cookies", choices=("env", "firefox"), default="env",
                   help="cookie source: 'env' reads X_AUTH_TOKEN/X_CT0 from .env (default); "
                        "'firefox' reads from your Firefox profile's cookies.sqlite")
    p.add_argument("--cooldown", type=float, default=DEFAULT_COOLDOWN_SECONDS,
                   help=f"minimum seconds between fetches (default: {DEFAULT_COOLDOWN_SECONDS})")
    p.add_argument("--force", action="store_true",
                   help="bypass the cooldown check (still logged)")
    p.add_argument("--show-state", action="store_true",
                   help="print current state and log paths, then exit")
    p.add_argument("--install-browsers", action="store_true",
                   help="download Chromium for Playwright (one-time, ~150MB), then exit")
    p.add_argument("--install-deps", action="store_true",
                   help="install Chromium's system library dependencies (Linux only, requires sudo), then exit")
    p.add_argument("--setup", action="store_true",
                   help="run the cookie setup wizard (Firefox or manual), then exit")
    return p.parse_args()


def _print_state_paths_and_exit() -> int:
    state = load_state()
    print(f"state file: {state_path()}")
    print(f"log file:   {log_path()}")
    print("current state:")
    print(json.dumps(state, indent=2))
    return 0


def _install_browsers() -> int:
    """Run `playwright install chromium` using the current Python interpreter."""
    import subprocess
    print("downloading Chromium for Playwright (~150MB)...")
    return subprocess.call([sys.executable, "-m", "playwright", "install", "chromium"])


def _install_deps() -> int:
    """Install Chromium's system library dependencies via `playwright install-deps`.

    Linux-only. On macOS/Windows, Playwright's Chromium is self-contained — this is a no-op
    with an informational message. Requires sudo on Linux; we re-invoke through `sudo`
    so the user gets a password prompt rather than a cryptic permission error.
    """
    import shutil
    import subprocess

    if sys.platform != "linux":
        print(f"--install-deps is Linux-only; nothing to do on {sys.platform}.")
        print("On macOS and Windows, Chromium ships with the libraries it needs.")
        return 0

    cmd = [sys.executable, "-m", "playwright", "install-deps", "chromium"]
    if shutil.which("sudo") and __import__("os").geteuid() != 0:
        cmd = ["sudo", *cmd]
        print("installing Chromium system dependencies (sudo required)...")
    else:
        print("installing Chromium system dependencies...")
    return subprocess.call(cmd)


def _run_setup() -> int:
    """Run the interactive cookie setup wizard, then exit."""
    try:
        setup_wizard()
        print()
        print("Setup complete. You can now run `xbook` to fetch your bookmarks.")
        return 0
    except MissingCookies as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\ncancelled.", file=sys.stderr)
        return 130


def _build_log_entry(args, outcome, duration, bookmarks, skipped, rate_limit_info, error=None):
    entry = {
        "ts": now_iso(),
        "outcome": outcome,
        "count_requested": args.count,
        "count_collected": len(bookmarks),
        "tombstones_skipped": skipped,
        "duration_seconds": round(duration, 1),
        "rate_limit_remaining": rate_limit_info.get("remaining"),
        "rate_limit_limit": rate_limit_info.get("limit"),
        "forced": args.force,
    }
    if error:
        entry["error"] = str(error)
    return entry


async def run(args: argparse.Namespace) -> int:
    state = load_state()

    if state.get("consecutive_failures", 0) >= 3:
        print(f"WARNING: {state['consecutive_failures']} consecutive failures recorded. "
              "Cookies may be expired, or the response shape may have changed. "
              "Run with --show-state to inspect.", file=sys.stderr)

    if not args.force:
        wait = cooldown_remaining(state, args.cooldown)
        if wait > 0:
            print(f"refusing: last fetch was {args.cooldown - wait:.0f}s ago; "
                  f"wait {wait:.0f}s or pass --force.", file=sys.stderr)
            append_log({
                "ts": now_iso(),
                "outcome": "refused_cooldown",
                "count_requested": args.count,
                "cooldown_seconds": args.cooldown,
                "wait_remaining_seconds": round(wait, 1),
            })
            return 6

    try:
        cookies = load_cookies(args.cookies)
    except MissingCookies as e:
        print(f"ERROR: {e}", file=sys.stderr)
        append_log({"ts": now_iso(), "outcome": "missing_cookies", "error": str(e)})
        state["consecutive_failures"] = state.get("consecutive_failures", 0) + 1
        save_state(state)
        return 2

    print(f"fetching up to {args.count} bookmarks (source: {args.cookies})...")

    def progress(n: int) -> None:
        print(f"  collected {min(n, args.count)}/{args.count}", end="\r", flush=True)

    start = time.monotonic()
    outcome = "ok"
    error: Exception | None = None
    bookmarks: list[dict] = []
    skipped = 0
    rate_limit_info: dict = {}

    try:
        bookmarks, skipped, rate_limit_info = await fetch_bookmarks(
            cookies, args.count, on_progress=progress
        )
    except BrowserNotInstalled as e:
        outcome, error = "browser_not_installed", e
    except BrowserDepsMissing as e:
        outcome, error = "browser_deps_missing", e
    except ExpiredCookies as e:
        outcome, error = "expired_cookies", e
    except RateLimited as e:
        outcome, error = "rate_limited", e
    except ResponseShapeChanged as e:
        outcome, error = "shape_changed", e
    except Exception as e:
        outcome, error = "unknown_error", e

    duration = time.monotonic() - start
    print()

    # Always update state + log, success or failure.
    if outcome == "ok":
        state["last_fetch_at"] = now_iso()
        state["last_fetch_count"] = len(bookmarks)
        state["consecutive_failures"] = 0
    else:
        state["consecutive_failures"] = state.get("consecutive_failures", 0) + 1

    if rate_limit_info:
        state["last_rate_limit_remaining"] = rate_limit_info.get("remaining")
        state["last_rate_limit_limit"] = rate_limit_info.get("limit")
        state["last_rate_limit_reset_at"] = rate_limit_info.get("reset_at")

    save_state(state)
    append_log(_build_log_entry(args, outcome, duration, bookmarks, skipped, rate_limit_info, error))

    if outcome != "ok":
        msg = str(error) if error else outcome
        print(f"ERROR ({outcome}): {msg}", file=sys.stderr)
        if outcome == "shape_changed":
            print("       Re-run spike.py and inspect spike_raw_response.json.", file=sys.stderr)
        return {
            "expired_cookies": 3,
            "rate_limited": 4,
            "shape_changed": 5,
            "browser_not_installed": 7,
            "browser_deps_missing": 8,
        }.get(outcome, 1)

    if not bookmarks:
        print("no bookmarks found.")
        return 0

    if len(bookmarks) < args.count:
        print(f"note: only {len(bookmarks)} bookmark(s) available; requested {args.count}.")
    if skipped:
        print(f"note: skipped {skipped} unavailable bookmark(s) (deleted/protected).")

    warning = rate_limit_warning(rate_limit_info)
    if warning:
        print(f"WARNING: {warning}", file=sys.stderr)

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(bookmarks, f, indent=2, ensure_ascii=False)
    print(f"wrote {len(bookmarks)} bookmark(s) to {args.output} (took {duration:.1f}s)")
    return 0


def main() -> int:
    args = parse_args()
    if args.show_state:
        return _print_state_paths_and_exit()
    if args.install_browsers:
        return _install_browsers()
    if args.install_deps:
        return _install_deps()
    if args.setup:
        return _run_setup()
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
