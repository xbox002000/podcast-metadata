# Podcast Metadata — Shows & Episodes (**No Transcription**)

**Apple Podcasts / iTunes Search & Lookup plus RSS episode metadata — without downloading audio or running Whisper.**

Give this Actor search keywords, Apple podcast/collection IDs, and/or feed URLs. It returns show rows (title, artist, feedUrl, genres, artwork, trackCount) and optional episode rows (guid, title, published, duration, enclosureUrl, showNotesHash). **No transcription. No audio download. No email or contact fields.** Failed lookups and empty/broken feeds are free by default. Default memory: **256 MB**.

Chain to **[RSS & Atom to Markdown](https://apify.com/YOUR_USERNAME/rss-atom-to-markdown)** when you want show notes / entry bodies as Markdown after you have the feed URL and episode list.

## What you get

- **Show list** from iTunes Search, iTunes Lookup, and/or direct feed URLs
- **Optional episode list** from each show’s RSS (`enclosureUrl` is metadata only)
- Fields: title, artist, feedUrl, genres, country, trackCount, artwork, duration, showNotesHash
- **Hard exclusions:** no Whisper / speech-to-text, no audio bytes stored, no host emails / guest booking contacts
- Failed / empty feeds **free** (unless you opt into `chargeFailedShows`)
- Default **256 MB**; httpx + feedparser only

## Measured results

Cloud benches on build **0.1.2**, **256 MB** (Asia/Taipei 2026-10-02):

| Run | Input | Result | Peak RSS | Platform cost |
|---|---|---|---|---|
| smoke | search+lookup → 4 shows / 9 eps | ok | 119 MB | ~$0.00031 |
| bench | 4 shows / 20 eps | ok | 107 MB | ~$0.00038 |
| search-only | 5 shows, no episodes | ok | ~80–100 MB | ~$0.00027 |
| empty feed | 404 URL | free error | ~77 MB | ~$0.00008 |

See **Measured results** above for measured platform cost.

## Use cases

- Agent / RAG pipelines that need podcast **catalog metadata** without transcription cost
- Discover shows by keyword, then pass `feedUrl` into **rss-atom-to-markdown** for show-notes Markdown
- Enrich known Apple collection IDs with genres, artwork, and recent episode URLs (enclosure links only)

## How to use

```json
{
  "searchKeywords": ["rachel maddow"],
  "podcastIds": ["1211780212"],
  "feedUrls": [{ "url": "https://feeds.simplecast.com/54nAGcIl" }],
  "country": "us",
  "includeEpisodes": true,
  "maxShows": 10,
  "maxEpisodesPerShow": 20
}
```

Example show row:

```json
{
  "kind": "show",
  "status": "success",
  "source": "itunes-search",
  "collectionId": "1211780212",
  "title": "The Indicator from Planet Money",
  "artist": "NPR",
  "feedUrl": "https://feeds.simplecast.com/...",
  "genres": ["Business", "News"],
  "country": "USA",
  "trackCount": 1200,
  "artworkUrl": "https://..."
}
```

Example episode row:

```json
{
  "kind": "episode",
  "status": "success",
  "guid": "...",
  "title": "Episode title",
  "showTitle": "The Indicator from Planet Money",
  "feedUrl": "https://...",
  "published": "2026-09-30T12:00:00+00:00",
  "duration": "00:09:12",
  "durationSecs": 552,
  "enclosureUrl": "https://.../episode.mp3",
  "showNotesHash": "sha256..."
}
```

## Pricing (PPE)

| Event | Price | Notes |
|---|---|---|
| Actor start | **$0.001** | Synthetic start (per GB memory, 1 GB min) |
| `podcast-show` | **$0.001** | Primary — successful show metadata |
| `podcast-episode` | **$0.0005** | Successful episode metadata row |

Failed / empty feeds are free. See **Measured results** above.

## Input highlights

| Field | Role |
|---|---|
| `searchKeywords` | iTunes Search (`media=podcast`) |
| `podcastIds` | Apple collection IDs or podcasts.apple.com `/id…` URLs |
| `feedUrls` | Direct RSS/Atom feeds |
| `includeEpisodes` | Parse feeds for episode rows (default true) |
| `maxShows` / `maxEpisodesPerShow` | Caps |
| `country` | iTunes store country (default `us`) |

## Related Actors

- [RSS & Atom to Markdown — JSON + RAG Chunks](https://apify.com/YOUR_USERNAME/rss-atom-to-markdown) — **metadata → notes MD**: use show `feedUrl` / episode links here, then convert entry bodies to Markdown there.
- [Sitemap URL Extractor](https://apify.com/YOUR_USERNAME/sitemap-url-discovery) — discover document URLs on the open web.
- [SEO Metadata Extract](https://apify.com/YOUR_USERNAME/seo-metadata-extract) — OG / JSON-LD from live pages.

## What this Actor does **not** do

- **No transcription** (no Whisper, no speech-to-text, no captions as a product)
- **No audio download** (enclosure URLs are listed; bytes are never fetched for media)
- **No email / contacts / guest-booking lead scrapes**
- Not a replacement for full-text show-notes Markdown pipelines — use **rss-atom-to-markdown** for that

## License & source code

This Actor is open source under the **GNU Affero General Public License v3.0 (AGPL-3.0)** — see `LICENSE`. Runtime stack: `httpx` (BSD-3-Clause), `feedparser` (BSD-2-Clause), Apify SDK. The full source code is public: https://github.com/xbox002000/podcast-metadata
