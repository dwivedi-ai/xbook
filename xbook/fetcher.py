"""Playwright session that captures Bookmarks GraphQL responses with pagination."""

import asyncio
from typing import Callable

from playwright.async_api import Response, async_playwright

from .errors import BrowserNotInstalled, ExpiredCookies, RateLimited, ResponseShapeChanged
from .normalize import extract_cursor, normalize_entry
from .state import parse_rate_limit_headers

BOOKMARKS_URL = "https://x.com/i/bookmarks"
GRAPHQL_MARKER = "/graphql/"
OPERATION_MARKER = "Bookmarks"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _entries_from_payload(payload: dict) -> list[dict]:
    try:
        instructions = payload["data"]["bookmark_timeline_v2"]["timeline"]["instructions"]
    except (KeyError, TypeError) as e:
        raise ResponseShapeChanged(
            f"could not find data.bookmark_timeline_v2.timeline.instructions: {e}"
        )
    entries: list[dict] = []
    for inst in instructions:
        if inst.get("type") == "TimelineAddEntries":
            entries.extend(inst.get("entries", []))
    return entries


async def fetch_bookmarks(
    cookies: list[dict],
    count: int,
    max_scrolls: int = 50,
    response_timeout: float = 20.0,
    on_progress: Callable[[int], None] | None = None,
) -> tuple[list[dict], int, dict]:
    """Drive the browser to collect `count` deduped, normalized bookmarks.

    Returns (bookmarks, skipped_tombstones, rate_limit_info).
    `rate_limit_info` carries the most recent X-Rate-Limit-* values observed;
    keys: limit, remaining, reset_at. May be empty if headers were absent.
    """
    collected: dict[str, dict] = {}  # tweet_id -> normalized
    skipped_tombstones = 0
    rate_limit_info: dict = {}
    pending_responses: asyncio.Queue[dict] = asyncio.Queue()

    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.launch(headless=True)
        except Exception as e:
            msg = str(e)
            if "Executable doesn't exist" in msg or "playwright install" in msg:
                raise BrowserNotInstalled(
                    "Chromium for Playwright is not installed yet. "
                    "Run: xbook --install-browsers   (or: playwright install chromium)"
                )
            raise
        context = await browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1280, "height": 900},
        )
        await context.add_cookies(cookies)
        page = await context.new_page()

        async def on_response(resp: Response):
            url = resp.url
            if GRAPHQL_MARKER not in url or OPERATION_MARKER not in url:
                return
            # Capture rate-limit headers regardless of status code.
            parsed = parse_rate_limit_headers(resp.headers)
            if parsed:
                rate_limit_info.update(parsed)
            if resp.status == 429:
                await pending_responses.put({"__rate_limited__": True})
                return
            try:
                payload = await resp.json()
            except Exception:
                return
            await pending_responses.put(payload)

        page.on("response", on_response)

        try:
            await page.goto(BOOKMARKS_URL, wait_until="domcontentloaded", timeout=30_000)
        except Exception as e:
            await browser.close()
            raise RuntimeError(f"failed to load {BOOKMARKS_URL}: {e}")

        if "login" in page.url or "flow/login" in page.url:
            await browser.close()
            raise ExpiredCookies(
                "redirected to login; auth_token/ct0 likely expired. "
                "Re-copy cookies from your browser."
            )

        last_cursor: str | None = None
        scrolls = 0

        while len(collected) < count and scrolls < max_scrolls:
            # Drain any payloads arrived since last loop iteration.
            try:
                payload = await asyncio.wait_for(pending_responses.get(), timeout=response_timeout)
            except asyncio.TimeoutError:
                # No new response in the wait window — probably end of feed.
                break

            if payload.get("__rate_limited__"):
                await browser.close()
                raise RateLimited("X returned HTTP 429. Wait several minutes before retrying.")

            entries = _entries_from_payload(payload)
            new_count = 0
            for entry in entries:
                normalized = normalize_entry(entry)
                if normalized is None:
                    # could be cursor entry or tombstone
                    content = entry.get("content", {})
                    if content.get("entryType") == "TimelineTimelineItem":
                        skipped_tombstones += 1
                    continue
                tid = normalized["tweet_id"]
                if tid not in collected:
                    collected[tid] = normalized
                    new_count += 1

            cursor = extract_cursor(entries)
            if on_progress:
                on_progress(len(collected))

            if len(collected) >= count:
                break

            # If we got no new tweets AND cursor didn't advance, we're at the end.
            if new_count == 0 and cursor == last_cursor:
                break
            last_cursor = cursor

            # Scroll to trigger the next page.
            await page.evaluate("window.scrollBy(0, window.innerHeight * 4)")
            scrolls += 1

        await browser.close()

    results = list(collected.values())[:count]
    return results, skipped_tombstones, rate_limit_info
