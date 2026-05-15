"""
Hypothesis spike: can headless Playwright + injected cookies reach
x.com/i/bookmarks and observe the 'Bookmarks' GraphQL response?

If yes -> the full tool is just engineering on top of this.
If no  -> we re-plan (stealth, headful, etc.) before building further.

Run:  .venv/bin/python spike.py
"""

import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from playwright.async_api import async_playwright

BOOKMARKS_URL = "https://x.com/i/bookmarks"
GRAPHQL_MARKER = "/graphql/"
OPERATION_MARKER = "Bookmarks"
WAIT_SECONDS = 15
RAW_DUMP = Path("spike_raw_response.json")


def load_cookies() -> list[dict]:
    load_dotenv()
    auth_token = os.getenv("X_AUTH_TOKEN")
    ct0 = os.getenv("X_CT0")
    if not auth_token or not ct0:
        sys.exit("ERROR: X_AUTH_TOKEN and X_CT0 must be set in .env")

    # x.com is the canonical domain; twitter.com still resolves for some flows.
    cookies = []
    for domain in (".x.com", ".twitter.com"):
        cookies.append({"name": "auth_token", "value": auth_token, "domain": domain,
                        "path": "/", "httpOnly": True, "secure": True, "sameSite": "Lax"})
        cookies.append({"name": "ct0", "value": ct0, "domain": domain,
                        "path": "/", "httpOnly": False, "secure": True, "sameSite": "Lax"})
    return cookies


async def main() -> int:
    cookies = load_cookies()
    captured: list[dict] = []
    matched_urls: list[str] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
            viewport={"width": 1280, "height": 900},
        )
        await context.add_cookies(cookies)
        page = await context.new_page()

        async def on_response(resp):
            url = resp.url
            if GRAPHQL_MARKER in url and OPERATION_MARKER in url:
                matched_urls.append(url)
                try:
                    captured.append(await resp.json())
                except Exception as e:
                    print(f"  [warn] could not parse JSON for {url}: {e}")

        page.on("response", on_response)

        print(f"-> navigating to {BOOKMARKS_URL}")
        try:
            await page.goto(BOOKMARKS_URL, wait_until="domcontentloaded", timeout=30_000)
        except Exception as e:
            print(f"NAVIGATION FAILED: {e}")
            await browser.close()
            return 2

        landed = page.url
        print(f"   landed on: {landed}")
        if "login" in landed or "flow/login" in landed:
            print("FAIL: redirected to login. Cookies are likely expired/invalid.")
            await browser.close()
            return 3

        title = await page.title()
        print(f"   page title: {title!r}")

        print(f"-> waiting up to {WAIT_SECONDS}s for Bookmarks GraphQL response...")
        for _ in range(WAIT_SECONDS):
            if captured:
                break
            await asyncio.sleep(1)

        await browser.close()

    if not captured:
        print("FAIL: no /graphql/.../Bookmarks response observed in the wait window.")
        print("      Possible causes: headless detection, slow page, cookies invalid,")
        print("      or the operation name has changed.")
        return 4

    print(f"OK: captured {len(captured)} Bookmarks response(s)")
    print(f"    matched URLs:")
    for u in matched_urls:
        print(f"      - {u[:120]}{'...' if len(u) > 120 else ''}")

    RAW_DUMP.write_text(json.dumps(captured[0], indent=2, ensure_ascii=False))
    print(f"    raw first response written to: {RAW_DUMP}")
    print("    -> inspect that file to design the parser.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
