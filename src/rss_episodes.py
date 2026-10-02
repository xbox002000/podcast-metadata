"""Parse podcast RSS/Atom for show + episode metadata. No audio download. No email fields."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from time import struct_time
from typing import Any

import feedparser

# Strip mailto / obvious emails from free-text so we never emit contacts accidentally
_EMAIL_RE = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.I)


def _scrub(text: str | None) -> str | None:
    if not text:
        return None
    s = _EMAIL_RE.sub("[redacted]", str(text))
    s = s.strip()
    return s or None


def _struct_to_iso(st: struct_time | None) -> str | None:
    if not st:
        return None
    try:
        return datetime(*st[:6], tzinfo=timezone.utc).isoformat(timespec="seconds")
    except Exception:  # noqa: BLE001
        return None


def _parse_duration(raw: Any) -> tuple[str | None, int | None]:
    """Return (durationRaw, durationSecs). itunes:duration may be HH:MM:SS or seconds."""
    if raw is None:
        return None, None
    s = str(raw).strip()
    if not s:
        return None, None
    if s.isdigit():
        return s, int(s)
    parts = s.split(":")
    try:
        if len(parts) == 3:
            h, m, sec = int(parts[0]), int(parts[1]), int(float(parts[2]))
            return s, h * 3600 + m * 60 + sec
        if len(parts) == 2:
            m, sec = int(parts[0]), int(float(parts[1]))
            return s, m * 60 + sec
    except ValueError:
        return s, None
    return s, None


def _enclosure(entry: dict) -> tuple[str | None, str | None, int | None]:
    """Return (url, type, length) from first enclosure — metadata only, never download."""
    encs = entry.get("enclosures") or []
    if not encs and entry.get("links"):
        for link in entry["links"]:
            if (link.get("rel") or "").lower() == "enclosure" or (link.get("type") or "").startswith("audio"):
                encs = [link]
                break
    if not encs:
        return None, None, None
    e = encs[0]
    url = (e.get("href") or e.get("url") or "").strip() or None
    ctype = (e.get("type") or "").strip() or None
    length = e.get("length")
    try:
        length_i = int(length) if length not in (None, "") else None
    except (TypeError, ValueError):
        length_i = None
    return url, ctype, length_i


def _notes_hash(entry: dict) -> str | None:
    parts = []
    for key in ("summary", "description"):
        if entry.get(key):
            parts.append(str(entry[key]))
    for c in entry.get("content") or []:
        if c.get("value"):
            parts.append(str(c["value"]))
    blob = "\n".join(parts).strip()
    if not blob:
        return None
    return hashlib.sha256(blob.encode("utf-8", errors="replace")).hexdigest()


def _categories(feed: dict, entry: dict | None = None) -> list[str]:
    tags = []
    src = entry if entry is not None else feed
    for t in src.get("tags") or []:
        term = (t.get("term") or t.get("label") or "").strip()
        if term and term not in tags:
            tags.append(term)
    return tags


def parse_feed(
    raw: bytes | str,
    *,
    feed_url: str,
    max_episodes: int | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    """Parse feed bytes/text → (show_overlay, episodes, warnings).

    show_overlay fills fields missing from iTunes (language, description).
    Episodes never include email/owner contacts. Audio is never downloaded.
    """
    warnings: list[str] = []
    parsed = feedparser.parse(raw)
    if getattr(parsed, "bozo", False) and parsed.bozo_exception:
        warnings.append(f"feedparser bozo: {type(parsed.bozo_exception).__name__}: {str(parsed.bozo_exception)[:160]}")

    feed = parsed.feed or {}
    title = _scrub((feed.get("title") or "").strip() or None)
    artist = _scrub(
        (feed.get("author") or feed.get("publisher") or (feed.get("author_detail") or {}).get("name") or "").strip()
        or None
    )
    # Explicitly DO NOT read itunes_owner / author email / managingEditor
    link = (feed.get("link") or "").strip() or None
    language = (feed.get("language") or "").strip() or None
    description = _scrub((feed.get("subtitle") or feed.get("summary") or feed.get("description") or "").strip() or None)
    genres = _categories(feed)
    artwork = None
    image = feed.get("image") or {}
    if isinstance(image, dict):
        artwork = (image.get("href") or image.get("url") or "").strip() or None
    if not artwork and feed.get("itunes_image"):
        img = feed.get("itunes_image")
        if isinstance(img, dict):
            artwork = (img.get("href") or "").strip() or None
        elif isinstance(img, str):
            artwork = img.strip() or None

    show_overlay = {
        "title": title,
        "artist": artist,
        "feedUrl": feed_url,
        "language": language,
        "description": description[:2000] if description else None,
        "genres": genres,
        "artworkUrl": artwork,
        "collectionViewUrl": link,
        "trackCount": len(parsed.entries or []),
    }

    episodes: list[dict[str, Any]] = []
    entries = list(parsed.entries or [])
    if max_episodes is not None:
        entries = entries[: max(0, max_episodes)]

    for entry in entries:
        enc_url, enc_type, enc_len = _enclosure(entry)
        dur_raw, dur_secs = _parse_duration(entry.get("itunes_duration") or entry.get("duration"))
        published = _struct_to_iso(entry.get("published_parsed") or entry.get("updated_parsed"))
        if not published and entry.get("published"):
            try:
                published = parsedate_to_datetime(str(entry["published"])).astimezone(timezone.utc).isoformat(
                    timespec="seconds"
                )
            except Exception:  # noqa: BLE001
                published = str(entry.get("published"))[:40]

        guid = entry.get("id") or entry.get("guid") or None
        if isinstance(guid, dict):
            guid = guid.get("value") or guid.get("guid")
        guid = str(guid).strip() if guid else None

        ep_title = _scrub((entry.get("title") or "").strip() or None)
        ep_link = (entry.get("link") or "").strip() or None
        summary = _scrub((entry.get("summary") or entry.get("description") or "").strip() or None)
        if summary and len(summary) > 500:
            summary = summary[:500] + "…"

        episodes.append(
            {
                "kind": "episode",
                "status": "success",
                "guid": guid,
                "title": ep_title,
                "showTitle": title,
                "feedUrl": feed_url,
                "published": published,
                "duration": dur_raw,
                "durationSecs": dur_secs,
                "enclosureUrl": enc_url,
                "enclosureType": enc_type,
                "enclosureLength": enc_len,
                "episodeLink": ep_link,
                "genres": _categories(feed, entry) or genres,
                "summary": summary,
                "showNotesHash": _notes_hash(entry),
                "errors": [],
            }
        )

    if not entries and not title and not show_overlay.get("description"):
        warnings.append("Empty or unparseable feed")

    return show_overlay, episodes, warnings
