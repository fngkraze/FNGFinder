# 2oo1finder

Public producer-credit discovery site for YouTube, hosted on Render.

## Deploy

1. Create a Render Web Service connected to `fngkraze/FNGFinder` (or use `render.yaml` as a Blueprint).
2. Set the secret environment variable `YOUTUBE_API_KEY` in Render. **Never commit the API key.**
3. Deploy. The server executes YouTube API requests; the browser does not see the key.
4. Optional: set `MAX_DAILY_SCANS` (default 5). Each uncached search uses up to 16 YouTube search requests (100 quota units each) plus inexpensive metadata calls. A default YouTube quota of 10,000 units/day means 5 full scans is a conservative starting budget.

## What it does

Visitors enter 1–3 comma-separated producer names/aliases, for example `mmijordan, mmi jordan` or `fngkraze, fng kraze`, optionally their YouTube @handle. The app runs up to sixteen YouTube search requests per scan across all aliases (single-name searches keep the eight-variation, two-page depth), checks full titles/descriptions, and includes review leads without visible credits, excludes their own matching channel uploads, filters likely beat listings, and exports links/metadata to CSV. It **cannot recognize uncredited audio** and can miss unindexed tracks. Results are leads, not conclusive verification.

## Operational note

Multiple aliases share the same 16-request scan budget, so each alias may have less depth than a single-name search. Results display the matching alias and CSV exports include it. The website's search results are not permanently retained across Render restarts, unlike the desktop app's local database. The built-in rate limiting/cache are in-memory and intended for one Render worker. They reset on restart, and are not sufficient against sustained abuse or multi-instance deployment. Use Redis, a shared daily quota counter, and external bot protection before advertising the site widely. Restrict your Google API key to YouTube Data API v3.
