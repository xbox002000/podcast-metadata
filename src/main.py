"""Apify Actor: Podcast show / episode metadata (iTunes + RSS). No transcription. No email/contacts."""
from __future__ import annotations

import asyncio
import datetime as dt
import resource
import time
from collections import Counter

import httpx
from apify import Actor

from charging import Charger
from inputs import collect_collection_ids, collect_feed_urls, collect_keywords
from itunes import lookup_podcasts, search_podcasts, show_from_itunes
from rss_episodes import parse_feed

EVENT_SHOW = "podcast-show"
EVENT_EPISODE = "podcast-episode"
UA = "Mozilla/5.0 (compatible; PodcastMetadata/0.1; +https://apify.com)"
PUSH_BATCH = 40


def _merge_show(base: dict, overlay: dict) -> dict:
    """Fill missing show fields from RSS overlay without inventing emails."""
    out = dict(base)
    for k, v in overlay.items():
        if v is None or v == [] or v == "":
            continue
        if k == "genres":
            existing = list(out.get("genres") or [])
            for g in v:
                if g not in existing:
                    existing.append(g)
            out["genres"] = existing
            continue
        if out.get(k) in (None, "", []):
            out[k] = v
    return out


async def main() -> None:
    async with Actor:
        t0 = time.time()
        inp = await Actor.get_input() or {}
        charger = Charger(EVENT_SHOW)

        country = (inp.get("country") or "us").strip().lower() or "us"
        include_episodes = bool(inp.get("includeEpisodes", True))
        max_shows = int(inp.get("maxShows", 50) or 0) or None
        max_ep = int(inp.get("maxEpisodesPerShow", 50) or 0) or None
        search_limit = int(inp.get("searchLimitPerKeyword", 25) or 25)
        timeout = float(inp.get("requestTimeoutSecs", 30))
        retries = int(inp.get("maxRetries", 2))
        conc = max(1, min(int(inp.get("maxConcurrency", 4)), 20))
        charge_failed = bool(inp.get("chargeFailedShows", False))
        max_feed_bytes = int(inp.get("maxFeedBytes", 5_000_000) or 5_000_000)

        keywords = collect_keywords(inp.get("searchKeywords") or inp.get("keywords"))
        collection_ids = collect_collection_ids(
            inp.get("podcastIds") or inp.get("collectionIds") or inp.get("applePodcastIds")
        )
        feed_urls, feed_warns = collect_feed_urls(inp.get("feedUrls"))
        for w in feed_warns:
            Actor.log.warning(w)

        if not keywords and not collection_ids and not feed_urls:
            raise ValueError(
                "Provide at least one of: searchKeywords, podcastIds (Apple collection ID), or feedUrls. "
                "This Actor returns metadata only — no audio download, no transcription, no email/contacts."
            )

        # Budget: prefer show budget; episodes optional
        b_show = charger.budget(EVENT_SHOW)
        if b_show is not None and (max_shows is None or b_show < max_shows):
            Actor.log.info(f"Spending limit allows about {b_show} show(s); capping maxShows.")
            max_shows = b_show

        proxy_url = None
        if (inp.get("proxyConfiguration") or {}).get("useApifyProxy") or (
            inp.get("proxyConfiguration") or {}
        ).get("proxyUrls"):
            pc = await Actor.create_proxy_configuration(actor_proxy_input=inp["proxyConfiguration"])
            proxy_url = await pc.new_url() if pc else None

        headers = {
            "User-Agent": inp.get("userAgent") or UA,
            "Accept": "application/json, application/rss+xml, application/atom+xml, application/xml, text/xml, */*;q=0.8",
        }
        limits = httpx.Limits(max_connections=conc * 2, max_keepalive_connections=conc)

        shows: list[dict] = []
        seen_show_keys: set[str] = set()
        status_counts: Counter = Counter()
        charged_shows = 0
        charged_eps = 0
        free_n = 0
        pending_shows: list[dict] = []
        pending_eps: list[dict] = []
        pending_free: list[dict] = []
        lock = asyncio.Lock()
        episodes_total = 0
        feeds_ok = 0
        feeds_err = 0
        feeds_empty = 0
        lookup_misses = 0
        shows_processed = 0

        def show_key(s: dict) -> str:
            if s.get("collectionId"):
                return f"id:{s['collectionId']}"
            if s.get("feedUrl"):
                return f"feed:{(s['feedUrl'] or '').rstrip('/').casefold()}"
            return f"title:{(s.get('title') or '').casefold()}|{(s.get('artist') or '').casefold()}"

        async def flush(force: bool = False) -> None:
            nonlocal charged_shows, charged_eps, free_n, pending_shows, pending_eps, pending_free
            if pending_shows and (force or len(pending_shows) >= PUSH_BATCH):
                batch = pending_shows
                pending_shows = []
                n = await charger.push_and_charge(batch, EVENT_SHOW)
                charged_shows += n
                if n < len(batch):
                    charger.limit_reached = True
            if pending_eps and (force or len(pending_eps) >= PUSH_BATCH):
                batch = pending_eps
                pending_eps = []
                n = await charger.push_and_charge(batch, EVENT_EPISODE)
                charged_eps += n
                if n < len(batch):
                    charger.limit_reached = True
            if pending_free and (force or len(pending_free) >= PUSH_BATCH):
                batch = pending_free
                pending_free = []
                await charger.push_free(batch)
                free_n += len(batch)

        async with httpx.AsyncClient(
            headers=headers, proxy=proxy_url, limits=limits, http2=False, follow_redirects=True
        ) as client:
            # 1) Search
            for term in keywords:
                if max_shows is not None and len(shows) >= max_shows:
                    break
                results, err = await search_podcasts(
                    client, term, country=country, limit=search_limit, timeout=timeout
                )
                if err:
                    Actor.log.warning(err)
                    row = {
                        "kind": "show",
                        "status": "error",
                        "source": "itunes-search",
                        "searchTerm": term,
                        "collectionId": None,
                        "title": None,
                        "artist": None,
                        "feedUrl": None,
                        "genres": [],
                        "errors": [err],
                    }
                    async with lock:
                        status_counts["error"] += 1
                        if charge_failed:
                            pending_shows.append(row)
                        else:
                            pending_free.append(row)
                        await flush()
                    continue
                for raw in results:
                    s = show_from_itunes(raw, source="itunes-search", search_term=term)
                    k = show_key(s)
                    if k in seen_show_keys:
                        continue
                    if max_shows is not None and len(shows) >= max_shows:
                        break
                    seen_show_keys.add(k)
                    shows.append(s)

            # 2) Lookup by ID
            if collection_ids and (max_shows is None or len(shows) < max_shows):
                # Batch lookup in chunks of 20
                remaining_ids = list(collection_ids)
                while remaining_ids and (max_shows is None or len(shows) < max_shows):
                    chunk = remaining_ids[:20]
                    remaining_ids = remaining_ids[20:]
                    results, err = await lookup_podcasts(client, chunk, country=country, timeout=timeout)
                    if err:
                        Actor.log.warning(err)
                        for cid in chunk:
                            row = {
                                "kind": "show",
                                "status": "error",
                                "source": "itunes-lookup",
                                "collectionId": cid,
                                "title": None,
                                "artist": None,
                                "feedUrl": None,
                                "genres": [],
                                "errors": [err],
                            }
                            async with lock:
                                status_counts["error"] += 1
                                if charge_failed:
                                    pending_shows.append(row)
                                else:
                                    pending_free.append(row)
                                await flush()
                        continue
                    found_ids = {str(r.get("collectionId") or r.get("trackId")) for r in results}
                    for raw in results:
                        s = show_from_itunes(raw, source="itunes-lookup")
                        k = show_key(s)
                        if k in seen_show_keys:
                            continue
                        if max_shows is not None and len(shows) >= max_shows:
                            break
                        seen_show_keys.add(k)
                        shows.append(s)
                    for cid in chunk:
                        if cid not in found_ids:
                            row = {
                                "kind": "show",
                                "status": "error",
                                "source": "itunes-lookup",
                                "collectionId": cid,
                                "title": None,
                                "artist": None,
                                "feedUrl": None,
                                "genres": [],
                                "errors": ["No podcast found for this collection ID"],
                            }
                            async with lock:
                                lookup_misses += 1
                                if charge_failed:
                                    pending_shows.append(row)
                                else:
                                    pending_free.append(row)
                                await flush()

            # 3) Direct feed URLs as shows (may not have Apple metadata)
            existing_feeds = {
                (s.get("feedUrl") or "").rstrip("/").casefold()
                for s in shows
                if s.get("feedUrl")
            }
            for fu in feed_urls:
                if max_shows is not None and len(shows) >= max_shows:
                    break
                fu_key = fu.rstrip("/").casefold()
                k = f"feed:{fu_key}"
                if k in seen_show_keys or fu_key in existing_feeds:
                    continue
                seen_show_keys.add(k)
                existing_feeds.add(fu_key)
                shows.append(
                    {
                        "kind": "show",
                        "status": "success",
                        "source": "feed-url",
                        "searchTerm": None,
                        "collectionId": None,
                        "title": None,
                        "artist": None,
                        "feedUrl": fu,
                        "genres": [],
                        "primaryGenre": None,
                        "country": country.upper(),
                        "trackCount": None,
                        "artworkUrl": None,
                        "collectionViewUrl": None,
                        "releaseDate": None,
                        "explicitness": None,
                        "language": None,
                        "description": None,
                        "errors": [],
                    }
                )

            Actor.log.info(
                f"{len(shows)} show(s) queued; includeEpisodes={include_episodes}; "
                f"maxEpisodesPerShow={max_ep or 'unlimited'}; concurrency={conc}; "
                f"No transcription / no audio download / no email fields."
            )
            await Actor.set_status_message(f"Resolving {len(shows)} podcast show(s)…")

            sem = asyncio.Semaphore(conc)

            async def fetch_feed_bytes(url: str) -> tuple[bytes | None, list[str]]:
                errs: list[str] = []
                last_err = None
                to = httpx.Timeout(timeout, connect=min(15.0, timeout), read=timeout, write=timeout, pool=timeout)
                for attempt in range(max(1, retries + 1)):
                    try:
                        r = await client.get(url, timeout=to)
                        if r.status_code >= 400:
                            last_err = f"HTTP {r.status_code}"
                            await asyncio.sleep(0.2 * (attempt + 1))
                            continue
                        cl = r.headers.get("content-length")
                        if cl and cl.isdigit() and int(cl) > max_feed_bytes:
                            last_err = f"Feed Content-Length {cl} exceeds maxFeedBytes {max_feed_bytes}"
                            break
                        data = r.content or b""
                        if len(data) > max_feed_bytes:
                            last_err = f"Feed body {len(data)} exceeds maxFeedBytes {max_feed_bytes}"
                            break
                        if not data.strip():
                            last_err = "Empty feed body"
                            continue
                        return data, []
                    except Exception as e:  # noqa: BLE001
                        last_err = f"{type(e).__name__}: {str(e)[:160]}"
                        await asyncio.sleep(0.25 * (attempt + 1))
                if last_err:
                    errs.append(last_err)
                return None, errs

            async def process_show(show: dict) -> None:
                nonlocal episodes_total, feeds_ok, feeds_err, feeds_empty, shows_processed, lookup_misses
                if charger.limit_reached:
                    return
                async with sem:
                    if charger.limit_reached:
                        return
                    feed_url = show.get("feedUrl")
                    episodes: list[dict] = []
                    feed_errs: list[str] = []

                    feed_stat = None  # ok | err | empty
                    if feed_url and (include_episodes or show.get("source") == "feed-url" or not show.get("title")):
                        raw, ferrs = await fetch_feed_bytes(feed_url)
                        feed_errs.extend(ferrs)
                        if raw is None:
                            feed_stat = "err"
                            if show.get("source") == "feed-url" or not show.get("title"):
                                show["status"] = "error"
                                show["errors"] = (show.get("errors") or []) + (ferrs or ["Feed fetch failed"])
                            else:
                                # Keep iTunes show metadata; episodes skipped free
                                show["errors"] = (show.get("errors") or []) + [
                                    f"Episode feed failed (show metadata kept): {ferrs[0] if ferrs else 'unknown'}"
                                ]
                        else:
                            overlay, episodes, warns = parse_feed(
                                raw, feed_url=feed_url, max_episodes=max_ep if include_episodes else 0
                            )
                            if not include_episodes:
                                episodes = []
                            show = _merge_show(show, overlay)
                            if warns and not episodes and not overlay.get("title"):
                                feed_stat = "empty"
                                show["status"] = "error"
                                show["errors"] = (show.get("errors") or []) + warns
                            else:
                                feed_stat = "ok"
                                if warns:
                                    show["errors"] = (show.get("errors") or []) + warns
                                if include_episodes and not episodes:
                                    # Parsed OK but zero episodes — still billable show; empty eps free
                                    feed_stat = "ok_empty"
                    elif include_episodes and not feed_url:
                        show["errors"] = (show.get("errors") or []) + [
                            "No feedUrl — episodes skipped (metadata-only from iTunes)"
                        ]

                    # Tag episodes with collectionId when known
                    cid = show.get("collectionId")
                    for ep in episodes:
                        ep["collectionId"] = cid
                        ep["artist"] = show.get("artist")
                        ep["source"] = "rss"

                    billable_show = show.get("status") == "success" and (
                        show.get("title") or show.get("feedUrl") or show.get("collectionId")
                    )
                    if charge_failed and show.get("status") == "error":
                        billable_show = True

                    async with lock:
                        if feed_stat in ("ok", "ok_empty"):
                            feeds_ok += 1
                        if feed_stat == "err":
                            feeds_err += 1
                        if feed_stat in ("empty", "ok_empty"):
                            feeds_empty += 1
                        status_counts[show.get("status") or "unknown"] += 1
                        shows_processed += 1
                        if billable_show:
                            pending_shows.append(show)
                        else:
                            pending_free.append(show)
                        for ep in episodes:
                            episodes_total += 1
                            if ep.get("status") == "success" and (ep.get("title") or ep.get("guid") or ep.get("enclosureUrl")):
                                pending_eps.append(ep)
                            else:
                                pending_free.append(ep)
                        await flush()
                        await Actor.set_status_message(
                            f"Shows {shows_processed}/{len(shows)} — ok {status_counts['success']}, "
                            f"err {status_counts['error']}, episodes {episodes_total}"
                        )

            await asyncio.gather(*(process_show(dict(s)) for s in shows))
            async with lock:
                await flush(force=True)

        peak_mb = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)
        store = await Actor.open_key_value_store()
        summary = {
            "totalShowsAttempted": len(shows),
            "showsProcessed": shows_processed,
            "showsSuccess": status_counts["success"],
            "showsError": status_counts["error"],
            "lookupMisses": lookup_misses,
            "episodesEmitted": episodes_total,
            "chargedShows": charged_shows,
            "chargedEpisodes": charged_eps,
            "free": free_n,
            "feedsOk": feeds_ok,
            "feedsErr": feeds_err,
            "feedsEmpty": feeds_empty,
            "includeEpisodes": include_episodes,
            "maxEpisodesPerShow": max_ep,
            "maxShows": max_shows,
            "maxFeedBytes": max_feed_bytes,
            "country": country,
            "keywords": keywords,
            "collectionIds": collection_ids,
            "feedUrls": feed_urls,
            "noTranscription": True,
            "noAudioDownload": True,
            "noEmailContacts": True,
            "chargedEvents": dict(charger.counts),
            "durationSecs": round(time.time() - t0, 1),
            "peakMemoryMb": peak_mb,
            "finishedAt": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        }
        await store.set_value("SUMMARY", summary)
        await store.set_value("OUTPUT", summary)
        msg = (
            f"Done: shows ok {status_counts['success']} / err {status_counts['error']}, "
            f"episodes {episodes_total}; charged shows {charged_shows}, eps {charged_eps}, free {free_n}. "
            f"No transcription. Charged events: {charger.counts or 'nothing'}."
        )
        Actor.log.info(msg + f" Peak memory {peak_mb} MB, {summary['durationSecs']} s.")
        await Actor.set_status_message(msg, is_terminal=True)


if __name__ == "__main__":
    asyncio.run(main())
