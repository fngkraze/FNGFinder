import csv
import hmac
import io
import os
import re
import threading
import time
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from flask import Flask, jsonify, render_template, request

app = Flask(__name__)
API_KEY = os.getenv("YOUTUBE_API_KEY", "").strip()
APP_PASSWORD = os.getenv("APP_PASSWORD", "").strip()
OWN_CHANNEL_ID = os.getenv("OWN_CHANNEL_ID", "").strip()
OWN_CHANNEL_HANDLE = os.getenv("OWN_CHANNEL_HANDLE", "").strip().lstrip("@").lower()
SEARCHES = [
    '"prod. fngkraze"',
    '"prod by fngkraze"',
    '"produced by fngkraze"',
    '"beatmaker fngkraze"',
    '"prod fngkraze"',
    '"fngkraze" song',
]
cache = {}
lock = threading.Lock()
last_scan = 0.0
COOLDOWN_SECONDS = 120
CACHE_SECONDS = 3600


def youtube(endpoint, **params):
    args = {"key": API_KEY, **params}
    url = "https://www.googleapis.com/youtube/v3/" + endpoint + "?" + urlencode(args)
    try:
        with urlopen(Request(url, headers={"User-Agent": "FNGFinder/1.0"}), timeout=18) as resp:
            import json
            return json.load(resp)
    except HTTPError as ex:
        import json
        try:
            details = json.loads(ex.read().decode("utf-8"))
            msg = details.get("error", {}).get("message", str(ex))
        except Exception:
            msg = str(ex)
        raise ValueError(f"YouTube API: {msg}") from ex
    except (URLError, TimeoutError) as ex:
        raise ValueError("Could not connect to YouTube. Try again.") from ex


def is_beat_listing(title, description):
    t = title.lower()
    d = description.lower()
    if re.search(r"\b(type beat|free beat|instrumental|beat tutorial|making a beat|fl studio tutorial|beat pack|loop kit|drum kit)\b", t):
        return True
    if re.search(r"\b(buy this beat|purchase this beat|lease this beat)\b", d) and not re.search(r"\b(official music video|official audio|freestyle)\b", t):
        return True
    return False


def score_video(title, description):
    t = title.lower()
    d = description.lower()
    score = 0
    if re.search(r"\b(prod\.?\s*(by\s*)?fngkraze|produced by fngkraze|beatmaker fngkraze)\b", t):
        score += 5
    if re.search(r"\b(prod\.?\s*(by\s*)?fngkraze|produced by fngkraze|beatmaker fngkraze)\b", d):
        score += 4
    if any(term in t for term in ("official music video", "official audio", "freestyle", "music video", "remix")):
        score += 2
    if "fngkraze" in t or "fngkraze" in d:
        score += 1
    return score


@app.get("/")
def index():
    return render_template("index.html", needs_password=bool(APP_PASSWORD), has_key=bool(API_KEY),
                           own_handle=OWN_CHANNEL_HANDLE)


@app.post("/api/scan")
def scan():
    global last_scan
    if not API_KEY:
        return jsonify(error="Add YOUTUBE_API_KEY to your Render environment variables first."), 503
    data = request.get_json(silent=True) or {}
    if not APP_PASSWORD:
        return jsonify(error="Set APP_PASSWORD in Render before scanning. This protects your YouTube quota."), 503
    supplied = str(data.get("password", ""))
    if not hmac.compare_digest(supplied, APP_PASSWORD):
        return jsonify(error="Incorrect dashboard password."), 401
    hide_beats = bool(data.get("hide_beats", True))
    handle = str(data.get("handle", OWN_CHANNEL_HANDLE)).strip().lstrip("@").lower()[:80]
    if handle and not re.fullmatch(r"[\w.-]+", handle):
        return jsonify(error="Invalid channel handle."), 400
    key = (hide_beats, handle)
    now = time.time()
    with lock:
        hit = cache.get(key)
        if hit and now - hit[0] < CACHE_SECONDS:
            return jsonify(**hit[1], cached=True)
        if now - last_scan < COOLDOWN_SECONDS:
            return jsonify(error="Scan cooldown active. Retry in a couple of minutes to save API quota."), 429
        last_scan = now

    excluded_id = OWN_CHANNEL_ID
    if handle and not excluded_id:
        try:
            info = youtube("channels", part="id", forHandle="@" + handle)
            items = info.get("items", [])
            if items:
                excluded_id = items[0]["id"]
        except ValueError:
            pass

    found = {}
    try:
        for term in SEARCHES:
            response = youtube("search", part="snippet", type="video", q=term,
                               maxResults=50, order="relevance", safeSearch="none")
            for item in response.get("items", []):
                vid = item.get("id", {}).get("videoId")
                if not vid:
                    continue
                snip = item.get("snippet", {})
                channel_id = snip.get("channelId", "")
                channel = snip.get("channelTitle", "")
                title = snip.get("title", "")
                desc = snip.get("description", "")
                if channel_id == excluded_id or channel.lower() == handle or channel.lower() == "@" + handle:
                    continue
                if hide_beats and is_beat_listing(title, desc):
                    continue
                entry = found.setdefault(vid, {
                    "id": vid, "title": title, "channel": channel, "channelId": channel_id,
                    "published": snip.get("publishedAt", "")[:10],
                    "thumbnail": snip.get("thumbnails", {}).get("medium", {}).get("url", ""),
                    "url": "https://www.youtube.com/watch?v=" + vid,
                    "description": desc, "views": 0, "score": score_video(title, desc)
                })
                entry["score"] = max(entry["score"], score_video(title, desc))

        vids = list(found)
        for start in range(0, len(vids), 50):
            details = youtube("videos", part="snippet,statistics", id=",".join(vids[start:start + 50]))
            for item in details.get("items", []):
                vid = item["id"]
                snip = item.get("snippet", {})
                if vid not in found:
                    continue
                found[vid]["description"] = snip.get("description", "")
                found[vid]["views"] = int(item.get("statistics", {}).get("viewCount", 0))
                found[vid]["score"] = score_video(found[vid]["title"], found[vid]["description"])
        results = [r for r in found.values() if "fngkraze" in (r["title"] + " " + r["description"]).lower()]
        if hide_beats:
            results = [r for r in results if not is_beat_listing(r["title"], r["description"])]
        results.sort(key=lambda r: (-r["score"], -r["views"]))
        output = {"results": results, "total": len(results),
                  "searched": SEARCHES, "scanned_at": datetime.now(timezone.utc).isoformat(),
                  "note": "Candidate credited uses only; review each song. Uncredited audio is not detectable."}
        with lock:
            cache[key] = (time.time(), output)
        return jsonify(**output, cached=False)
    except ValueError as ex:
        return jsonify(error=str(ex)), 502


@app.get("/health")
def health():
    return jsonify(status="ok")


if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
