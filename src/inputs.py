"""Normalize search keywords, Apple collection IDs, and feed URLs."""
from __future__ import annotations

import re
from typing import Any, Iterable

_URL_RE = re.compile(r"^[a-z][a-z0-9+.-]*://", re.I)
_ID_RE = re.compile(r"(?:id)?(\d{5,})", re.I)


def normalize_url(raw: str, default_scheme: str = "https") -> str | None:
    s = (raw or "").strip().strip("\"'<>")
    if not s or s.startswith("#") or s.startswith("data:"):
        return None
    if not _URL_RE.match(s):
        if " " in s or "." not in s:
            return None
        s = f"{default_scheme}://{s}"
    if not s.lower().startswith(("http://", "https://")):
        return None
    return s.split("#", 1)[0]


def collect_keywords(values: Iterable[Any] | str | None) -> list[str]:
    """Deduplicate search keywords, preserve order."""
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    out: list[str] = []
    seen: set[str] = set()
    for v in values:
        if isinstance(v, dict):
            v = v.get("term") or v.get("keyword") or v.get("q") or ""
        s = str(v or "").strip()
        if not s:
            continue
        key = s.casefold()
        if key not in seen:
            seen.add(key)
            out.append(s)
    return out


def collect_collection_ids(values: Iterable[Any] | str | int | None) -> list[str]:
    """Extract Apple podcast/collection IDs from ints, strings, or Apple Podcasts URLs."""
    if values is None:
        return []
    if isinstance(values, (str, int)):
        values = [values]
    out: list[str] = []
    seen: set[str] = set()
    for v in values:
        if isinstance(v, dict):
            v = v.get("id") or v.get("collectionId") or v.get("url") or ""
        if isinstance(v, int):
            cid = str(v)
        else:
            s = str(v or "").strip()
            if not s:
                continue
            m = _ID_RE.search(s)
            cid = m.group(1) if m else (s if s.isdigit() else "")
        if cid and cid not in seen:
            seen.add(cid)
            out.append(cid)
    return out


def collect_feed_urls(values: Iterable[Any] | str | None) -> tuple[list[str], list[str]]:
    """Return (urls, warnings). Deduplicates, order preserved."""
    warnings: list[str] = []
    if values is None:
        return [], warnings
    if isinstance(values, str):
        values = [values]
    raw: list[str] = []
    for v in values:
        if isinstance(v, dict):
            if v.get("url"):
                raw.append(str(v["url"]))
            else:
                warnings.append("Ignored feed entry without url field")
        elif isinstance(v, str):
            s = v.strip()
            if not s:
                continue
            # Single URL (may contain commas inside query rarely — treat whole line as URL if scheme present)
            if re.match(r"https?://", s, re.I):
                raw.append(s)
            else:
                for part in re.split(r"[\s,]+", s):
                    if part:
                        raw.append(part)
        else:
            warnings.append(f"Ignored non-string feed input: {type(v).__name__}")
    seen: set[str] = set()
    out: list[str] = []
    for r in raw:
        u = normalize_url(r)
        if u is None:
            warnings.append(f"Ignored invalid feed URL: {r[:200]}")
            continue
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out, warnings
