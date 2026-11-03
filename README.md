# 2oo1finder

Public producer-credit discovery site for YouTube, hosted on Render.

## Deploy

1. Create a Render Web Service connected to `fngkraze/FNGFinder` (or use `render.yaml` as a Blueprint).
2. Set the secret environment variable `YOUTUBE_API_KEY` in Render. **Never commit the API key.**
3. Deploy. The server executes YouTube API requests; the browser does not see the key.
4. Optional: set `MAX_DAILY_SCANS` (default 20). Each uncached search uses 4 YouTube search requests (100 quota units each) plus inexpensive metadata calls. A default YouTube quota of 10,000 units/day means 20 scans is a conservative starting budget.

## What it does

Visitors enter a producer name, optionally their YouTube @handle. The app searches titles and descriptions for explicit credits and broad name-only mentions, excludes their own matching channel uploads, filters likely beat listings, and exports links/metadata to CSV. It **cannot recognize uncredited audio** and can miss unindexed tracks. Results are leads, not conclusive verification.

## Operational note

The built-in rate limiting/cache are in-memory and intended for one Render worker. They reset on restart, and are not sufficient against sustained abuse or multi-instance deployment. Use Redis, a shared daily quota counter, and external bot protection before advertising the site widely. Restrict your Google API key to YouTube Data API v3.
