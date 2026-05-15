# xbook — Design

## Goal
A local CLI that exports the latest `N` bookmarks from an authenticated X account to `bookmarks.json`, without using X's official API and without requiring a visible browser session.

## Why the obvious approaches fail

| Approach | Why we're not using it |
|---|---|
| Official X API v2 | Bookmark endpoint is paywalled (Basic/Pro tiers, $100+/mo). |
| Pure-HTTP client (`twikit`, `bird-cli`, raw `requests`) | X requires a header `x-client-transaction-id` derived from obfuscated client-side JS (the "KEY_BYTE" puzzle). The JS changes every few weeks; every pure-HTTP library is in a permanent reverse-engineering treadmill. This is the failure mode you already observed. |
| DOM scraping of `x.com/i/bookmarks` | Virtual scrolling renders only a handful of tweets at a time; HTML markup churns frequently; text extraction loses link/media metadata. |

## The chosen approach: headless browser + GraphQL response interception

Conceptually:

1. Launch **Playwright** with Chromium in headless mode (no visible window).
2. Inject `auth_token` and `ct0` cookies into the browser context.
3. Navigate to `https://x.com/i/bookmarks`.
4. **Don't touch the DOM.** Instead, register a network listener for responses matching `**/graphql/**/Bookmarks**`. Each response is already a clean JSON payload with the full tweet objects.
5. Scroll the page to trigger pagination; accumulate tweets across responses, dedupe by `tweet_id`, stop once we have `N` (or the cursor stops advancing).
6. Normalize each tweet to the required schema and write `bookmarks.json`.

The browser itself generates a valid `x-client-transaction-id` because real JS runs in real Chromium — we don't have to forge it. That's the entire reason this approach is durable where `twikit` is not.

## Component sketch

```
xbook/
├── main.py                # CLI entry (argparse: --count, --output, --cookie-source)
├── xbook/
│   ├── cookies.py         # Load from .env OR read from Firefox/Chromium cookie store
│   ├── fetcher.py         # Playwright session, navigate, intercept GraphQL, paginate
│   ├── normalize.py       # GraphQL tweet object → output schema, t.co expansion
│   └── errors.py          # Typed errors: ExpiredCookies, RateLimited, ShapeChanged
├── docs/
└── .env                   # X_AUTH_TOKEN, X_CT0
```

## Output schema (per bookmark)

```json
{
  "tweet_id": "1234567890",
  "author_name": "Jane Doe",
  "author_handle": "janedoe",
  "full_text": "...with t.co links expanded; long-form tweets fully expanded...",
  "timestamp": "2026-05-14T18:23:01Z",
  "source_url": "https://x.com/janedoe/status/1234567890",
  "media": [
    {"type": "photo", "url": "https://pbs.twimg.com/media/..."},
    {"type": "video", "url": "https://video.twimg.com/..."}
  ]
}
```

`media` is present only when the bookmark has attached media. For videos and animated GIFs, we pick the highest-bitrate `mp4` from `video_info.variants`.

## GraphQL response shape (validated against spike output)

```
data.bookmark_timeline_v2.timeline.instructions[0].entries[]
  ├── content.entryType == "TimelineTimelineItem"  (a tweet)
  │   └── content.itemContent.tweet_results.result   (Tweet object)
  │       ├── rest_id                                → tweet_id
  │       ├── legacy.created_at                      → parse to ISO 8601
  │       ├── legacy.full_text                       → short-form text
  │       ├── legacy.entities.urls[]                 → t.co → expanded_url map
  │       ├── legacy.extended_entities.media[]       → media list
  │       ├── note_tweet.note_tweet_results.result.text → long-form text (overrides full_text)
  │       └── core.user_results.result.core.{name,screen_name}
  └── content.entryType == "TimelineTimelineCursor"  (cursor entry, last)
      └── content.value                              → cursor for next page
```

URL pattern observed: `https://x.com/i/api/graphql/{HASH}/Bookmarks?variables={...}`. Match by operation name (`Bookmarks` in path), not by hash.

## Cookie source

Two modes, both supported:

- **`.env` (default):** `X_AUTH_TOKEN=...` and `X_CT0=...`. Explicit, portable, easy to rotate.
- **`--cookie-source firefox|chromium`:** read directly from the browser's local cookie store. Convenience feature; comes with caveats documented in [risks.md](./risks.md).

## Pagination & "exactly N"

- The Bookmarks GraphQL response is paginated via a cursor embedded in the response itself.
- We scroll → wait for new response → extract tweets + next cursor → repeat.
- Stop conditions: (a) `len(deduped) >= N`, (b) cursor unchanged across two consecutive responses (end of bookmarks), (c) hard cap on scrolls to bound runtime.
- If the account has fewer than `N` bookmarks total, return what's available and warn.

## Non-goals (explicit)

- Re-bookmarking, deleting, or modifying state on X.
- Capturing media files (we capture URLs only).
- Real-time sync / a daemon / a UI.
- Multi-account support.
