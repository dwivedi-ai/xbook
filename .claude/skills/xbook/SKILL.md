---
name: xbook
description: Export the user's X (Twitter) bookmarks to JSON using the local xbook CLI. Read-only — never posts, deletes, or modifies X-side state. Use when the user asks to fetch, export, save, archive, or process their X/Twitter bookmarks. Respects cooldown and rate-limit guardrails to protect the user's account.
---

You are operating xbook, a local CLI that exports the user's X (Twitter) bookmarks to a JSON file. This skill encodes the operational rules for using it **safely**. Read this fully before invoking the tool.

## What xbook does

- Drives a headless Chromium via Playwright using the user's session cookies.
- Reads bookmarks via X's GraphQL `Bookmarks` endpoint by intercepting browser responses.
- Writes a clean JSON file with: `tweet_id`, `author_name`, `author_handle`, `full_text`, `timestamp`, `source_url`, and optionally `media[]`.

## What xbook does NOT do

- Does NOT post tweets, like, follow, delete bookmarks, or issue any write to X.
- Does NOT support read+write modes. This is deliberate: write automation is what X's anti-bot system actively flags, and mixing read+write in one session contaminates the read path's safety profile. If the user asks for write capability, explain that xbook is read-only by design and do not attempt to extend it.

## How to invoke

The tool is on PATH as `xbook` (after `uv tool install` or `pip install`). From a clone, use `python -m xbook` or `python main.py`.

```bash
xbook                            # default: 5 bookmarks -> bookmarks.json
xbook -n 50 -o recent.json       # 50 bookmarks to custom file
xbook -o -                       # write JSON to stdout (info goes to stderr) — agent-friendly
xbook --show                     # print the saved bookmarks.json to stdout, no fetch
xbook --cookies firefox          # read auth from Firefox profile (no save)
xbook --setup                    # run the interactive cookie setup wizard
xbook --show-state               # print state file path + current state
xbook --update                   # upgrade xbook in place from GitHub
xbook --install-browsers         # one-time, downloads Chromium
xbook --force                    # bypass cooldown (use sparingly)
xbook --cooldown 60              # use a larger cooldown for this run
```

**`-n` / `--count` is capped at 100.** Passing a larger value errors out — the cap protects rate-limit headroom. If the user has fewer bookmarks than `--count`, xbook returns whatever is available (does not hang or pad); the CLI prints `note: only N bookmark(s) available; requested M`.

Cookie discovery order (when `--cookies env` / default):
1. process env vars `X_AUTH_TOKEN` + `X_CT0`
2. `./.env` in the current working directory
3. `~/.config/xbook/.env` (or `$XDG_CONFIG_HOME/xbook/.env`)

If none have both values and stdin is a TTY, the wizard runs automatically. In non-TTY contexts (CI, scripts), missing cookies error out cleanly — don't try to spawn the wizard.

## Pre-flight — ALWAYS do this first

Before calling `xbook` for any non-trivial export:

1. Run `xbook --show-state` and read the output. Note:
   - `last_fetch_at` — when the user last ran it
   - `last_rate_limit_remaining` / `last_rate_limit_limit` — how much headroom is left
   - `consecutive_failures` — if ≥ 1, something was wrong in a recent run

2. If `last_fetch_at` is within the last 60 seconds and you don't have a specific reason to fetch again, **do not run**. Tell the user the previous fetch is recent and ask if they really need a new one.

3. If `consecutive_failures` ≥ 3, **do not run**. Tell the user there's a recurring failure and that they should investigate (likely expired cookies or response-shape drift). Suggest they re-copy cookies from their browser.

## Hard rules — do not violate these

1. **Never pass `--force` without an explicit, recent user request.** Cooldown exists to protect the account. If the user asked to run xbook 30 seconds after a previous run, ask them whether they really want to bypass the cooldown rather than assuming yes.

2. **Never retry after a `rate_limited` (HTTP 429) failure.** The state file records `last_rate_limit_reset_at`; tell the user to wait until that time. Do not loop, do not back off and retry, do not pass `--force`.

3. **Never run xbook in a loop without explicit user instruction.** This tool is for on-demand or scheduled use, not for polling. If the user wants periodic exports, suggest a cron job at minimum every 15 minutes, not a tight loop.

