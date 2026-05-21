# xbook

Export your X (Twitter) bookmarks to JSON. Local, read-only, no API key required.

xbook drives a headless Chromium via Playwright using your existing session cookies, intercepts the `Bookmarks` GraphQL response, and writes a clean `bookmarks.json`. **It is read-only by design** — it does not post, like, follow, or mutate any X-side state. This is a deliberate safety property: write automation is what X's anti-bot system actively flags, so keeping the surface read-only preserves the "looks like a real logged-in user" character of the session.

## Features

- Latest *N* bookmarks → `bookmarks.json` (tweet_id, author, full text, timestamp, source URL, media URLs)
- Long-form (note_tweet) bookmarks expanded in full
- Media URLs included (best-quality MP4 for videos, full-res image URLs)
- Built-in **30s cooldown** between fetches (override with `--force`)
- **Rate-limit awareness** — warns when X rate-limit headroom drops below 20%
- **Structured JSONL log** of every run at `~/.local/state/xbook/log.jsonl`
- Cookies from `.env` or directly from your Firefox profile

## Install

Requires Python 3.10+. Pick whichever installer you like.

### With [uv](https://docs.astral.sh/uv/) (recommended)

Install xbook as a system-wide tool so the `xbook` command is on your PATH from any directory:

```bash
uv tool install git+https://github.com/dwivedi-ai/xbook
xbook --install-browsers     # one-time, downloads ~150MB Chromium
xbook --install-deps         # Linux only: installs Chromium's system libs (needs sudo)
xbook                        # first run launches the cookie setup wizard
```

Or run it without installing at all:

```bash
uvx --from git+https://github.com/dwivedi-ai/xbook xbook --install-browsers
uvx --from git+https://github.com/dwivedi-ai/xbook xbook --install-deps   # Linux only
uvx --from git+https://github.com/dwivedi-ai/xbook xbook
```

For local development:

```bash
git clone https://github.com/dwivedi-ai/xbook
cd xbook
uv venv
uv pip install -e .
uv run playwright install chromium
uv run xbook --install-deps  # Linux only
uv run xbook                 # or: .venv/bin/xbook
```

### With pip + venv

```bash
git clone https://github.com/dwivedi-ai/xbook
cd xbook
python -m venv .venv
.venv/bin/pip install -e .
.venv/bin/xbook --install-browsers    # ~150MB, one-time
.venv/bin/xbook --install-deps        # Linux only
.venv/bin/xbook
```

> **Why `--install-deps`?** Chromium needs a handful of system shared libraries
> (`libnspr4`, `libnss3`, `libasound2`, etc.) that aren't shipped with the
> browser binary. On macOS and Windows these are present by default, so the
> command is a no-op. On Linux — especially minimal server/container images —
> they're often missing, and you'll see errors like `error while loading shared
> libraries: libnspr4.so`. Run `xbook --install-deps` once and you're set.

## First-time setup

xbook needs two cookies from your logged-in X session: `auth_token` and `ct0`. The easiest way is to let xbook walk you through it:

```bash
xbook --setup
```

This launches an interactive wizard that asks how you'd like to provide the cookies:

1. **Read from Firefox** — works if you're logged in to x.com in Firefox already. xbook reads from `~/.mozilla/firefox/<profile>/cookies.sqlite` (copied to a tempfile first to avoid lock contention with a running Firefox). If reading fails, the wizard falls back to manual entry.

2. **Paste them manually** — open x.com in any browser → DevTools → Application → Cookies → `https://x.com` → copy the values of `auth_token` and `ct0`. Paste at the prompt (values are hidden as you type).

The wizard saves the cookies to `~/.config/xbook/.env` with `chmod 600`, so any future `xbook` invocation from any directory just works. The first time you run `xbook` without configured credentials, the wizard runs automatically — no need to invoke `--setup` explicitly.

### Where xbook looks for cookies

In order:

1. Process environment (`X_AUTH_TOKEN` + `X_CT0` already exported)
2. `./.env` in the current working directory (handy for development)
3. `~/.config/xbook/.env` (or `$XDG_CONFIG_HOME/xbook/.env` if set) — what the wizard writes

If none have both values, the wizard runs (or, in non-interactive contexts like CI, you get a clear error).

### Manual setup (if you prefer)

If you'd rather skip the wizard, create `~/.config/xbook/.env` yourself:

```env
X_AUTH_TOKEN=paste_auth_token_here
X_CT0=paste_ct0_here
```

Then `chmod 600 ~/.config/xbook/.env`. **Never commit this file.**

### `--cookies firefox` shortcut

If you want to skip the saved-credential path entirely and read from Firefox on every run:

```bash
xbook --cookies firefox
```

This bypasses the `.env` lookup and reads directly from Firefox each time.

## Usage

Once installed, invoke as `xbook` (uv/pip install) or `python -m xbook` (from a clone):

```bash
# Latest 20 bookmarks → bookmarks.json
xbook

# Latest 50 → a different file
xbook --count 50 --output recent.json

# Use Firefox cookies instead of .env
xbook --cookies firefox

# Override the 30s cooldown
xbook --force

# Inspect state without fetching
xbook --show-state

# Re-download the Chromium binary (e.g. after upgrading playwright)
xbook --install-browsers

# Install Chromium's system library dependencies (Linux only, requires sudo)
xbook --install-deps
```

