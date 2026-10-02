"""iTunes Search / Lookup public API helpers (no API key)."""
from __future__ import annotations

from typing import Any

import httpx

SEARCH_URL = "https://itunes.apple.com/search"
LOOKUP_URL = "https://itunes.apple.com/lookup"


def _genres(r: dict) -> list[str]:
    g = r.get("genres") or []
    out: list[str] = []
    if isinstance(g, list):
        for x in g:
            s = str(x).strip()
            if s and s.casefold() != "podcasts" and s not in out:
                out.append(s)
    primary = (r.get("primaryGenreName") or "").strip()
    if primary and primary.casefold() != "podcasts" and primary not in out:
        out.insert(0, primary)
    return out


def show_from_itunes(r: dict, *, source: str, search_term: str | None = None) -> dict[str, Any]:
    """Map an iTunes podcast result to our show schema. Never includes email/contacts."""
    collection_id = r.get("collectionId") or r.get("trackId")
    return {
        "kind": "show",
        "status": "success",
        "source": source,
        "searchTerm": search_term,
        "collectionId": str(collection_id) if collection_id is not None else None,
        "title": (r.get("collectionName") or r.get("trackName") or "").strip() or None,
        "artist": (r.get("artistName") or "").strip() or None,
        "feedUrl": (r.get("feedUrl") or "").strip() or None,
        "genres": _genres(r),
        "primaryGenre": (r.get("primaryGenreName") or "").strip() or None,
        "country": (r.get("country") or "").strip() or None,
        "trackCount": r.get("trackCount"),
        "artworkUrl": (r.get("artworkUrl600") or r.get("artworkUrl100") or r.get("artworkUrl60") or "").strip()
        or None,
        "collectionViewUrl": (r.get("collectionViewUrl") or r.get("trackViewUrl") or "").strip() or None,
        "releaseDate": (r.get("releaseDate") or "").strip() or None,
        "explicitness": (r.get("collectionExplicitness") or r.get("trackExplicitness") or "").strip() or None,
        "language": None,
        "description": None,
        "errors": [],
    }


async def search_podcasts(
    client: httpx.AsyncClient,
    term: str,
    *,
    country: str = "us",
    limit: int = 25,
    timeout: float = 30.0,
) -> tuple[list[dict], str | None]:
    """Return (results, error). results are raw iTunes dicts with kind podcast."""
    params = {
        "term": term,
        "media": "podcast",
        "entity": "podcast",
        "country": country.lower(),
        "limit": max(1, min(int(limit), 200)),
    }
    try:
        r = await client.get(SEARCH_URL, params=params, timeout=timeout)
        r.raise_for_status()
        data = r.json()
    except Exception as e:  # noqa: BLE001
        return [], f"iTunes search failed: {type(e).__name__}: {str(e)[:200]}"
    results = []
    for item in data.get("results") or []:
        kind = (item.get("kind") or "").lower()
        wrapper = (item.get("wrapperType") or "").lower()
        if kind == "podcast" or (wrapper == "track" and item.get("feedUrl")) or item.get("collectionId"):
            # Prefer podcast shows (not episodes)
            if kind in ("podcast-episode",):
                continue
            results.append(item)
    return results, None


async def lookup_podcasts(
    client: httpx.AsyncClient,
    ids: list[str],
    *,
    country: str = "us",
    timeout: float = 30.0,
) -> tuple[list[dict], str | None]:
    """Lookup by collection/track IDs. Returns (raw results, error)."""
    if not ids:
        return [], None
    # API accepts comma-separated ids
    params = {
        "id": ",".join(ids),
        "entity": "podcast",
        "country": country.lower(),
    }
    try:
        r = await client.get(LOOKUP_URL, params=params, timeout=timeout)
        r.raise_for_status()
        data = r.json()
    except Exception as e:  # noqa: BLE001
        return [], f"iTunes lookup failed: {type(e).__name__}: {str(e)[:200]}"
    results = []
    for item in data.get("results") or []:
        kind = (item.get("kind") or "").lower()
        if kind == "podcast-episode":
            continue
        if item.get("collectionId") or item.get("feedUrl") or kind == "podcast":
            results.append(item)
    return results, None