4. **Never attempt to add or simulate write operations** (posting, deleting bookmarks, etc.) by editing xbook code or by using Playwright directly. xbook is read-only by deliberate design — see the project's intro and the explanation in `design.md`. If the user wants write capability, ask them to file an issue for discussion rather than implementing it.

5. **Never log or print the cookie values** (`X_AUTH_TOKEN`, `X_CT0`, or the file contents of `.env`). Treat them like passwords. If you need to verify cookies are present, check existence not value.

## Reading the result

`bookmarks.json` is an array of objects in the schema documented in README.md. To work with it:

- Most recent bookmark first (the order reflects bookmarking recency, not tweet creation time).
- `media` is present only when the bookmark has attached photos/videos.
- `full_text` already has t.co URLs expanded and long-form (`note_tweet`) content unfurled.
- Tweets bookmarked from suspended/protected/deleted authors are silently skipped; the CLI prints `note: skipped N unavailable bookmark(s)` when this happens.

## Outcome handling

The CLI exits with these codes and matching `outcome` strings in `~/.local/state/xbook/log.jsonl`. Map each to the right next step:

| Outcome              | Exit | What to do                                                                   |
|----------------------|------|------------------------------------------------------------------------------|
| `ok`                 | 0    | Read `bookmarks.json` (or the user's `--output` path) and proceed.            |
| `missing_cookies`    | 2    | Suggest the user run `xbook --setup` interactively. Don't try to paste cookies yourself or guess them. |
| `expired_cookies`    | 3    | Tell the user to re-copy `auth_token` and `ct0` from their browser. Don't retry. |
| `rate_limited`       | 4    | Tell the user to wait until `last_rate_limit_reset_at` (visible via `--show-state`). Don't retry. |
| `shape_changed`      | 5    | X likely changed the response shape. Tell the user to run `spike.py` and file an issue; suggest they share `spike_raw_response.json` (sanitized). |
| `refused_cooldown`   | 6    | Wait the indicated seconds. Do not auto-pass `--force`. Confirm with user.   |
| `browser_not_installed` | 7 | Run `xbook --install-browsers` once. Then retry the original command.        |
| `browser_deps_missing` | 8  | Run `xbook --install-deps` (Linux only, sudo required). Then retry.          |
| (invalid args)       | 2    | `--count` is out of range (1..100) or `--show` couldn't find the saved JSON. Adjust args; don't retry blindly. |
| `unknown_error`      | 1    | Report the error message verbatim to the user. Don't retry without investigation. |

## Common workflows

### "Export my latest 50 bookmarks"

```
1. xbook --show-state              # check headroom + last_fetch
2. (if last_fetch was recent: confirm with user)
3. xbook -n 50
4. read bookmarks.json
```

### "I want a daily export"

Do NOT recommend running xbook in a tight loop. Recommend either:
- A cron job: `0 9 * * * cd /path/to/xbook && uv run xbook -n 50 -o daily-$(date +\%F).json`
- Or a manual `xbook` call once per day.

### "Why did my export only get N bookmarks when I asked for M?"

The user likely has fewer than M bookmarks total. The CLI prints `note: only N bookmark(s) available; requested M` in that case. Confirm by checking the JSON length. This is expected behavior — xbook detects end-of-feed and returns what exists rather than hanging.

### "I want to pipe the bookmarks into another tool"

Use `xbook -o -` to write JSON to stdout. Informational output (progress, notes, the `wrote N bookmark(s)` line) is routed to stderr in this mode, so `xbook -o - | jq ...` or `xbook -o - > file.json` is clean. For reading the *previously saved* file without fetching, use `xbook --show`.

### "Update xbook"

Run `xbook --update` — it upgrades from GitHub via pip against the current interpreter. Safe to run; no fetch happens, no cookies needed. After updating, the user may also want to re-run `xbook --install-browsers` if the playwright dependency bumped.

### "It says my cookies expired"

Walk the user through re-copying cookies:
1. Open x.com in their browser (logged in).
2. DevTools → Application → Cookies → `https://x.com`.
3. Copy `auth_token` and `ct0` values into `.env`.
4. Re-run xbook.

## When NOT to use this skill

- The user is asking about a different tool (twikit, bird-cli, official X API).
- The user wants to post to X.
- The user wants to read another user's bookmarks (xbook can only read the authenticated user's own).

In these cases, do not invoke xbook and explain the limitation.