### Output schema

```json
[
  {
    "tweet_id": "1234567890",
    "author_name": "Jane Doe",
    "author_handle": "janedoe",
    "full_text": "Tweet text with t.co URLs expanded.",
    "timestamp": "2026-05-15T09:14:00Z",
    "source_url": "https://x.com/janedoe/status/1234567890",
    "media": [
      {"type": "photo", "url": "https://pbs.twimg.com/media/..."}
    ]
  }
]
```

`media` is present only when the bookmark has attached media. Videos use the highest-bitrate MP4 variant.

## Safety model

xbook is designed to minimize the risk of account suspension. The defaults:

| Guard | Default | Override |
|---|---|---|
| Minimum interval between fetches | 30s | `--force` or `--cooldown <s>` |
| Hard stop on HTTP 429 | always | none — wait for the rate-limit window to reset |
| Warning when rate-limit headroom < 20% | always | none |
| Read-only by design | always | none — deliberate scope decision |

Every run appends one JSON line to `~/.local/state/xbook/log.jsonl`. Inspect it any time:

```bash
.venv/bin/python main.py --show-state
tail ~/.local/state/xbook/log.jsonl
```

Three consecutive failures trigger a warning that your cookies may be expired or X may have changed the response shape.

## Troubleshooting

**`ERROR: X_AUTH_TOKEN and X_CT0 not found...`**
You haven't configured cookies yet. Run `xbook --setup` to launch the wizard, or set the variables manually (see [First-time setup](#first-time-setup)).

**`ERROR: no cookies found and stdin is not a TTY...`**
The wizard can't run in a non-interactive context (CI, piped input). Either set `X_AUTH_TOKEN` and `X_CT0` in the process environment, or run `xbook --setup` once in a terminal so they're saved to `~/.config/xbook/.env`.

**`ERROR: redirected to login; auth_token/ct0 likely expired`**
Your session expired or the cookies are invalid. Open x.com, log in again, re-copy the cookies into `.env`.

**`ERROR: X returned HTTP 429`**
You've hit X's rate limit. Wait for the reset time (visible in `--show-state` under `last_rate_limit_reset_at`) before retrying.

**`ERROR (browser_deps_missing): Chromium is installed but required system libraries are missing`**
You're on Linux and Chromium can't find libraries like `libnspr4.so` or `libnss3.so`. Run `xbook --install-deps` (it wraps `playwright install-deps chromium` and will prompt for sudo). This is a one-time step.

**`ERROR: could not find data.bookmark_timeline_v2...`**
X changed the response shape. Re-run `spike.py` to capture a fresh response, then file an issue with `spike_raw_response.json` attached (be sure to strip any sensitive content first).

**`refusing: last fetch was Ns ago; wait Ys or pass --force`**
Working as designed — the cooldown is protecting your account. Either wait or pass `--force` if you understand the trade-off.

## Architecture

The codebase is small and lays out as:

```
main.py                  Convenience entry; equivalent to `python -m xbook`
xbook/cli.py             CLI orchestration, cooldown + logging
xbook/cookies.py         .env loader, Firefox reader, setup wizard
xbook/fetcher.py         Playwright session, GraphQL response capture, pagination
xbook/normalize.py       GraphQL Tweet → output schema
xbook/state.py           XDG state file + JSONL log
xbook/errors.py          Typed errors
design.md                Technical design — read this for the "why"
spike.py                 Diagnostic script; run if the response shape changes
```

See [`design.md`](./design.md) for the full technical design, including the GraphQL response shape, schema mappings, and the reasoning behind the headless-browser-with-response-interception approach.

## Agent skill

If you're using Claude Code or another agent that supports skills, xbook ships an agent-facing skill at [`.claude/skills/xbook/SKILL.md`](.claude/skills/xbook/SKILL.md). It encodes the safety rules: pre-flight state check, never auto-`--force`, never retry on 429, never simulate writes.

Install it for any project:

```bash
mkdir -p ~/.claude/skills/xbook
cp .claude/skills/xbook/SKILL.md ~/.claude/skills/xbook/
```

After this, any agent invoked in any directory will know how to operate xbook safely.

## Contributing

xbook is intentionally small. Before submitting code that adds features:

- If it adds any write capability (posting, deleting, etc.), please don't. xbook is read-only by deliberate design — see the explanation in the intro. Open an issue for discussion before writing code; the bar for changing this is high.
- If it changes the response shape or schema handling, run `spike.py` first and include the resulting `spike_raw_response.json` (sanitized) in your PR.

## License

MIT — see [LICENSE](LICENSE).

## Disclaimer

xbook uses your X session cookies to retrieve your own bookmarks. It is not affiliated with X Corp. You are responsible for your use of the tool, including compliance with X's terms of service. The project's design prioritizes minimal account risk (read-only operation, conservative defaults, no automation patterns), but no scraping tool can offer zero risk.
