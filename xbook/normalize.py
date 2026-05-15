"""Normalize a GraphQL Tweet object into our output schema."""

from datetime import datetime, timezone

TWITTER_TIME_FMT = "%a %b %d %H:%M:%S %z %Y"


def _to_iso(twitter_ts: str) -> str:
    """'Fri May 15 09:14:00 +0000 2026' -> '2026-05-15T09:14:00Z'"""
    dt = datetime.strptime(twitter_ts, TWITTER_TIME_FMT).astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _expand_tco(text: str, url_entities: list[dict]) -> str:
    """Replace t.co shorturls in `text` with expanded URLs."""
    for u in url_entities or []:
        short = u.get("url")
        expanded = u.get("expanded_url")
        if short and expanded:
            text = text.replace(short, expanded)
    return text


def _extract_media(legacy: dict) -> list[dict]:
    media_items = legacy.get("extended_entities", {}).get("media") or []
    out = []
    for m in media_items:
        mtype = m.get("type")
        if mtype in ("video", "animated_gif"):
            variants = m.get("video_info", {}).get("variants", []) or []
            mp4s = [v for v in variants if v.get("content_type") == "video/mp4" and v.get("url")]
            if mp4s:
                best = max(mp4s, key=lambda v: v.get("bitrate", 0))
                out.append({"type": mtype, "url": best["url"]})
                continue
        # photo, or video fallback to thumbnail
        url = m.get("media_url_https")
        if url:
            out.append({"type": mtype or "unknown", "url": url})
    return out


def normalize_entry(entry: dict) -> dict | None:
    """Convert one entries[] item to our schema. Returns None for tombstones / non-tweets."""
    content = entry.get("content", {})
    if content.get("entryType") != "TimelineTimelineItem":
        return None
    item = content.get("itemContent", {})
    if item.get("itemType") != "TimelineTweet":
        return None

    result = item.get("tweet_results", {}).get("result")
    if not result:
        return None

    # Tombstones / unavailable / suspended-author tweets show up with different typenames.
    if result.get("__typename") != "Tweet":
        return None

    rest_id = result.get("rest_id")
    legacy = result.get("legacy") or {}
    if not rest_id or not legacy:
        return None

    user = (result.get("core", {}).get("user_results", {}).get("result", {}).get("core") or {})
    author_name = user.get("name", "")
    author_handle = user.get("screen_name", "")

    # Prefer note_tweet (long-form) when present
    note = result.get("note_tweet", {}).get("note_tweet_results", {}).get("result")
    if note and note.get("text"):
        text_raw = note["text"]
        url_entities = (note.get("entity_set", {}) or {}).get("urls", [])
    else:
        text_raw = legacy.get("full_text", "")
        url_entities = legacy.get("entities", {}).get("urls", [])

    text = _expand_tco(text_raw, url_entities)
    timestamp = _to_iso(legacy["created_at"])
    source_url = f"https://x.com/{author_handle}/status/{rest_id}" if author_handle else f"https://x.com/i/status/{rest_id}"

    bookmark = {
        "tweet_id": rest_id,
        "author_name": author_name,
        "author_handle": author_handle,
        "full_text": text,
        "timestamp": timestamp,
        "source_url": source_url,
    }
    media = _extract_media(legacy)
    if media:
        bookmark["media"] = media
    return bookmark


def extract_cursor(entries: list[dict]) -> str | None:
    """Return the 'Bottom' cursor value if present."""
    for entry in reversed(entries):
        content = entry.get("content", {})
        if content.get("entryType") == "TimelineTimelineCursor" and content.get("cursorType") == "Bottom":
            return content.get("value")
    return None
